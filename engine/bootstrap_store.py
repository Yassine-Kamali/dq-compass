"""Bootstrap catalogue/store.json (templates, dataset contracts, controls)."""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from store import CatalogueStore, STORE_PATH  # noqa: E402

USER = "bootstrap"

TEMPLATES = [
    ("NOT_NULL", "Completeness", "column | role", "",
     "Aucune valeur nulle ou vide sur la colonne ciblee",
     "% de completude", '{"column": "turnover_notionnel"}'),
    ("MATCHES_REGEX", "Validity", "column, pattern", "ignorer_nulls",
     "La valeur respecte une expression reguliere",
     "% de valeurs valides", '{"column": "DER_CURR_LEG1", "pattern": "^[A-Z]{3}$"}'),
    ("IN_DOMAIN", "Validity", "column, values", "ignorer_nulls",
     "La valeur appartient a une liste fermee",
     "% de valeurs valides", '{"column": "DER_BASIS", "values": ["A", "B", "C"]}'),
    ("RANGE", "Validity", "column, min | max", "ignorer_nulls",
     "La valeur numerique est comprise dans l'intervalle",
     "% de valeurs dans la plage", '{"column": "turnover_notionnel", "min": 0}'),
    ("UNIQUE_KEY", "Uniqueness", "columns | role", "",
     "Aucun doublon sur la cle composee",
     "taux de doublons", '{"role": "cle_primaire"}'),
    ("FOREIGN_KEY", "Consistency", "column, ref_dataset, ref_column", "ignorer_nulls",
     "Toute valeur existe dans le referentiel cible (integrite referentielle)",
     "% de valeurs rattachees",
     '{"column": "DER_CURR_LEG1", "ref_dataset": "ref_devises", "ref_column": "code_devise"}'),
    ("FIELD_EQUALS", "Consistency", "field_a, field_b", "ignorer_nulls",
     "Deux champs portent la meme valeur",
     "% de lignes coherentes", '{"field_a": "annee", "field_b": "annee_derivee"}'),
    ("DATE_ORDER", "Consistency", "before, after", "ignorer_nulls",
     "La date 'before' est anterieure ou egale a la date 'after'",
     "% de lignes coherentes", '{"before": "date_debut", "after": "date_fin"}'),
    ("FRESHNESS", "Timeliness", "column, max_lag_days", "",
     "L'observation la plus recente date de moins de N jours",
     "age maximum en jours", '{"column": "date_observation", "max_lag_days": 1460}'),
    ("SUM_RECONCILIATION", "Reconciliation",
     "amount, group_by, total_column, total_value", "tolerance_pct",
     "Le total declare egale la somme de ses composantes",
     "ecart relatif en %",
     '{"amount": "turnover_notionnel", "group_by": ["annee"], '
     '"total_column": "DER_REP_CTY", "total_value": "5J"}'),
    ("COUNT_RECONCILIATION", "Reconciliation", "ref_dataset", "group_by",
     "Le nombre de lignes concorde avec le dataset de reference",
     "ecart en nombre de lignes", '{"ref_dataset": "bis_turnover"}'),
    ("CUSTOM_EXPRESSION", "Multi", "expression", "description_metier",
     "Expression pandas evaluee ligne a ligne ; doit etre vraie partout",
     "% de lignes conformes",
     '{"expression": "DER_CURR_LEG1 != DER_CURR_LEG2"}'),
]


def col(name, typ, role, pk="NON", oblig="OUI", desc="", fk_ds="", fk_col=""):
    return {"colonne": name, "type": typ, "role": role, "cle_primaire": pk,
            "obligatoire": oblig, "description": desc,
            "fk_dataset": fk_ds, "fk_colonne": fk_col}


BIS_COLS = [
    col("observation_id", "string", "identifiant", "OUI", "OUI",
        "Cle composee des 14 dimensions + annee"),
    col("FREQ", "string", "code", desc="Frequence de la serie"),
    col("DER_TYPE", "string", "code", desc="Type de mesure"),
    col("DER_INSTR", "string", "code", desc="Instrument"),
    col("DER_RISK", "string", "code", desc="Categorie de risque"),
    col("DER_REP_CTY", "string", "code", desc="Pays declarant",
        fk_ds="ref_pays", fk_col="code_pays"),
    col("DER_SECTOR_CPY", "string", "code", desc="Secteur de la contrepartie"),
    col("DER_CPC", "string", "code", desc="Pays de la contrepartie",
        fk_ds="ref_pays", fk_col="code_pays"),
    col("DER_SECTOR_UDL", "string", "code", desc="Secteur du sous-jacent"),
    col("DER_CURR_LEG1", "string", "code", desc="Devise jambe 1",
        fk_ds="ref_devises", fk_col="code_devise"),
    col("DER_CURR_LEG2", "string", "code", desc="Devise jambe 2",
        fk_ds="ref_devises", fk_col="code_devise"),
    col("DER_ISSUE_MAT", "string", "code", desc="Maturite"),
    col("DER_RATING", "string", "code", desc="Notation ou reglement"),
    col("DER_EX_METHOD", "string", "code", desc="Methode d'execution"),
    col("DER_BASIS", "string", "code", desc="Base brute ou nette"),
    col("Instrument", "string", "libelle", oblig="NON", desc="Libelle instrument"),
    col("Risk category", "string", "libelle", oblig="NON", desc="Libelle risque"),
    col("Reporting country", "string", "libelle", oblig="NON",
        desc="Libelle pays declarant"),
    col("Counterparty sector", "string", "libelle", oblig="NON",
        desc="Libelle secteur contrepartie"),
    col("Currency leg 1", "string", "libelle", oblig="NON", desc="Libelle devise 1"),
    col("Currency leg 2", "string", "libelle", oblig="NON", desc="Libelle devise 2"),
    col("Execution method", "string", "libelle", oblig="NON",
        desc="Libelle methode d'execution"),
    col("Basis", "string", "libelle", oblig="NON", desc="Libelle base"),
    col("annee", "integer", "code", desc="Annee de l'enquete triennale"),
    col("date_observation", "date", "date_evenement", desc="Fin de periode observee"),
    col("turnover_notionnel", "decimal", "mesure",
        desc="Montant notionnel, moyenne journaliere, USD millions"),
]

REF_DEVISES_COLS = [
    col("code_devise", "string", "identifiant", "OUI", "OUI",
        "Code devise ISO 4217 ou agregat BIS"),
    col("libelle_devise", "string", "libelle", desc="Denomination de la devise"),
    col("type", "string", "code", desc="REEL ou AGREGAT"),
]

REF_PAYS_COLS = [
    col("code_pays", "string", "identifiant", "OUI", "OUI",
        "Code pays BIS ou agregat de zone"),
    col("libelle_pays", "string", "libelle", desc="Denomination du pays"),
    col("type", "string", "code", desc="REEL ou AGREGAT"),
]

DATASETS = {
    "bis_turnover": {
        "libelle": "BIS Triennial Survey - OTC derivatives turnover (format long)",
        "source": "data/prepared/bis_turnover.csv",
        "proprietaire": "Market Data Office",
        "colonnes": BIS_COLS,
    },
    "bis_turnover_demo": {
        "libelle": "Extrait enquete 2022 - jeu de demonstration",
        "source": "data/prepared/bis_turnover_demo.csv",
        "proprietaire": "Market Data Office",
        "colonnes": BIS_COLS,
    },
    "ref_devises": {
        "libelle": "Referentiel des devises",
        "source": "data/ref/ref_devises.csv",
        "proprietaire": "Referential Management",
        "colonnes": REF_DEVISES_COLS,
    },
    "ref_pays": {
        "libelle": "Referentiel des pays declarants et de contrepartie",
        "source": "data/ref/ref_pays.csv",
        "proprietaire": "Referential Management",
        "colonnes": REF_PAYS_COLS,
    },
}


def ctrl(rid, name, dim, desc, template, params, scope, element, seuil, sev,
         freq, owner, out, kpi, remed, logic, statut="Actif"):
    return {"rule_id": rid, "control_name": name, "control_type": dim,
            "description": desc, "template": template,
            "params": json.dumps(params, ensure_ascii=False),
            "logic_definition": logic, "dataset_scope": scope,
            "data_element": element, "seuil_tolerance_pct": seuil,
            "severity": sev, "frequency": freq, "owner": owner,
            "output_type": out, "kpi": kpi, "remediation_action": remed,
            "statut": statut}


BIS_SCOPE = "bis_turnover,bis_turnover_demo"

CONTROLS = [
    ctrl("DQ01", "Completude du montant notionnel", "Completeness",
         "Chaque observation publiee doit porter un montant.", "NOT_NULL",
         {"column": "turnover_notionnel"}, BIS_SCOPE,
         "turnover_notionnel", 0, "Critical", "Trimestrielle", "Market Data Office",
         "Exception report", "% de completude",
         "Rejeter la publication et redemander l'extraction a la source BIS.",
         "COUNT(turnover_notionnel IS NULL) = 0"),
    ctrl("DQ02", "Completude des identifiants (transverse)", "Completeness",
         "Tout identifiant declare au contrat doit etre renseigne.", "NOT_NULL",
         {"role": "identifiant"}, "*", "role=identifiant", 0, "Critical",
         "Trimestrielle", "Data Steward", "Exception report", "% de completude",
         "Bloquer le chargement ; l'identifiant est reconstruit a la preparation.",
         "COUNT(identifiant IS NULL) = 0"),
    ctrl("DQ03", "Positivite du montant notionnel", "Validity",
         "Un turnover notionnel ne peut pas etre negatif.", "RANGE",
         {"column": "turnover_notionnel", "min": 0}, BIS_SCOPE,
         "turnover_notionnel", 0, "High", "Trimestrielle", "Market Data Office",
         "Exception report", "% de valeurs dans la plage",
         "Escalade au Market Data Office ; verifier le signe a la source.",
         "turnover_notionnel >= 0"),
    ctrl("DQ04", "Domaine de la base de declaration", "Validity",
         "La base doit valoir A (brut), B (net) ou C (net-net).", "IN_DOMAIN",
         {"column": "DER_BASIS", "values": ["A", "B", "C"]}, BIS_SCOPE,
         "DER_BASIS", 0, "Medium", "Trimestrielle", "Data Steward", "Scorecard",
         "% de valeurs valides",
         "Corriger le mapping de codification a l'ingestion.",
         "DER_BASIS IN ('A','B','C')"),
    ctrl("DQ05", "Format des codes devise", "Validity",
         "Un code devise doit comporter exactement trois lettres majuscules.",
         "MATCHES_REGEX", {"column": "DER_CURR_LEG1", "pattern": "^[A-Z]{3}$"},
         BIS_SCOPE, "DER_CURR_LEG1", 0, "Medium", "Trimestrielle",
         "Referential Management", "Exception report", "% de valeurs valides",
         "Normaliser le code ou l'ajouter au referentiel apres arbitrage.",
         "DER_CURR_LEG1 respecte ^[A-Z]{3}$"),
    ctrl("DQ06", "Plage des annees d'enquete", "Validity",
         "L'annee doit appartenir a la periode couverte par l'enquete.", "RANGE",
         {"column": "annee", "min": 1986, "max": 2022}, BIS_SCOPE,
         "annee", 0, "Medium", "Trimestrielle", "Market Data Office", "Scorecard",
         "% de valeurs dans la plage",
         "Etendre la borne haute a chaque nouvelle vague d'enquete.",
         "annee BETWEEN 1986 AND 2022"),
    ctrl("DQ07", "Unicite des cles primaires (transverse)", "Uniqueness",
         "Aucune cle primaire declaree ne doit apparaitre deux fois.", "UNIQUE_KEY",
         {"role": "cle_primaire"}, "*", "role=cle_primaire", 0, "Critical",
         "Trimestrielle", "Data Steward", "Exception report", "taux de doublons",
         "Dedoublonner a la preparation et tracer la ligne ecartee.",
         "COUNT(*) = COUNT(DISTINCT cle_primaire)"),
    ctrl("DQ08", "Integrite referentielle - devise jambe 1", "Consistency",
         "Toute devise declaree doit exister dans le referentiel devises.",
         "FOREIGN_KEY",
         {"column": "DER_CURR_LEG1", "ref_dataset": "ref_devises",
          "ref_column": "code_devise"},
         BIS_SCOPE, "DER_CURR_LEG1", 0, "High", "Trimestrielle",
         "Referential Management", "Exception report", "% de valeurs rattachees",
         "Arbitrage Data Steward : enrichir le referentiel ou rejeter la ligne.",
         "DER_CURR_LEG1 IN (SELECT code_devise FROM ref_devises)"),
    ctrl("DQ09", "Integrite referentielle - devise jambe 2", "Consistency",
         "Toute devise declaree doit exister dans le referentiel devises.",
         "FOREIGN_KEY",
         {"column": "DER_CURR_LEG2", "ref_dataset": "ref_devises",
          "ref_column": "code_devise"},
         BIS_SCOPE, "DER_CURR_LEG2", 0, "High", "Trimestrielle",
         "Referential Management", "Exception report", "% de valeurs rattachees",
         "Arbitrage Data Steward : enrichir le referentiel ou rejeter la ligne.",
         "DER_CURR_LEG2 IN (SELECT code_devise FROM ref_devises)"),
    ctrl("DQ10", "Integrite referentielle - pays declarant", "Consistency",
         "Tout pays declarant doit exister dans le referentiel pays.", "FOREIGN_KEY",
         {"column": "DER_REP_CTY", "ref_dataset": "ref_pays",
          "ref_column": "code_pays"},
         BIS_SCOPE, "DER_REP_CTY", 0, "High", "Trimestrielle",
         "Referential Management", "Exception report", "% de valeurs rattachees",
         "Arbitrage Data Steward : enrichir le referentiel ou rejeter la ligne.",
         "DER_REP_CTY IN (SELECT code_pays FROM ref_pays)"),
    ctrl("DQ11", "Coherence des jambes de change", "Consistency",
         "Une operation de change ne peut porter deux fois la meme devise.",
         "CUSTOM_EXPRESSION",
         {"expression": "(DER_CURR_LEG1 != DER_CURR_LEG2) or (DER_CURR_LEG1 == 'TO1')",
          "description_metier": "Hors totaux, les deux jambes different"},
         BIS_SCOPE, "DER_CURR_LEG1, DER_CURR_LEG2", 0.5, "Medium", "Trimestrielle",
         "Market Data Office", "Exception report", "% de lignes conformes",
         "Verifier la paire a la source ; requalifier en total si agregat.",
         "DER_CURR_LEG1 <> DER_CURR_LEG2 OU DER_CURR_LEG1 = 'TO1'"),
    ctrl("DQ12", "Fraicheur de l'enquete triennale", "Timeliness",
         "La derniere observation doit dater de moins de quatre ans.", "FRESHNESS",
         {"column": "date_observation", "max_lag_days": 1460}, BIS_SCOPE,
         "date_observation", 0, "High", "Trimestrielle", "Market Data Office",
         "Scorecard", "age maximum en jours",
         "Declencher l'integration de la vague d'enquete suivante.",
         "MAX(date_observation) >= AUJOURDHUI - 1460 jours"),
    ctrl("DQ13", "Additivite des pays declarants", "Reconciliation",
         "Le total 'toutes zones' egale la somme des pays declarants.",
         "SUM_RECONCILIATION",
         {"amount": "turnover_notionnel", "total_column": "DER_REP_CTY",
          "total_value": "5J",
          "group_by": ["annee", "DER_INSTR", "DER_RISK", "DER_SECTOR_CPY",
                       "DER_CPC", "DER_CURR_LEG1", "DER_CURR_LEG2",
                       "DER_ISSUE_MAT", "DER_RATING", "DER_EX_METHOD",
                       "DER_BASIS"],
          "tolerance_pct": 1.0},
         BIS_SCOPE, "turnover_notionnel", 1.0, "High", "Trimestrielle",
         "Market Data Office", "Exception report", "ecart relatif en %",
         "Rapprocher avec la publication BIS ; documenter l'ecart residuel.",
         "SUM(pays declarants) = total 5J a 1% pres"),
    ctrl("DQ14", "Completude du referentiel devises", "Completeness",
         "Chaque code devise porte un libelle exploitable.", "NOT_NULL",
         {"column": "libelle_devise"}, "ref_devises", "libelle_devise", 0,
         "Medium", "Annuelle", "Referential Management", "Scorecard",
         "% de completude",
         "Completer le libelle depuis la source ISO 4217.",
         "COUNT(libelle_devise IS NULL) = 0"),
    ctrl("DQ15", "Ancien seuil de fraicheur (12 mois)", "Timeliness",
         "Seuil annuel abandonne : l'enquete BIS est triennale.", "FRESHNESS",
         {"column": "date_observation", "max_lag_days": 365}, "bis_turnover",
         "date_observation", 0, "Low", "Trimestrielle", "Market Data Office",
         "Scorecard", "age maximum en jours",
         "Sans objet - controle deprecie le 2026-09-07.",
         "MAX(date_observation) >= AUJOURDHUI - 365 jours", statut="Deprecie"),
]


def main() -> None:
    st = CatalogueStore(STORE_PATH)
    st.data = {
        "meta": {"schema": "1.0", "libelle": "DQ Compass - catalogue de controles"},
        "templates": [], "datasets": {}, "controls": [], "changelog": [],
    }
    st.data["templates"] = [
        {"template_id": t[0], "dimension": t[1], "params_requis": t[2],
         "params_optionnels": t[3], "description_logique": t[4],
         "kpi_produit": t[5], "exemple_params": t[6]} for t in TEMPLATES
    ]
    for name, definition in DATASETS.items():
        st.upsert_dataset(name, definition, USER, "Initialisation du catalogue")
    for c in CONTROLS:
        st.add_control(c, USER, "Initialisation du catalogue")
    st.save()
    print(f"store.json : {len(st.templates)} templates, {len(st.datasets)} datasets, "
          f"{len(st.controls)} controles, {len(st.changelog)} entrees de journal")


if __name__ == "__main__":
    main()
