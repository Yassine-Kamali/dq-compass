"""
DQ Compass - couche de controle qualite generique, pilotee par catalogue.

L'interface suit le cycle de vie impose par le brief (§4, « Functional
architecture ») et en reprend le vocabulaire, ecran par ecran :

    ① Catalogue de controles   Control Catalogue  DEFINES
    ② Execution                Data Quality Engine  EXECUTES
    ③ Restitution              Reporting Layer  REPORTS
    ④ Piste d'audit            Audit Layer  EVIDENCES

Trois partis pris tiennent l'ensemble :

1. RIEN A DECLARER. On depose un fichier, quel qu'il soit. Le moteur le profile
   et applique les regles dont les colonnes existent. Aucun schema n'est saisi :
   le brief demande une couche « plug-and-play […] configurable with minimal
   effort » (§2.1, §3.2), et un formulaire de declaration prealable est
   exactement ce qu'elle interdit.

2. LE FICHIER PROPOSE SES PROPRES CONTROLES. Sur un fichier inconnu, le
   catalogue serait muet. Le profil sert donc a proposer des controles
   candidats, chiffres, que l'analyste accepte ou refuse — ce que le brief
   autorise explicitement (§14 : « suggesting candidate rules from a dataset
   profile […] assists the analyst, never issues the control verdict »).

3. AUCUN JARGON. Personne ne lit `MATCHES_REGEX` ni `{"column": "montant"}`.
   Ce vocabulaire vient du catalogue, pas du code de l'interface.

    streamlit run ui/app.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import altair as alt
import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "reporting"))

from dq_engine import (EVIDENCE_DIR, applicabilite, charger_fichier,  # noqa: E402
                       parse_params, resolve_targets, run_dq, validate_control)
from excel_report import build_workbook  # noqa: E402
from profiler import cle_candidate, profiler  # noqa: E402
from store import (SEVERITES, SEVERITES_AIDE, STATUTS,  # noqa: E402
                   CatalogueStore, libelle_severite, libelle_statut,
                   phrase_controle, scope_matches)
from suggestions import en_controle, suggerer  # noqa: E402

st.set_page_config(page_title="DQ Compass", page_icon="🧭", layout="wide")

ENTREES = ROOT / "data" / "entrees"
DOSSIERS_CONNUS = [ROOT / "data" / "entrees", ROOT / "data" / "prepared",
                   ROOT / "data" / "ref", ROOT / "data" / "exemples"]
EXTENSIONS = {".csv", ".xlsx", ".xls", ".xlsm"}

# Les six dimensions du brief (§2.2), dans son ordre, avec leur nom courant.
DIMENSIONS = {
    "Completeness": "Complétude",
    "Validity": "Validité",
    "Uniqueness": "Unicité",
    "Consistency": "Cohérence",
    "Timeliness": "Fraîcheur",
    "Reconciliation": "Réconciliation",
}

# Les 14 attributs obligatoires du catalogue (annexe A.2), dans l'ordre du brief.
ATTRIBUTS_BRIEF = {
    "rule_id": "Rule ID", "control_name": "Control Name",
    "control_type": "Control Type", "description": "Description",
    "logic_definition": "Logic Definition", "dataset_scope": "Dataset Scope",
    "data_element": "Data Element", "seuil_tolerance_pct": "Threshold",
    "severity": "Severity", "frequency": "Frequency", "owner": "Owner",
    "output_type": "Output Type", "kpi": "KPI",
    "remediation_action": "Remediation Action",
}

# Les composants obligatoires de l'evidence pack (annexe B.2) et leur support.
PIECES_AUDIT = [
    ("Run ID", "manifest.json", "run_id"),
    ("Execution Timestamp", "manifest.json", "horodatage"),
    ("Dataset Reference", "manifest.json", "sources + profil_fichier"),
    ("Rule Configuration", "catalogue_snapshot.json", "définition intégrale"),
    ("Execution Parameters", "manifest.json", "as_of + paramètres des règles"),
    ("Control Results", "results.json", "verdicts et KPI"),
    ("Exception Dataset", "exceptions.csv", "lignes en écart"),
    ("Logs", "execution.log", "journal d'exécution"),
    ("System Trace", "manifest.json", "moteur, Python, pandas, machine"),
    ("Sign-off Fields", "signoff.json", "validation humaine (facultatif)"),
]

# Les quatre etapes du cycle de vie (§4 du brief), chacune avec sa couleur.
# La meme teinte porte l'entree de menu, l'en-tete de l'ecran et son cartouche :
# on sait ou l'on se trouve dans le cycle sans lire une ligne.
ETAPES = [
    ("①", "Catalogue de contrôles", "Control Catalogue", "DÉFINIT", "#5B4B8A"),
    ("②", "Exécution", "Data Quality Engine", "EXÉCUTE", "#1565C0"),
    ("③", "Restitution", "Reporting Layer", "RESTITUE", "#00796B"),
    ("④", "Piste d'audit", "Audit Layer", "PROUVE", "#455A64"),
]
PAGES = [f"{n} {titre}" for n, titre, _, _, _ in ETAPES]

STYLE = """<style>
section[data-testid="stSidebar"] div[role="radiogroup"] > label {
    border-left: 5px solid transparent;
    border-radius: 6px;
    padding: 7px 10px;
    margin-bottom: 5px;
    transition: background .15s ease;
}
section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
    background: rgba(127, 127, 127, .12);
}
""" + "".join(
    f'section[data-testid="stSidebar"] div[role="radiogroup"] > label:'
    f'nth-of-type({i + 1}) {{ border-left-color: {c}; }}\n'
    for i, (_, _, _, _, c) in enumerate(ETAPES)) + "</style>"


def bandeau_etape(index: int, description: str) -> None:
    """En-tete colore d'un ecran, qui le situe dans le cycle de vie."""
    numero, titre, composant, verbe, couleur = ETAPES[index]
    st.markdown(
        f"""<div style="background:linear-gradient(90deg,{couleur}1A,transparent);
        border-left:6px solid {couleur};border-radius:10px;
        padding:16px 22px;margin:0 0 16px 0;">
        <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;">
        <span style="font-size:30px;font-weight:700;color:{couleur};
        line-height:1;">{numero}</span>
        <span style="font-size:26px;font-weight:700;color:{couleur};">{titre}</span>
        <span style="font-size:11px;letter-spacing:.14em;color:{couleur};
        border:1px solid {couleur}66;border-radius:999px;padding:3px 11px;
        white-space:nowrap;">{verbe} · {composant}</span></div>
        <div style="font-size:14px;opacity:.75;margin-top:8px;max-width:70ch;">
        {description}</div></div>""",
        unsafe_allow_html=True)


# Statuts d'execution, dans l'ordre d'empilement valide par le validateur de
# palette : vert et rouge ne sont jamais adjacents (ecart CVD 4,1 -> 10,7).
# La pastille double la couleur, qui ne porte donc jamais seule le sens.
STATUTS_VUE = [
    ("PASS", "Sans écart", "🟢", "#0ca30c"),
    ("NON_APPLICABLE", "Hors périmètre", "⚪", "#898781"),
    ("ERREUR", "Erreur technique", "🟠", "#fab219"),
    ("FAIL", "En échec", "🔴", "#d03b3b"),
]
# Teinte unique des barres de magnitude : 4,30:1 sur fond clair, 3,94:1 sur fond
# sombre - lisible sans connaitre le theme de l'utilisateur.
TEINTE_MAGNITUDE = "#2a78d6"

PUCE_STATUT = {"Actif": "🟢", "Suspendu": "🟠", "Deprecie": "⚪"}
PUCE_RUN = {"PASS": "🟢", "FAIL": "🔴", "ERREUR": "🟠", "NON_APPLICABLE": "⚪"}

TOLERANCES = {
    "Aucun écart toléré": 0.0,
    "Jusqu'à 1 % des lignes": 1.0,
    "Jusqu'à 5 % des lignes": 5.0,
    "Jusqu'à 10 % des lignes": 10.0,
}

FREQUENCES = ["Quotidienne", "Hebdomadaire", "Mensuelle", "Trimestrielle",
              "Annuelle", "A la demande"]

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


def lire_fichier(chemin: pathlib.Path) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """Profile le fichier et en deduit des controles candidats, une seule fois.

    Le calcul est garde en session tant que le fichier ne change pas : sans
    cela, chaque case cochee relancerait un profilage complet.
    """
    stat = chemin.stat()
    cle = (str(chemin), stat.st_mtime, stat.st_size)
    cache = st.session_state.setdefault("cache_fichier", {})
    if cache.get("cle") != cle:
        df = charger_fichier(chemin)
        profil = profiler(df)
        cache.clear()
        cache.update({"cle": cle, "df": df, "profil": profil,
                      "props": suggerer(df, profil, chemin.stem)})
    memoriser_profil(chemin.stem, cache["profil"])
    return cache["df"], cache["profil"], cache["props"]


def memoriser_profil(nom: str, profil: list[dict]) -> None:
    """Retient les colonnes vues pendant la session.

    L'editeur de regles n'a aucun schema declare ou puiser. Il propose donc les
    colonnes des fichiers reellement rencontres : ce qu'on a vu passer, pas ce
    que quelqu'un a promis.
    """
    st.session_state.setdefault("profils_vus", {})[nom] = profil


def colonnes_vues() -> list[str]:
    noms: list[str] = []
    for profil in st.session_state.get("profils_vus", {}).values():
        for col in profil:
            if col["colonne"] not in noms:
                noms.append(col["colonne"])
    return noms


def fichiers_connus() -> list[pathlib.Path]:
    trouves: list[pathlib.Path] = []
    for dossier in DOSSIERS_CONNUS:
        if dossier.exists():
            trouves += sorted(p for p in dossier.iterdir()
                              if p.suffix.lower() in EXTENSIONS)
    return trouves


def fichiers_de_reference() -> list[str]:
    return [str(p.relative_to(ROOT)).replace("\\", "/") for p in fichiers_connus()]


def entetes_du_fichier(chemin: pathlib.Path) -> list[str]:
    """Noms de colonnes seuls : lire l'en-tete suffit a construire une regle.

    Inutile de profiler 42 Mo pour proposer une liste deroulante ; seul l'ecran
    d'execution a besoin des types et des distributions.
    """
    if chemin.suffix.lower() in (".xlsx", ".xls", ".xlsm"):
        entete = pd.read_excel(chemin, nrows=0)
    else:
        entete = pd.read_csv(chemin, nrows=0)
    return [str(c) for c in entete.columns]


def retenir_entetes(nom: str, colonnes: list[str]) -> None:
    """Verse des colonnes connues dans la memoire de session de l'editeur."""
    memoriser_profil(nom, [{"colonne": c, "type": "", "taux_nuls_pct": 0.0,
                            "valeurs_nulles": 0, "valeurs_distinctes": 0,
                            "unique": False} for c in colonnes])


def selecteur_de_colonnes() -> None:
    """Permet de charger les colonnes d'un fichier sans quitter l'editeur.

    Sans cela, une regle ciblant une colonne nommee n'est constructible qu'apres
    etre passe par l'ecran d'execution : l'editeur n'a aucun schema declare ou
    puiser.
    """
    connues = colonnes_vues()
    titre = (f"📋 Colonnes disponibles ({len(connues)})" if connues
             else "📋 Aucune colonne connue — chargez un fichier")
    with st.expander(titre, expanded=not connues):
        st.caption("L'éditeur ne connaît aucune structure de fichier : il n'y a "
                   "plus de schéma déclaré. Chargez un fichier pour que ses "
                   "colonnes soient proposées dans les listes ci-dessous. "
                   "Seul l'en-tête est lu.")
        g, d = st.columns(2)
        with g:
            connus = fichiers_connus()
            choix = st.selectbox(
                "Un fichier déjà présent", connus, index=None,
                placeholder="Choisir un fichier…", key="edit_fichier_connu",
                format_func=lambda p: p.name)
            if choix is not None:
                try:
                    retenir_entetes(choix.stem, entetes_du_fichier(choix))
                except (ValueError, FileNotFoundError) as exc:
                    st.error(f"Lecture impossible : {exc}")
        with d:
            depose = st.file_uploader("Ou déposer un fichier",
                                      type=["csv", "xlsx", "xls", "xlsm"],
                                      key="edit_fichier_depose")
            if depose is not None:
                ENTREES.mkdir(parents=True, exist_ok=True)
                chemin = ENTREES / depose.name
                chemin.write_bytes(depose.getbuffer())
                try:
                    retenir_entetes(chemin.stem, entetes_du_fichier(chemin))
                    st.caption(f"Enregistré dans `data/entrees/{depose.name}`")
                except (ValueError, FileNotFoundError) as exc:
                    st.error(f"Lecture impossible : {exc}")

        vus = st.session_state.get("profils_vus", {})
        if vus:
            for nom, profil in vus.items():
                st.markdown(f"**`{nom}`** — "
                            + ", ".join(f"`{c['colonne']}`" for c in profil))
        else:
            st.info("Vous pouvez aussi écrire une règle sans fichier : "
                    "choisissez **Motif des colonnes visées** plutôt qu'une "
                    "colonne nommée. C'est ce que fait tout le socle livré.")


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


def tuiles(valeurs: list[tuple[str, str, str]]) -> None:
    """Bandeau de tuiles compactes : libelle, valeur, teinte."""
    cols = st.columns(len(valeurs))
    for col, (titre, valeur, couleur) in zip(cols, valeurs):
        col.markdown(
            f"""<div style="border:1px solid #DADCE0;border-left:4px solid {couleur};
            border-radius:8px;padding:10px 14px;background:#FFFFFF10;">
            <div style="font-size:12px;color:#5F6368;text-transform:uppercase;
            letter-spacing:.04em;">{titre}</div>
            <div style="font-size:26px;font-weight:700;color:{couleur};
            line-height:1.2;">{valeur}</div></div>""",
            unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# ① Catalogue de controles - DEFINES
# --------------------------------------------------------------------------- #
def couverture_dimensions(store: CatalogueStore) -> dict[str, int]:
    compte = {d: 0 for d in DIMENSIONS}
    for c in store.active_controls():
        if c.get("control_type") in compte:
            compte[c["control_type"]] += 1
    return compte


def widget_param(nom: str, tpl: dict, valeur, cle: str):
    label = libelle_param(tpl, nom)
    colonnes = colonnes_vues()

    if nom == "colonnes_motif":
        return st.text_input(
            label, value=valeur or "", key=cle, placeholder="(?i)(^|_)id$",
            help="Expression régulière sur le NOM des colonnes. La règle "
                 "s'appliquera à toute colonne dont le nom correspond, dans "
                 "tous les fichiers concernés — y compris ceux qui n'existent "
                 "pas encore.")
    if nom in PARAM_COLONNE:
        if colonnes:
            options = colonnes + ["✏️ Saisir un autre nom…"]
            index = options.index(valeur) if valeur in options else None
            choix = st.selectbox(label, options, index=index,
                                 placeholder="Choisir une colonne…", key=cle)
            if choix == "✏️ Saisir un autre nom…":
                return st.text_input(f"{label} (nom exact)", value="", key=f"{cle}_libre")
            return choix
        return st.text_input(label, value=valeur or "", key=cle,
                             help="Contrôlez un fichier une première fois et ses "
                                  "colonnes seront proposées ici.")
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
        if colonnes:
            return st.multiselect(label, colonnes,
                                  default=[c for c in defaut if c in colonnes], key=cle)
        brut = st.text_input(label, value=", ".join(defaut), key=cle,
                             placeholder="colonne_a, colonne_b")
        return [v.strip() for v in brut.split(",") if v.strip()]
    if nom == "ref_fichier":
        options = fichiers_de_reference()
        return st.selectbox(label, options,
                            index=options.index(valeur) if valeur in options else None,
                            placeholder="Choisir un fichier de référence…", key=cle)
    if nom == "ref_column":
        ref = st.session_state.get("param_ref_fichier")
        options: list[str] = []
        if ref and (ROOT / ref).exists():
            try:
                options = [c["colonne"] for c in profiler(charger_fichier(ROOT / ref))]
            except (ValueError, FileNotFoundError):
                options = []
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

    st.markdown(f"#### 2. {tpl.get('question_metier', 'Sur quelles données ?')}")
    selecteur_de_colonnes()
    scope_defaut = str(control.get("dataset_scope", "*")).strip() or "*"
    tous = st.checkbox("Appliquer à tous les fichiers, présents et à venir",
                       value=scope_defaut == "*",
                       help="Combiné à un ciblage par motif de colonnes, la règle "
                            "se propage seule aux fichiers qui n'existent pas encore.")
    if tous:
        scope = "*"
    else:
        scope = st.text_input(
            "Fichiers concernés", value="" if scope_defaut == "*" else scope_defaut,
            placeholder="ventes_*  ou  bis_turnover, inventaire",
            help="Nom de fichier sans extension. Le caractère * remplace "
                 "n'importe quelle suite de caractères. Séparez par des virgules.")
        vus = list(st.session_state.get("profils_vus", {}))
        if vus:
            st.caption("Fichiers vus pendant cette session : "
                       + ", ".join(f"`{v}`" for v in vus))
        if scope.strip():
            couverts = [v for v in vus if scope_matches(scope, v)]
            if couverts:
                st.caption("✅ Couvre : " + ", ".join(f"`{c}`" for c in couverts))

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
            choisi = st.radio(
                "Comment désigner la cible ?", [etiquettes[g] for g in groupe],
                index=groupe.index(defaut), horizontal=True,
                key=f"choix_{'_'.join(groupe)}")
            nom_param = next(g for g in groupe if etiquettes[g] == choisi)
        valeur = widget_param(nom_param, tpl, params_actuels.get(nom_param),
                              f"param_{nom_param}")
        if valeur not in (None, "", []):
            params[nom_param] = valeur

    if optionnels:
        with st.expander("Réglages complémentaires",
                         expanded=any(o in params_actuels for o in optionnels)):
            for nom_param in optionnels:
                if st.checkbox(libelle_param(tpl, nom_param),
                               value=nom_param in params_actuels, key=f"opt_{nom_param}"):
                    valeur = widget_param(nom_param, tpl,
                                          params_actuels.get(nom_param),
                                          f"param_{nom_param}")
                    if valeur not in (None, "", []):
                        params[nom_param] = valeur

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
    portee = "tous les fichiers" if scope.strip() in ("", "*") else scope
    st.markdown(
        f"""<div style="background:#F1F3F4;border-left:4px solid #37474F;
        border-radius:6px;padding:14px 18px;font-size:16px;">{phrase}<br>
        <span style="font-size:13px;color:#5F6368;">Sur {portee} · gravité
        {libelle_severite(severity).lower()} · tolérance {seuil:g} % ·
        {owner} intervient · contrôle {frequency.lower()}</span></div>""",
        unsafe_allow_html=True)
    with st.expander("Détail technique"):
        st.code(json.dumps({"template": tpl["template_id"], "params": params,
                            "dataset_scope": scope}, ensure_ascii=False, indent=2),
                language="json")

    candidat = {
        "rule_id": control.get("rule_id", store.next_rule_id()),
        "control_name": nom.strip(), "control_type": tpl["dimension"],
        "description": description.strip(), "template": tpl["template_id"],
        "params": json.dumps(params, ensure_ascii=False),
        "logic_definition": phrase, "dataset_scope": scope.strip() or "*",
        "data_element": ", ".join(str(v) for k, v in params.items()
                                  if k in PARAM_COLONNE | {"colonnes_motif"}),
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

    erreurs = validate_control(candidat, store)

    if manques:
        st.warning("Il manque encore " + ", ".join(manques) + ".")
    if erreurs:
        st.error("La règle est refusée par le validateur :\n\n"
                 + "\n".join(f"- {e}" for e in erreurs))
    if not manques and not erreurs:
        st.success("Règle valide. Elle s'exécutera sur les fichiers de sa portée "
                   "dont les colonnes correspondent.")

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


def ecran_catalogue(store: CatalogueStore, utilisateur: str) -> None:
    bandeau_etape(0, "Le référentiel central de toutes les règles. Aucune "
                     "d'elles ne nomme une colonne : elles ciblent des "
                     "conventions de nommage, et s'appliquent donc à des "
                     "fichiers qui n'existent pas encore.")
    afficher_flash()

    if "edition" in st.session_state:
        if st.button("← Revenir au catalogue"):
            st.session_state.pop("edition")
            st.rerun()
        rid = st.session_state["edition"]
        editeur_regle(store, utilisateur, store.control(rid) if rid else None)
        return

    couverture = couverture_dimensions(store)
    couvertes = sum(1 for v in couverture.values() if v)
    st.markdown("##### Couverture des six dimensions du brief")
    tuiles([(DIMENSIONS[d], str(n), "#1E7B34" if n else "#B3261E")
            for d, n in couverture.items()])
    if couvertes < len(DIMENSIONS):
        manquantes = [DIMENSIONS[d] for d, n in couverture.items() if not n]
        st.caption(f"⚠️ {len(manquantes)} dimension(s) sans aucune règle active : "
                   + ", ".join(manquantes) + ". L'écran **Exécution** en propose "
                   "automatiquement à partir de n'importe quel fichier.")

    st.divider()
    df = pd.DataFrame(store.controls)
    creer, agir = st.columns([1, 2])
    with creer:
        if st.button("➕ Créer une règle", type="primary", width='stretch'):
            st.session_state["edition"] = None
            st.rerun()
        st.caption("Quatre étapes, en français. Vous pouvez y charger un "
                   "fichier pour récupérer ses colonnes.")
    with agir:
        rid = st.selectbox(
            "Agir sur une règle existante", [""] + list(df["rule_id"]),
            format_func=lambda r: "" if not r
            else f"{r} · {store.control(r)['control_name']}")

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    f_dim = c1.multiselect("Dimension", list(DIMENSIONS),
                           format_func=lambda d: DIMENSIONS[d])
    f_sev = c2.multiselect("Gravité", SEVERITES, format_func=libelle_severite)
    f_statut = c3.multiselect("État", STATUTS, default=["Actif"],
                              format_func=libelle_statut)
    f_texte = c4.text_input("Rechercher", placeholder="un mot du nom ou de la règle…")

    vue = df.copy()
    if f_dim:
        vue = vue[vue["control_type"].isin(f_dim)]
    if f_sev:
        vue = vue[vue["severity"].isin(f_sev)]
    if f_statut:
        vue = vue[vue["statut"].isin(f_statut)]
    if f_texte:
        vue = vue[vue.astype(str).apply(
            lambda r: f_texte.lower() in " ".join(r).lower(), axis=1)]

    st.caption(f"{len(vue)} règle(s) affichée(s) sur {len(df)} — "
               f"{len(store.active_controls())} en service")
    if vue.empty:
        st.info("Aucune règle ne correspond à ces filtres.")
    else:
        st.dataframe(pd.DataFrame({
            "": vue["statut"].map(PUCE_STATUT),
            "Règle": vue["rule_id"],
            "Nom": vue["control_name"],
            "Dimension": vue["control_type"].map(lambda d: DIMENSIONS.get(d, d)),
            "Ce qui est vérifié": [phrase_controle(c, store)
                                   for c in vue.to_dict("records")],
            "S'applique à": vue["dataset_scope"].map(
                lambda s: "tous les fichiers" if str(s).strip() in ("", "*") else s),
            "Gravité": vue["severity"].map(libelle_severite),
            "Tolérance": vue["seuil_tolerance_pct"].map(lambda v: f"{float(v or 0):g} %"),
            "Responsable": vue["owner"],
            "Version": vue["version"],
        }), width='stretch', hide_index=True)

        with st.expander("Les 14 attributs exigés par le brief (annexe A.2)"):
            st.caption("Chaque règle porte les quatorze attributs standardisés, "
                       "sous les noms du brief.")
            st.dataframe(vue[list(ATTRIBUTS_BRIEF)].rename(columns=ATTRIBUTS_BRIEF),
                         width='stretch', hide_index=True)
            st.download_button(
                "⬇️ Extraire le catalogue (CSV)",
                data=vue[list(ATTRIBUTS_BRIEF)].rename(columns=ATTRIBUTS_BRIEF)
                .to_csv(index=False).encode("utf-8-sig"),
                file_name="catalogue_extract.csv", mime="text/csv",
                help="Le « Catalogue extract » attendu par la cartographie "
                     "prudentielle (annexe C.2).")

    retirees = [c for c in store.controls if c["statut"] == "Deprecie"]
    if retirees:
        with st.expander(f"🧹 {len(retirees)} règle(s) retirée(s) du service"):
            st.caption(
                "Ces règles ne s'exécutent plus. Elles restent visibles pour "
                "mémoire — filtrez sur l'état « Retiré » pour les lire. Vous "
                "pouvez les supprimer en bloc : leur définition partira au "
                "journal, où elle reste consultable.")
            motif_purge = st.text_input(
                "Motif de la purge", key="motif_purge",
                placeholder="Règles héritées d'un jeu de données qui n'est plus suivi")
            if st.button(f"Supprimer les {len(retirees)} règles retirées",
                         disabled=not motif_purge.strip()):
                for c in retirees:
                    store.delete_control(c["rule_id"],
                                         utilisateur or "data.steward", motif_purge)
                enregistrer(store, f"{len(retirees)} règle(s) retirée(s) "
                                   f"supprimées. Définitions conservées au journal.")
                st.rerun()

    if not rid:
        return
    st.divider()

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
        st.session_state["suppression"] = rid
        st.rerun()

    if st.session_state.get("suppression") == rid:
        st.warning(
            f"**Supprimer {rid} définitivement ?** La règle quitte le catalogue. "
            f"Elle reste reconstructible : sa définition complète part au "
            f"journal, et les packs de preuves déjà produits en gardent une "
            f"copie intégrale — un run passé reste donc explicable.")
        if not motif.strip():
            st.info("Renseignez le **motif** ci-dessus : il est exigé pour "
                    "supprimer, et conservé au journal.")
        s1, s2, _ = st.columns([1, 1, 2])
        if s1.button("Confirmer la suppression", type="primary",
                     disabled=not motif.strip(), width='stretch'):
            store.delete_control(rid, utilisateur or "data.steward", motif)
            st.session_state.pop("suppression", None)
            enregistrer(store, f"{rid} supprimée. Sa définition reste au journal.")
            st.rerun()
        if s2.button("Annuler", width='stretch'):
            st.session_state.pop("suppression", None)
            st.rerun()


# --------------------------------------------------------------------------- #
# ② Execution - EXECUTES
# --------------------------------------------------------------------------- #
def repartir_regles(store: CatalogueStore, profil: list[dict],
                    nom: str) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Separe les regles de la portee en applicables et hors perimetre.

    Toutes les regles sont desormais tentees sur tous les fichiers : c'est la
    presence des colonnes, et non le nom du fichier, qui decide. Le dire *avant*
    de lancer evite de noyer le tableau de bord sous des lignes grises.
    """
    applicables: list[dict] = []
    hors: list[tuple[dict, str]] = []
    for c in store.active_controls(nom):
        try:
            params = parse_params(c.get("params"))
            cibles = resolve_targets(c["template"], params, profil)
            raison = applicabilite(c["template"], params, cibles, profil)
        except Exception as exc:  # noqa: BLE001 - un diagnostic ne casse rien
            raison = f"règle illisible : {exc}"
        if raison:
            hors.append((c, raison))
        else:
            applicables.append(c)
    return applicables, hors


def tableau_profil(profil: list[dict]) -> pd.DataFrame:
    return pd.DataFrame({
        "Colonne": [c["colonne"] for c in profil],
        "Type déduit": [c["type"] for c in profil],
        "Vide": [f"{c['taux_nuls_pct']:g} %" for c in profil],
        "Valeurs distinctes": [c["valeurs_distinctes"] for c in profil],
        "Sans doublon": ["oui" if c["unique"] else "" for c in profil],
    })


def bloc_suggestions(store: CatalogueStore, utilisateur: str, nom: str,
                     propositions: list[dict], deja: set[str]) -> None:
    """Propose des controles deduits du fichier, chiffres, a accepter ou non."""
    restantes = [p for p in propositions if p["control_name"] not in deja]
    if not restantes:
        st.success("Tous les contrôles déductibles de ce fichier sont déjà au "
                   "catalogue.")
        return

    utiles = [p for p in restantes if (p["impact"] or 0) > 0]
    st.markdown(f"##### {len(restantes)} contrôle(s) déduits de ce fichier")
    st.caption(
        "Ces règles ne viennent d'aucune connaissance métier : elles sortent de "
        "la distribution observée — colonnes toujours remplies, valeurs "
        "marginales, bornes aberrantes, ordre des dates. Le brief autorise "
        "explicitement cette assistance (§14) ; **le verdict reste au moteur, "
        "la décision reste à vous**."
        + (f"  \n**{len(utiles)} d'entre elles signaleraient déjà quelque chose "
           f"sur ce fichier**, et sont pré-cochées." if utiles else ""))

    portee_choix = st.radio(
        "Portée des règles acceptées", ["tous les fichiers", "ce fichier"],
        horizontal=True,
        help="Par défaut une règle vaut pour tout fichier : là où ses colonnes "
             "n'existent pas, elle est simplement déclarée hors périmètre. "
             "Restreindre à ce fichier n'est utile que pour limiter le bruit.")
    scope = "*" if portee_choix == "tous les fichiers" else nom

    retenues: list[dict] = []
    par_dimension: dict[str, list[dict]] = {}
    for p in restantes:
        par_dimension.setdefault(p["dimension"], []).append(p)

    for dimension in DIMENSIONS:
        groupe = par_dimension.get(dimension, [])
        if not groupe:
            continue
        actifs = sum(1 for p in groupe if (p["impact"] or 0) > 0)
        titre = (f"{DIMENSIONS[dimension]} — {len(groupe)} proposition(s)"
                 + (f", dont {actifs} avec un écart constaté" if actifs else ""))
        with st.expander(titre, expanded=bool(actifs)):
            for p in groupe:
                impact = p["impact"] or 0
                marque = f"🔴 **{impact} ligne(s) en écart**" if impact else "🟢 aucun écart aujourd'hui"
                if st.checkbox(p["control_name"], value=impact > 0,
                               key=f"sug_{nom}_{p['cle']}"):
                    retenues.append(p)
                st.caption(f"{marque} · {p['constat']} · gravité "
                           f"{libelle_severite(p['severity']).lower()}  \n"
                           f"{p['description']}")

    if st.button(f"➕ Ajouter {len(retenues)} contrôle(s) au catalogue",
                 type="primary", disabled=not retenues, width='stretch'):
        ajoutes, refuses = [], []
        for p in retenues:
            candidat = en_controle(p, store.next_rule_id())
            candidat["dataset_scope"] = scope
            candidat["owner"] = utilisateur or candidat["owner"]
            candidat["logic_definition"] = phrase_controle(candidat, store)
            candidat["kpi"] = (store.template(p["template"]) or {}).get("kpi_produit", "")
            erreurs = validate_control(candidat, store)
            if erreurs:
                refuses.append(f"{p['control_name']} : {erreurs[0]}")
                continue
            store.add_control(
                candidat, utilisateur or "data.steward",
                f"Déduite du profil de {nom} — {p['constat']}")
            ajoutes.append(candidat["rule_id"])
        message = (f"{len(ajoutes)} contrôle(s) ajoutés au catalogue "
                   f"({', '.join(ajoutes)}).") if ajoutes else "Aucun ajout."
        if refuses:
            message += " Refusés par le validateur : " + " ; ".join(refuses)
        enregistrer(store, message)
        st.rerun()


def ecran_execution(store: CatalogueStore, utilisateur: str) -> None:
    bandeau_etape(1, "Déposez un fichier, n'importe lequel. Sa structure est "
                     "déduite à la lecture, les règles dont les colonnes "
                     "existent s'appliquent, et le fichier propose lui-même les "
                     "contrôles qui lui manquent.")
    afficher_flash()

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
            st.info("Aucun fichier dans `data/entrees`, `data/prepared`, "
                    "`data/ref` ou `data/exemples`.")

    if chemin is None:
        st.stop()

    try:
        with st.spinner("Lecture et profilage du fichier…"):
            df, profil, propositions = lire_fichier(chemin)
    except (ValueError, FileNotFoundError) as exc:
        st.error(f"Lecture impossible : {exc}")
        st.stop()

    nom = chemin.stem
    applicables, hors = repartir_regles(store, profil, nom)
    st.divider()

    tuiles([
        ("Lignes", f"{len(df):,}".replace(",", " "), "#37474F"),
        ("Colonnes", str(len(profil)), "#37474F"),
        ("Règles applicables", str(len(applicables)),
         "#1E7B34" if applicables else "#B3261E"),
        ("Hors périmètre", str(len(hors)), "#5F6368"),
        ("Contrôles proposés", str(len(propositions)), "#0B5394"),
    ])

    with st.expander(f"Structure déduite — {len(profil)} colonnes", expanded=False):
        st.dataframe(tableau_profil(profil), width='stretch', hide_index=True)
        cle = cle_candidate(profil)
        st.caption(
            f"Identifiant de ligne retenu pour les rapports d'exception : **{cle}**."
            if cle else
            "Aucune colonne intégralement unique : les exceptions seront repérées "
            "par leur position dans le fichier.")
        st.caption("Personne n'a saisi cette structure. Elle est déduite du "
                   "contenu et versée aux preuves d'exécution.")
    with st.expander("Aperçu — 50 premières lignes"):
        st.dataframe(df.head(50), width='stretch', hide_index=True)

    onglet_regles, onglet_sug, onglet_hors = st.tabs(
        [f"Règles applicables ({len(applicables)})",
         f"Contrôles proposés par le fichier ({len(propositions)})",
         f"Hors périmètre ({len(hors)})"])
    with onglet_hors:
        st.caption("Ces règles du catalogue sont actives et couvrent ce fichier "
                   "par leur portée, mais les colonnes qu'elles visent n'y "
                   "existent pas. Elles ne sont ni en échec, ni en erreur : "
                   "elles sont hors sujet, et le moteur le dit.")
        if hors:
            st.dataframe(pd.DataFrame({
                "Règle": [c["rule_id"] for c, _ in hors],
                "Nom": [c["control_name"] for c, _ in hors],
                "Pourquoi elle ne s'applique pas": [r for _, r in hors],
            }), width='stretch', hide_index=True)
        else:
            st.success("Toutes les règles du catalogue trouvent leurs colonnes "
                       "dans ce fichier.")
    with onglet_regles:
        if applicables:
            st.dataframe(pd.DataFrame({
                "Règle": [c["rule_id"] for c in applicables],
                "Nom": [c["control_name"] for c in applicables],
                "Dimension": [DIMENSIONS.get(c["control_type"], c["control_type"])
                              for c in applicables],
                "Ce qui est vérifié": [phrase_controle(c, store) for c in applicables],
                "Gravité": [libelle_severite(c["severity"]) for c in applicables],
            }), width='stretch', hide_index=True)
        else:
            st.warning(
                f"**Aucune règle du catalogue ne trouve ses colonnes dans "
                f"`{nom}`.** C'est le cas normal d'un fichier jamais rencontré : "
                f"les règles existantes visent d'autres colonnes. L'onglet "
                f"*Contrôles proposés* en déduit {len(propositions)} de son "
                f"seul contenu.")
    with onglet_sug:
        bloc_suggestions(store, utilisateur, nom, propositions,
                         {c["control_name"] for c in store.controls})

    st.divider()
    libelle = st.text_input("Intitulé du contrôle (figure dans le rapport)",
                            f"Contrôle de {chemin.name}")
    if st.button("▶️ Lancer le contrôle", type="primary", width='stretch',
                 disabled=not applicables):
        with st.spinner("Contrôle en cours…"):
            run = run_dq(chemin, store=store, run_label=libelle)
            classeur = build_workbook(run, store)
        st.session_state["dernier_run"] = run
        st.session_state["dernier_classeur"] = classeur
        st.rerun()

    run = st.session_state.get("dernier_run")
    if run is not None and pathlib.Path(run.fichier).stem == nom:
        st.divider()
        bandeau_resultat(run)
        st.info("Le détail complet — tableau de bord, exceptions ligne à ligne, "
                "couverture et classeur Excel — est dans l'écran **③ Restitution**.")


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
        sous = f"{s['pass']} contrôle(s) passés sans écart"

    st.markdown(
        f"""<div style="background:{fond};border:2px solid {bordure};
        border-radius:12px;padding:22px 26px;margin:6px 0 18px 0;">
        <div style="font-size:46px;font-weight:700;color:{couleur};
        line-height:1.05;">{titre}</div>
        <div style="font-size:15px;color:{couleur};opacity:.85;margin-top:6px;">
        {sous}</div></div>""",
        unsafe_allow_html=True)

    if echecs or erreurs:
        for _, r in sc[sc["statut"].isin(["FAIL", "ERREUR"])].iterrows():
            st.markdown(
                f"**{r['rule_id']} · {r['control_name']}** — "
                f"{libelle_severite(r['severity'])}  \n"
                f"{r['lignes_ko']:,} ligne(s) en écart sur {r['lignes_testees']:,} "
                f"· responsable : {r['owner']}  \n"
                f"→ *{r['remediation_action'] or 'Aucune action de remédiation définie.'}*"
                .replace(",", " "))


# --------------------------------------------------------------------------- #
# ③ Restitution - REPORTS
# --------------------------------------------------------------------------- #
def graphe_statuts_par_dimension(sc: pd.DataFrame):
    """Part-a-tout par dimension : ou le controle tient, ou il lache."""
    lignes = []
    for rang, (code, nom, puce, _) in enumerate(STATUTS_VUE):
        for dimension, libelle in DIMENSIONS.items():
            n = int(((sc["dimension"] == dimension) & (sc["statut"] == code)).sum())
            if n:
                lignes.append({"Dimension": libelle, "Statut": f"{puce} {nom}",
                               "Contrôles": n, "ordre": rang})
    if not lignes:
        return None
    domaine = [f"{puce} {nom}" for _, nom, puce, _ in STATUTS_VUE]
    couleurs = [teinte for _, _, _, teinte in STATUTS_VUE]
    return alt.Chart(pd.DataFrame(lignes)).mark_bar(height=22).encode(
        y=alt.Y("Dimension:N", title=None,
                sort=[v for v in DIMENSIONS.values()]),
        x=alt.X("Contrôles:Q", title="Contrôles exécutés",
                axis=alt.Axis(tickMinStep=1)),
        color=alt.Color("Statut:N",
                        scale=alt.Scale(domain=domaine, range=couleurs),
                        legend=alt.Legend(title=None, orient="bottom", columns=2)),
        order=alt.Order("ordre:Q", sort="ascending"),
        tooltip=["Dimension", "Statut", "Contrôles"],
    ).properties(height=max(150, 34 * len(set(x["Dimension"] for x in lignes))))


def graphe_ecarts_par_regle(sc: pd.DataFrame):
    """Magnitude : quelles regles remontent le plus de lignes a instruire."""
    ko = sc[sc["lignes_ko"] > 0].nlargest(8, "lignes_ko").copy()
    if ko.empty:
        return None
    ko["Règle"] = ko["rule_id"] + " · " + ko["control_name"].str.slice(0, 30)
    ko["Lignes"] = ko["lignes_ko"]
    base = alt.Chart(ko).encode(
        y=alt.Y("Règle:N", sort="-x", title=None),
        x=alt.X("Lignes:Q", title="Lignes en écart"))
    barres = base.mark_bar(height=20, color=TEINTE_MAGNITUDE, cornerRadiusEnd=4)
    etiquettes = base.mark_text(align="left", dx=6, fontSize=12).encode(
        text=alt.Text("Lignes:Q", format=","))
    return (barres + etiquettes).properties(height=max(150, 34 * len(ko)))


def historique_du_fichier(nom_fichier: str) -> pd.DataFrame:
    """Verdicts successifs sur le meme fichier, lus dans les packs de preuves.

    C'est la couche d'audit qui rend cette courbe possible : chaque run a laisse
    ses resultats sur disque, donc la qualite se lit dans le temps sans qu'aucune
    base ne soit tenue a cote.
    """
    points = []
    for pack in reversed(packs_de_preuve()):
        try:
            manifeste = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
            if manifeste.get("fichier_nom") != nom_fichier:
                continue
            resultats = json.loads((pack / "results.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        points.append({
            "Exécution": manifeste.get("horodatage", "")[:16].replace("T", " "),
            "Contrôles en échec": sum(1 for r in resultats if r["statut"] == "FAIL"),
            "Lignes en écart": sum(int(r.get("lignes_ko", 0)) for r in resultats),
            "Run": pack.name,
        })
    return pd.DataFrame(points)


def graphe_historique(points: pd.DataFrame):
    """Evolution : une seule serie, donc aucune legende - le titre la nomme."""
    base = alt.Chart(points).encode(
        x=alt.X("Exécution:N", title=None, axis=alt.Axis(labelAngle=-30)),
        y=alt.Y("Contrôles en échec:Q", title="Contrôles en échec",
                axis=alt.Axis(tickMinStep=1)),
        tooltip=["Exécution", "Contrôles en échec", "Lignes en écart", "Run"])
    return (base.mark_line(color=TEINTE_MAGNITUDE, strokeWidth=2)
            + base.mark_point(color=TEINTE_MAGNITUDE, size=90, filled=True)
            ).properties(height=260)



def ecran_restitution(store: CatalogueStore) -> None:
    bandeau_etape(2, "Tableau de bord à feux tricolores, rapport d'exceptions "
                     "ligne à ligne, vue de couverture des contrôles.")

    run = st.session_state.get("dernier_run")
    if run is None:
        st.info("Aucun contrôle n'a encore été lancé dans cette session. "
                "Rendez-vous dans l'écran **② Exécution**.")
        return

    sc = run.scorecard.copy()
    s_res = run.summary()
    nom_fichier = pathlib.Path(run.fichier).stem
    executes = s_res["pass"] + s_res["fail"] + s_res["erreur"]
    conformite = (100.0 * s_res["pass"] / executes) if executes else 0.0

    st.caption(f"**{pathlib.Path(run.fichier).name}** · run `{run.run_id}` · "
               f"{run.horodatage.replace('T', ' ')}")
    bandeau_resultat(run)

    tuiles([
        ("Contrôles exécutés", str(s_res["controles"]), "#37474F"),
        ("Sans écart", str(s_res["pass"]), "#0ca30c"),
        ("En échec", str(s_res["fail"]),
         "#d03b3b" if s_res["fail"] else "#0ca30c"),
        ("Hors périmètre", str(s_res["non_applicable"]), "#898781"),
        ("Conformité", f"{conformite:.0f} %",
         "#0ca30c" if conformite == 100 else TEINTE_MAGNITUDE),
        ("Lignes en exception", f"{s_res['exceptions']:,}".replace(",", " "),
         "#d03b3b" if s_res["exceptions"] else "#0ca30c"),
    ])

    classeur = st.session_state.get("dernier_classeur")
    if classeur and pathlib.Path(classeur).exists():
        st.download_button(
            "⬇️ Télécharger le rapport Excel (6 onglets)", type="primary",
            data=pathlib.Path(classeur).read_bytes(),
            file_name=pathlib.Path(classeur).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # --- les deux graphiques de tête ---------------------------------------
    g, d = st.columns(2)
    with g:
        st.markdown("##### Statut des contrôles, par dimension")
        graphe = graphe_statuts_par_dimension(sc)
        if graphe is None:
            st.info("Aucun contrôle exécuté.")
        else:
            st.altair_chart(graphe, width='stretch')
            st.caption("Les six dimensions du brief. Une dimension absente n'a "
                       "aucune règle applicable à ce fichier.")
    with d:
        st.markdown("##### Lignes en écart, par règle")
        graphe = graphe_ecarts_par_regle(sc)
        if graphe is None:
            st.success("Aucune ligne en écart sur ce run.")
        else:
            st.altair_chart(graphe, width='stretch')
            st.caption("Les huit règles qui remontent le plus de lignes à "
                       "instruire.")

    t1, t2, t3, t4, t5 = st.tabs([
        "Détail des contrôles", "Exceptions ligne à ligne",
        "Couverture des contrôles", "Historique du fichier", "Règles refusées"])

    with t1:
        horsp = int((sc["statut"] == "NON_APPLICABLE").sum())
        vue = sc
        if horsp and not st.checkbox(
                f"Afficher aussi les {horsp} règle(s) hors périmètre",
                help="Règles actives dont les colonnes n'existent pas dans ce "
                     "fichier. Le détail figure dans l'onglet Couverture."):
            vue = sc[sc["statut"] != "NON_APPLICABLE"]
        tableau = pd.DataFrame({
            "": vue["statut"].map(PUCE_RUN),
            "Règle": vue["rule_id"],
            "Contrôle": vue["control_name"],
            "Dimension": vue["dimension"].map(lambda x: DIMENSIONS.get(x, x)),
            "Ce qui est vérifié": [
                phrase_controle(store.control(r) or {}, store) for r in vue["rule_id"]],
            "Colonne testée": vue["cible"],
            "Gravité": vue["severity"].map(libelle_severite),
            "Lignes en écart": vue["lignes_ko"],
            "Lignes testées": vue["lignes_testees"],
            "Indicateur": vue["kpi_nom"] + " : " + vue["kpi_valeur"].astype(str),
            "Responsable": vue["owner"],
            "Commentaire": vue["message"],
        })
        st.dataframe(tableau, width='stretch', hide_index=True)
        st.caption("🟢 sans écart · 🔴 en échec · 🟠 erreur technique · "
                   "⚪ hors périmètre (motif en commentaire)")
        st.download_button(
            "⬇️ Exporter le tableau de bord (CSV)",
            data=tableau.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"scorecard_{run.run_id}.csv", mime="text/csv")

    with t2:
        exceptions = run.exceptions
        if exceptions.empty:
            st.success("Aucune ligne en exception sur ce run.")
        else:
            f1, f2 = st.columns(2)
            regles = ["(toutes)"] + sorted(exceptions["rule_id"].unique())
            choix = f1.selectbox("Règle", regles)
            gravites = ["(toutes)"] + [g for g in SEVERITES
                                       if g in set(exceptions["severity"])]
            grav = f2.selectbox("Gravité", gravites, format_func=libelle_severite)
            vue = exceptions
            if choix != "(toutes)":
                vue = vue[vue["rule_id"] == choix]
            if grav != "(toutes)":
                vue = vue[vue["severity"] == grav]
            st.dataframe(vue.head(2000), width='stretch', hide_index=True)
            st.caption(f"{len(vue):,} exception(s) — 2 000 premières affichées."
                       .replace(",", " "))
            st.download_button(
                "⬇️ Exporter ces exceptions (CSV)",
                data=vue.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"exceptions_{run.run_id}.csv", mime="text/csv")

    with t3:
        executees = set(sc["dimension"]) if not sc.empty else set()
        st.dataframe(pd.DataFrame([
            {"Dimension": libelle,
             "Contrôles exécutés": int((sc["dimension"] == d).sum())
             if not sc.empty else 0,
             "Couverte sur ce fichier": "oui" if d in executees else "non"}
            for d, libelle in DIMENSIONS.items()]),
            width='stretch', hide_index=True)

        vues = set(sc["rule_id"]) if not sc.empty else set()
        hors = [c for c in store.active_controls() if c["rule_id"] not in vues]
        if hors:
            st.markdown("**Règles actives hors portée de ce fichier**")
            st.dataframe(pd.DataFrame({
                "Règle": [c["rule_id"] for c in hors],
                "Nom": [c["control_name"] for c in hors],
                "Portée": [c["dataset_scope"] for c in hors],
            }), width='stretch', hide_index=True)
        na = sc[sc["statut"] == "NON_APPLICABLE"] if not sc.empty else pd.DataFrame()
        if not na.empty:
            st.markdown("**Règles dans la portée mais non applicables**")
            st.dataframe(pd.DataFrame({
                "Règle": na["rule_id"], "Nom": na["control_name"],
                "Motif": na["message"],
            }), width='stretch', hide_index=True)

    with t4:
        points = historique_du_fichier(nom_fichier)
        if len(points) < 2:
            st.info(f"Un seul contrôle de `{nom_fichier}` a laissé des preuves. "
                    f"L'historique se construit d'une exécution à l'autre — "
                    f"relancez le contrôle pour voir la courbe.")
            if not points.empty:
                st.dataframe(points, width='stretch', hide_index=True)
        else:
            st.altair_chart(graphe_historique(points), width='stretch')
            st.caption(f"Lu dans les packs de preuves de `{nom_fichier}`. "
                       f"Aucune base tenue à côté : c'est la couche d'audit qui "
                       f"porte l'historique.")
            st.dataframe(points, width='stretch', hide_index=True)
            st.download_button(
                "⬇️ Exporter l'historique (CSV)",
                data=points.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"historique_{nom_fichier}.csv", mime="text/csv")

    with t5:
        rejets = pd.DataFrame(run.rejets)
        if rejets.empty:
            st.success("Aucune règle refusée : toutes les définitions sont saines.")
        else:
            st.dataframe(rejets, width='stretch', hide_index=True)
        st.caption("Règles écartées par le validateur avant exécution : "
                   "une règle mal définie ne fait jamais échouer un contrôle.")


# --------------------------------------------------------------------------- #
# ④ Piste d'audit - EVIDENCES
# --------------------------------------------------------------------------- #
def packs_de_preuve() -> list[pathlib.Path]:
    if not EVIDENCE_DIR.exists():
        return []
    return sorted((p for p in EVIDENCE_DIR.iterdir()
                   if p.is_dir() and (p / "manifest.json").exists()),
                  key=lambda p: p.name, reverse=True)


def ecran_audit(store: CatalogueStore, utilisateur: str) -> None:
    bandeau_etape(3, "Chaque exécution laisse une trace reconstructible : "
                     "identifiant de run, empreintes des sources, configuration "
                     "exacte des règles, résultats, exceptions et journaux.")
    afficher_flash()

    onglet_packs, onglet_journal = st.tabs(
        ["Preuves d'exécution", "Journal des modifications du catalogue"])

    with onglet_packs:
        packs = packs_de_preuve()
        if not packs:
            st.info("Aucune exécution n'a encore produit de preuves.")
        else:
            st.caption(f"{len(packs)} pack(s) de preuves sur disque, dans "
                       f"`evidence/`.")
            choix = st.selectbox("Exécution", packs, format_func=lambda p: p.name)
            manifeste = json.loads((choix / "manifest.json").read_text(encoding="utf-8"))

            g, d = st.columns([3, 2])
            with g:
                st.markdown("##### Les dix pièces exigées par le brief (annexe B.2)")
                lignes = []
                for piece, fichier, contenu in PIECES_AUDIT:
                    present = (choix / fichier).exists()
                    lignes.append({
                        "": "🟢" if present else "⚪",
                        "Pièce attendue": piece,
                        "Support": fichier,
                        "Contenu": contenu,
                    })
                st.dataframe(pd.DataFrame(lignes), width='stretch', hide_index=True)
                st.caption("⚪ *Sign-off Fields* est marqué facultatif par le "
                           "brief ; il se remplit ci-contre.")
            with d:
                st.markdown("##### Empreintes des sources")
                sources = manifeste.get("sources", {})
                st.dataframe(pd.DataFrame([
                    {"Source": k, "SHA-256": v.get("sha256", "")[:16] + "…",
                     "Lignes": v.get("lignes", "")}
                    for k, v in sources.items()]), width='stretch', hide_index=True)
                st.metric("Empreinte du catalogue",
                          manifeste.get("catalogue_sha256", "")[:16] + "…")

            with st.expander("Manifeste complet"):
                st.json(manifeste)
            if (choix / "profil_fichier.json").exists() or manifeste.get("profil_fichier"):
                with st.expander("Structure déduite au moment du run"):
                    st.dataframe(tableau_profil(manifeste.get("profil_fichier", [])),
                                 width='stretch', hide_index=True)

            st.markdown("##### Validation humaine (*Sign-off*)")
            signoff = choix / "signoff.json"
            if signoff.exists():
                st.success("Ce run est validé.")
                st.json(json.loads(signoff.read_text(encoding="utf-8")))
            else:
                commentaire = st.text_input(
                    "Commentaire de validation",
                    placeholder="Résultats revus, écarts DQ09 pris en charge.")
                if st.button("✅ Valider ce run", type="primary"):
                    signoff.write_text(json.dumps({
                        "run_id": manifeste.get("run_id"),
                        "valide_par": utilisateur or "data.steward",
                        "horodatage": dt.datetime.now().isoformat(timespec="seconds"),
                        "commentaire": commentaire,
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    st.session_state["flash"] = f"Run {choix.name} validé."
                    st.rerun()

    with onglet_journal:
        st.caption("Qui a changé quoi, quand, et pourquoi. Rien ne peut être "
                   "effacé : une règle se met en pause, ne se supprime pas.")
        df = pd.DataFrame(store.changelog)
        if df.empty:
            st.info("Journal vide.")
        else:
            st.dataframe(pd.DataFrame({
                "Date": df["timestamp"], "Auteur": df["utilisateur"],
                "Action": df["action"], "Règle": df["rule_id"],
                "Champ": df["champ"], "Avant": df["avant"], "Après": df["apres"],
                "Motif": df["motif"],
            }).iloc[::-1], width='stretch', hide_index=True)


# --------------------------------------------------------------------------- #
def main() -> None:
    store = get_store()
    st.markdown(STYLE, unsafe_allow_html=True)
    with st.sidebar:
        st.title("🧭 DQ Compass")
        st.caption("Couche de contrôle qualité générique, pilotée par catalogue")
        utilisateur = st.text_input("Votre nom", "data.steward",
                                    help="Identifie l'auteur au journal.")
        page = st.radio("Cycle de vie du contrôle", PAGES,
                        captions=[f"{verbe.capitalize()} · {composant}"
                                  for _, _, composant, verbe, _ in ETAPES])
        st.divider()
        actives = store.active_controls()
        couverture = couverture_dimensions(store)
        st.metric("Règles en service", len(actives))
        st.metric("Dimensions couvertes",
                  f"{sum(1 for v in couverture.values() if v)} / {len(DIMENSIONS)}")
        universelles = [c for c in actives
                        if str(c.get("dataset_scope", "")).strip() in ("", "*")]
        st.metric("Règles universelles", len(universelles),
                  help="Règles qui s'appliquent à tout fichier, y compris ceux "
                       "qui n'existent pas encore.")
        if st.button("🔄 Recharger le catalogue"):
            get_store(force=True)
            st.rerun()
        st.caption(f"{dt.date.today():%d/%m/%Y}")

    if page.startswith("①"):
        ecran_catalogue(store, utilisateur)
    elif page.startswith("②"):
        ecran_execution(store, utilisateur)
    elif page.startswith("③"):
        ecran_restitution(store)
    else:
        ecran_audit(store, utilisateur)


main()
