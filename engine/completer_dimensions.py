"""
Complete le catalogue sur les deux dimensions du brief restees vides.

Le brief (§2.2) impose six dimensions. Le socle livre en couvrait quatre :
Completude, Unicite, Validite, Fraicheur. Coherence et Reconciliation etaient
absentes pour une raison technique, pas metier : les modeles correspondants
exigeaient de NOMMER deux colonnes precises, ce que l'annexe A.4 interdit a un
controle reutilisable (« independent of datasets »).

Le moteur sait desormais viser ces deux dimensions par convention de nommage :

  - DATE_ORDER apparie une colonne « avant » et une colonne « apres » sur leur
    radical commun (`date_debut` / `date_fin`, `order_date` / `ship_date`) ;
  - FOREIGN_KEY accepte un motif de colonnes, comme les autres modeles ;
  - ROW_SUM_RECONCILIATION, nouveau, rapproche une colonne de total et ses
    composantes a l'interieur d'une meme ligne, sans seconde source.

Quatre regles sont ajoutees sur cette base. Comme tout le socle, aucune ne
nomme de colonne : elles s'appliqueront a des fichiers qui n'existent pas
encore, et se declarent hors perimetre ailleurs.

Tout passe par l'API du store : le journal du catalogue porte donc qui, quand,
quoi et pourquoi. Le script est rejouable - il ne recree pas ce qui existe.

    .venv/Scripts/python.exe engine/completer_dimensions.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from store import CatalogueStore, phrase_controle  # noqa: E402

USER = "data.steward"
MOTIF = ("Couverture des six dimensions du brief : Coherence et Reconciliation "
         "etaient sans aucune regle active")

# --------------------------------------------------------------------------- #
# 1. Les modeles apprennent le ciblage par motif
# --------------------------------------------------------------------------- #
MOTIF_DEVISE = ("(?i)^(?!.*(libell|label|nom|name|desc))"
                "(code[_-]?(devise|currency)|(devise|currency)[_-]?code)$")
MOTIF_PAYS = "(?i)(^|_)(pays|country)(_?code)?$"
MOTIF_AVANT = ("(?i)(^|_)(debut|start|creation|created|ouverture|open|commande|"
               "order|emission|admission|entree|arrivee)(_|$)")
MOTIF_APRES = ("(?i)(^|_)(fin|end|cloture|close|closed|sortie|expedition|ship|"
               "shipped|livraison|delivery|delivered|reception|received|"
               "discharge|paiement|payment)(_|$)")
MOTIF_TOTAL = "(?i)(^|_)total(_|$)"
MOTIF_PARTIES = ("(?i)^(montant|amount|prix|price|cout|cost|frais|fee|taxe|tax|"
                 "tva|vat|remise|discount|net|brut|gross)(_|$)")


def maj_templates(st: CatalogueStore) -> None:
    fk = st.template("FOREIGN_KEY")
    fk["params_requis"] = "column | colonnes_motif, ref_fichier, ref_column"
    fk["params_libelles"]["colonnes_motif"] = (
        "Motif des colonnes visées (expression régulière sur le nom)")
    fk["phrase_motif"] = ("Toute colonne dont le nom correspond à "
                          "« {colonnes_motif} » ne peut contenir que des valeurs "
                          "présentes dans « {ref_fichier} ».")

    do = st.template("DATE_ORDER")
    do["params_requis"] = "before | motif_avant, after | motif_apres"
    do["params_libelles"].update({
        "motif_avant": "Motif des colonnes de date qui viennent en premier",
        "motif_apres": "Motif des colonnes de date qui viennent en second",
    })
    do["phrase_motif"] = ("Toute date dont le nom correspond à « {motif_avant} » "
                          "doit être antérieure ou égale à la date "
                          "« {motif_apres} » du même événement.")
    do["explication"] = (
        "Le contrôle échoue si les deux dates sont inversées. Désignées par "
        "motif, les colonnes sont appariées sur ce qui reste de leur nom une "
        "fois le marqueur retiré : « date_debut » va avec « date_fin », "
        "« order_date » avec « ship_date ».")

    if st.template("ROW_SUM_RECONCILIATION") is None:
        st.templates.append({
            "template_id": "ROW_SUM_RECONCILIATION",
            "dimension": "Reconciliation",
            "params_requis": "total_column | motif_total, columns | motif_parties",
            "params_optionnels": "tolerance_pct",
            "description_logique": (
                "Le total porte par une ligne egale la somme de ses composantes"),
            "kpi_produit": "% de lignes rapprochees",
            "exemple_params": '{"motif_total": "(?i)(^|_)total(_|$)"}',
            "libelle_metier": "Égaler la somme de ses composantes",
            "question_metier": "Quel total doit correspondre à la somme d'autres colonnes ?",
            "explication": (
                "Le contrôle additionne les colonnes de détail d'une ligne et "
                "compare le résultat à la colonne de total de cette même ligne. "
                "Aucun second fichier n'est nécessaire. Une ligne dont une "
                "composante manque n'est pas testée."),
            "exemple_metier": "« total_ttc » doit égaler « montant_ht » + « tva ».",
            "phrase": ("« {total_column} » doit égaler la somme de « {columns} » "
                       "sur chaque ligne."),
            "phrase_motif": ("Toute colonne de total « {motif_total} » doit "
                             "égaler, sur chaque ligne, la somme des colonnes "
                             "« {motif_parties} »."),
            "params_libelles": {
                "total_column": "Colonne qui porte le total",
                "columns": "Colonnes à additionner",
                "motif_total": "Motif des colonnes de total",
                "motif_parties": "Motif des colonnes à additionner",
                "tolerance_pct": "Écart d'arrondi toléré, en %",
            },
            "niveau": "simple",
        })
        st._journal("CREATION", "-", "template", None,
                    "ROW_SUM_RECONCILIATION", USER, MOTIF)


# --------------------------------------------------------------------------- #
# 2. Les regles qui manquaient
# --------------------------------------------------------------------------- #
NOUVELLES = [
    {
        "control_name": "Codes devise reconnus par le référentiel",
        "control_type": "Consistency",
        "description": (
            "Un code devise peut être bien formé et ne désigner aucune devise. "
            "Cette règle complète le contrôle de format : elle exige que la "
            "valeur existe dans le référentiel ISO 4217 livré."),
        "template": "FOREIGN_KEY",
        "params": {"colonnes_motif": MOTIF_DEVISE,
                   "ref_fichier": "data/ref/ref_devises.csv",
                   "ref_column": "code_devise"},
        "severity": "High",
        "remediation_action": (
            "Rapprocher le code du référentiel devises, ou compléter le "
            "référentiel s'il s'agit d'une devise légitime."),
    },
    {
        "control_name": "Codes pays reconnus par le référentiel",
        "control_type": "Consistency",
        "description": (
            "« XX » et « ZZ » sont des codes de deux lettres parfaitement "
            "formés qui ne désignent aucun pays. Seul un rapprochement avec le "
            "référentiel ISO 3166-1 livré les distingue d'un vrai code."),
        "template": "FOREIGN_KEY",
        "params": {"colonnes_motif": MOTIF_PAYS,
                   "ref_fichier": "data/ref/ref_pays.csv",
                   "ref_column": "code_pays"},
        "severity": "Medium",
        "remediation_action": (
            "Corriger le code pays à la source, ou compléter le référentiel "
            "pays s'il s'agit d'un pays non encore listé."),
    },
    {
        "control_name": "Ordre chronologique des dates d'un même événement",
        "control_type": "Consistency",
        "description": (
            "Une date de début postérieure à sa date de fin est incohérente, "
            "quel que soit le métier. Les colonnes sont appariées sur leur "
            "nom : « date_debut » avec « date_fin », « order_date » avec "
            "« ship_date »."),
        "template": "DATE_ORDER",
        "params": {"motif_avant": MOTIF_AVANT, "motif_apres": MOTIF_APRES},
        "severity": "High",
        "remediation_action": (
            "Faire corriger les deux dates à la source : l'une des deux est "
            "fausse, le rapprochement métier dit laquelle."),
    },
    {
        "control_name": "Total de ligne égal à la somme de ses composantes",
        "control_type": "Reconciliation",
        "description": (
            "Réconciliation interne à un fichier : une colonne de total doit "
            "égaler, sur chaque ligne, la somme des colonnes de détail. Aucune "
            "seconde source n'est requise, seulement la convention de nommage."),
        "template": "ROW_SUM_RECONCILIATION",
        "params": {"motif_total": MOTIF_TOTAL, "motif_parties": MOTIF_PARTIES,
                   "tolerance_pct": 0.01},
        "severity": "High",
        "remediation_action": (
            "Recalculer le total à la source. Un écart de total invalide toute "
            "agrégation construite sur ce fichier."),
    },
]


def ajouter_regles(st: CatalogueStore) -> list[str]:
    existants = {c["control_name"] for c in st.controls}
    ajoutes = []
    for modele in NOUVELLES:
        if modele["control_name"] in existants:
            continue
        params = modele["params"]
        cibles = [str(v) for k, v in params.items()
                  if k in ("colonnes_motif", "column", "total_column")
                  or str(k).startswith("motif_")]
        controle = {
            "rule_id": st.next_rule_id(),
            "control_name": modele["control_name"],
            "control_type": modele["control_type"],
            "description": modele["description"],
            "template": modele["template"],
            "params": json.dumps(params, ensure_ascii=False),
            "dataset_scope": "*",
            "data_element": ", ".join(cibles),
            "seuil_tolerance_pct": 0.0,
            "severity": modele["severity"],
            "frequency": "A la demande",
            "owner": "Data Steward",
            "output_type": "Exception report",
            "kpi": (st.template(modele["template"]) or {}).get("kpi_produit", ""),
            "remediation_action": modele["remediation_action"],
        }
        controle["logic_definition"] = phrase_controle(controle, st)
        cree = st.add_control(controle, USER, MOTIF)
        ajoutes.append(f"{cree['rule_id']} · {cree['control_name']}")
    return ajoutes


def main() -> None:
    st = CatalogueStore()
    maj_templates(st)
    ajoutes = ajouter_regles(st)
    st.save()
    print("Modeles a jour : FOREIGN_KEY, DATE_ORDER, ROW_SUM_RECONCILIATION")
    print("\n".join(f"  + {a}" for a in ajoutes) or "  (aucune regle a ajouter)")
    couverture: dict[str, int] = {}
    for c in st.active_controls():
        couverture[c["control_type"]] = couverture.get(c["control_type"], 0) + 1
    print(f"{len(st.active_controls())} regles en service - couverture : "
          + ", ".join(f"{d} {n}" for d, n in sorted(couverture.items())))


if __name__ == "__main__":
    main()
