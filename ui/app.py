"""
DQ Compass - couche de controle qualite generique, pilotee par catalogue.

L'interface suit le cycle de vie impose par le brief (§4, « Functional
architecture ») et en reprend le vocabulaire, ecran par ecran :

    ① Control catalogue   Control Catalogue  DEFINES
    ② Execution           Data Quality Engine  EXECUTES
    ③ Reporting           Reporting Layer  REPORTS
    ④ Audit trail         Audit Layer  EVIDENCES

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

L'interface parle anglais ; les commentaires du code restent en francais, comme
dans le reste du depot.

    streamlit run ui/app.py
"""
from __future__ import annotations

import datetime as dt
import io
import json
import pathlib
import sys
import zipfile

import altair as alt
import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "reporting"))

from dq_engine import (EVIDENCE_DIR, applicabilite, charger_fichier,  # noqa: E402
                       parse_params, rejouer_pack, resolve_targets, run_dq,
                       validate_control, verifier_pack)
from excel_report import build_workbook  # noqa: E402
from profiler import cle_candidate, profiler  # noqa: E402
from store import (SEVERITES, SEVERITES_AIDE, STATUTS,  # noqa: E402
                   CatalogueStore, libelle_severite, libelle_statut,
                   phrase_controle, scope_matches)
from suggestions import en_controle, suggerer  # noqa: E402

# La marque du projet, detouree sur fond transparent par
# `engine/preparer_logo.py`. Elle sert d'icone d'onglet et de logo de barre
# laterale ; l'emoji ne reste qu'en secours si le fichier a disparu.
LOGO = ROOT / "logo" / "logo_mark.png"

st.set_page_config(page_title="DQ Compass", layout="wide",
                   page_icon=str(LOGO) if LOGO.exists() else "🧭")

ENTREES = ROOT / "data" / "entrees"
DOSSIERS_CONNUS = [ROOT / "data" / "entrees", ROOT / "data" / "prepared",
                   ROOT / "data" / "ref", ROOT / "data" / "exemples"]
EXTENSIONS = {".csv", ".xlsx", ".xls", ".xlsm"}

# Les six dimensions du brief (§2.2), dans son ordre et sous ses propres noms.
DIMENSIONS = {
    "Completeness": "Completeness",
    "Validity": "Validity",
    "Uniqueness": "Uniqueness",
    "Consistency": "Consistency",
    "Timeliness": "Timeliness",
    "Reconciliation": "Reconciliation",
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
    ("Run ID", "manifest.json", "unique execution identifier"),
    ("Execution Timestamp", "manifest.json", "date, time and as-of date"),
    ("Dataset Reference", "manifest.json", "path, SHA-256, row count, profile"),
    ("Rule Configuration", "executed_rules.json", "full definition of every rule run"),
    ("Execution Parameters", "executed_rules.json", "parameters and thresholds applied"),
    ("Control Results", "results.json", "verdict, KPI and explanation per control"),
    ("Exception Dataset", "exceptions.csv", "every failing row, untruncated"),
    ("Logs", "execution.log", "step-by-step execution log"),
    ("System Trace", "manifest.json", "engine, Python, pandas, machine, user"),
    ("Sign-off Fields", "signoff.json", "human validation (optional)"),
]

# Pieces complementaires : elles ne figurent pas a l'annexe B.2 mais rendent le
# pack verifiable par un tiers sans rien connaitre du moteur.
PIECES_COMPLEMENT = [
    ("Summary", "summary.json", "run totals, RAG status, per-dimension counts"),
    ("Catalogue snapshot", "catalogue_snapshot.json", "the whole catalogue, frozen"),
    ("Rejected rules", "rejected_rules.json", "rules refused by the validator"),
    ("Checksums", "checksums.json", "SHA-256 of every file in this pack"),
]

# Les quatre etapes du cycle de vie (§4 du brief), chacune avec sa couleur.
# La meme teinte porte l'entree de menu, l'en-tete de l'ecran et son cartouche :
# on sait ou l'on se trouve dans le cycle sans lire une ligne.
ETAPES = [
    ("①", "Control catalogue", "Control Catalogue", "DEFINES", "#9B87E0"),
    ("②", "Execution", "Data Quality Engine", "EXECUTES", "#5A9DF8"),
    ("③", "Reporting", "Reporting Layer", "REPORTS", "#2FBFA8"),
    ("④", "Audit trail", "Audit Layer", "EVIDENCES", "#9AAAB8"),
]

# Surfaces de l'interface. Le fond de l'application est noir - impose une fois
# pour toutes dans `.streamlit/config.toml`, y compris pour le theme clair de
# Streamlit. Tout ce qui est dessine ici part donc du noir : une carte est un
# cran au-dessus, un filet reste un filet, et aucune teinte de theme clair ne
# subsiste dans le code.
FOND_CARTE = "#0E1114"
FOND_CREUX = "#080A0C"
BORDURE = "#22262E"
TEXTE_SOURDINE = "#93A0AE"
PAGES = [f"{n} {titre}" for n, titre, _, _, _ in ETAPES]

# Feuille de style. Elle ne repeint rien que le theme sache faire : le fond
# noir, les couleurs de texte et les bordures viennent de
# `.streamlit/config.toml`. Ne restent ici que les elements que Streamlit ne
# thematise pas - l'en-tete flottant, le trace des onglets, la carte d'un bloc
# depliable - et le reperage colore du cycle de vie dans la barre laterale.
STYLE = """<style>
/* L'en-tete flottant de Streamlit ne doit pas trancher sur la page. */
[data-testid="stHeader"] { background: transparent; }

/* Onglets : de l'air entre les libelles, un filet sous la rangee. */
[data-testid="stTabs"] [role="tablist"] {
    gap: 28px;
    border-bottom: 1px solid __BORDURE__;
}
[data-testid="stTab"] { padding: 10px 0 12px 0; font-weight: 600; }

/* Bloc depliable : une carte, pas un accordeon de formulaire. */
[data-testid="stExpander"] details {
    background: __FOND_CARTE__;
    border: 1px solid __BORDURE__;
    border-radius: 10px;
    overflow: hidden;
}
[data-testid="stExpander"] details > summary { padding: 12px 16px; }
[data-testid="stExpander"] details > summary:hover {
    background: rgba(255, 255, 255, .05);
}

/* Tableaux : des angles adoucis, comme les cartes. */
[data-testid="stDataFrame"] { border-radius: 10px; }

/* Barre laterale : chaque etape du cycle porte sa couleur. */
section[data-testid="stSidebar"] div[role="radiogroup"] > label {
    border-left: 5px solid transparent;
    border-radius: 8px;
    padding: 9px 12px;
    margin-bottom: 6px;
    transition: background .15s ease;
}
section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
    background: rgba(255, 255, 255, .06);
}
""" + "".join(
    f'section[data-testid="stSidebar"] div[role="radiogroup"] > label:'
    f'nth-of-type({i + 1}) {{ border-left-color: {c}; }}\n'
    for i, (_, _, _, _, c) in enumerate(ETAPES)) + "</style>"
STYLE = (STYLE.replace("__BORDURE__", BORDURE)
         .replace("__FOND_CARTE__", FOND_CARTE))


def bandeau_etape(index: int, description: str) -> None:
    """En-tete colore d'un ecran, qui le situe dans le cycle de vie."""
    numero, titre, composant, verbe, couleur = ETAPES[index]
    st.markdown(
        f"""<div style="background:linear-gradient(90deg,{couleur}1F,{FOND_CREUX} 70%);
        border:1px solid {BORDURE};border-left:5px solid {couleur};
        border-radius:12px;padding:18px 24px;margin:0 0 18px 0;">
        <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;">
        <span style="font-size:30px;font-weight:700;color:{couleur};
        line-height:1;">{numero}</span>
        <span style="font-size:26px;font-weight:700;color:{couleur};
        letter-spacing:-.01em;">{titre}</span>
        <span style="font-size:11px;letter-spacing:.14em;color:{couleur};
        border:1px solid {couleur}55;border-radius:999px;padding:3px 11px;
        white-space:nowrap;">{verbe} · {composant}</span></div>
        <div style="font-size:14px;color:{TEXTE_SOURDINE};margin-top:9px;
        max-width:78ch;line-height:1.5;">{description}</div></div>""",
        unsafe_allow_html=True)


# Statuts d'execution, dans l'ordre d'empilement valide par le validateur de
# palette : vert et rouge ne sont jamais adjacents (ecart CVD 4,1 -> 10,7).
# La pastille double la couleur, qui ne porte donc jamais seule le sens.
STATUTS_VUE = [
    ("PASS", "Passed", "🟢", "#0ca30c"),
    ("SKIPPED", "Skipped", "⚪", "#898781"),
    ("ERROR", "Technical error", "🟠", "#fab219"),
    ("FAIL", "In breach", "🔴", "#d03b3b"),
]
# Teinte unique des barres de magnitude : 4,30:1 sur fond clair, 3,94:1 sur fond
# sombre - lisible sans connaitre le theme de l'utilisateur.
TEINTE_MAGNITUDE = "#2a78d6"

# Le feu tricolore du run, tel que le moteur le calcule (dq_engine.statut_global).
RAG_VUE = {
    "GREEN": ("🟢", "#3FD37A", "#071A0F", "#1E5233"),
    "AMBER": ("🟠", "#F5B33C", "#1C1405", "#5C4520"),
    "RED": ("🔴", "#FF6B6B", "#20090A", "#5C2226"),
}

PUCE_STATUT = {"Actif": "🟢", "Suspendu": "🟠", "Deprecie": "⚪"}
PUCE_RUN = {"PASS": "🟢", "FAIL": "🔴", "ERROR": "🟠", "SKIPPED": "⚪"}

TOLERANCES = {
    "No breach tolerated": 0.0,
    "Up to 1 % of rows": 1.0,
    "Up to 5 % of rows": 5.0,
    "Up to 10 % of rows": 10.0,
}

FREQUENCES = ["Daily", "Weekly", "Monthly", "Quarterly", "Annual", "On demand"]

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


def nombre(valeur) -> str:
    """Un millier se lit avec une espace, jamais avec une virgule."""
    return f"{int(valeur):,}".replace(",", " ")


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
    titre = (f"📋 Columns available ({len(connues)})" if connues
             else "📋 No column known yet — load a file")
    with st.expander(titre, expanded=not connues):
        st.caption("The editor knows no file structure: there is no declared "
                   "schema any more. Load a file and its columns will be "
                   "offered in the lists below. Only the header is read.")
        g, d = st.columns(2)
        with g:
            connus = fichiers_connus()
            choix = st.selectbox(
                "A file already present", connus, index=None,
                placeholder="Pick a file…", key="edit_fichier_connu",
                format_func=lambda p: p.name)
            if choix is not None:
                try:
                    retenir_entetes(choix.stem, entetes_du_fichier(choix))
                except (ValueError, FileNotFoundError) as exc:
                    st.error(f"Cannot read the file: {exc}")
        with d:
            depose = st.file_uploader("Or drop a file",
                                      type=["csv", "xlsx", "xls", "xlsm"],
                                      key="edit_fichier_depose")
            if depose is not None:
                ENTREES.mkdir(parents=True, exist_ok=True)
                chemin = ENTREES / depose.name
                chemin.write_bytes(depose.getbuffer())
                try:
                    retenir_entetes(chemin.stem, entetes_du_fichier(chemin))
                    st.caption(f"Saved to `data/entrees/{depose.name}`")
                except (ValueError, FileNotFoundError) as exc:
                    st.error(f"Cannot read the file: {exc}")

        vus = st.session_state.get("profils_vus", {})
        if vus:
            for nom, profil in vus.items():
                st.markdown(f"**`{nom}`** — "
                            + ", ".join(f"`{c['colonne']}`" for c in profil))
        else:
            st.info("You can also write a rule without any file: pick "
                    "**Column name pattern** instead of a named column. That is "
                    "how every shipped rule is written.")


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
            f"""<div style="background:{FOND_CARTE};border:1px solid {BORDURE};
            border-top:3px solid {couleur};border-radius:10px;
            padding:12px 16px 14px 16px;height:100%;">
            <div style="font-size:11px;color:{TEXTE_SOURDINE};
            text-transform:uppercase;letter-spacing:.09em;font-weight:600;
            ">{titre}</div>
            <div style="font-size:27px;font-weight:700;color:{couleur};
            line-height:1.25;margin-top:3px;">{valeur}</div></div>""",
            unsafe_allow_html=True)


def fiche(paires: list[tuple[str, str]]) -> None:
    """Grille libelle / valeur, pour le detail d'un controle.

    Une douzaine d'attributs alignes se lisent d'un coup d'oeil ; les memes
    empiles en phrases ne se lisent pas du tout.
    """
    cases = "".join(
        f"""<div style="min-width:150px;flex:1 1 150px;">
        <div style="font-size:10.5px;color:{TEXTE_SOURDINE};
        text-transform:uppercase;letter-spacing:.09em;font-weight:600;
        ">{libelle}</div>
        <div style="font-size:14.5px;margin-top:2px;">{valeur}</div></div>"""
        for libelle, valeur in paires)
    st.markdown(
        f"""<div style="display:flex;flex-wrap:wrap;gap:18px 26px;
        background:{FOND_CREUX};border:1px solid {BORDURE};border-radius:10px;
        padding:14px 18px;margin-bottom:10px;">{cases}</div>""",
        unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# ① Control catalogue - DEFINES
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

    if nom == "colonnes_motif" or nom.startswith("motif_"):
        return st.text_input(
            label, value=valeur or "", key=cle, placeholder="(?i)(^|_)id$",
            help="Regular expression on the column NAME. The rule applies to "
                 "every column whose name matches, in every file in scope — "
                 "including files that do not exist yet.")
    if nom in PARAM_COLONNE:
        if colonnes:
            options = colonnes + ["✏️ Type another name…"]
            index = options.index(valeur) if valeur in options else None
            choix = st.selectbox(label, options, index=index,
                                 placeholder="Pick a column…", key=cle)
            if choix == "✏️ Type another name…":
                return st.text_input(f"{label} (exact name)", value="", key=f"{cle}_libre")
            return choix
        return st.text_input(label, value=valeur or "", key=cle,
                             help="Run a control on a file once and its columns "
                                  "will be offered here.")
    if nom in PARAM_NOMBRE:
        return st.number_input(label, value=float(valeur) if valeur is not None else 0.0,
                               step=1.0, key=cle)
    if nom == "values":
        brut = st.text_input(
            label, key=cle, placeholder="PERMANENT, FIXED_TERM, INTERNSHIP",
            value=", ".join(str(v) for v in valeur) if isinstance(valeur, list)
            else (valeur or ""),
            help="Separate the values with commas.")
        return [v.strip() for v in brut.split(",") if v.strip()]
    if nom in ("columns", "group_by"):
        defaut = valeur if isinstance(valeur, list) else []
        if colonnes:
            return st.multiselect(label, colonnes, key=cle,
                                  placeholder="Pick one or more columns…",
                                  default=[c for c in defaut if c in colonnes])
        brut = st.text_input(label, value=", ".join(defaut), key=cle,
                             placeholder="column_a, column_b")
        return [v.strip() for v in brut.split(",") if v.strip()]
    if nom == "ref_fichier":
        options = fichiers_de_reference()
        return st.selectbox(label, options,
                            index=options.index(valeur) if valeur in options else None,
                            placeholder="Pick a reference file…", key=cle)
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
        options = {"parties_max": "The parts must never exceed the total",
                   "couverture_min": "The parts must cover a minimum of the total",
                   "bilateral": "Any gap counts, in either direction"}
        cles = list(options)
        return st.selectbox(label, cles,
                            index=cles.index(valeur) if valeur in cles else 0,
                            format_func=lambda k: options[k], key=cle)
    return st.text_input(label, value="" if valeur is None else str(valeur), key=cle)


def editeur_regle(store: CatalogueStore, utilisateur: str, control: dict | None) -> None:
    creation = control is None
    control = control or {}
    st.subheader("Create a control rule" if creation
                 else f"Edit {control['rule_id']} · "
                      f"version {control.get('version', 1)}")

    st.markdown("#### 1. What do you want to check?")
    simples = [t for t in store.templates if t.get("niveau", "simple") == "simple"]
    avances = [t for t in store.templates if t.get("niveau") == "avance"]

    tid_courant = control.get("template")
    avance_courant = any(t["template_id"] == tid_courant for t in avances)
    mode_avance = st.toggle("Show advanced rules", value=avance_courant,
                            help="Reconciliations and custom conditions. For "
                                 "data profiles only.")
    proposes = simples + avances if mode_avance else simples

    def etiquette(t: dict) -> str:
        return t.get("libelle_metier", t["template_id"])

    index = next((i for i, t in enumerate(proposes)
                  if t["template_id"] == tid_courant), 0)
    tpl = st.radio("Type of check", proposes, index=index,
                   format_func=etiquette, key="edit_template",
                   label_visibility="collapsed")
    st.info(f"**{etiquette(tpl)}** — {tpl.get('explication', '')}  \n"
            f"*Example: {tpl.get('exemple_metier', '—')}*")

    st.markdown(f"#### 2. {tpl.get('question_metier', 'On which data?')}")
    selecteur_de_colonnes()
    scope_defaut = str(control.get("dataset_scope", "*")).strip() or "*"
    tous = st.checkbox("Apply to every file, present and future",
                       value=scope_defaut == "*",
                       help="Combined with a column-name pattern, the rule "
                            "spreads on its own to files that do not exist yet.")
    if tous:
        scope = "*"
    else:
        scope = st.text_input(
            "Files in scope", value="" if scope_defaut == "*" else scope_defaut,
            placeholder="sales_*  or  bis_turnover, inventory",
            help="File name without extension. The * character stands for any "
                 "sequence of characters. Separate with commas.")
        vus = list(st.session_state.get("profils_vus", {}))
        if vus:
            st.caption("Files seen during this session: "
                       + ", ".join(f"`{v}`" for v in vus))
        if scope.strip():
            couverts = [v for v in vus if scope_matches(scope, v)]
            if couverts:
                st.caption("✅ Covers: " + ", ".join(f"`{c}`" for c in couverts))

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
                "How should the target be designated?",
                [etiquettes[g] for g in groupe],
                index=groupe.index(defaut), horizontal=True,
                key=f"choix_{'_'.join(groupe)}")
            nom_param = next(g for g in groupe if etiquettes[g] == choisi)
        valeur = widget_param(nom_param, tpl, params_actuels.get(nom_param),
                              f"param_{nom_param}")
        if valeur not in (None, "", []):
            params[nom_param] = valeur

    if optionnels:
        with st.expander("Additional settings",
                         expanded=any(o in params_actuels for o in optionnels)):
            for nom_param in optionnels:
                if st.checkbox(libelle_param(tpl, nom_param),
                               value=nom_param in params_actuels, key=f"opt_{nom_param}"):
                    valeur = widget_param(nom_param, tpl,
                                          params_actuels.get(nom_param),
                                          f"param_{nom_param}")
                    if valeur not in (None, "", []):
                        params[nom_param] = valeur

    st.markdown("#### 3. What happens when the rule is breached?")
    c1, c2 = st.columns(2)
    with c1:
        severity = st.selectbox(
            "Severity", SEVERITES, format_func=libelle_severite,
            index=SEVERITES.index(control["severity"])
            if control.get("severity") in SEVERITES else 2)
        st.caption(SEVERITES_AIDE.get(severity, ""))

        seuil_actuel = float(control.get("seuil_tolerance_pct") or 0)
        etiquettes = list(TOLERANCES) + ["Custom threshold"]
        defaut_tol = next((i for i, k in enumerate(TOLERANCES)
                           if TOLERANCES[k] == seuil_actuel), len(etiquettes) - 1)
        choix_tol = st.selectbox("Tolerance before failure", etiquettes,
                                 index=defaut_tol,
                                 help="Share of breaching rows accepted before "
                                      "the control is declared failed.")
        seuil = (st.number_input("Custom threshold (%)", value=seuil_actuel,
                                 min_value=0.0, max_value=100.0, step=0.5)
                 if choix_tol == "Custom threshold" else TOLERANCES[choix_tol])
    with c2:
        owner = st.text_input("Who must act?", control.get("owner", "Data Steward"))
        frequency = st.selectbox(
            "How often should this run?", FREQUENCES,
            index=FREQUENCES.index(control["frequency"])
            if control.get("frequency") in FREQUENCES else 3)
    remediation = st.text_area(
        "What must that person do?", height=80,
        value=control.get("remediation_action", ""),
        placeholder="Fix the file at the source and request a fresh extract.")

    st.markdown("#### 4. Name the rule")
    phrase = phrase_controle({"template": tpl["template_id"],
                              "params": json.dumps(params, ensure_ascii=False)}, store)
    c3, c4 = st.columns(2)
    with c3:
        nom = st.text_input("Rule name", control.get("control_name", ""),
                            placeholder="Completeness of the notional amount")
    with c4:
        description = st.text_area(
            "Why this rule exists", height=68,
            value=control.get("description", ""),
            placeholder="A transaction without an amount is unusable downstream.")
    motif = st.text_input(
        "Reason for the change (kept in the change log)",
        placeholder="Requested by the data quality committee of 8 September")

    st.markdown("##### Summary")
    portee = "every file" if scope.strip() in ("", "*") else scope
    st.markdown(
        f"""<div style="background:{FOND_CARTE};border:1px solid {BORDURE};
        border-left:4px solid {ETAPES[0][4]};border-radius:10px;
        padding:16px 20px;font-size:16px;line-height:1.5;">{phrase}<br>
        <span style="font-size:13px;color:{TEXTE_SOURDINE};">On {portee} ·
        severity {libelle_severite(severity).lower()} · tolerance {seuil:g} % ·
        {owner} acts · runs {frequency.lower()}</span></div>""",
        unsafe_allow_html=True)
    with st.expander("Technical detail"):
        st.code(json.dumps({"template": tpl["template_id"], "params": params,
                            "dataset_scope": scope}, ensure_ascii=False, indent=2),
                language="json")

    candidat = {
        "rule_id": control.get("rule_id", store.next_rule_id()),
        "control_name": nom.strip(), "control_type": tpl["dimension"],
        "description": description.strip(), "template": tpl["template_id"],
        "params": json.dumps(params, ensure_ascii=False),
        "logic_definition": phrase, "dataset_scope": scope.strip() or "*",
        "data_element": ", ".join(
            str(v) for k, v in params.items()
            if k in PARAM_COLONNE | {"colonnes_motif"} or k.startswith("motif_")),
        "seuil_tolerance_pct": seuil, "severity": severity, "frequency": frequency,
        "owner": owner.strip(),
        "output_type": control.get("output_type", "Exception report"),
        "kpi": tpl.get("kpi_produit", ""), "remediation_action": remediation.strip(),
    }

    manques = []
    if not nom.strip():
        manques.append("the rule name")
    if not description.strip():
        manques.append("why the rule exists")
    if not remediation.strip():
        manques.append("the action to take when it breaches")

    erreurs = validate_control(candidat, store)

    if manques:
        st.warning("Still missing: " + ", ".join(manques) + ".")
    if erreurs:
        st.error("The validator refuses this rule:\n\n"
                 + "\n".join(f"- {e}" for e in erreurs))
    if not manques and not erreurs:
        st.success("Rule is valid. It will run on the files in its scope whose "
                   "columns match.")

    if st.button("Save the rule", type="primary",
                 disabled=bool(manques or erreurs)):
        if creation:
            cree = store.add_control(candidat, utilisateur,
                                     motif or "Created from the interface")
            enregistrer(store, f"Rule {cree['rule_id']} created.")
        else:
            store.update_control(control["rule_id"], candidat, utilisateur,
                                 motif or "Edited from the interface")
            enregistrer(store, f"Rule {control['rule_id']} updated "
                               f"(version {store.control(control['rule_id'])['version']}).")
        st.session_state.pop("edition", None)
        st.rerun()


def ecran_catalogue(store: CatalogueStore, utilisateur: str) -> None:
    bandeau_etape(0, "The central repository of every rule. None of them names a "
                     "column: they target naming conventions, and therefore "
                     "apply to files that do not exist yet.")
    afficher_flash()

    if "edition" in st.session_state:
        if st.button("← Back to the catalogue"):
            st.session_state.pop("edition")
            st.rerun()
        rid = st.session_state["edition"]
        editeur_regle(store, utilisateur, store.control(rid) if rid else None)
        return

    couverture = couverture_dimensions(store)
    couvertes = sum(1 for v in couverture.values() if v)
    st.markdown("##### Coverage of the six dimensions of the brief")
    tuiles([(DIMENSIONS[d], str(n), "#0ca30c" if n else "#d03b3b")
            for d, n in couverture.items()])
    if couvertes < len(DIMENSIONS):
        manquantes = [DIMENSIONS[d] for d, n in couverture.items() if not n]
        st.caption(f"⚠️ {len(manquantes)} dimension(s) with no active rule: "
                   + ", ".join(manquantes) + ". The **Execution** screen "
                   "proposes some automatically from any file.")

    st.divider()
    df = pd.DataFrame(store.controls)
    creer, agir = st.columns([1, 2])
    with creer:
        if st.button("➕ Create a rule", type="primary", width='stretch'):
            st.session_state["edition"] = None
            st.rerun()
        st.caption("Four steps, in plain language. You can load a file there to "
                   "pull in its columns.")
    with agir:
        rid = st.selectbox(
            "Act on an existing rule", [""] + list(df["rule_id"]),
            placeholder="Pick a rule…",
            format_func=lambda r: "" if not r
            else f"{r} · {store.control(r)['control_name']}")

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    f_dim = c1.multiselect("Dimension", list(DIMENSIONS), placeholder="All",
                           format_func=lambda d: DIMENSIONS[d])
    f_sev = c2.multiselect("Severity", SEVERITES, placeholder="All",
                           format_func=libelle_severite)
    f_statut = c3.multiselect("State", STATUTS, default=["Actif"],
                              placeholder="All", format_func=libelle_statut)
    f_texte = c4.text_input("Search", placeholder="a word from the name or the rule…")

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

    st.caption(f"{len(vue)} rule(s) shown out of {len(df)} — "
               f"{len(store.active_controls())} in service")
    if vue.empty:
        st.info("No rule matches these filters.")
    else:
        st.dataframe(pd.DataFrame({
            "": vue["statut"].map(PUCE_STATUT),
            "Rule": vue["rule_id"],
            "Name": vue["control_name"],
            "Dimension": vue["control_type"].map(lambda d: DIMENSIONS.get(d, d)),
            "What is checked": [phrase_controle(c, store)
                                for c in vue.to_dict("records")],
            "Applies to": vue["dataset_scope"].map(
                lambda s: "every file" if str(s).strip() in ("", "*") else s),
            "Severity": vue["severity"].map(libelle_severite),
            "Tolerance": vue["seuil_tolerance_pct"].map(lambda v: f"{float(v or 0):g} %"),
            "Owner": vue["owner"],
            "Version": vue["version"],
        }), width='stretch', hide_index=True)

        with st.expander("The 14 attributes required by the brief (appendix A.2)"):
            st.caption("Every rule carries the fourteen standardised attributes, "
                       "under the names used by the brief.")
            st.dataframe(vue[list(ATTRIBUTS_BRIEF)].rename(columns=ATTRIBUTS_BRIEF),
                         width='stretch', hide_index=True)
            st.download_button(
                "⬇️ Export the catalogue (CSV)",
                data=vue[list(ATTRIBUTS_BRIEF)].rename(columns=ATTRIBUTS_BRIEF)
                .to_csv(index=False).encode("utf-8-sig"),
                file_name="catalogue_extract.csv", mime="text/csv",
                help="The « Catalogue extract » expected by the supervisory "
                     "mapping (appendix C.2).")

    retirees = [c for c in store.controls if c["statut"] == "Deprecie"]
    if retirees:
        with st.expander(f"🧹 {len(retirees)} rule(s) retired from service"):
            st.caption(
                "These rules no longer run. They stay visible for the record — "
                "filter on the « Retired » state to read them. You can delete "
                "them in bulk: their definition goes to the change log, where "
                "it stays readable.")
            motif_purge = st.text_input(
                "Reason for the purge", key="motif_purge",
                placeholder="Rules inherited from a dataset no longer monitored")
            if st.button(f"Delete the {len(retirees)} retired rules",
                         disabled=not motif_purge.strip()):
                for c in retirees:
                    store.delete_control(c["rule_id"],
                                         utilisateur or "data.steward", motif_purge)
                enregistrer(store, f"{len(retirees)} retired rule(s) deleted. "
                                   f"Definitions kept in the change log.")
                st.rerun()

    if not rid:
        return
    st.divider()

    ctrl = store.control(rid)
    st.info(f"**{ctrl['control_name']}**  \n{phrase_controle(ctrl, store)}  \n"
            f"*{ctrl['description'] or 'No documented rationale.'}*")
    motif = st.text_input("Reason (kept in the change log)", key="motif_action")
    a1, a2, a3, a4 = st.columns(4)
    if a1.button("✏️ Edit", width='stretch'):
        st.session_state["edition"] = rid
        st.rerun()
    if a2.button("⏸️ Pause", width='stretch',
                 disabled=ctrl["statut"] == "Suspendu"):
        store.set_statut(rid, "Suspendu", utilisateur, motif or "Paused")
        enregistrer(store, f"{rid} paused: it will no longer run.")
        st.rerun()
    if a3.button("▶️ Put back in service", width='stretch',
                 disabled=ctrl["statut"] == "Actif"):
        store.set_statut(rid, "Actif", utilisateur, motif or "Back in service")
        enregistrer(store, f"{rid} back in service.")
        st.rerun()
    if a4.button("🗑️ Delete", width='stretch'):
        st.session_state["suppression"] = rid
        st.rerun()

    if st.session_state.get("suppression") == rid:
        st.warning(
            f"**Delete {rid} for good?** The rule leaves the catalogue. It stays "
            f"reconstructible: its full definition goes to the change log, and "
            f"the evidence packs already produced keep a complete copy — a past "
            f"run therefore remains explainable.")
        if not motif.strip():
            st.info("Fill in the **reason** above: it is required to delete, and "
                    "kept in the change log.")
        s1, s2, _ = st.columns([1, 1, 2])
        if s1.button("Confirm deletion", type="primary",
                     disabled=not motif.strip(), width='stretch'):
            store.delete_control(rid, utilisateur or "data.steward", motif)
            st.session_state.pop("suppression", None)
            enregistrer(store, f"{rid} deleted. Its definition stays in the log.")
            st.rerun()
        if s2.button("Cancel", width='stretch'):
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
            raison = f"unreadable rule: {exc}"
        if raison:
            hors.append((c, raison))
        else:
            applicables.append(c)
    return applicables, hors


def tableau_profil(profil: list[dict]) -> pd.DataFrame:
    return pd.DataFrame({
        "Column": [c["colonne"] for c in profil],
        "Inferred type": [c["type"] for c in profil],
        "Empty": [f"{c['taux_nuls_pct']:g} %" for c in profil],
        "Distinct values": [c["valeurs_distinctes"] for c in profil],
        "No duplicate": ["yes" if c["unique"] else "" for c in profil],
    })


def bloc_suggestions(store: CatalogueStore, utilisateur: str, nom: str,
                     propositions: list[dict], deja: set[str]) -> None:
    """Propose des controles deduits du fichier, chiffres, a accepter ou non."""
    restantes = [p for p in propositions if p["control_name"] not in deja]
    if not restantes:
        st.success("Every control derivable from this file is already in the "
                   "catalogue.")
        return

    utiles = [p for p in restantes if (p["impact"] or 0) > 0]
    st.markdown(f"##### {len(restantes)} control(s) derived from this file")
    st.caption(
        "These rules come from no business knowledge: they come out of the "
        "observed distribution — always-filled columns, marginal values, "
        "outlying bounds, date ordering. The brief explicitly allows this "
        "assistance (§14); **the verdict stays with the engine, the decision "
        "stays with you**."
        + (f"  \n**{len(utiles)} of them would already flag something on this "
           f"file**, and are pre-ticked." if utiles else ""))

    portee_choix = st.radio(
        "Scope of the accepted rules", ["every file", "this file"],
        horizontal=True,
        help="By default a rule holds for every file: where its columns do not "
             "exist, it is simply reported as skipped. Restricting it to this "
             "file is only useful to limit noise.")
    scope = "*" if portee_choix == "every file" else nom

    retenues: list[dict] = []
    par_dimension: dict[str, list[dict]] = {}
    for p in restantes:
        par_dimension.setdefault(p["dimension"], []).append(p)

    for dimension in DIMENSIONS:
        groupe = par_dimension.get(dimension, [])
        if not groupe:
            continue
        actifs = sum(1 for p in groupe if (p["impact"] or 0) > 0)
        titre = (f"{DIMENSIONS[dimension]} — {len(groupe)} proposal(s)"
                 + (f", of which {actifs} already breaching" if actifs else ""))
        with st.expander(titre, expanded=bool(actifs)):
            for p in groupe:
                impact = p["impact"] or 0
                marque = (f"🔴 **{nombre(impact)} row(s) breaching today**"
                          if impact else "🟢 no breach today")
                with st.container(border=True):
                    if st.checkbox(f"**{p['control_name']}**", value=impact > 0,
                                   key=f"sug_{nom}_{p['cle']}"):
                        retenues.append(p)
                    st.caption(f"{marque} · {p['constat']} · severity "
                               f"{libelle_severite(p['severity']).lower()}")
                    st.caption(p["description"])

    if st.button(f"➕ Add {len(retenues)} control(s) to the catalogue",
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
                refuses.append(f"{p['control_name']}: {erreurs[0]}")
                continue
            store.add_control(
                candidat, utilisateur or "data.steward",
                f"Derived from the profile of {nom} — {p['constat']}")
            ajoutes.append(candidat["rule_id"])
        message = (f"{len(ajoutes)} control(s) added to the catalogue "
                   f"({', '.join(ajoutes)}).") if ajoutes else "Nothing added."
        if refuses:
            message += " Refused by the validator: " + " ; ".join(refuses)
        enregistrer(store, message)
        st.rerun()


def ecran_execution(store: CatalogueStore, utilisateur: str) -> None:
    bandeau_etape(1, "Drop any file. Its structure is inferred as it is read, "
                     "the rules whose columns exist are applied, and the file "
                     "itself proposes the controls it is missing.")
    afficher_flash()

    onglet_depot, onglet_connu = st.tabs(["📎 Drop a file",
                                          "📂 Pick a file already present"])
    chemin: pathlib.Path | None = None

    with onglet_depot:
        depose = st.file_uploader("CSV or Excel file",
                                  type=["csv", "xlsx", "xls", "xlsm"])
        if depose is not None:
            ENTREES.mkdir(parents=True, exist_ok=True)
            chemin = ENTREES / depose.name
            chemin.write_bytes(depose.getbuffer())
            st.caption(f"Saved to `data/entrees/{depose.name}`")

    with onglet_connu:
        connus = fichiers_connus()
        if connus:
            choix = st.selectbox(
                "File", connus, index=None, placeholder="Pick a file…",
                format_func=lambda p: f"{p.name}  ({p.stat().st_size / 1e6:.1f} MB)")
            if choix is not None:
                chemin = choix
        else:
            st.info("No file in `data/entrees`, `data/prepared`, `data/ref` or "
                    "`data/exemples`.")

    if chemin is None:
        st.stop()

    try:
        with st.spinner("Reading and profiling the file…"):
            df, profil, propositions = lire_fichier(chemin)
    except (ValueError, FileNotFoundError) as exc:
        st.error(f"Cannot read the file: {exc}")
        st.stop()

    nom = chemin.stem
    applicables, hors = repartir_regles(store, profil, nom)
    st.divider()

    st.markdown(f"##### What the engine read in `{chemin.name}`")
    tuiles([
        ("Rows", nombre(len(df)), "#9AAAB8"),
        ("Columns", str(len(profil)), "#9AAAB8"),
        ("Applicable rules", str(len(applicables)),
         "#0ca30c" if applicables else "#d03b3b"),
        ("Skipped", str(len(hors)), "#898781"),
        ("Controls proposed", str(len(propositions)), TEINTE_MAGNITUDE),
    ])

    with st.expander(f"Inferred structure — {len(profil)} columns", expanded=False):
        st.dataframe(tableau_profil(profil), width='stretch', hide_index=True)
        cle = cle_candidate(profil)
        st.caption(
            f"Row identifier used in the exception reports: **{cle}**."
            if cle else
            "No fully unique column: exceptions will be located by their "
            "position in the file.")
        st.caption("Nobody keyed this structure in. It is inferred from the "
                   "content and written to the execution evidence.")
    with st.expander("Preview — first 50 rows"):
        st.dataframe(df.head(50), width='stretch', hide_index=True)

    onglet_regles, onglet_sug, onglet_hors = st.tabs(
        [f"Applicable rules ({len(applicables)})",
         f"Controls proposed by the file ({len(propositions)})",
         f"Skipped ({len(hors)})"])
    with onglet_hors:
        st.caption("These catalogue rules are active and their scope covers this "
                   "file, but the columns they target do not exist here. They "
                   "are neither failed nor in error: they are out of subject, "
                   "and the engine says so.")
        if hors:
            st.dataframe(pd.DataFrame({
                "Rule": [c["rule_id"] for c, _ in hors],
                "Name": [c["control_name"] for c, _ in hors],
                "Why it does not apply": [r for _, r in hors],
            }), width='stretch', hide_index=True)
        else:
            st.success("Every catalogue rule finds its columns in this file.")
    with onglet_regles:
        if applicables:
            st.dataframe(pd.DataFrame({
                "Rule": [c["rule_id"] for c in applicables],
                "Name": [c["control_name"] for c in applicables],
                "Dimension": [DIMENSIONS.get(c["control_type"], c["control_type"])
                              for c in applicables],
                "What is checked": [phrase_controle(c, store) for c in applicables],
                "Severity": [libelle_severite(c["severity"]) for c in applicables],
            }), width='stretch', hide_index=True)
        else:
            st.warning(
                f"**No catalogue rule finds its columns in `{nom}`.** That is "
                f"the normal case for a file never seen before: the existing "
                f"rules target other columns. The *Controls proposed* tab "
                f"derives {len(propositions)} of them from its content alone.")
    with onglet_sug:
        bloc_suggestions(store, utilisateur, nom, propositions,
                         {c["control_name"] for c in store.controls})

    st.divider()
    libelle = st.text_input("Run label (shown in the report)",
                            f"Control of {chemin.name}")
    if st.button("▶️ Run the controls", type="primary", width='stretch',
                 disabled=not applicables):
        with st.spinner("Running the controls…"):
            run = run_dq(chemin, store=store, run_label=libelle)
            classeur = build_workbook(run, store)
        st.session_state["dernier_run"] = run
        st.session_state["dernier_classeur"] = classeur
        st.rerun()

    run = st.session_state.get("dernier_run")
    if run is not None and pathlib.Path(run.fichier).stem == nom:
        st.divider()
        bandeau_resultat(run)
        bloc_ecarts(run, store)
        st.info("The full detail — scorecard, row-level exceptions, coverage "
                "and Excel workbook — is on screen **③ Reporting**.")


def bandeau_resultat(run) -> None:
    """Le verdict du run, en grand : feu tricolore et nombre d'echecs.

    Le statut global suit les regles du moteur (`dq_engine.statut_global`) :
    une erreur technique ou un echec bloquant met le run au rouge, un echec
    mineur ou un controle hors perimetre a l'orange, le reste au vert.
    """
    s = run.summary()
    echecs, erreurs = s["fail"], s["error"]
    sc = run.scorecard
    bloquants = int(((sc["statut"] == "FAIL") & (sc["severity"].isin(
        ["Critical", "High"]))).sum()) if not sc.empty else 0

    rag = s.get("rag", "GREEN")
    puce, couleur, fond, bordure = RAG_VUE.get(rag, RAG_VUE["GREEN"])

    if echecs or erreurs:
        titre = f"{echecs} control{'s' if echecs > 1 else ''} in breach"
        if erreurs:
            titre += f" · {erreurs} technical error{'s' if erreurs > 1 else ''}"
        sous = (f"of which {bloquants} rated Blocking or Major — "
                f"{nombre(s['exceptions'])} row(s) to review" if bloquants else
                f"{nombre(s['exceptions'])} row(s) to review")
    else:
        titre = "No control in breach"
        sous = (f"{s['pass']} control(s) passed"
                + (f" · {s['skipped']} skipped, hence AMBER"
                   if s["skipped"] else ""))

    st.markdown(
        f"""<div style="background:linear-gradient(135deg,{fond},{FOND_CREUX} 70%);
        border:1px solid {bordure};border-left:5px solid {couleur};
        border-radius:14px;padding:24px 28px;margin:6px 0 18px 0;">
        <div style="font-size:11px;letter-spacing:.16em;font-weight:700;
        color:{couleur};">{puce} OVERALL STATUS · {rag}</div>
        <div style="font-size:44px;font-weight:700;color:{couleur};
        line-height:1.05;letter-spacing:-.02em;margin-top:4px;">{titre}</div>
        <div style="font-size:15px;color:{TEXTE_SOURDINE};margin-top:8px;">
        {sous}</div></div>""",
        unsafe_allow_html=True)


# Teinte par gravite. Elle n'est jamais seule a porter le sens : le libelle
# metier - Blocking, Major, Moderate, Minor - l'accompagne partout.
TEINTE_GRAVITE = {"Critical": "#FF5C5C", "High": "#FF9A52",
                  "Medium": "#F2C14E", "Low": "#8FA3B8"}


def bloc_ecarts(run, store: CatalogueStore | None = None) -> None:
    """Un bloc depliable par controle en ecart, du plus grave au plus volumineux."""
    sc = run.scorecard
    if sc.empty:
        return
    incidents = sc[sc["statut"].isin(["FAIL", "ERROR"])].copy()
    if incidents.empty:
        return
    rang = {s: i for i, s in enumerate(SEVERITES)}
    incidents["_rang"] = incidents["severity"].map(lambda g: rang.get(g, 99))
    incidents = incidents.sort_values(["_rang", "lignes_ko"],
                                      ascending=[True, False])

    st.markdown(f"##### {len(incidents)} control(s) to review")
    st.caption("Most severe first, then by volume. Each line unfolds on its "
               "detail: what was checked, where, on how many rows, and the "
               "action expected.")
    for _, r in incidents.iterrows():
        with st.expander(entete_ecart(r), expanded=False):
            detail_ecart(run, r, store)


def entete_ecart(r) -> str:
    """Le titre replie doit suffire a decider si on ouvre : quoi, combien, gravite."""
    if r["statut"] == "ERROR":
        return (f"🟠  {r['rule_id']} · {r['control_name']} — technical error, "
                f"no verdict issued")
    cible = "" if str(r["cible"]) in ("-", "", "nan") else f" · {r['cible']}"
    return (f"🔴  {r['rule_id']} · {r['control_name']}{cible} — "
            f"{nombre(r['lignes_ko'])} row(s) in breach out of "
            f"{nombre(r['lignes_testees'])} · {libelle_severite(r['severity'])}")


def detail_ecart(run, r, store: CatalogueStore | None) -> None:
    """Le contenu d'un bloc deplie : le fait, le contexte, puis l'action."""
    if r["statut"] == "ERROR":
        st.warning("The rule could not run on this file. This is neither a "
                   "failure nor a pass: no verdict is issued, and the technical "
                   "message below goes to the execution log.")
        st.code(r["message"] or "—", language="text")
    else:
        taux = float(r["taux_ko_pct"] or 0)
        seuil = float(r["seuil_pct"] or 0)
        fiche([
            ("Dimension", DIMENSIONS.get(r["dimension"], r["dimension"])),
            ("Column tested",
             f"<code style='font-size:13px;'>{r['cible']}</code>"),
            ("Rows in breach", f"{nombre(r['lignes_ko'])} of "
                               f"{nombre(r['lignes_testees'])}"),
            ("Share of rows", f"{taux:g} % · tolerated {seuil:g} %"),
            ("KPI", f"{r['kpi_nom']}: {r['kpi_valeur']}"),
            ("Severity", libelle_severite(r["severity"])),
        ])

    controle = store.control(r["rule_id"]) if store is not None else None
    if controle:
        st.markdown(f"**What was checked** — {phrase_controle(controle, store)}")
        if controle.get("description"):
            st.caption(controle["description"])

    couleur = TEINTE_GRAVITE.get(r["severity"], "#8FA3B8")
    action = r["remediation_action"] or "No remediation action defined."
    st.markdown(
        f"""<div style="background:{FOND_CREUX};border:1px solid {BORDURE};
        border-left:3px solid {couleur};border-radius:8px;padding:12px 16px;
        margin:6px 0 4px 0;">
        <span style="font-size:10.5px;letter-spacing:.1em;font-weight:700;
        color:{couleur};">ACTION EXPECTED</span>
        <div style="font-size:14.5px;margin-top:4px;">{action}</div>
        <div style="font-size:12.5px;color:{TEXTE_SOURDINE};margin-top:6px;">
        Owner: {r['owner'] or '—'} · control frequency:
        {str(r['frequency'] or '—').lower()} · rule version {r['version']}</div>
        </div>""",
        unsafe_allow_html=True)

    apercu = lignes_en_ecart(run, r)
    if apercu is not None:
        st.caption("The first rows concerned — the full report is in the "
                   "**Row-level exceptions** tab.")
        st.dataframe(apercu, width='stretch', hide_index=True)


def lignes_en_ecart(run, r, limite: int = 10) -> pd.DataFrame | None:
    """Les premieres exceptions de ce controle, ou None s'il n'y en a pas.

    Le rapport d'exceptions nomme sa colonne comme l'executeur l'a ecrite ; le
    tableau de bord nomme la sienne comme la cible resolue. Les deux coincident
    le plus souvent, jamais toujours : on ne filtre donc sur la colonne que si
    ce filtre laisse quelque chose.
    """
    exceptions = run.exceptions
    if exceptions.empty:
        return None
    vue = exceptions[exceptions["rule_id"] == r["rule_id"]]
    if vue.empty:
        return None
    if "colonne" in vue.columns:
        ciblees = vue[vue["colonne"].astype(str) == str(r["cible"])]
        if not ciblees.empty:
            vue = ciblees
    colonnes = [c for c in ("identifiant_ligne", "colonne", "valeur", "motif")
                if c in vue.columns]
    apercu = vue[colonnes].head(limite).copy()
    if "valeur" in apercu.columns:
        # Une valeur absente s'ecrit « (empty) » : `nan` est un mot de Python,
        # pas une explication.
        apercu["valeur"] = apercu["valeur"].astype(str).replace(
            {"nan": "(empty)", "None": "(empty)", "": "(empty)", "NaT": "(empty)"})
    return apercu.rename(columns={
        "identifiant_ligne": "Row", "colonne": "Column",
        "valeur": "Value read", "motif": "Why it breaches"})


# --------------------------------------------------------------------------- #
# ③ Reporting - REPORTS
# --------------------------------------------------------------------------- #
def graphe_statuts_par_dimension(sc: pd.DataFrame):
    """Part-a-tout par dimension : ou le controle tient, ou il lache."""
    lignes = []
    for rang, (code, nom, puce, _) in enumerate(STATUTS_VUE):
        for dimension, libelle in DIMENSIONS.items():
            n = int(((sc["dimension"] == dimension) & (sc["statut"] == code)).sum())
            if n:
                lignes.append({"Dimension": libelle, "Status": f"{puce} {nom}",
                               "Controls": n, "ordre": rang})
    if not lignes:
        return None
    domaine = [f"{puce} {nom}" for _, nom, puce, _ in STATUTS_VUE]
    couleurs = [teinte for _, _, _, teinte in STATUTS_VUE]
    dims_presentes = [d for d in DIMENSIONS.values()
                      if d in set(x["Dimension"] for x in lignes)]
    nb_dim = len(dims_presentes)
    hauteur = max(240, 38 * nb_dim + 50)
    return alt.Chart(pd.DataFrame(lignes)).mark_bar(size=18, cornerRadiusEnd=3).encode(
        y=alt.Y("Dimension:N", title=None, sort=dims_presentes,
                axis=alt.Axis(labelLimit=160, labelFontSize=12)),
        x=alt.X("Controls:Q", title="Controls executed",
                axis=alt.Axis(tickMinStep=1)),
        color=alt.Color("Status:N",
                        scale=alt.Scale(domain=domaine, range=couleurs),
                        legend=alt.Legend(title=None, orient="bottom", columns=2,
                                          labelFontSize=11)),
        order=alt.Order("ordre:Q", sort="ascending"),
        tooltip=["Dimension", "Status", "Controls"],
    ).properties(height=hauteur)


def graphe_ecarts_par_regle(sc: pd.DataFrame):
    """Magnitude : quelles regles remontent le plus de lignes a instruire."""
    ko = sc[sc["lignes_ko"] > 0].nlargest(8, "lignes_ko").copy()
    if ko.empty:
        return None
    ko["Rule"] = ko["rule_id"] + " · " + ko["control_name"].str.slice(0, 30)
    ko["Rows"] = ko["lignes_ko"]
    base = alt.Chart(ko).encode(
        y=alt.Y("Rule:N", sort="-x", title=None,
                axis=alt.Axis(labelLimit=160, labelFontSize=12)),
        x=alt.X("Rows:Q", title="Rows in breach"))
    barres = base.mark_bar(size=18, color=TEINTE_MAGNITUDE, cornerRadiusEnd=3)
    etiquettes = base.mark_text(align="left", dx=6, fontSize=12).encode(
        text=alt.Text("Rows:Q", format=","))
    hauteur = max(240, 38 * len(ko) + 50)
    return (barres + etiquettes).properties(height=hauteur)


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
            "Run": manifeste.get("horodatage", "")[:16].replace("T", " "),
            "Controls in breach": sum(1 for r in resultats if r["statut"] == "FAIL"),
            "Rows in breach": sum(int(r.get("lignes_ko", 0)) for r in resultats),
            "Status": manifeste.get("statut_global", ""),
            "Run ID": pack.name,
        })
    return pd.DataFrame(points)


def graphe_historique(points: pd.DataFrame):
    """Evolution : une seule serie, donc aucune legende - le titre la nomme."""
    base = alt.Chart(points).encode(
        x=alt.X("Run:N", title=None, axis=alt.Axis(labelAngle=-30)),
        y=alt.Y("Controls in breach:Q", title="Controls in breach",
                axis=alt.Axis(tickMinStep=1)),
        tooltip=["Run", "Controls in breach", "Rows in breach", "Status", "Run ID"])
    return (base.mark_line(color=TEINTE_MAGNITUDE, strokeWidth=2)
            + base.mark_point(color=TEINTE_MAGNITUDE, size=90, filled=True)
            ).properties(height=260)


def ecran_restitution(store: CatalogueStore) -> None:
    bandeau_etape(2, "Traffic-light scorecard, row-level exception report, "
                     "control coverage view.")

    run = st.session_state.get("dernier_run")
    if run is None:
        st.info("No control has been run in this session yet. Go to screen "
                "**② Execution**.")
        return

    sc = run.scorecard.copy()
    s_res = run.summary()
    nom_fichier = pathlib.Path(run.fichier).stem
    executes = s_res["pass"] + s_res["fail"] + s_res["error"]
    conformite = (100.0 * s_res["pass"] / executes) if executes else 0.0

    st.caption(f"**{pathlib.Path(run.fichier).name}** · run `{run.run_id}` · "
               f"{run.horodatage.replace('T', ' ')}")
    bandeau_resultat(run)

    # Les chiffres du run viennent AVANT le detail des ecarts : on lit d'abord
    # la mesure, ensuite les cas a instruire.
    st.markdown("##### The run in six figures")
    tuiles([
        ("Controls executed", str(s_res["controls"]), "#9AAAB8"),
        ("Passed", str(s_res["pass"]), "#0ca30c"),
        ("In breach", str(s_res["fail"]),
         "#d03b3b" if s_res["fail"] else "#0ca30c"),
        ("Skipped", str(s_res["skipped"]), "#898781"),
        ("Compliance", f"{conformite:.0f} %",
         "#0ca30c" if conformite == 100 else TEINTE_MAGNITUDE),
        ("Exception rows", nombre(s_res["exceptions"]),
         "#d03b3b" if s_res["exceptions"] else "#0ca30c"),
    ])

    classeur = st.session_state.get("dernier_classeur")
    if classeur and pathlib.Path(classeur).exists():
        st.download_button(
            "⬇️ Download the Excel report (6 sheets)", type="primary",
            data=pathlib.Path(classeur).read_bytes(),
            file_name=pathlib.Path(classeur).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # --- les deux graphiques ------------------------------------------------
    st.divider()
    g, d = st.columns(2)
    with g:
        st.markdown("##### Control status, by dimension")
        graphe = graphe_statuts_par_dimension(sc)
        if graphe is None:
            st.info("No control executed.")
        else:
            st.altair_chart(graphe, width='stretch')
            st.caption("The six dimensions of the brief. A missing dimension "
                       "has no rule applicable to this file.")
    with d:
        st.markdown("##### Rows in breach, by rule")
        graphe = graphe_ecarts_par_regle(sc)
        if graphe is None:
            st.success("No row in breach on this run.")
        else:
            st.altair_chart(graphe, width='stretch')
            st.caption("The eight rules raising the most rows to review.")

    # --- detail des ecarts a instruire -------------------------------------
    st.divider()
    bloc_ecarts(run, store)

    st.divider()
    t1, t2, t3, t4, t5 = st.tabs([
        "Control detail", "Row-level exceptions", "Control coverage",
        "File history", "Rejected rules"])

    with t1:
        horsp = int((sc["statut"] == "SKIPPED").sum())
        vue = sc
        if horsp and not st.checkbox(
                f"Also show the {horsp} skipped rule(s)",
                help="Active rules whose columns do not exist in this file. The "
                     "detail is in the Coverage tab."):
            vue = sc[sc["statut"] != "SKIPPED"]
        tableau = pd.DataFrame({
            "": vue["statut"].map(PUCE_RUN),
            "Rule": vue["rule_id"],
            "Control": vue["control_name"],
            "Dimension": vue["dimension"].map(lambda x: DIMENSIONS.get(x, x)),
            "What is checked": [
                phrase_controle(store.control(r) or {}, store) for r in vue["rule_id"]],
            "Column tested": vue["cible"],
            "Severity": vue["severity"].map(libelle_severite),
            "Rows in breach": vue["lignes_ko"],
            "Rows tested": vue["lignes_testees"],
            "KPI": vue["kpi_nom"] + ": " + vue["kpi_valeur"].astype(str),
            "Owner": vue["owner"],
            "Explanation": vue["message"],
        })
        st.dataframe(tableau, width='stretch', hide_index=True)
        st.caption("🟢 passed · 🔴 in breach · 🟠 technical error · "
                   "⚪ skipped (reason in the explanation)")
        st.download_button(
            "⬇️ Export the scorecard (CSV)",
            data=tableau.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"scorecard_{run.run_id}.csv", mime="text/csv")

    with t2:
        exceptions = run.exceptions
        if exceptions.empty:
            st.success("No exception row on this run.")
        else:
            f1, f2 = st.columns(2)
            regles = ["(all)"] + sorted(exceptions["rule_id"].unique())
            choix = f1.selectbox("Rule", regles)
            gravites = ["(all)"] + [g for g in SEVERITES
                                    if g in set(exceptions["severity"])]
            grav = f2.selectbox("Severity", gravites,
                                format_func=lambda g: g if g == "(all)"
                                else libelle_severite(g))
            vue = exceptions
            if choix != "(all)":
                vue = vue[vue["rule_id"] == choix]
            if grav != "(all)":
                vue = vue[vue["severity"] == grav]
            st.dataframe(vue.head(2000), width='stretch', hide_index=True)
            st.caption(f"{nombre(len(vue))} exception(s) — first 2 000 shown. "
                       f"The evidence pack keeps them all, untruncated.")
            st.download_button(
                "⬇️ Export these exceptions (CSV)",
                data=vue.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"exceptions_{run.run_id}.csv", mime="text/csv")

    with t3:
        executees = set(sc["dimension"]) if not sc.empty else set()
        st.dataframe(pd.DataFrame([
            {"Dimension": libelle,
             "Controls executed": int((sc["dimension"] == d).sum())
             if not sc.empty else 0,
             "Covered on this file": "yes" if d in executees else "no"}
            for d, libelle in DIMENSIONS.items()]),
            width='stretch', hide_index=True)

        vues = set(sc["rule_id"]) if not sc.empty else set()
        hors = [c for c in store.active_controls() if c["rule_id"] not in vues]
        if hors:
            st.markdown("**Active rules out of scope for this file**")
            st.dataframe(pd.DataFrame({
                "Rule": [c["rule_id"] for c in hors],
                "Name": [c["control_name"] for c in hors],
                "Scope": [c["dataset_scope"] for c in hors],
            }), width='stretch', hide_index=True)
        na = sc[sc["statut"] == "SKIPPED"] if not sc.empty else pd.DataFrame()
        if not na.empty:
            st.markdown("**Rules in scope but not applicable**")
            st.dataframe(pd.DataFrame({
                "Rule": na["rule_id"], "Name": na["control_name"],
                "Reason": na["message"],
            }), width='stretch', hide_index=True)

    with t4:
        points = historique_du_fichier(nom_fichier)
        if len(points) < 2:
            st.info(f"Only one control run of `{nom_fichier}` has left evidence. "
                    f"The history builds up from one run to the next — run the "
                    f"controls again to see the curve.")
            if not points.empty:
                st.dataframe(points, width='stretch', hide_index=True)
        else:
            st.altair_chart(graphe_historique(points), width='stretch')
            st.caption(f"Read from the evidence packs of `{nom_fichier}`. No "
                       f"database is kept on the side: the audit layer carries "
                       f"the history.")
            st.dataframe(points, width='stretch', hide_index=True)
            st.download_button(
                "⬇️ Export the history (CSV)",
                data=points.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"history_{nom_fichier}.csv", mime="text/csv")

    with t5:
        rejets = pd.DataFrame(run.rejets)
        if rejets.empty:
            st.success("No rule rejected: every definition is sound.")
        else:
            st.dataframe(rejets.rename(columns={
                "rule_id": "Rule", "control_name": "Name",
                "dataset": "File", "motif": "Why it was rejected"}),
                width='stretch', hide_index=True)
        st.caption("Rules discarded by the validator before execution: a "
                   "malformed rule never makes a control fail.")


# --------------------------------------------------------------------------- #
# ④ Audit trail - EVIDENCES
# --------------------------------------------------------------------------- #
def packs_de_preuve() -> list[pathlib.Path]:
    if not EVIDENCE_DIR.exists():
        return []
    return sorted((p for p in EVIDENCE_DIR.iterdir()
                   if p.is_dir() and (p / "manifest.json").exists()),
                  key=lambda p: p.name, reverse=True)


def lire_piece(pack: pathlib.Path, nom: str, defaut=None):
    """Lit une piece JSON du pack sans jamais lever : un pack peut etre partiel."""
    try:
        return json.loads((pack / nom).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaut


def zip_du_pack(pack: pathlib.Path) -> bytes:
    """Assemble le pack complet en un ZIP telechargeable."""
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for fichier in sorted(pack.iterdir()):
            if fichier.is_file():
                archive.write(fichier, arcname=f"{pack.name}/{fichier.name}")
    return tampon.getvalue()


def badge_rag(statut: str) -> str:
    puce, couleur, fond, bordure = RAG_VUE.get(statut, RAG_VUE["GREEN"])
    return (f"""<span style="background:{fond};border:1px solid {bordure};
            color:{couleur};border-radius:999px;padding:4px 12px;font-weight:700;
            font-size:12px;letter-spacing:.1em;">{puce} {statut}</span>""")


def onglet_pack(pack: pathlib.Path, utilisateur: str) -> None:
    """Tout ce qu'un auditeur doit pouvoir faire sur un run, sans le moteur."""
    manifeste = lire_piece(pack, "manifest.json", {}) or {}
    resume = lire_piece(pack, "summary.json", {}) or {}
    totaux = resume.get("totaux", {})

    entete, telecharger = st.columns([3, 1])
    with entete:
        st.markdown(
            f"""<div style="display:flex;align-items:center;gap:14px;
            flex-wrap:wrap;margin-bottom:6px;">
            {badge_rag(manifeste.get('statut_global', 'GREEN'))}
            <span style="font-size:20px;font-weight:700;">
            {manifeste.get('fichier_nom', '—')}</span>
            <span style="color:{TEXTE_SOURDINE};font-size:13px;">
            run <code>{manifeste.get('run_id', pack.name)}</code> ·
            {str(manifeste.get('horodatage', '')).replace('T', ' ')} ·
            as-of {manifeste.get('as_of', '—')} ·
            engine {manifeste.get('moteur_version', '—')}</span></div>""",
            unsafe_allow_html=True)
    with telecharger:
        st.download_button("⬇️ Evidence pack (ZIP)", data=zip_du_pack(pack),
                           file_name=f"{pack.name}.zip", mime="application/zip",
                           width='stretch', type="primary",
                           key=f"zip_{pack.name}")

    tuiles([
        ("Controls", str(totaux.get("controls", "—")), "#9AAAB8"),
        ("In breach", str(totaux.get("fail", "—")),
         "#d03b3b" if totaux.get("fail") else "#0ca30c"),
        ("Errors", str(totaux.get("error", "—")),
         "#fab219" if totaux.get("error") else "#0ca30c"),
        ("Skipped", str(totaux.get("skipped", "—")), "#898781"),
        ("Exception rows", nombre(totaux.get("exceptions", 0)),
         "#d03b3b" if totaux.get("exceptions") else "#0ca30c"),
        ("Rules frozen", str(manifeste.get("regles_executees", "—")),
         TEINTE_MAGNITUDE),
    ])

    o_pieces, o_verif, o_rejeu, o_trace, o_signoff = st.tabs(
        ["Pack contents", "Integrity check", "Replay", "Traceability",
         "Sign-off"])

    # --- 1. Les pieces exigees par l'annexe B.2 ----------------------------
    with o_pieces:
        g, d = st.columns([3, 2])
        with g:
            st.markdown("##### The ten components required by the brief (B.2)")
            st.dataframe(pd.DataFrame([
                {"": "🟢" if (pack / fichier).exists() else "⚪",
                 "Required component": piece, "File": fichier,
                 "Content": contenu}
                for piece, fichier, contenu in PIECES_AUDIT]),
                width='stretch', hide_index=True)
            st.caption("⚪ *Sign-off Fields* is marked optional by the brief; it "
                       "is filled in the Sign-off tab.")
            st.markdown("##### Additional pieces, for independent verification")
            st.dataframe(pd.DataFrame([
                {"": "🟢" if (pack / fichier).exists() else "⚪",
                 "Piece": piece, "File": fichier, "Content": contenu}
                for piece, fichier, contenu in PIECES_COMPLEMENT]),
                width='stretch', hide_index=True)
        with d:
            st.markdown("##### Input dataset references")
            sources = manifeste.get("sources", {})
            st.dataframe(pd.DataFrame([
                {"Source": k, "SHA-256": str(v.get("sha256", ""))[:16] + "…",
                 "Rows": v.get("lignes", "")}
                for k, v in sources.items()]), width='stretch', hide_index=True)
            st.metric("Catalogue fingerprint",
                      str(manifeste.get("catalogue_sha256", ""))[:16] + "…")
            st.metric("Executed rules fingerprint",
                      str(manifeste.get("regles_executees_sha256", ""))[:16] + "…")
            st.caption("Exceptions complete: "
                       + ("yes, nothing truncated"
                          if manifeste.get("exceptions_completes", True)
                          else "**no** — "
                               + ", ".join(manifeste.get(
                                   "exceptions_tronquees_pour", []))))

        with st.expander("Full manifest"):
            st.json(manifeste)
        if manifeste.get("profil_fichier"):
            with st.expander("Structure inferred at run time"):
                st.dataframe(tableau_profil(manifeste.get("profil_fichier", [])),
                             width='stretch', hide_index=True)
        with st.expander("Execution log"):
            try:
                st.code((pack / "execution.log").read_text(encoding="utf-8"),
                        language="text")
            except OSError:
                st.info("No log in this pack.")

    # --- 2. Verification d'integrite --------------------------------------
    with o_verif:
        st.caption("Seven checks an auditor would run: are the pieces there, "
                   "have they been altered since, is the controlled file still "
                   "the one that was read, do the frozen rules match the ones "
                   "that ran, and do the results agree with the exceptions "
                   "delivered.")
        if st.button("🔍 Verify this evidence pack", type="primary",
                     key=f"verif_{pack.name}"):
            with st.spinner("Recomputing the fingerprints…"):
                st.session_state[f"rapport_verif_{pack.name}"] = verifier_pack(pack)
        rapport = st.session_state.get(f"rapport_verif_{pack.name}")
        if rapport:
            conforme = rapport["verdict"] == "VERIFIED"
            (st.success if conforme else st.error)(
                "**Pack verified — no discrepancy.** The run can be "
                "reconstructed from these files alone." if conforme else
                "**Discrepancies found.** See the failed checks below.")
            st.dataframe(pd.DataFrame([
                {"": {"OK": "🟢", "GAP": "🔴"}.get(c["statut"], "⚪"),
                 "Check": c["controle"], "Result": c["statut"],
                 "Detail": c["detail"]}
                for c in rapport["controles"]]),
                width='stretch', hide_index=True)
            st.download_button(
                "⬇️ Export the verification report (JSON)",
                data=json.dumps(rapport, ensure_ascii=False, indent=2)
                .encode("utf-8"),
                file_name=f"verification_{pack.name}.json",
                mime="application/json", key=f"dlverif_{pack.name}")

    # --- 3. Rejeu ----------------------------------------------------------
    with o_rejeu:
        st.caption("Reproducibility is the point of the whole layer: the same "
                   "file, the same frozen rules and the same as-of date must "
                   "yield the same verdicts, the same volumes and the same "
                   "KPIs. The replay uses the catalogue snapshot of this pack, "
                   "never today's catalogue, and writes no new evidence.")
        if st.button("♻️ Replay this run and compare", type="primary",
                     key=f"rejeu_{pack.name}"):
            with st.spinner("Replaying the run…"):
                st.session_state[f"rapport_rejeu_{pack.name}"] = rejouer_pack(pack)
        rapport = st.session_state.get(f"rapport_rejeu_{pack.name}")
        if rapport:
            if not rapport.get("rejouable"):
                st.warning(f"Replay impossible: {rapport.get('motif')}")
            elif rapport["identique"]:
                st.success(
                    f"**Identical results.** {rapport['controles_compares']} "
                    f"control(s) compared on status, rows tested, rows in "
                    f"breach, breach rate and KPI — no difference. "
                    f"Overall status {rapport['statut_global_rejeu']} in both "
                    f"runs, {nombre(rapport['exceptions_rejeu'])} exception "
                    f"row(s) in both.")
            else:
                st.error(f"**{len(rapport['differences'])} difference(s)** "
                         f"between the original run and the replay.")
                st.dataframe(pd.DataFrame(rapport["differences"]).rename(columns={
                    "controle": "Control", "champ": "Field",
                    "origine": "Original run", "rejeu": "Replay"}),
                    width='stretch', hide_index=True)

    # --- 4. Tracabilite donnee -> regle -> execution -> resultat -----------
    with o_trace:
        regles = lire_piece(pack, "executed_rules.json", []) or []
        resultats = lire_piece(pack, "results.json", []) or []
        if not regles or not resultats:
            st.info("This pack carries no frozen rules (produced by an older "
                    "engine version).")
        else:
            par_regle = {r["rule_id"]: r for r in regles}
            exceptions_par_regle: dict[str, int] = {}
            for r in resultats:
                exceptions_par_regle[r["rule_id"]] = (
                    exceptions_par_regle.get(r["rule_id"], 0)
                    + int(r.get("lignes_ko", 0)))
            trace = pd.DataFrame([{
                "": PUCE_RUN.get(r["statut"], "⚪"),
                "Dataset": manifeste.get("fichier_nom", ""),
                "Rule": r["rule_id"],
                "Version": r.get("version", ""),
                "Control": r.get("control_name", ""),
                "Parameters applied": par_regle.get(r["rule_id"], {}).get("params", ""),
                "Threshold %": par_regle.get(r["rule_id"], {})
                .get("seuil_tolerance_pct", ""),
                "Severity": libelle_severite(r.get("severity", "")),
                "Target": r.get("cible", ""),
                "Status": r["statut"],
                "KPI": f"{r.get('kpi_nom', '')}: {r.get('kpi_valeur', '')}",
                "Rows in breach": r.get("lignes_ko", 0),
                "Explanation": r.get("message", ""),
                "Remediation": par_regle.get(r["rule_id"], {})
                .get("remediation_action", ""),
                "Rule SHA-256": str(par_regle.get(r["rule_id"], {})
                                    .get("rule_sha256", ""))[:16] + "…",
            } for r in resultats])
            st.dataframe(trace, width='stretch', hide_index=True)
            st.caption("One line per executed control: which dataset, which rule "
                       "in which version, with which parameters and threshold, "
                       "what came out, and who must act.")
            st.download_button(
                "⬇️ Export the traceability view (CSV)",
                data=trace.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"traceability_{pack.name}.csv", mime="text/csv",
                key=f"dltrace_{pack.name}")

            rejetees = lire_piece(pack, "rejected_rules.json",
                                  lire_piece(pack, "rejets.json", [])) or []
            if rejetees:
                st.markdown("**Rules rejected by the validator before execution**")
                st.dataframe(pd.DataFrame(rejetees), width='stretch',
                             hide_index=True)

    # --- 5. Sign-off -------------------------------------------------------
    with o_signoff:
        st.caption("The optional « Sign-off Fields » of appendix B.2: a human "
                   "states that the results were reviewed. The signature is "
                   "written into the pack itself.")
        signoff = pack / "signoff.json"
        if signoff.exists():
            st.success("This run is signed off.")
            st.json(lire_piece(pack, "signoff.json", {}))
        else:
            commentaire = st.text_input(
                "Sign-off comment", key=f"signoff_{pack.name}",
                placeholder="Results reviewed, DQ26 breaches taken in charge.")
            if st.button("✅ Sign off this run", type="primary",
                         key=f"btn_signoff_{pack.name}"):
                signoff.write_text(json.dumps({
                    "run_id": manifeste.get("run_id"),
                    "valide_par": utilisateur or "data.steward",
                    "horodatage": dt.datetime.now().isoformat(timespec="seconds"),
                    "commentaire": commentaire,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                st.session_state["flash"] = f"Run {pack.name} signed off."
                st.rerun()


def ecran_audit(store: CatalogueStore, utilisateur: str) -> None:
    bandeau_etape(3, "Every execution leaves a reconstructible trace: run ID, "
                     "source fingerprints, the exact rule configuration, "
                     "results, exceptions and logs — verifiable and replayable "
                     "by a third party.")
    afficher_flash()

    onglet_packs, onglet_journal = st.tabs(
        ["Evidence packs", "Catalogue change log"])

    with onglet_packs:
        packs = packs_de_preuve()
        if not packs:
            st.info("No execution has produced evidence yet.")
        else:
            st.caption(f"{len(packs)} evidence pack(s) on disk, under "
                       f"`evidence/`.")
            choix = st.selectbox(
                "Run", packs, format_func=lambda p: (
                    f"{p.name} · "
                    f"{(lire_piece(p, 'manifest.json', {}) or {}).get('fichier_nom', '')}"
                    f" · {(lire_piece(p, 'manifest.json', {}) or {}).get('statut_global', '')}"
                ))
            onglet_pack(choix, utilisateur)

    with onglet_journal:
        st.caption("Who changed what, when and why. Nothing can be erased: a "
                   "rule is paused, never silently dropped.")
        df = pd.DataFrame(store.changelog)
        if df.empty:
            st.info("Empty change log.")
        else:
            st.dataframe(pd.DataFrame({
                "Date": df["timestamp"], "Author": df["utilisateur"],
                "Action": df["action"], "Rule": df["rule_id"],
                "Field": df["champ"], "Before": df["avant"], "After": df["apres"],
                "Reason": df["motif"],
            }).iloc[::-1], width='stretch', hide_index=True)


# --------------------------------------------------------------------------- #
def main() -> None:
    store = get_store()
    st.markdown(STYLE, unsafe_allow_html=True)
    with st.sidebar:
        marque, titre = st.columns([1, 2.6], vertical_alignment="center")
        if LOGO.exists():
            marque.image(str(LOGO))
        else:
            marque.markdown("<div style='font-size:38px;'>🧭</div>",
                            unsafe_allow_html=True)
        titre.markdown(
            "<div style='font-size:27px;font-weight:800;letter-spacing:-.02em;"
            "line-height:1.1;'>DQ&nbsp;Compass</div>", unsafe_allow_html=True)
        st.caption("Generic, catalogue-driven data quality control layer")
        utilisateur = st.text_input("Your name", "data.steward",
                                    help="Identifies the author in the change log.")
        page = st.radio("Control lifecycle", PAGES,
                        captions=[f"{verbe.capitalize()} · {composant}"
                                  for _, _, composant, verbe, _ in ETAPES])
        st.divider()
        actives = store.active_controls()
        couverture = couverture_dimensions(store)
        st.metric("Rules in service", len(actives))
        st.metric("Dimensions covered",
                  f"{sum(1 for v in couverture.values() if v)} / {len(DIMENSIONS)}")
        universelles = [c for c in actives
                        if str(c.get("dataset_scope", "")).strip() in ("", "*")]
        st.metric("Universal rules", len(universelles),
                  help="Rules that apply to any file, including files that do "
                       "not exist yet.")
        if st.button("🔄 Reload the catalogue"):
            get_store(force=True)
            st.rerun()
        st.caption(f"{dt.date.today():%Y-%m-%d}")

    if page.startswith("①"):
        ecran_catalogue(store, utilisateur)
    elif page.startswith("②"):
        ecran_execution(store, utilisateur)
    elif page.startswith("③"):
        ecran_restitution(store)
    else:
        ecran_audit(store, utilisateur)


main()
