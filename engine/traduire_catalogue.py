"""
Passe le langage metier du catalogue en anglais.

Le moteur n'a jamais parle francais : ce sont les LIBELLES qui l'etaient -
`libelle_metier`, `question_metier`, `explication`, `phrase`, les noms de
regles, leurs descriptions et leurs actions de remediation. Ce sont eux que
l'interface affiche, et qui devaient suivre le passage a l'anglais.

Rien d'executable ne change : ni `template_id`, ni `params`, ni les seuils, ni
les statuts de gouvernance. `logic_definition` est simplement regenere depuis
le nouveau gabarit, pour que la phrase affichee reste celle que la regle
applique reellement.

    .venv/Scripts/python.exe engine/traduire_catalogue.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from store import CatalogueStore, phrase_controle  # noqa: E402

USER = "data.steward"
MOTIF = "Passage de l'interface et du catalogue en anglais"

MOTIF_LIB = "Column name pattern (regular expression)"

TEMPLATES = {
    "NOT_NULL": {
        "libelle_metier": "Never be empty",
        "question_metier": "Which information must never be missing?",
        "explication": "The control fails when rows carry no value in that column.",
        "exemple_metier": "Every transaction must carry an amount.",
        "phrase": "Column « {column} » must be filled on every row.",
        "phrase_motif": "Every column whose name matches « {colonnes_motif} » must be filled on every row.",
        "description_logique": "No null or empty value in the target column",
        "kpi_produit": "% completeness",
        "params_libelles": {"column": "Column that must always be filled",
                            "colonnes_motif": MOTIF_LIB},
    },
    "MATCHES_REGEX": {
        "libelle_metier": "Follow a strict format",
        "question_metier": "Which format must this information follow?",
        "explication": "The control fails when a value does not follow the expected format.",
        "exemple_metier": "A currency code is exactly three upper-case letters, such as EUR or USD.",
        "phrase": "Column « {column} » must follow the format {pattern}.",
        "phrase_motif": "Every column whose name matches « {colonnes_motif} » must follow the format {pattern}.",
        "description_logique": "The value matches a regular expression",
        "kpi_produit": "% valid values",
        "params_libelles": {"column": "Column to check",
                            "pattern": "Expected format (regular expression)",
                            "colonnes_motif": MOTIF_LIB},
    },
    "IN_DOMAIN": {
        "libelle_metier": "Belong to an allowed list",
        "question_metier": "Which values are the only ones accepted?",
        "explication": "The control fails as soon as a value falls outside the list.",
        "exemple_metier": "A contract type can only be Permanent, Fixed-term or Internship.",
        "phrase": "Column « {column} » may only contain: {values}.",
        "phrase_motif": "Every column whose name matches « {colonnes_motif} » may only contain: {values}.",
        "description_logique": "The value belongs to a closed list",
        "kpi_produit": "% valid values",
        "params_libelles": {"column": "Column to check",
                            "values": "List of allowed values",
                            "colonnes_motif": MOTIF_LIB},
    },
    "RANGE": {
        "libelle_metier": "Stay within numeric bounds",
        "question_metier": "Between which values must this number stay?",
        "explication": "The control fails when a number falls outside the range. Empty cells are ignored.",
        "exemple_metier": "An amount cannot be negative.",
        "phrase": "Column « {column} » must stay between {min} and {max}.",
        "phrase_motif": "Every column whose name matches « {colonnes_motif} » must stay between {min} and {max}.",
        "phrase_min": "Column « {column} » must be greater than or equal to {min}.",
        "phrase_max": "Column « {column} » must be less than or equal to {max}.",
        "phrase_motif_min": "Every column whose name matches « {colonnes_motif} » must be greater than or equal to {min}.",
        "phrase_motif_max": "Every column whose name matches « {colonnes_motif} » must be less than or equal to {max}.",
        "description_logique": "The numeric value falls within the range",
        "kpi_produit": "% values in range",
        "params_libelles": {"column": "Numeric column to check",
                            "min": "Lowest accepted value",
                            "max": "Highest accepted value",
                            "colonnes_motif": MOTIF_LIB},
    },
    "UNIQUE_KEY": {
        "libelle_metier": "Carry no duplicate",
        "question_metier": "What must be unique in the file?",
        "explication": "The control fails when two rows carry the same value.",
        "exemple_metier": "The same invoice number cannot appear twice.",
        "phrase": "No two rows may share the same « {columns} ».",
        "phrase_motif": "Every column whose name matches « {colonnes_motif} » must be free of duplicates.",
        "description_logique": "No duplicate on the composite key",
        "kpi_produit": "duplicate rate",
        "params_libelles": {"columns": "Column(s) uniquely identifying a row",
                            "colonnes_motif": MOTIF_LIB},
    },
    "FOREIGN_KEY": {
        "libelle_metier": "Exist in a reference table",
        "question_metier": "Which code must a reference table recognise?",
        "explication": "The control fails when a code in the file cannot be found in the reference table.",
        "exemple_metier": "Every currency quoted must appear in the currency reference table.",
        "phrase": "Every « {column} » must exist in reference file « {ref_fichier} ».",
        "phrase_motif": "Every column whose name matches « {colonnes_motif} » may only contain values present in « {ref_fichier} ».",
        "description_logique": "Every value exists in the target reference table (referential integrity)",
        "kpi_produit": "% values matched",
        "params_libelles": {"column": "Column to match",
                            "ref_column": "Reference column carrying the code",
                            "ref_fichier": "Reference file (path)",
                            "colonnes_motif": MOTIF_LIB},
    },
    "FIELD_EQUALS": {
        "libelle_metier": "Agree with another column",
        "question_metier": "Which two columns must carry the same value?",
        "explication": "The control fails when the two columns diverge.",
        "exemple_metier": "The total shown at the foot of the file must equal the computed total.",
        "phrase": "« {field_a} » and « {field_b} » must carry the same value.",
        "description_logique": "Two fields carry the same value",
        "kpi_produit": "% consistent rows",
        "params_libelles": {"field_a": "First column", "field_b": "Second column"},
    },
    "DATE_ORDER": {
        "libelle_metier": "Follow chronological order",
        "question_metier": "Which date must come before the other?",
        "explication": ("The control fails when the two dates are reversed. When "
                        "targeted by pattern, columns are paired on what is left "
                        "of their name once the marker is removed: « start_date » "
                        "goes with « end_date », « order_date » with « ship_date »."),
        "exemple_metier": "An admission date always precedes a discharge date.",
        "phrase": "« {before} » must be earlier than or equal to « {after} ».",
        "phrase_motif": "Every date whose name matches « {motif_avant} » must be earlier than or equal to the « {motif_apres} » date of the same event.",
        "description_logique": "The 'before' date is earlier than or equal to the 'after' date",
        "kpi_produit": "% consistent rows",
        "params_libelles": {"before": "Date that must come first",
                            "after": "Date that must come second",
                            "motif_avant": "Pattern for the date columns that come first",
                            "motif_apres": "Pattern for the date columns that come second"},
    },
    "FRESHNESS": {
        "libelle_metier": "Be recent enough",
        "question_metier": "How old may the data be at most?",
        "explication": "The control fails when the most recent value is too old.",
        "exemple_metier": "A file refreshed monthly must not be older than 45 days.",
        "phrase": "The most recent value of « {column} » must be less than {max_lag_days} days old.",
        "phrase_motif": "The most recent value of every column whose name matches « {colonnes_motif} » must be less than {max_lag_days} days old.",
        "description_logique": "The most recent observation is less than N days old",
        "kpi_produit": "maximum age in days",
        "params_libelles": {"column": "Date column to watch",
                            "max_lag_days": "Maximum tolerated age, in days",
                            "colonnes_motif": MOTIF_LIB},
    },
    "SUM_RECONCILIATION": {
        "libelle_metier": "Add up to a consistent total",
        "question_metier": "Which total must match the sum of its parts?",
        "explication": "The control compares a total row against the sum of its components and reports the gaps.",
        "exemple_metier": "Turnover for « all regions » must equal the sum of the regions.",
        "phrase": "Total « {total_value} » of « {total_column} » must match the sum of « {amount} ».",
        "phrase_parties_max": "The sum of « {amount} » must never exceed total « {total_value} ».",
        "phrase_couverture_min": "The detail of « {amount} » must cover at least {couverture_min_pct} % of total « {total_value} ».",
        "description_logique": "Compares a declared total against the sum of its components. sens = parties_max | couverture_min | bilateral",
        "kpi_produit": "relative gap in %",
        "params_libelles": {"amount": "Amount column to add up",
                            "total_column": "Column carrying the total row",
                            "total_value": "Value identifying the total",
                            "group_by": "Dimensions to hold fixed when comparing",
                            "sens": "What counts as an unacceptable gap",
                            "tolerance_pct": "Tolerated gap, in %",
                            "couverture_min_pct": "Minimum required coverage, in %"},
    },
    "ROW_SUM_RECONCILIATION": {
        "libelle_metier": "Equal the sum of its components",
        "question_metier": "Which total must match the sum of other columns?",
        "explication": ("The control adds up the detail columns of a row and "
                        "compares the result against the total column of that "
                        "same row. No second file is needed. A row with a "
                        "missing component is not tested."),
        "exemple_metier": "« total_gross » must equal « amount_net » + « amount_vat ».",
        "phrase": "« {total_column} » must equal the sum of « {columns} » on every row.",
        "phrase_motif": "Every total column « {motif_total} » must equal, on every row, the sum of columns « {motif_parties} ».",
        "description_logique": "The total carried by a row equals the sum of its components",
        "kpi_produit": "% reconciled rows",
        "params_libelles": {"total_column": "Column carrying the total",
                            "columns": "Columns to add up",
                            "motif_total": "Pattern for the total columns",
                            "motif_parties": "Pattern for the columns to add up",
                            "tolerance_pct": "Tolerated rounding gap, in %"},
    },
    "COUNT_RECONCILIATION": {
        "libelle_metier": "Hold the right number of rows",
        "question_metier": "Which file must the row count match?",
        "explication": "The control fails when the row counts diverge between the two files.",
        "exemple_metier": "After a transfer, the received file must hold as many rows as the file sent.",
        "phrase": "The row count must match that of « {ref_fichier} ».",
        "description_logique": "The row count matches the reference dataset",
        "kpi_produit": "row count gap",
        "params_libelles": {"group_by": "Compare by subset (optional)",
                            "ref_fichier": "Reference file (path)"},
    },
    "CUSTOM_EXPRESSION": {
        "libelle_metier": "Custom rule",
        "question_metier": "Which condition must hold true on every row?",
        "explication": "For technical profiles only: the condition is written in pandas syntax and evaluated row by row.",
        "exemple_metier": "buy_currency != sell_currency",
        "phrase": "Every row must satisfy: {expression}.",
        "description_logique": "Pandas expression evaluated row by row; must be true everywhere",
        "kpi_produit": "% compliant rows",
        "params_libelles": {"expression": "Condition to check",
                            "description_metier": "Plain-English reading of the condition"},
    },
}

# rule_id -> (control_name, description, remediation_action)
CONTROLES = {
    "DQ01": ("Completeness of the notional amount",
             "Every published observation must carry an amount.",
             "Reject the publication and request a fresh extract from the BIS source."),
    "DQ02": ("Completeness of identifiers (cross-dataset)",
             "Every column named as an identifier must be filled.",
             "Block the load; the identifier is rebuilt during preparation."),
    "DQ03": ("Notional amount must be positive",
             "A notional turnover cannot be negative.",
             "Escalate to the Market Data Office; check the sign at the source."),
    "DQ04": ("Reporting basis domain",
             "The basis must be A (gross), B (net) or C (net-net).",
             "Fix the code mapping at ingestion."),
    "DQ05": ("Currency code format",
             "A currency code is three upper-case letters, or TO1 for the "
             "« all currencies » aggregate.",
             "Normalise the code, or add it to the reference table after arbitration."),
    "DQ06": ("Survey year range",
             "The year must fall within the period covered by the survey.",
             "Extend the upper bound with each new survey wave."),
    "DQ07": ("Uniqueness of primary keys (cross-dataset)",
             "No declared primary key may appear twice.",
             "De-duplicate during preparation and log the discarded row."),
    "DQ08": ("Referential integrity - currency leg 1",
             "Every currency quoted must exist in the currency reference table.",
             "Data Steward arbitration: extend the reference table or reject the row."),
    "DQ09": ("Referential integrity - currency leg 2",
             "Every currency quoted must exist in the currency reference table.",
             "Data Steward arbitration: extend the reference table or reject the row."),
    "DQ10": ("Referential integrity - reporting country",
             "Every reporting country must exist in the country reference table.",
             "Data Steward arbitration: extend the reference table or reject the row."),
    "DQ11": ("Consistency of the FX legs",
             "An FX trade cannot carry the same currency twice.",
             "Check the pair at the source; reclassify as a total if it is an aggregate."),
    "DQ12": ("Freshness of the triennial survey",
             "The latest observation must be less than four years old.",
             "Trigger the integration of the next survey wave."),
    "DQ13": ("Reporting countries must not exceed the total",
             "The sum of reporting countries can never exceed the « all countries » "
             "total. An overshoot signals double counting or an aggregation error.",
             "Blocking: investigate the double counting with the Market Data Office "
             "before publication."),
    "DQ14": ("Completeness of the currency reference table",
             "Every currency code carries a usable label.",
             "Complete the label from the ISO 4217 source."),
    "DQ15": ("Former freshness threshold (12 months)",
             "Annual threshold dropped: the BIS survey is triennial.",
             "Not applicable - control retired on 2026-09-07."),
    "DQ16": ("Country detail must cover the total",
             "Published country detail must cover at least 95% of the world total. "
             "Below that, geographic analysis is no longer usable.",
             "Document the unpublished jurisdictions in the methodological note "
             "accompanying the release."),
    "DQ17": ("COMPLETENESS_2", "", ""),
    "DQ18": ("Uniqueness of technical row identifiers",
             "A column named after the convention for a technical row identifier "
             "denotes a key: no two rows may share it.",
             "De-duplicate at the source and check the key generation."),
    "DQ19": ("Currency code format (ISO 4217)",
             "A column announcing a currency code must carry an ISO 4217 code: "
             "three upper-case letters. The pattern deliberately excludes label columns.",
             "Map the value to an ISO 4217 code, or document the local convention "
             "and adjust the expected format."),
    "DQ20": ("Country code format (ISO 3166-1)",
             "A country code column must carry an ISO 3166-1 alpha-2 code: two "
             "upper-case letters.",
             "Fix the country code at the source."),
    "DQ21": ("Amounts and quantities are never negative",
             "A figure measuring a quantity, a price or an amount cannot be "
             "negative without justification. An isolated minus sign almost always "
             "betrays a keying or sign error.",
             "Review every negative value: sign error, legitimate credit note, or "
             "correction at the source."),
    "DQ22": ("Percentages between 0 and 100",
             "A column announcing a percentage must stay within 0-100.",
             "Check the unit: a rate expressed as a fraction (0-1) or above 100 "
             "signals a missed conversion."),
    "DQ23": ("Well-formed e-mail addresses",
             "A malformed e-mail address makes every notification useless and "
             "distorts matching.",
             "Fix the address at the source."),
    "DQ24": ("Freshness of the last update",
             "An update timestamp column reveals a feed that is drying up: the "
             "most recent data must not be too old.",
             "Check that the file feed has not been interrupted."),
    "DQ25": ("Currency codes recognised by the reference table",
             "A currency code can be well formed and still denote no currency. "
             "This rule completes the format control: it requires the value to "
             "exist in the shipped ISO 4217 reference table.",
             "Match the code against the currency reference table, or extend the "
             "table if the currency is legitimate."),
    "DQ26": ("Country codes recognised by the reference table",
             "« XX » and « ZZ » are perfectly well-formed two-letter codes that "
             "denote no country. Only a match against the shipped ISO 3166-1 "
             "reference table tells them apart from a real code.",
             "Fix the country code at the source, or extend the country reference "
             "table if the country is not listed yet."),
    "DQ27": ("Chronological order of the dates of one event",
             "A start date later than its end date is inconsistent in any "
             "business. Columns are paired on their name: « start_date » with "
             "« end_date », « order_date » with « ship_date ».",
             "Have both dates fixed at the source: one of the two is wrong, and "
             "the business reconciliation says which."),
    "DQ28": ("Row total equals the sum of its components",
             "Reconciliation internal to one file: a total column must equal, on "
             "every row, the sum of the detail columns. No second source is "
             "required, only the naming convention.",
             "Recompute the total at the source. A wrong total invalidates every "
             "aggregate built on this file."),
}


def main() -> None:
    st = CatalogueStore()

    for tpl in st.templates:
        traduction = TEMPLATES.get(tpl["template_id"])
        if not traduction:
            continue
        for champ, valeur in traduction.items():
            tpl[champ] = valeur

    for ctrl in st.controls:
        traduction = CONTROLES.get(ctrl["rule_id"])
        if not traduction:
            continue
        nom, description, remediation = traduction
        changements = {"control_name": nom, "description": description,
                       "remediation_action": remediation}
        changements["logic_definition"] = phrase_controle(
            {**ctrl, **changements}, st)
        changements["kpi"] = (st.template(ctrl["template"]) or {}).get(
            "kpi_produit", ctrl.get("kpi", ""))
        st.update_control(ctrl["rule_id"], changements, USER, MOTIF)

    st.save()
    print(f"{len(st.templates)} modeles et {len(st.controls)} controles en anglais")
    for c in st.active_controls():
        print(f"  {c['rule_id']} · {c['logic_definition'][:88]}")


if __name__ == "__main__":
    main()
