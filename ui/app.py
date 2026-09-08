"""
DQ Compass - interface de pilotage du catalogue.

Quatre ecrans : Controler un fichier, Regles de controle, Fichiers connus,
Journal.

Deux partis pris tiennent toute l'interface :

1. UN FICHIER A LA FOIS. On depose un fichier, le systeme reconnait de quoi il
   s'agit et applique les regles qui le concernent. Les referentiels ne sont
   pas des fichiers a choisir : le moteur va les chercher tout seul.

2. AUCUN JARGON. Un utilisateur qui n'est pas data ne voit jamais un
   template_id ni un dictionnaire de parametres. Il lit "Ne jamais etre vide"
   et "La colonne Montant doit etre renseignee sur chaque ligne". Ce
   vocabulaire vient du catalogue, pas de ce fichier : ajouter un template
   suffit a le rendre pilotable en langage clair, sans toucher a l'UI.

    streamlit run ui/app.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "reporting"))

from dq_engine import (ContratIntrouvable, charger_fichier, detecter_contrat,  # noqa: E402
                       run_dq, validate_control)
from excel_report import build_workbook  # noqa: E402
from store import (SEVERITES, SEVERITES_AIDE, SEVERITES_LIBELLES,  # noqa: E402
                   STATUTS, CatalogueStore, libelle_severite, libelle_statut,
                   phrase_controle)

st.set_page_config(page_title="DQ Compass", page_icon="🧭", layout="wide")

ENTREES = ROOT / "data" / "entrees"
DOSSIERS_CONNUS = [ROOT / "data" / "entrees", ROOT / "data" / "prepared",
                   ROOT / "data" / "ref"]

PUCE_STATUT = {"Actif": "🟢", "Suspendu": "🟠", "Deprecie": "⚪"}
PUCE_RUN = {"PASS": "🟢", "FAIL": "🔴", "ERREUR": "🟠", "NON_APPLICABLE": "⚪"}

# Tolerances proposees en clair. Le champ libre reste possible, mais il n'est
# plus le chemin par defaut : c'est ce qui produit les seuils a 0,9 % saisis
# par megarde.
TOLERANCES = {
    "Aucun écart toléré": 0.0,
    "Jusqu'à 1 % des lignes": 1.0,
    "Jusqu'à 5 % des lignes": 5.0,
    "Jusqu'à 10 % des lignes": 10.0,
}

FREQUENCES = ["Quotidienne", "Hebdomadaire", "Mensuelle", "Trimestrielle",
              "Annuelle", "A la demande"]

# Parametres qui designent une colonne du fichier : liste deroulante, pas saisie.
PARAM_COLONNE = {"column", "field_a", "field_b", "before", "after", "amount",
                 "total_column"}
PARAM_NOMBRE = {"min", "max", "max_lag_days", "tolerance_pct", "couverture_min_pct"}


# --------------------------------------------------------------------------- #
# Etat partage
# --------------------------------------------------------------------------- #
def get_store(force: bool = False) -> CatalogueStore:
    if force or "store" not in st.session_state:
        st.session_state.store = CatalogueStore()
    return st.session_state.store


def enregistrer(store: CatalogueStore, message: str) -> None:
    store.save()
    get_store(force=True)
    st.session_state["flash"] = message


def afficher_flash() -> None:
    if message := st.session_state.pop("flash", None):
        st.success(message)


def nom_lisible(store: CatalogueStore, dataset: str) -> str:
    ds = store.dataset(dataset)
    return f"{ds['libelle']}" if ds else dataset


def scope_datasets(store: CatalogueStore, scope: str) -> list[str]:
    scope = (scope or "").strip()
    if scope in ("", "*"):
        return list(store.datasets)
    return [s.strip() for s in scope.split(",") if s.strip() in store.datasets]


def colonnes_du_scope(store: CatalogueStore, scope: str) -> list[str]:
    noms: list[str] = []
    for ds in scope_datasets(store, scope):
        for c in store.columns_of(ds):
            if c["colonne"] not in noms:
                noms.append(c["colonne"])
    return noms


def roles_disponibles(store: CatalogueStore) -> list[str]:
    roles = {c.get("role", "") for ds in store.datasets
             for c in store.columns_of(ds) if c.get("role")}
    return sorted(roles | {"cle_primaire"})


def params_du_template(tpl: dict) -> tuple[list[list[str]], list[str]]:
    def decouper(txt: str) -> list[list[str]]:
        out = []
        for part in str(txt or "").split(","):
            part = part.strip()
            if part:
                out.append([a.strip() for a in part.split("|") if a.strip()])
        return out
    requis = decouper(tpl.get("params_requis", ""))
    optionnels = [a for grp in decouper(tpl.get("params_optionnels", "")) for a in grp]
    return requis, optionnels


def libelle_param(tpl: dict, nom: str) -> str:
    return tpl.get("params_libelles", {}).get(nom, nom)


# --------------------------------------------------------------------------- #
# Ecran 1 - Controler un fichier
# --------------------------------------------------------------------------- #
def fichiers_connus() -> list[pathlib.Path]:
    trouves: list[pathlib.Path] = []
    for dossier in DOSSIERS_CONNUS:
        if dossier.exists():
            trouves += sorted(p for p in dossier.iterdir()
                              if p.suffix.lower() in {".csv", ".xlsx", ".xls", ".xlsm"})
    return trouves


def bandeau_resultat(run) -> None:
    """Le nombre d'echecs d'abord, en grand. C'est la seule chose qui declenche
    une action ; le taux de conformite ne fait que rassurer."""
    s = run.summary()
    echecs, erreurs = s["fail"], s["erreur"]
    sc = run.scorecard
    bloquants = int(((sc["statut"] == "FAIL") & (sc["severity"].isin(
        ["Critical", "High"]))).sum()) if not sc.empty else 0

    if echecs or erreurs:
        couleur, fond, bordure = "#B3261E", "#FCE8E6", "#F2B8B5"
        titre = f"{echecs} contrôle{'s' if echecs > 1 else ''} en échec"
        if erreurs:
            titre += f" · {erreurs} en erreur technique"
        sous = (f"dont {bloquants} de gravité Bloquant ou Important — "
                f"{s['exceptions']:,} ligne(s) à instruire".replace(",", " ")
                if bloquants else
                f"{s['exceptions']:,} ligne(s) à instruire".replace(",", " "))
    else:
        couleur, fond, bordure = "#1E7B34", "#E6F4EA", "#A8DAB5"
        titre = "Aucun contrôle en échec"
        sous = f"{s['controles']} contrôle(s) passés sans écart"

    st.markdown(
        f"""<div style="background:{fond};border:2px solid {bordure};
        border-radius:12px;padding:22px 26px;margin:6px 0 18px 0;">
        <div style="font-size:46px;font-weight:700;color:{couleur};
        line-height:1.05;">{titre}</div>
        <div style="font-size:15px;color:{couleur};opacity:.85;margin-top:6px;">
        {sous}</div></div>""",
        unsafe_allow_html=True)

    if echecs or erreurs:
        en_echec = sc[sc["statut"].isin(["FAIL", "ERREUR"])]
        for _, r in en_echec.iterrows():
            st.markdown(
                f"**{r['rule_id']} · {r['control_name']}** — "
                f"{libelle_severite(r['severity'])}  \n"
                f"{r['lignes_ko']:,} ligne(s) en écart sur {r['lignes_testees']:,} "
                f"· responsable : {r['owner']}  \n"
                f"→ *{r['remediation_action'] or 'Aucune action de remédiation définie.'}*"
                .replace(",", " "))

    c = st.columns(4)
    c[0].metric("Contrôles exécutés", s["controles"])
    c[1].metric("Sans écart", s["pass"])
    c[2].metric("Lignes en exception", f"{s['exceptions']:,}".replace(",", " "))
    c[3].metric("Règles refusées", s["rejets"],
                help="Règles mal définies, écartées avant exécution.")


def ecran_controler(store: CatalogueStore) -> None:
    st.header("Contrôler un fichier")
    st.caption("Déposez un fichier. Le système reconnaît de quoi il s'agit et "
               "applique les règles qui le concernent. Les référentiels sont "
               "chargés automatiquement, vous n'avez pas à les fournir.")

    onglet_depot, onglet_connu = st.tabs(["📎 Déposer un fichier",
                                          "📂 Choisir un fichier déjà présent"])
    chemin: pathlib.Path | None = None

    with onglet_depot:
        depose = st.file_uploader("Fichier CSV ou Excel", type=["csv", "xlsx", "xls", "xlsm"])
        if depose is not None:
            ENTREES.mkdir(parents=True, exist_ok=True)
            chemin = ENTREES / depose.name
            chemin.write_bytes(depose.getbuffer())
            st.caption(f"Enregistré dans `data/entrees/{depose.name}`")

    with onglet_connu:
        connus = fichiers_connus()
        if connus:
            choix = st.selectbox(
                "Fichier", connus, index=None, placeholder="Choisir un fichier…",
                format_func=lambda p: f"{p.name}  ({p.stat().st_size / 1e6:.1f} Mo)")
            if choix is not None:
                chemin = choix
        else:
            st.info("Aucun fichier dans `data/entrees`, `data/prepared` ou `data/ref`.")

    if chemin is None:
        st.stop()

    # --- Reconnaissance du fichier ----------------------------------------
    try:
        apercu = charger_fichier(chemin)
    except (ValueError, FileNotFoundError) as exc:
        st.error(f"Lecture impossible : {exc}")
        st.stop()

    candidats = detecter_contrat(apercu.columns, store)
    meilleur, score = candidats[0] if candidats else (None, 0.0)
    reconnu = score >= 0.7

    st.divider()
    g, d = st.columns([2, 1])
    with g:
        if reconnu:
            st.success(f"**Fichier reconnu : {nom_lisible(store, meilleur)}**  \n"
                       f"{score:.0%} des colonnes attendues sont présentes. "
                       f"{len(apercu):,} lignes, {len(apercu.columns)} colonnes."
                       .replace(",", " "))
        else:
            st.warning(
                f"**Fichier non reconnu.** Le plus proche est "
                f"« {nom_lisible(store, meilleur) if meilleur else '—'} » avec "
                f"{score:.0%} des colonnes attendues, sous le seuil de 70 %.  \n"
                f"Décrivez ce fichier dans l'écran **Fichiers connus** pour que "
                f"les règles puissent s'y appliquer.")
    with d:
        options = [nom for nom, _ in candidats]
        defaut = options.index(meilleur) if reconnu and meilleur in options else None
        contrat = st.selectbox(
            "Traiter ce fichier comme", options, index=defaut,
            placeholder="Choisir…",
            format_func=lambda n: f"{nom_lisible(store, n)} ({dict(candidats)[n]:.0%})",
            help="Le contrat décrit les colonnes attendues et les règles applicables.")

    with st.expander(f"Aperçu du fichier — {len(apercu):,} lignes".replace(",", " ")):
        st.dataframe(apercu.head(50), width='stretch', hide_index=True)

    if contrat is None:
        st.stop()

    applicables = [c for c in store.active_controls(contrat)]
    st.caption(f"**{len(applicables)} règle(s)** s'appliquent à ce fichier : "
               + ", ".join(f"{c['rule_id']} {c['control_name']}" for c in applicables))

    libelle = st.text_input("Intitulé du contrôle (figure dans le rapport)",
                            f"Contrôle de {chemin.name}")
    if st.button("▶️ Lancer le contrôle", type="primary", width='stretch'):
        with st.spinner("Contrôle en cours…"):
            try:
                run = run_dq(chemin, dataset=contrat, store=store, run_label=libelle)
            except ContratIntrouvable as exc:
                st.error(str(exc))
                st.stop()
            classeur = build_workbook(run, store)
        st.session_state["dernier_run"] = run
        st.session_state["dernier_classeur"] = classeur

    run = st.session_state.get("dernier_run")
    if run is None:
        return

    st.divider()
    st.subheader(f"Résultat — {pathlib.Path(run.fichier).name}")
    bandeau_resultat(run)

    classeur = st.session_state.get("dernier_classeur")
    if classeur and pathlib.Path(classeur).exists():
        st.download_button(
            "⬇️ Télécharger le rapport Excel", type="primary",
            data=pathlib.Path(classeur).read_bytes(),
            file_name=pathlib.Path(classeur).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    t1, t2, t3, t4 = st.tabs(["Détail des contrôles", "Lignes en exception",
                              "Règles refusées", "Journal technique"])
    with t1:
        sc = run.scorecard.copy()
        vue = pd.DataFrame({
            "": sc["statut"].map(PUCE_RUN),
            "Règle": sc["rule_id"],
            "Contrôle": sc["control_name"],
            "Ce qui est vérifié": [
                phrase_controle(store.control(r) or {}, store) for r in sc["rule_id"]],
            "Gravité": sc["severity"].map(libelle_severite),
            "Lignes en écart": sc["lignes_ko"],
            "Lignes testées": sc["lignes_testees"],
            "Indicateur": sc["kpi_nom"] + " : " + sc["kpi_valeur"].astype(str),
            "Responsable": sc["owner"],
        })
        st.dataframe(vue, width='stretch', hide_index=True)
    with t2:
        st.dataframe(run.exceptions.head(2000), width='stretch', hide_index=True)
        st.caption(f"{len(run.exceptions):,} exception(s) — 2 000 premières affichées. "
                   "Le rapport Excel contient le détail complet.".replace(",", " "))
    with t3:
        rejets = pd.DataFrame(run.rejets)
        st.dataframe(rejets, width='stretch', hide_index=True)
        st.caption("Règles écartées par le validateur avant exécution : "
                   "une règle mal définie ne fait jamais échouer un contrôle.")
    with t4:
        st.code("\n".join(run.journal), language="text")
    st.caption(f"Preuves d'exécution : `{run.evidence_path}`")


# --------------------------------------------------------------------------- #
# Ecran 2 - Regles de controle
# --------------------------------------------------------------------------- #
def widget_param(nom: str, tpl: dict, store: CatalogueStore, scope: str,
                 valeur, cle: str):
    label = libelle_param(tpl, nom)
    colonnes = colonnes_du_scope(store, scope)

    if nom == "role":
        options = roles_disponibles(store)
        return st.selectbox(label, options,
                            index=options.index(valeur) if valeur in options else 0,
                            key=cle,
                            help="La règle s'appliquera à toutes les colonnes de ce "
                                 "type, dans tous les fichiers concernés — y compris "
                                 "ceux déclarés plus tard.")
    if nom in PARAM_COLONNE and colonnes:
        return st.selectbox(label, colonnes,
                            index=colonnes.index(valeur) if valeur in colonnes else None,
                            placeholder="Choisir une colonne…", key=cle)
    if nom in PARAM_NOMBRE:
        return st.number_input(label, value=float(valeur) if valeur is not None else 0.0,
                               step=1.0, key=cle)
    if nom == "values":
        brut = st.text_input(
            label, key=cle, placeholder="CDI, CDD, Stage",
            value=", ".join(str(v) for v in valeur) if isinstance(valeur, list)
            else (valeur or ""),
            help="Séparez les valeurs par des virgules.")
        return [v.strip() for v in brut.split(",") if v.strip()]
    if nom in ("columns", "group_by"):
        defaut = valeur if isinstance(valeur, list) else []
        return st.multiselect(label, colonnes,
                              default=[c for c in defaut if c in colonnes], key=cle)
    if nom == "ref_dataset":
        options = list(store.datasets)
        return st.selectbox(label, options,
                            index=options.index(valeur) if valeur in options else None,
                            placeholder="Choisir un référentiel…",
                            format_func=lambda n: nom_lisible(store, n), key=cle)
    if nom == "ref_column":
        ref_ds = st.session_state.get("param_ref_dataset")
        options = [c["colonne"] for c in store.columns_of(ref_ds)] if ref_ds else []
        if not options:
            return st.text_input(label, value=valeur or "", key=cle)
        return st.selectbox(label, options,
                            index=options.index(valeur) if valeur in options else 0,
                            key=cle)
    if nom == "sens":
        options = {"parties_max": "Les parties ne doivent pas dépasser le total",
                   "couverture_min": "Les parties doivent couvrir un minimum du total",
                   "bilateral": "Tout écart compte, dans les deux sens"}
        cles = list(options)
        return st.selectbox(label, cles,
                            index=cles.index(valeur) if valeur in cles else 0,
                            format_func=lambda k: options[k], key=cle)
    return st.text_input(label, value="" if valeur is None else str(valeur), key=cle)


def editeur_regle(store: CatalogueStore, utilisateur: str, control: dict | None) -> None:
    creation = control is None
    control = control or {}
    st.subheader("Créer une règle de contrôle" if creation
                 else f"Modifier {control['rule_id']} · "
                      f"version {control.get('version', 1)}")

    # --- Etape 1 : que veut-on verifier ? ---------------------------------
    st.markdown("#### 1. Que voulez-vous vérifier ?")
    simples = [t for t in store.templates if t.get("niveau", "simple") == "simple"]
    avances = [t for t in store.templates if t.get("niveau") == "avance"]

    tid_courant = control.get("template")
    avance_courant = any(t["template_id"] == tid_courant for t in avances)
    mode_avance = st.toggle("Afficher les règles avancées", value=avance_courant,
                            help="Réconciliations et conditions sur mesure. "
                                 "Réservées aux profils data.")
    proposes = simples + avances if mode_avance else simples

    def etiquette(t: dict) -> str:
        return t.get("libelle_metier", t["template_id"])

    index = next((i for i, t in enumerate(proposes)
                  if t["template_id"] == tid_courant), 0)
    tpl = st.radio("Type de vérification", proposes, index=index,
                   format_func=etiquette, key="edit_template",
                   label_visibility="collapsed")
    st.info(f"**{etiquette(tpl)}** — {tpl.get('explication', '')}  \n"
            f"*Exemple : {tpl.get('exemple_metier', '—')}*")

    # --- Etape 2 : sur quoi ? ---------------------------------------------
    st.markdown(f"#### 2. {tpl.get('question_metier', 'Sur quelles données ?')}")
    datasets = list(store.datasets)
    scope_defaut = control.get("dataset_scope", datasets[0] if datasets else "*")
    tous = st.checkbox("Appliquer à tous les fichiers, présents et à venir",
                       value=scope_defaut.strip() == "*",
                       help="Utile avec un ciblage par type d'information : la règle "
                            "se propage seule aux nouveaux fichiers.")
    if tous:
        scope = "*"
    else:
        choisis = st.multiselect(
            "Fichiers concernés", datasets,
            default=[d for d in scope_defaut.split(",") if d.strip() in datasets]
            or datasets[:1],
            format_func=lambda n: nom_lisible(store, n))
        scope = ",".join(choisis)

    params_actuels: dict = {}
    if control.get("params") and control.get("template") == tpl["template_id"]:
        try:
            params_actuels = json.loads(control["params"])
        except json.JSONDecodeError:
            params_actuels = {}

    requis, optionnels = params_du_template(tpl)
    params: dict = {}
    for groupe in requis:
        if len(groupe) == 1:
            nom_param = groupe[0]
        else:
            etiquettes = {g: libelle_param(tpl, g) for g in groupe}
            defaut = next((a for a in groupe if a in params_actuels), groupe[0])
            nom_param = st.radio(
                "Comment désigner la cible ?", groupe,
                index=groupe.index(defaut), horizontal=True,
                format_func=lambda g: etiquettes[g], key=f"choix_{'_'.join(groupe)}")
        valeur = widget_param(nom_param, tpl, store, scope,
                              params_actuels.get(nom_param), f"param_{nom_param}")
        if valeur not in (None, "", []):
            params[nom_param] = valeur

    if optionnels:
        with st.expander("Réglages complémentaires",
                         expanded=any(o in params_actuels for o in optionnels)):
            for nom_param in optionnels:
                if st.checkbox(libelle_param(tpl, nom_param),
                               value=nom_param in params_actuels, key=f"opt_{nom_param}"):
                    valeur = widget_param(nom_param, tpl, store, scope,
                                          params_actuels.get(nom_param),
                                          f"param_{nom_param}")
                    if valeur not in (None, "", []):
                        params[nom_param] = valeur

    # --- Etape 3 : et si c'est en ecart ? ---------------------------------
    st.markdown("#### 3. Que se passe-t-il en cas d'écart ?")
    c1, c2 = st.columns(2)
    with c1:
        severity = st.selectbox(
            "Gravité", SEVERITES, format_func=libelle_severite,
            index=SEVERITES.index(control["severity"])
            if control.get("severity") in SEVERITES else 2)
        st.caption(SEVERITES_AIDE.get(severity, ""))

        seuil_actuel = float(control.get("seuil_tolerance_pct") or 0)
        etiquettes = list(TOLERANCES) + ["Seuil personnalisé"]
        defaut_tol = next((i for i, k in enumerate(TOLERANCES)
                           if TOLERANCES[k] == seuil_actuel), len(etiquettes) - 1)
        choix_tol = st.selectbox("Tolérance avant échec", etiquettes, index=defaut_tol,
                                 help="Part de lignes en écart admise avant que le "
                                      "contrôle ne soit déclaré en échec.")
        seuil = (st.number_input("Seuil personnalisé (%)", value=seuil_actuel,
                                 min_value=0.0, max_value=100.0, step=0.5)
                 if choix_tol == "Seuil personnalisé" else TOLERANCES[choix_tol])
    with c2:
        owner = st.text_input("Qui doit intervenir ?",
                              control.get("owner", "Data Steward"))
        frequency = st.selectbox(
            "À quelle fréquence contrôler ?", FREQUENCES,
            index=FREQUENCES.index(control["frequency"])
            if control.get("frequency") in FREQUENCES else 3)
    remediation = st.text_area(
        "Que doit faire cette personne ?", height=80,
        value=control.get("remediation_action", ""),
        placeholder="Corriger le fichier à la source et redemander une extraction.")

    st.markdown("#### 4. Nommer la règle")
    phrase = phrase_controle({"template": tpl["template_id"],
                              "params": json.dumps(params, ensure_ascii=False)}, store)
    c3, c4 = st.columns(2)
    with c3:
        nom = st.text_input("Nom de la règle", control.get("control_name", ""),
                            placeholder="Complétude du montant notionnel")
    with c4:
        description = st.text_area(
            "Pourquoi cette règle existe", height=68,
            value=control.get("description", ""),
            placeholder="Une opération sans montant n'est pas exploitable en aval.")
    motif = st.text_input(
        "Motif de l'enregistrement (conservé au journal)",
        placeholder="Demandé par le comité qualité du 8 septembre")

    st.markdown("##### Récapitulatif")
    st.markdown(
        f"""<div style="background:#F1F3F4;border-left:4px solid #37474F;
        border-radius:6px;padding:14px 18px;font-size:16px;">{phrase}<br>
        <span style="font-size:13px;color:#5F6368;">Gravité
        {libelle_severite(severity).lower()} · tolérance {seuil:g} % ·
        {owner} intervient · contrôle {frequency.lower()}</span></div>""",
        unsafe_allow_html=True)
    with st.expander("Détail technique"):
        st.code(json.dumps({"template": tpl["template_id"], "params": params,
                            "dataset_scope": scope}, ensure_ascii=False, indent=2),
                language="json")

    # --- Validation --------------------------------------------------------
    candidat = {
        "rule_id": control.get("rule_id", store.next_rule_id()),
        "control_name": nom.strip(), "control_type": tpl["dimension"],
        "description": description.strip(), "template": tpl["template_id"],
        "params": json.dumps(params, ensure_ascii=False),
        "logic_definition": phrase, "dataset_scope": scope,
        "data_element": ", ".join(str(v) for k, v in params.items()
                                  if k in PARAM_COLONNE | {"role"}),
        "seuil_tolerance_pct": seuil, "severity": severity, "frequency": frequency,
        "owner": owner.strip(),
        "output_type": control.get("output_type", "Exception report"),
        "kpi": tpl.get("kpi_produit", ""), "remediation_action": remediation.strip(),
    }

    manques = []
    if not nom.strip():
        manques.append("le nom de la règle")
    if not description.strip():
        manques.append("la raison d'être de la règle")
    if not remediation.strip():
        manques.append("l'action à mener en cas d'écart")
    cibles = scope_datasets(store, scope)
    if not cibles:
        manques.append("au moins un fichier concerné")

    erreurs = [f"{nom_lisible(store, ds)} : {err}"
               for ds in cibles for err in validate_control(candidat, store, ds)]

    if manques:
        st.warning("Il manque encore " + ", ".join(manques) + ".")
    if erreurs:
        st.error("La règle est refusée par le validateur :\n\n"
                 + "\n".join(f"- {e}" for e in erreurs))
    if not manques and not erreurs:
        st.success(f"Règle valide sur {len(cibles)} fichier(s) : "
                   + ", ".join(nom_lisible(store, d) for d in cibles))

    if st.button("Enregistrer la règle", type="primary",
                 disabled=bool(manques or erreurs)):
        if creation:
            cree = store.add_control(candidat, utilisateur,
                                     motif or "Création via l'interface")
            enregistrer(store, f"Règle {cree['rule_id']} créée.")
        else:
            store.update_control(control["rule_id"], candidat, utilisateur,
                                 motif or "Modification via l'interface")
            enregistrer(store, f"Règle {control['rule_id']} mise à jour "
                               f"(version {store.control(control['rule_id'])['version']}).")
        st.session_state.pop("edition", None)
        st.rerun()


def ecran_regles(store: CatalogueStore, utilisateur: str) -> None:
    st.header("Règles de contrôle")
    afficher_flash()

    if "edition" in st.session_state:
        if st.button("← Revenir à la liste"):
            st.session_state.pop("edition")
            st.rerun()
        rid = st.session_state["edition"]
        editeur_regle(store, utilisateur, store.control(rid) if rid else None)
        return

    df = pd.DataFrame(store.controls)
    c1, c2, c3 = st.columns([1, 1, 2])
    f_sev = c1.multiselect("Gravité", SEVERITES, format_func=libelle_severite)
    f_statut = c2.multiselect("État", STATUTS, default=["Actif"],
                              format_func=libelle_statut)
    f_texte = c3.text_input("Rechercher", placeholder="un mot du nom ou de la règle…")

    vue = df.copy()
    if f_sev:
        vue = vue[vue["severity"].isin(f_sev)]
    if f_statut:
        vue = vue[vue["statut"].isin(f_statut)]
    if f_texte:
        vue = vue[vue.astype(str).apply(
            lambda r: f_texte.lower() in " ".join(r).lower(), axis=1)]

    st.caption(f"{len(vue)} règle(s) affichée(s) sur {len(df)} — "
               f"{len(store.active_controls())} en service")
    st.dataframe(pd.DataFrame({
        "": vue["statut"].map(PUCE_STATUT),
        "Règle": vue["rule_id"],
        "Nom": vue["control_name"],
        "Ce qui est vérifié": [phrase_controle(c, store)
                               for c in vue.to_dict("records")],
        "Gravité": vue["severity"].map(libelle_severite),
        "Tolérance": vue["seuil_tolerance_pct"].map(lambda v: f"{float(v or 0):g} %"),
        "Responsable": vue["owner"],
        "État": vue["statut"].map(libelle_statut),
        "Version": vue["version"],
    }), width='stretch', hide_index=True)

    st.divider()
    g, d = st.columns([1, 2])
    with g:
        if st.button("➕ Créer une règle", type="primary", width='stretch'):
            st.session_state["edition"] = None
            st.rerun()
    with d:
        rid = st.selectbox("Agir sur une règle existante", [""] + list(df["rule_id"]),
                           format_func=lambda r: "" if not r
                           else f"{r} · {store.control(r)['control_name']}")
    if not rid:
        return

    ctrl = store.control(rid)
    st.info(f"**{ctrl['control_name']}**  \n{phrase_controle(ctrl, store)}  \n"
            f"*{ctrl['description'] or 'Aucune raison d’être documentée.'}*")
    motif = st.text_input("Motif (conservé au journal)", key="motif_action")
    a1, a2, a3, a4 = st.columns(4)
    if a1.button("✏️ Modifier", width='stretch'):
        st.session_state["edition"] = rid
        st.rerun()
    if a2.button("⏸️ Mettre en pause", width='stretch',
                 disabled=ctrl["statut"] == "Suspendu"):
        store.set_statut(rid, "Suspendu", utilisateur, motif or "Mise en pause")
        enregistrer(store, f"{rid} mise en pause : elle ne s'exécutera plus.")
        st.rerun()
    if a3.button("▶️ Remettre en service", width='stretch',
                 disabled=ctrl["statut"] == "Actif"):
        store.set_statut(rid, "Actif", utilisateur, motif or "Remise en service")
        enregistrer(store, f"{rid} remise en service.")
        st.rerun()
    if a4.button("🗑️ Supprimer", width='stretch'):
        st.error("**Suppression impossible.** Les rapports déjà produits doivent "
                 "rester explicables : supprimer une règle rendrait leurs résultats "
                 "incompréhensibles. Utilisez **Mettre en pause** — la règle cesse "
                 "de s'exécuter, sa définition est conservée.")


# --------------------------------------------------------------------------- #
# Ecran 3 - Fichiers connus
# --------------------------------------------------------------------------- #
def ecran_fichiers(store: CatalogueStore, utilisateur: str) -> None:
    st.header("Fichiers connus")
    st.caption("Décrire un fichier ici suffit à lui appliquer toutes les règles "
               "transverses. Aucune ligne de code à écrire.")
    afficher_flash()

    onglets = st.tabs([nom_lisible(store, n) for n in store.datasets]
                      + ["➕ Décrire un nouveau fichier"])
    for onglet, nom in zip(onglets, store.datasets):
        with onglet:
            ds = store.dataset(nom)
            st.markdown(f"**{ds['libelle']}**  \nNom technique : `{nom}` · "
                        f"fichier de référence : `{ds['source']}` · "
                        f"responsable : {ds.get('proprietaire', '—')}")
            cols = pd.DataFrame(ds["colonnes"])
            st.dataframe(pd.DataFrame({
                "Colonne": cols["colonne"],
                "Type": cols["type"],
                "Rôle": cols["role"],
                "Identifiant unique": cols["cle_primaire"],
                "Obligatoire": cols["obligatoire"],
                "Rattachée au référentiel": (
                    cols["fk_dataset"].fillna("") + cols["fk_colonne"].fillna("").map(
                        lambda v: f".{v}" if v else "")),
                "Description": cols["description"],
            }), width='stretch', hide_index=True)
            actifs = store.active_controls(nom)
            st.info(f"**{len(actifs)} règle(s)** s'appliquent : "
                    + ", ".join(f"{c['rule_id']} {c['control_name']}" for c in actifs))

    with onglets[-1]:
        st.markdown("##### Décrire un nouveau fichier")
        st.caption("Une ligne par colonne. Seul le nom de la colonne est "
                   "obligatoire ; le reste peut être complété plus tard.")
        c1, c2 = st.columns(2)
        with c1:
            nom = st.text_input("Nom technique", placeholder="inventaire")
            libelle = st.text_input("Nom métier", placeholder="Inventaire des stocks")
        with c2:
            source = st.text_input("Chemin du fichier de référence",
                                   placeholder="data/entrees/inventaire.csv")
            proprietaire = st.text_input("Responsable", "Data Steward")
        st.caption("Format : `colonne;type;rôle;identifiant;obligatoire;"
                   "référentiel;colonne_du_référentiel`")
        brut = st.text_area(
            "Colonnes", height=180,
            placeholder="article_id;string;identifiant;OUI;OUI;;\n"
                        "code_devise;string;code;NON;OUI;ref_devises;code_devise\n"
                        "quantite;integer;mesure;NON;OUI;;")
        if st.button("Enregistrer ce fichier", type="primary",
                     disabled=not (nom and source and brut.strip())):
            colonnes = []
            for ligne in brut.strip().splitlines():
                p = [x.strip() for x in ligne.split(";")]
                p += [""] * (7 - len(p))
                colonnes.append({"colonne": p[0], "type": p[1] or "string",
                                 "role": p[2] or "code", "cle_primaire": p[3] or "NON",
                                 "obligatoire": p[4] or "OUI", "description": "",
                                 "fk_dataset": p[5], "fk_colonne": p[6]})
            store.upsert_dataset(nom, {"libelle": libelle or nom, "source": source,
                                       "proprietaire": proprietaire,
                                       "colonnes": colonnes},
                                 utilisateur, "Déclaration via l'interface")
            transverses = len(store.active_controls(nom))
            enregistrer(store, f"Fichier « {libelle or nom} » enregistré avec "
                               f"{len(colonnes)} colonnes. {transverses} règle(s) "
                               f"s'y appliquent déjà.")
            st.rerun()


# --------------------------------------------------------------------------- #
# Ecran 4 - Journal
# --------------------------------------------------------------------------- #
def ecran_journal(store: CatalogueStore) -> None:
    st.header("Journal des modifications")
    st.caption("Qui a changé quoi, quand, et pourquoi. Rien ne peut être effacé.")
    df = pd.DataFrame(store.changelog)
    if df.empty:
        st.info("Journal vide.")
        return
    st.dataframe(pd.DataFrame({
        "Date": df["timestamp"],
        "Auteur": df["utilisateur"],
        "Action": df["action"],
        "Règle": df["rule_id"],
        "Champ": df["champ"],
        "Avant": df["avant"],
        "Après": df["apres"],
        "Motif": df["motif"],
    }).iloc[::-1], width='stretch', hide_index=True)


# --------------------------------------------------------------------------- #
def main() -> None:
    store = get_store()
    with st.sidebar:
        st.title("🧭 DQ Compass")
        st.caption("Contrôle qualité des données")
        utilisateur = st.text_input("Votre nom", "data.steward",
                                    help="Identifie l'auteur au journal.")
        page = st.radio("Aller à", ["Contrôler un fichier", "Règles de contrôle",
                                    "Fichiers connus", "Journal"])
        st.divider()
        st.metric("Règles en service", len(store.active_controls()))
        st.metric("Fichiers connus", len(store.datasets))
        if st.button("🔄 Recharger le catalogue"):
            get_store(force=True)
            st.rerun()
        st.caption(f"{dt.date.today():%d/%m/%Y}")

    if page == "Contrôler un fichier":
        ecran_controler(store)
    elif page == "Règles de contrôle":
        ecran_regles(store, utilisateur)
    elif page == "Fichiers connus":
        ecran_fichiers(store, utilisateur)
    else:
        ecran_journal(store)


main()
