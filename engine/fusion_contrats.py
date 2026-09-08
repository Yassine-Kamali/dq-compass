"""
Fusionne les contrats bis_turnover et bis_turnover_demo.

Sous le modele "un run = un fichier", un contrat decrit une STRUCTURE, pas un
fichier. Les deux contrats declaraient exactement les memes colonnes : garder
les deux obligeait a choisir entre deux entrees identiques et faussait la
detection automatique. Le fichier de demonstration est simplement un autre
fichier qui respecte le meme contrat.

Toutes les modifications passent par le store, donc par le journal d'audit.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from store import CatalogueStore  # noqa: E402

USER = "data.steward"
ANCIEN, GARDE = "bis_turnover_demo", "bis_turnover"
MOTIF = ("Modele 'un run = un fichier' : un contrat decrit une structure, pas un "
         "fichier. bis_turnover_demo declarait les memes colonnes que "
         "bis_turnover ; les deux entrees rendaient la detection automatique "
         "ambigue. L'extrait de demonstration respecte desormais le contrat "
         "bis_turnover, comme le fichier complet.")


def main() -> None:
    st = CatalogueStore()

    if st.dataset(ANCIEN) is None:
        print(f"Contrat '{ANCIEN}' deja absent, rien a faire.")
        return

    touches = []
    for control in st.controls:
        scope = str(control.get("dataset_scope", ""))
        if ANCIEN not in scope:
            continue
        restants = [s.strip() for s in scope.split(",")
                    if s.strip() and s.strip() != ANCIEN]
        if GARDE not in restants:
            restants.append(GARDE)
        st.update_control(control["rule_id"], {"dataset_scope": ",".join(restants)},
                          USER, MOTIF)
        touches.append(control["rule_id"])

    st.datasets.pop(ANCIEN)
    st._journal("DATASET_FUSION", ANCIEN, "contrat", ANCIEN, GARDE, USER, MOTIF)
    st.save()

    print(f"Contrat '{ANCIEN}' fusionne dans '{GARDE}'.")
    print(f"Perimetre mis a jour sur {len(touches)} controle(s) : {', '.join(touches)}")
    print(f"Contrats restants : {', '.join(st.datasets)}")


if __name__ == "__main__":
    main()
