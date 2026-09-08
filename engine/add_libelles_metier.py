"""
Ajoute la couche de langage metier aux templates du catalogue.

Un utilisateur qui n'est pas data ne doit jamais lire "MATCHES_REGEX" ni
"{'column': 'montant'}". Il doit lire "Respecter un format precis" et
"La colonne Montant doit respecter le format ^[0-9]+$".

Cette couche vit dans le CATALOGUE, pas dans l'interface : ajouter un template
suffit a le rendre pilotable en langage clair, sans toucher au code de l'UI.

  libelle_metier   : le nom de la regle, en clair, a la place du template_id
  question_metier  : la question posee a l'utilisateur dans l'editeur
  explication      : ce que fait le controle, en une phrase
  exemple_metier   : un cas concret, pour lever le doute
  phrase           : gabarit de la phrase recapitulative, rempli avec les params
  params_libelles  : le nom en clair de chaque parametre
  niveau           : simple (tout le monde) ou avance (profils data)
"""
# ---------------------------------------------------------------------------
# SCRIPT HISTORIQUE - NE PLUS EXECUTER.
#
# Ecrit pour le schema de catalogue 1.0, quand un fichier devait etre declare
# avant d'etre controle. Le catalogue est passe au schema 2.0 : plus aucun
# contrat n'est declare, la structure est deduite a la lecture par
# engine/profiler.py.
#
# Son effet est integralement acquis dans catalogue/store.json, et trace dans
# le journal du catalogue. Conserve pour la provenance, pas pour l'usage.
# ---------------------------------------------------------------------------

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from store import CatalogueStore  # noqa: E402

LIBELLES = {
    "NOT_NULL": {
        "libelle_metier": "Ne jamais être vide",
        "question_metier": "Quelle information ne doit jamais manquer ?",
        "explication": "Le contrôle échoue si des lignes n'ont aucune valeur "
                       "dans cette colonne.",
        "exemple_metier": "Chaque opération doit porter un montant.",
        "phrase": "La colonne « {column} » doit être renseignée sur chaque ligne.",
        "phrase_role": "Toute information de type « {role} » doit être renseignée.",
        "params_libelles": {
            "column": "Colonne qui doit toujours être remplie",
            "role": "Type d'information concerné",
        },
        "niveau": "simple",
    },
    "UNIQUE_KEY": {
        "libelle_metier": "Ne pas contenir de doublon",
        "question_metier": "Qu'est-ce qui doit être unique dans le fichier ?",
        "explication": "Le contrôle échoue si deux lignes portent la même valeur.",
        "exemple_metier": "Un même numéro de facture ne peut pas apparaître deux fois.",
        "phrase": "Deux lignes ne peuvent pas avoir le même « {columns} ».",
        "phrase_role": "Les identifiants de type « {role} » doivent être uniques.",
        "params_libelles": {
            "columns": "Colonne(s) qui identifient une ligne de façon unique",
            "role": "Type d'identifiant concerné",
        },
        "niveau": "simple",
    },
    "IN_DOMAIN": {
        "libelle_metier": "Faire partie d'une liste autorisée",
        "question_metier": "Quelles sont les seules valeurs acceptées ?",
        "explication": "Le contrôle échoue dès qu'une valeur sort de la liste.",
        "exemple_metier": "Un type de contrat ne peut être que CDI, CDD ou Stage.",
        "phrase": "La colonne « {column} » ne peut contenir que : {values}.",
        "params_libelles": {
            "column": "Colonne à vérifier",
            "values": "Liste des valeurs autorisées",
        },
        "niveau": "simple",
    },
    "RANGE": {
        "libelle_metier": "Rester dans des bornes chiffrées",
        "question_metier": "Entre quelles valeurs ce nombre doit-il rester ?",
        "explication": "Le contrôle échoue si un nombre sort de l'intervalle. "
                       "Les cases vides sont ignorées.",
        "exemple_metier": "Un montant ne peut pas être négatif.",
        "phrase": "La colonne « {column} » doit rester entre {min} et {max}.",
        "phrase_min": "La colonne « {column} » doit être supérieure ou égale à {min}.",
        "phrase_max": "La colonne « {column} » doit être inférieure ou égale à {max}.",
        "params_libelles": {
            "column": "Colonne chiffrée à vérifier",
            "min": "Valeur minimale acceptée",
            "max": "Valeur maximale acceptée",
        },
        "niveau": "simple",
    },
    "MATCHES_REGEX": {
        "libelle_metier": "Respecter un format précis",
        "question_metier": "Quel format cette information doit-elle respecter ?",
        "explication": "Le contrôle échoue si la valeur ne suit pas le format attendu.",
        "exemple_metier": "Un code devise s'écrit avec exactement trois lettres "
                          "majuscules, comme EUR ou USD.",
        "phrase": "La colonne « {column} » doit respecter le format {pattern}.",
        "params_libelles": {
            "column": "Colonne à vérifier",
            "pattern": "Format attendu (expression régulière)",
        },
        "niveau": "simple",
    },
    "FOREIGN_KEY": {
        "libelle_metier": "Exister dans un référentiel",
        "question_metier": "Quel code doit être reconnu par un référentiel ?",
        "explication": "Le contrôle échoue si un code du fichier est introuvable "
                       "dans la table de référence.",
        "exemple_metier": "Toute devise citée doit figurer au référentiel des devises.",
        "phrase": "Chaque « {column} » doit exister dans le référentiel "
                  "« {ref_dataset} ».",
        "params_libelles": {
            "column": "Colonne du fichier à rattacher",
            "ref_dataset": "Référentiel de rattachement",
            "ref_column": "Colonne du référentiel qui porte le code",
        },
        "niveau": "simple",
    },
    "FRESHNESS": {
        "libelle_metier": "Être suffisamment récent",
        "question_metier": "Quel âge maximal la donnée peut-elle avoir ?",
        "explication": "Le contrôle échoue si la donnée la plus récente est "
                       "trop ancienne.",
        "exemple_metier": "Un fichier mis à jour tous les mois ne doit pas "
                          "dater de plus de 45 jours.",
        "phrase": "La donnée la plus récente de « {column} » doit dater de "
                  "moins de {max_lag_days} jours.",
        "params_libelles": {
            "column": "Colonne de date à surveiller",
            "max_lag_days": "Ancienneté maximale tolérée, en jours",
        },
        "niveau": "simple",
    },
    "DATE_ORDER": {
        "libelle_metier": "Respecter l'ordre chronologique",
        "question_metier": "Quelle date doit venir avant l'autre ?",
        "explication": "Le contrôle échoue si les deux dates sont inversées.",
        "exemple_metier": "Une date d'entrée précède toujours une date de sortie.",
        "phrase": "« {before} » doit être antérieure ou égale à « {after} ».",
        "params_libelles": {
            "before": "Date qui doit venir en premier",
            "after": "Date qui doit venir en second",
        },
        "niveau": "simple",
    },
    "FIELD_EQUALS": {
        "libelle_metier": "Concorder avec une autre colonne",
        "question_metier": "Quelles deux colonnes doivent porter la même valeur ?",
        "explication": "Le contrôle échoue si les deux colonnes divergent.",
        "exemple_metier": "Le total repris en pied de fichier doit égaler "
                          "le total calculé.",
        "phrase": "« {field_a} » et « {field_b} » doivent porter la même valeur.",
        "params_libelles": {
            "field_a": "Première colonne",
            "field_b": "Seconde colonne",
        },
        "niveau": "simple",
    },
    "SUM_RECONCILIATION": {
        "libelle_metier": "Faire un total cohérent",
        "question_metier": "Quel total doit correspondre à la somme de ses parties ?",
        "explication": "Le contrôle compare une ligne de total au cumul de ses "
                       "composantes, et signale les écarts.",
        "exemple_metier": "Le chiffre d'affaires « toutes régions » doit égaler "
                          "la somme des régions.",
        "phrase": "Le total « {total_value} » de « {total_column} » doit "
                  "correspondre à la somme de « {amount} ».",
        "phrase_parties_max": "La somme de « {amount} » ne doit jamais dépasser "
                              "le total « {total_value} ».",
        "phrase_couverture_min": "Le détail de « {amount} » doit couvrir au moins "
                                 "{couverture_min_pct} % du total « {total_value} ».",
        "params_libelles": {
            "amount": "Colonne du montant à cumuler",
            "total_column": "Colonne qui porte la ligne de total",
            "total_value": "Valeur qui désigne le total",
            "group_by": "Dimensions à figer pour comparer",
            "sens": "Ce qui constitue un écart inacceptable",
            "tolerance_pct": "Écart toléré, en %",
            "couverture_min_pct": "Couverture minimale exigée, en %",
        },
        "niveau": "avance",
    },
    "COUNT_RECONCILIATION": {
        "libelle_metier": "Avoir le bon nombre de lignes",
        "question_metier": "À quel fichier le nombre de lignes doit-il correspondre ?",
        "explication": "Le contrôle échoue si les effectifs divergent entre "
                       "les deux fichiers.",
        "exemple_metier": "Après un transfert, le fichier reçu doit contenir "
                          "autant de lignes que le fichier envoyé.",
        "phrase": "Le nombre de lignes doit correspondre à celui de "
                  "« {ref_dataset} ».",
        "params_libelles": {
            "ref_dataset": "Fichier de référence",
            "group_by": "Comparer par sous-ensemble (facultatif)",
        },
        "niveau": "avance",
    },
    "CUSTOM_EXPRESSION": {
        "libelle_metier": "Règle sur mesure",
        "question_metier": "Quelle condition doit être vraie sur chaque ligne ?",
        "explication": "Réservé aux profils techniques : la condition est écrite "
                       "en syntaxe pandas et évaluée ligne à ligne.",
        "exemple_metier": "devise_achat != devise_vente",
        "phrase": "Chaque ligne doit vérifier : {expression}.",
        "params_libelles": {
            "expression": "Condition à vérifier",
            "description_metier": "Traduction en français de la condition",
        },
        "niveau": "avance",
    },
}


def main() -> None:
    st = CatalogueStore()
    inconnus = [t["template_id"] for t in st.templates if t["template_id"] not in LIBELLES]
    manquants = [k for k in LIBELLES if st.template(k) is None]

    for tpl in st.templates:
        libelle = LIBELLES.get(tpl["template_id"])
        if libelle:
            tpl.update(libelle)

    st.save()
    print(f"Langage metier ajoute a {len(st.templates) - len(inconnus)} template(s).")
    if inconnus:
        print(f"  Sans libelle metier (a completer) : {', '.join(inconnus)}")
    if manquants:
        print(f"  Libelles sans template correspondant : {', '.join(manquants)}")
    for tpl in st.templates:
        if "libelle_metier" in tpl:
            print(f"  {tpl['niveau']:<7} {tpl['template_id']:<22} {tpl['libelle_metier']}")


if __name__ == "__main__":
    main()
