"""
Calibrate the catalogue from the findings of the first run on real BIS data.

Every change goes through the store API so the changelog carries a real audit
trail, with a motive, rather than a silent rewrite of the catalogue. This
script is itself the evidence that the governance layer works.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from store import CatalogueStore  # noqa: E402

USER = "data.steward"

GROUP_BY = ["annee", "DER_INSTR", "DER_RISK", "DER_SECTOR_CPY", "DER_CPC",
            "DER_CURR_LEG1", "DER_CURR_LEG2", "DER_ISSUE_MAT", "DER_RATING",
            "DER_EX_METHOD", "DER_BASIS"]


def main() -> None:
    st = CatalogueStore()

    # -- Template documentation catches up with the new option -------------
    tpl = st.template("SUM_RECONCILIATION")
    tpl["params_optionnels"] = "tolerance_pct, sens, couverture_min_pct"
    tpl["description_logique"] = (
        "Compare un total declare a la somme de ses composantes. "
        "sens = parties_max | couverture_min | bilateral"
    )

    # -- DQ05 : TO1 is the official BIS aggregate code, not a bad value ----
    st.update_control(
        "DQ05",
        {"params": json.dumps({"column": "DER_CURR_LEG1",
                               "pattern": "^([A-Z]{3}|TO1)$"}),
         "description": ("Un code devise comporte trois lettres majuscules, "
                         "ou vaut TO1 pour l'agregat 'toutes devises'."),
         "logic_definition": "DER_CURR_LEG1 respecte ^([A-Z]{3}|TO1)$"},
        USER,
        motif=("Run RUN-20260907-170144 : 22 063 exceptions, toutes sur TO1. "
               "TO1 est le code agregat officiel BIS. La regle produisait "
               "100% de faux positifs ; le format valide doit l'admettre."),
    )

    # -- DQ13 : one-sided. Components must never exceed the declared total --
    st.update_control(
        "DQ13",
        {"control_name": "Non-depassement du total par les pays declarants",
         "description": ("La somme des pays declarants ne peut jamais depasser "
                         "le total 'toutes zones'. Un depassement signale un "
                         "double comptage ou une erreur d'agregation."),
         "params": json.dumps({
             "amount": "turnover_notionnel", "total_column": "DER_REP_CTY",
             "total_value": "5J", "group_by": GROUP_BY,
             "sens": "parties_max", "tolerance_pct": 0.1}),
         "seuil_tolerance_pct": 0,
         "severity": "Critical",
         "logic_definition": "SUM(pays declarants) <= total 5J + 0.1%",
         "kpi": "depassement maximal %",
         "remediation_action": ("Blocage : investiguer le double comptage avec "
                                "le Market Data Office avant publication.")},
        USER,
        motif=("Run RUN-20260907-170144 : sur 1 024 agregats, 781 ont une somme "
               "INFERIEURE au total, 243 sont egaux, aucun ne le depasse. "
               "L'ecart est structurel (juridictions non publiees), pas une "
               "erreur. Le controle bilateral mesurait la mauvaise chose."),
    )

    # -- DQ16 : the other half of the same arithmetic, as a completeness KPI
    st.add_control({
        "rule_id": "DQ16",
        "control_name": "Couverture du detail par pays declarant",
        "control_type": "Completeness",
        "description": ("Le detail par pays publie doit couvrir au moins 95% du "
                        "total mondial. En dessous, l'analyse geographique n'est "
                        "plus exploitable."),
        "template": "SUM_RECONCILIATION",
        "params": json.dumps({
            "amount": "turnover_notionnel", "total_column": "DER_REP_CTY",
            "total_value": "5J", "group_by": GROUP_BY,
            "sens": "couverture_min", "couverture_min_pct": 95.0}),
        "logic_definition": "SUM(pays declarants) / total 5J >= 95%",
        "dataset_scope": "bis_turnover,bis_turnover_demo",
        "data_element": "turnover_notionnel",
        "seuil_tolerance_pct": 20,
        "severity": "Medium",
        "frequency": "Trimestrielle",
        "owner": "Market Data Office",
        "output_type": "Scorecard",
        "kpi": "couverture mediane %",
        "remediation_action": ("Documenter les juridictions non publiees dans la "
                               "note methodologique accompagnant la diffusion."),
    }, USER,
        motif=("Contrepartie de la scission de DQ13 : l'ecart residuel mesure la "
               "completude du detail geographique, pas une erreur de calcul. "
               "Seuil a 20% d'agregats en exception, calibre sur le percentile "
               "observe au run RUN-20260907-170144."))

    st.save()
    print(f"Calibration appliquee. {len(st.controls)} controles, "
          f"{len(st.changelog)} entrees de journal.")
    for entry in st.changelog[-8:]:
        print(f"  {entry['timestamp']} {entry['action']:<13} {entry['rule_id']:<6} "
              f"{entry['champ']}")


if __name__ == "__main__":
    main()
