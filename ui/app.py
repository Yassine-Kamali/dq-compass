"""
DQ Compass - interface de pilotage du catalogue.

Quatre ecrans : Catalogue, Editeur de regle, Datasets, Execution.

L'interface ne contient aucune logique de controle. Elle lit et ecrit le store,
appelle le moteur, et telecharge le classeur Excel. Le formulaire de l'editeur
est genere a partir des parametres declares par le template : ajouter un
template au catalogue suffit a le rendre pilotable ici, sans toucher a l'UI.

    streamlit run ui/app.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "reporting"))

from dq_engine import run_dq, validate_control  # noqa: E402
from excel_report import build_workbook  # noqa: E402
from store import SEVERITES, STATUTS, CatalogueStore  # noqa: E402

st.set_page_config(page_title="DQ Compass", page_icon="🧭", layout="wide")

COULEUR_STATUT = {"Actif": "🟢", "Suspendu": "🟠", "Deprecie": "⚪"}
COULEUR_RUN = {"PASS": "🟢", "FAIL": "🔴", "ERREUR": "🟠", "NON_APPLICABLE": "⚪"}

# Widget choice per parameter name. Anything unknown falls back to free text.
PARAM_COLONNE = {"column", "field_a", "field_b", "before", "after", "amount",
                 "total_column"}
PARAM_NOMBRE = {"min", "max", "max_lag_days", "tolerance_pct", "couverture_min_pct"}


# --------------------------------------------------------------------------- #
def get_store(force: bool = False) -> CatalogueStore:
    if force or "store" not in st.session_state:
        st.session_state.store = CatalogueStore()
    return st.session_state.store


def save(store: CatalogueStore, message: str) -> None:
    store.save()
    get_store(force=True)
    st.success(message)


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
    def split(txt: str) -> list[list[str]]:
        out = []
        for part in str(txt or "").split(","):
            part = part.strip()
            if part:
                out.append([a.strip() for a in part.split("|") if a.strip()])
        return out
    requis = split(tpl.get("params_requis", ""))
    optionnels = [a for grp in split(tpl.get("params_optionnels", "")) for a in grp]
    return requis, optionnels


# --------------------------------------------------------------------------- #
# Ecran 2 - Editeur de regle : formulaire pilote par le template
# --------------------------------------------------------------------------- #
def widget_param(nom: str, store: CatalogueStore, scope: str, valeur, cle: str):
    colonnes = colonnes_du_scope(store, scope)

    if nom == "role":
        options = roles_disponibles(store)
        idx = options.index(valeur) if valeur in options else 0
        return st.selectbox(f"`{nom}`", options, index=idx, key=cle,
                            help="Cible toutes les colonnes portant ce role, "
                                 "dans tous les datasets du perimetre.")
    if nom in PARAM_COLONNE and colonnes:
        options = ["(saisie libre)"] + colonnes
        idx = options.index(valeur) if valeur in options else 0
        choix = st.selectbox(f"`{nom}`", options, index=idx, key=cle)
        if choix == "(saisie libre)":
            return st.text_input(f"`{nom}` (libre)", value="" if valeur in (None, "(saisie libre)") else str(valeur), key=cle + "_libre")
        return choix
    if nom in PARAM_NOMBRE:
        return st.number_input(f"`{nom}`", value=float(valeur) if valeur is not None else 0.0,
                               step=1.0, key=cle)
    if nom == "values":
        brut = st.text_input(f"`{nom}` (valeurs separees par des virgules)",
                             value=", ".join(valeur) if isinstance(valeur, list) else (valeur or ""),
                             key=cle)
        return [v.strip() for v in brut.split(",") if v.strip()]
    if nom == "columns":
        defaut = valeur if isinstance(valeur, list) else []
        return st.multiselect(f"`{nom}`", colonnes,
                              default=[c for c in defaut if c in colonnes], key=cle)
    if nom == "group_by":
        defaut = valeur if isinstance(valeur, list) else []
        return st.multiselect(f"`{nom}`", colonnes,
                              default=[c for c in defaut if c in colonnes], key=cle,
                              help="Dimensions maintenues fixes pendant la reconciliation.")
    if nom == "ref_dataset":
        options = list(store.datasets)
        idx = options.index(valeur) if valeur in options else 0
        return st.selectbox(f"`{nom}`", options, index=idx, key=cle)
    if nom == "ref_column":
        ref_ds = st.session_state.get("param_ref_dataset")
        options = [c["colonne"] for c in store.columns_of(ref_ds)] if ref_ds else []
        if not options:
            return st.text_input(f"`{nom}`", value=valeur or "", key=cle)
        idx = options.index(valeur) if valeur in options else 0
        return st.selectbox(f"`{nom}`", options, index=idx, key=cle)
    if nom == "sens":
        options = ["bilateral", "parties_max", "couverture_min"]
        idx = options.index(valeur) if valeur in options else 0
        return st.selectbox(f"`{nom}`", options, index=idx, key=cle)
    return st.text_input(f"`{nom}`", value="" if valeur is None else str(valeur), key=cle)


def editeur_regle(store: CatalogueStore, utilisateur: str, control: dict | None) -> None:
    creation = control is None
    control = control or {}
    st.subheader("Nouveau controle" if creation
                 else f"Controle {control['rule_id']} — version {control.get('version', 1)}")

    templates = [t["template_id"] for t in store.templates]
    tid_courant = control.get("template", templates[0])
    tid = st.selectbox("Template", templates,
                       index=templates.index(tid_courant) if tid_courant in templates else 0,
                       key="edit_template")
    tpl = store.template(tid)
    st.caption(f"**{tpl['dimension']}** — {tpl['description_logique']}  \n"
               f"KPI produit : {tpl['kpi_produit']}")

    c1, c2 = st.columns(2)
    with c1:
        nom = st.text_input("Nom du controle", control.get("control_name", ""))
        description = st.text_area("Description metier", control.get("description", ""),
                                   height=80)
        datasets = list(store.datasets)
        scope_defaut = control.get("dataset_scope", datasets[0] if datasets else "*")
        mode_scope = st.radio("Perimetre", ["Datasets choisis", "Tous les datasets (*)"],
                              index=1 if scope_defaut.strip() == "*" else 0,
                              horizontal=True)
        if mode_scope.startswith("Tous"):
            scope = "*"
        else:
            choisis = st.multiselect(
                "Datasets", datasets,
                default=[d for d in scope_defaut.split(",") if d.strip() in datasets]
                or datasets[:1])
            scope = ",".join(choisis)
    with c2:
        severity = st.selectbox("Severite", SEVERITES,
                                index=SEVERITES.index(control["severity"])
                                if control.get("severity") in SEVERITES else 2)
        seuil = st.number_input("Seuil de tolerance (% de lignes en exception admis)",
                                value=float(control.get("seuil_tolerance_pct") or 0),
                                min_value=0.0, max_value=100.0, step=0.5)
        owner = st.text_input("Proprietaire", control.get("owner", "Data Steward"))
        frequency = st.selectbox(
            "Frequence", ["Quotidienne", "Hebdomadaire", "Mensuelle", "Trimestrielle",
                          "Annuelle", "A la demande"],
            index=3 if not control.get("frequency") else max(0, [
                "Quotidienne", "Hebdomadaire", "Mensuelle", "Trimestrielle", "Annuelle",
                "A la demande"].index(control["frequency"])
                if control.get("frequency") in ["Quotidienne", "Hebdomadaire", "Mensuelle",
                                                "Trimestrielle", "Annuelle", "A la demande"]
                else 3))

    st.markdown("##### Parametres du template")
    st.caption("Ce bloc est genere a partir des parametres declares par le template. "
               "Il change quand vous changez de template.")
    params_actuels = {}
    if control.get("params"):
        try:
            params_actuels = json.loads(control["params"])
        except json.JSONDecodeError:
            params_actuels = {}
    if control.get("template") != tid:
        params_actuels = {}

    requis, optionnels = params_du_template(tpl)
    params: dict = {}
    for groupe in requis:
        if len(groupe) == 1:
            nom_param = groupe[0]
        else:
            defaut = next((a for a in groupe if a in params_actuels), groupe[0])
            nom_param = st.radio(f"Cibler par", groupe, horizontal=True,
                                 index=groupe.index(defaut), key=f"choix_{'_'.join(groupe)}")
        valeur = widget_param(nom_param, store, scope, params_actuels.get(nom_param),
                              f"param_{nom_param}")
        if valeur not in (None, "", []):
            params[nom_param] = valeur

    if optionnels:
        with st.expander(f"Parametres optionnels ({', '.join(optionnels)})",
                         expanded=any(o in params_actuels for o in optionnels)):
            for nom_param in optionnels:
                actif = st.checkbox(f"Definir `{nom_param}`",
                                    value=nom_param in params_actuels,
                                    key=f"opt_{nom_param}")
                if actif:
                    valeur = widget_param(nom_param, store, scope,
                                          params_actuels.get(nom_param), f"param_{nom_param}")
                    if valeur not in (None, "", []):
                        params[nom_param] = valeur

    c3, c4 = st.columns(2)
    with c3:
        remediation = st.text_area("Action de remediation", height=70,
                                   value=control.get("remediation_action", ""))
    with c4:
        motif = st.text_area("Motif de la modification (journalise)", height=70,
                             value="", placeholder="Pourquoi ce changement ?")

    st.markdown("##### Validation")
    candidat = {
        "rule_id": control.get("rule_id", store.next_rule_id()),
        "control_name": nom, "control_type": tpl["dimension"], "description": description,
        "template": tid, "params": json.dumps(params, ensure_ascii=False),
        "logic_definition": control.get("logic_definition", ""),
        "dataset_scope": scope, "data_element": ", ".join(
            str(v) for k, v in params.items() if k in PARAM_COLONNE | {"role"}),
        "seuil_tolerance_pct": seuil, "severity": severity, "frequency": frequency,
        "owner": owner, "output_type": control.get("output_type", "Exception report"),
        "kpi": tpl["kpi_produit"], "remediation_action": remediation,
    }
    st.code(json.dumps(params, ensure_ascii=False, indent=2), language="json")

    cibles = scope_datasets(store, scope)
    erreurs: list[str] = []
    if not nom.strip():
        erreurs.append("Le nom du controle est obligatoire.")
    for ds in cibles:
        for err in validate_control(candidat, store, ds):
            erreurs.append(f"[{ds}] {err}")
    if not cibles:
        erreurs.append("Aucun dataset dans le perimetre.")

    if erreurs:
        st.error("Le controle est refuse par le validateur :\n\n"
                 + "\n".join(f"- {e}" for e in erreurs))
    else:
        st.success(f"Controle valide sur {len(cibles)} dataset(s) : {', '.join(cibles)}")

    if st.button("Enregistrer", type="primary", disabled=bool(erreurs)):
        if creation:
            store.add_control(candidat, utilisateur, motif or "Creation via l'interface")
            save(store, f"Controle {candidat['rule_id']} cree.")
        else:
            store.update_control(control["rule_id"], candidat, utilisateur,
                                 motif or "Modification via l'interface")
            save(store, f"Controle {control['rule_id']} mis a jour "
                        f"(version {store.control(control['rule_id'])['version']}).")
        st.session_state.pop("edition", None)
        st.rerun()


# --------------------------------------------------------------------------- #
# Ecran 1 - Catalogue
# --------------------------------------------------------------------------- #
def ecran_catalogue(store: CatalogueStore, utilisateur: str) -> None:
    st.header("Catalogue de controles")

    # La cle "edition" absente = pas en edition ; presente a None = creation.
    if "edition" in st.session_state:
        rid = st.session_state["edition"]
        if st.button("← Retour au catalogue"):
            st.session_state.pop("edition")
            st.rerun()
        editeur_regle(store, utilisateur, store.control(rid) if rid else None)
        return

    df = pd.DataFrame(store.controls)
    c1, c2, c3, c4 = st.columns(4)
    dims = sorted(df["control_type"].dropna().unique()) if not df.empty else []
    f_dim = c1.multiselect("Dimension", dims)
    f_sev = c2.multiselect("Severite", SEVERITES)
    f_statut = c3.multiselect("Statut", STATUTS, default=["Actif"])
    f_texte = c4.text_input("Recherche", placeholder="nom, colonne, template…")

    vue = df.copy()
    if f_dim:
        vue = vue[vue["control_type"].isin(f_dim)]
    if f_sev:
        vue = vue[vue["severity"].isin(f_sev)]
    if f_statut:
        vue = vue[vue["statut"].isin(f_statut)]
    if f_texte:
        masque = vue.astype(str).apply(
            lambda r: f_texte.lower() in " ".join(r).lower(), axis=1)
        vue = vue[masque]

    st.caption(f"{len(vue)} controle(s) sur {len(df)} — "
               f"{len(store.active_controls())} actifs au total")
    affichage = vue.assign(
        statut=vue["statut"].map(lambda s: f"{COULEUR_STATUT.get(s, '')} {s}"))
    st.dataframe(
        affichage[["rule_id", "control_name", "control_type", "template",
                   "dataset_scope", "severity", "seuil_tolerance_pct", "statut",
                   "version", "owner"]],
        width='stretch', hide_index=True)

    st.divider()
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("➕ Ajouter un controle", type="primary", width='stretch'):
            st.session_state["edition"] = None
            st.rerun()
    with c2:
        rid = st.selectbox("Agir sur le controle", [""] + list(df["rule_id"]))

    if rid:
        ctrl = store.control(rid)
        st.info(f"**{ctrl['control_name']}** — {ctrl['description']}")
        motif = st.text_input("Motif (journalise)", key="motif_action")
        a1, a2, a3, a4 = st.columns(4)
        if a1.button("✏️ Modifier", width='stretch'):
            st.session_state["edition"] = rid
            st.rerun()
        if a2.button("⏸️ Suspendre", width='stretch',
                     disabled=ctrl["statut"] == "Suspendu"):
            store.set_statut(rid, "Suspendu", utilisateur, motif or "Suspension via l'interface")
            save(store, f"{rid} suspendu.")
            st.rerun()
        if a3.button("▶️ Reactiver", width='stretch',
                     disabled=ctrl["statut"] == "Actif"):
            store.set_statut(rid, "Actif", utilisateur, motif or "Reactivation via l'interface")
            save(store, f"{rid} reactive.")
            st.rerun()
        if a4.button("🗑️ Supprimer", width='stretch'):
            st.error("Suppression interdite : l'audit exige de pouvoir rejouer les runs "
                     "historiques. Utilisez **Suspendre** (arret des executions futures, "
                     "definition conservee) ou **Deprecier**.")


# --------------------------------------------------------------------------- #
# Ecran 3 - Datasets
# --------------------------------------------------------------------------- #
def ecran_datasets(store: CatalogueStore, utilisateur: str) -> None:
    st.header("Contrats de dataset")
    st.caption("Decrire un dataset ici suffit a lui appliquer tous les controles "
               "transverses (perimetre `*` ou ciblage par role). Aucun code a ecrire.")

    onglets = st.tabs(list(store.datasets) + ["➕ Declarer un dataset"])
    for onglet, nom in zip(onglets, store.datasets):
        with onglet:
            ds = store.dataset(nom)
            st.markdown(f"**{ds['libelle']}**  \nSource : `{ds['source']}` — "
                        f"proprietaire : {ds.get('proprietaire', '-')}")
            cols = pd.DataFrame(ds["colonnes"])
            st.dataframe(cols, width='stretch', hide_index=True)
            fk = cols[cols["fk_dataset"].astype(str).str.len() > 0] if "fk_dataset" in cols else pd.DataFrame()
            if not fk.empty:
                st.caption("Cles etrangeres declarees : " + ", ".join(
                    f"`{r.colonne}` → `{r.fk_dataset}.{r.fk_colonne}`"
                    for r in fk.itertuples()))
            actifs = [c["rule_id"] for c in store.active_controls(nom)]
            st.info(f"{len(actifs)} controle(s) actif(s) s'appliquent : {', '.join(actifs)}")

    with onglets[-1]:
        st.markdown("##### Declarer un nouveau dataset")
        nom = st.text_input("Nom technique", placeholder="ex. inventaire")
        libelle = st.text_input("Libelle metier")
        source = st.text_input("Chemin du fichier CSV (relatif a la racine du projet)",
                               placeholder="data/prepared/inventaire.csv")
        proprietaire = st.text_input("Proprietaire", "Data Steward")
        st.caption("Collez le contrat de colonnes, une ligne par colonne : "
                   "`colonne;type;role;cle_primaire;obligatoire;fk_dataset;fk_colonne`")
        brut = st.text_area("Contrat de colonnes", height=180,
                            placeholder="article_id;string;identifiant;OUI;OUI;;\n"
                                        "code_devise;string;code;NON;OUI;ref_devises;code_devise")
        if st.button("Declarer le dataset", type="primary",
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
                                       "proprietaire": proprietaire, "colonnes": colonnes},
                                 utilisateur, "Declaration via l'interface")
            save(store, f"Dataset `{nom}` declare avec {len(colonnes)} colonnes. "
                        f"Les controles transverses s'y appliquent deja.")
            st.rerun()


# --------------------------------------------------------------------------- #
# Ecran 4 - Execution
# --------------------------------------------------------------------------- #
def ecran_execution(store: CatalogueStore) -> None:
    st.header("Execution et rapport")
    choix = st.multiselect("Datasets a controler", list(store.datasets),
                           default=[d for d in store.datasets if d != "bis_turnover"])
    libelle = st.text_input("Libelle du run", "Execution depuis l'interface")

    if st.button("▶️ Lancer les controles", type="primary", disabled=not choix):
        with st.spinner("Execution en cours…"):
            run = run_dq(datasets=choix, store=store, run_label=libelle)
            chemin = build_workbook(run, store)
        st.session_state["dernier_run"] = run
        st.session_state["dernier_classeur"] = chemin

    run = st.session_state.get("dernier_run")
    if run is None:
        st.info("Aucun run dans cette session.")
        return

    s = run.summary()
    c = st.columns(6)
    c[0].metric("Conformite", f"{100 * s['pass'] / max(s['controles'], 1):.0f}%")
    c[1].metric("Controles", s["controles"])
    c[2].metric("En echec", s["fail"], delta_color="inverse")
    c[3].metric("Erreurs", s["erreur"], delta_color="inverse")
    c[4].metric("Exceptions", f"{s['exceptions']:,}".replace(",", " "))
    c[5].metric("Rejets", s["rejets"], delta_color="inverse")

    chemin = st.session_state.get("dernier_classeur")
    if chemin and pathlib.Path(chemin).exists():
        st.download_button("⬇️ Telecharger le rapport Excel",
                           data=pathlib.Path(chemin).read_bytes(),
                           file_name=pathlib.Path(chemin).name, type="primary",
                           mime="application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet")

    t1, t2, t3, t4 = st.tabs(["Scorecard", "Exceptions", "Rejets", "Journal d'execution"])
    with t1:
        sc = run.scorecard
        sc = sc.assign(statut=sc["statut"].map(lambda v: f"{COULEUR_RUN.get(v, '')} {v}"))
        st.dataframe(sc[["rule_id", "control_name", "dimension", "dataset", "cible",
                         "statut", "kpi_nom", "kpi_valeur", "seuil_pct", "lignes_testees",
                         "lignes_ko", "severity", "owner"]],
                     width='stretch', hide_index=True)
    with t2:
        st.dataframe(run.exceptions.head(2000), width='stretch', hide_index=True)
        st.caption(f"{len(run.exceptions)} exception(s) — 2 000 premieres affichees. "
                   "Le classeur Excel contient le detail complet.")
    with t3:
        st.dataframe(pd.DataFrame(run.rejets), width='stretch', hide_index=True)
        st.caption("Controles refuses par le validateur avant execution.")
    with t4:
        st.code("\n".join(run.journal), language="text")
    st.caption(f"Evidence pack : `{run.evidence_path}`")


# --------------------------------------------------------------------------- #
def ecran_journal(store: CatalogueStore) -> None:
    st.header("Journal des modifications du catalogue")
    st.caption("Trace d'audit de toutes les evolutions du catalogue : qui, quand, "
               "quel champ, valeur avant et apres, et pour quel motif.")
    df = pd.DataFrame(store.changelog)
    if df.empty:
        st.info("Journal vide.")
        return
    st.dataframe(df.iloc[::-1], width='stretch', hide_index=True)


def main() -> None:
    store = get_store()
    with st.sidebar:
        st.title("🧭 DQ Compass")
        st.caption("Pilotage du catalogue de controles qualite")
        utilisateur = st.text_input("Utilisateur", "data.steward",
                                    help="Identifie l'auteur dans le journal d'audit.")
        page = st.radio("Navigation", ["Catalogue", "Datasets", "Execution", "Journal"])
        st.divider()
        st.metric("Controles actifs", len(store.active_controls()))
        st.metric("Datasets declares", len(store.datasets))
        st.metric("Templates disponibles", len(store.templates))
        if st.button("🔄 Recharger le catalogue"):
            get_store(force=True)
            st.rerun()

    if page == "Catalogue":
        ecran_catalogue(store, utilisateur)
    elif page == "Datasets":
        ecran_datasets(store, utilisateur)
    elif page == "Execution":
        ecran_execution(store)
    else:
        ecran_journal(store)


main()
