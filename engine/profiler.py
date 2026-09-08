"""
Profilage automatique d'un fichier.

Le brief impose deux choses en apparence contradictoires :

  - la couche de controle doit etre *plug-and-play*, applicable a n'importe
    quel fichier, « configurable with minimal effort » (§2.1, §3.2) ;
  - chaque execution doit produire une « Dataset Reference : version /
    snapshot / hash of input dataset » dans son evidence pack (annexe B.2).

La conciliation tient en une phrase : le schema n'est plus declare, il est
*derive*. Personne ne saisit la structure d'un fichier ; le moteur la deduit
a la lecture, et ce profil devient la piece d'audit qui decrit l'entree.

Aucun nom de colonne, aucun type, aucun domaine metier n'est ecrit ici.
"""
from __future__ import annotations

import pandas as pd

# Types deduits, alignes sur ce que les executeurs savent exiger.
TYPE_ENTIER = "integer"
TYPE_DECIMAL = "decimal"
TYPE_DATE = "date"
TYPE_TEXTE = "string"

ECHANTILLON = 500


def _est_date(serie: pd.Series) -> bool:
    """Vrai si la colonne se lit comme une date.

    On exige un separateur de date dans *toutes* les valeurs testees avant de
    tenter la conversion : sans ce garde-fou, une colonne d'annees (1986, 2022)
    serait convertie sans broncher et declaree « date ».
    """
    echantillon = serie.dropna().astype(str).head(ECHANTILLON)
    if echantillon.empty:
        return False
    if not echantillon.str.contains(r"[-/]").all():
        return False
    converti = pd.to_datetime(echantillon, errors="coerce", format="mixed")
    return bool(converti.notna().mean() >= 0.95)


def deduire_type(serie: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(serie):
        return TYPE_TEXTE
    if pd.api.types.is_integer_dtype(serie):
        return TYPE_ENTIER
    if pd.api.types.is_float_dtype(serie):
        # Un flottant reste un decimal, meme si toutes ses valeurs sont rondes :
        # deviner l'inverse ferait passer une colonne de montants pour des
        # entiers. Les deux types sont numeriques, la distinction n'engage que
        # la lisibilite du profil - autant qu'elle ne mente pas.
        return TYPE_DECIMAL
    if pd.api.types.is_datetime64_any_dtype(serie):
        return TYPE_DATE
    if _est_date(serie):
        return TYPE_DATE
    # Colonne texte qui ne contient que des nombres : frequent apres un
    # read_csv sur une colonne trouee.
    valeurs = serie.dropna()
    if not valeurs.empty:
        numerique = pd.to_numeric(valeurs, errors="coerce")
        if numerique.notna().all():
            return TYPE_ENTIER if (numerique % 1 == 0).all() else TYPE_DECIMAL
    return TYPE_TEXTE


def profiler(df: pd.DataFrame) -> list[dict]:
    """Rend le profil du fichier : une ligne par colonne.

    `unique` vaut True quand la colonne est integralement renseignee et sans
    doublon : c'est une cle candidate, pas une cle metier. Le moteur s'en sert
    pour identifier les lignes en exception, jamais pour decider ce qui doit
    etre unique — cette decision appartient a une regle du catalogue.
    """
    lignes = len(df)
    profil: list[dict] = []
    for nom in df.columns:
        serie = df[nom]
        nuls = int(serie.isna().sum())
        profil.append({
            "colonne": str(nom),
            "type": deduire_type(serie),
            "taux_nuls_pct": round(100.0 * nuls / lignes, 4) if lignes else 0.0,
            "valeurs_nulles": nuls,
            "valeurs_distinctes": int(serie.nunique(dropna=True)),
            "unique": bool(nuls == 0 and serie.is_unique),
        })
    return profil


def colonnes(profil: list[dict]) -> list[str]:
    return [c["colonne"] for c in profil]


def type_de(profil: list[dict], colonne: str) -> str | None:
    for c in profil:
        if c["colonne"] == colonne:
            return c["type"]
    return None


def cle_candidate(profil: list[dict]) -> str | None:
    """Premiere colonne integralement unique : sert d'identifiant de ligne
    dans les rapports d'exception. Aucune portee metier."""
    return next((c["colonne"] for c in profil if c["unique"]), None)
