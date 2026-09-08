"""
Proposition de controles a partir du seul profil d'un fichier.

Le brief autorise explicitement cette couche (§14, « Frugal by design ») :

  - « Statistical methods : profiling, outlier detection, distribution drift.
    Useful where no fixed threshold can be agreed in advance. »
  - « Suggesting candidate rules from a dataset profile […] Assists the
    analyst, never issues the control verdict. »

Ce module ne decide donc rien et n'ecrit rien au catalogue. Il observe un
fichier, propose des controles candidats, et chiffre ce que chacun attraperait
aujourd'hui. Un humain accepte ou refuse ; le verdict reste au moteur.

Aucun nom de colonne, aucun domaine metier, aucun seuil n'est ecrit ici. Tout
sort de la distribution observee. Un fichier de derives de gre a gre et un
export hospitalier passent par le meme code.

Six dimensions sont couvertes ; la reconciliation ne peut pas l'etre : elle
suppose une seconde source, que seul un humain peut designer.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from profiler import cle_candidate  # noqa: E402

# --------------------------------------------------------------------------- #
# Seuils de proposition. Ce sont des reglages de *suggestion*, jamais de
# verdict : ils decident de ce qu'on montre a l'analyste, pas de ce qui echoue.
# --------------------------------------------------------------------------- #
QUASI_UNIQUE = 0.95        # part de valeurs distinctes -> cle candidate
MAX_MODALITES = 15         # au-dela, une colonne n'est plus un domaine ferme
PART_RARE = 0.005          # une valeur sous 0,5 % des lignes est marginale
COMPTE_MARGINAL = 3        # ... ou vue au plus 3 fois, quel que soit le volume :
                           # en part seule, le seuil serait aveugle aux petits fichiers
PART_ORDRE = 0.90          # part de lignes respectant un ordre pour le proposer
FACTEUR_IQR = 3.0          # Q3 + 3 x IQR : borne haute classique des aberrations
PART_FORME = 0.90          # part de valeurs partageant une meme forme
MIN_LIGNES = 20            # sous ce volume, aucune statistique n'est credible
MAX_NULS_ABSOLUS = 3       # peu de trous en valeur absolue : anomalie probable
PART_ANOMALIE = 0.05       # au-dela, ce n'est plus un defaut isole mais la
                           # forme de la distribution : on se tait
MIN_MODALITES_DOMAINE = 2  # un domaine a une seule valeur ne decrit rien
MAX_PAIRES_DATES = 6

ECHELLE_FRAICHEUR = [7, 30, 90, 180, 365, 730, 1460]

NUMERIQUES = {"integer", "decimal"}


# --------------------------------------------------------------------------- #
# Outils
# --------------------------------------------------------------------------- #
def _colonnes(profil: list[dict], *types: str) -> list[dict]:
    return [c for c in profil if c["type"] in types]


def _forme(valeur: str) -> str:
    """Signature de forme : 'P00026' -> 'A99999'. Sert a reperer un format."""
    return re.sub(r"\d", "9", re.sub(r"[A-Za-z]", "A", str(valeur)))


def _regex_depuis_forme(forme: str) -> str:
    """Traduit une signature de forme en expression reguliere lisible."""
    morceaux, i = [], 0
    while i < len(forme):
        symbole = forme[i]
        longueur = 1
        while i + longueur < len(forme) and forme[i + longueur] == symbole:
            longueur += 1
        if symbole == "A":
            base = "[A-Za-z]"
        elif symbole == "9":
            base = r"\d"
        else:
            base = re.escape(symbole)
        morceaux.append(base if longueur == 1 else f"{base}{{{longueur}}}")
        i += longueur
    return "^" + "".join(morceaux) + "$"


def _seuil_fraicheur(age_jours: int) -> int:
    """Choisit un palier rond au-dessus de l'age observe, avec de la marge."""
    cible = max(age_jours * 1.2, 7)
    for palier in ECHELLE_FRAICHEUR:
        if palier >= cible:
            return palier
    return ECHELLE_FRAICHEUR[-1]


def _dates(df: pd.DataFrame, colonne: str) -> pd.Series:
    return pd.to_datetime(df[colonne], errors="coerce", format="mixed")


# --------------------------------------------------------------------------- #
# Generateurs, un par dimension
# --------------------------------------------------------------------------- #
def _completude(df: pd.DataFrame, profil: list[dict]) -> list[dict]:
    """Une colonne integralement remplie aujourd'hui a vocation a le rester."""
    out = []
    for col in profil:
        if col["taux_nuls_pct"] > 0 or col["valeurs_distinctes"] == 0:
            continue
        out.append({
            "dimension": "Completeness",
            "template": "NOT_NULL",
            "params": {"column": col["colonne"]},
            "control_name": f"Completeness of {col['colonne']}",
            "description": f"Column {col['colonne']} is filled on every observed "
                           f"row; a missing value would signal a break in the "
                           f"feed.",
            "constat": "no empty value today",
            "severity": "Medium",
            "seuil_tolerance_pct": 0.0,
            "remediation_action": "Trace the incomplete row at the source and "
                                  "request a fresh extract.",
        })
    return out


def _completude_partielle(df: pd.DataFrame, profil: list[dict]) -> list[dict]:
    """Une colonne presque pleine : les rares trous sont probablement des defauts."""
    out = []
    for col in profil:
        taux, nuls = col["taux_nuls_pct"], col["valeurs_nulles"]
        if nuls == 0 or not (taux <= 5.0 or nuls <= MAX_NULS_ABSOLUS):
            continue
        out.append({
            "dimension": "Completeness",
            "template": "NOT_NULL",
            "params": {"column": col["colonne"]},
            "control_name": f"Completeness of {col['colonne']}",
            "description": f"Column {col['colonne']} is filled on "
                           f"{100 - taux:.1f} % of rows: the missing "
                           f"values are rare enough to look like defects "
                           f"rather than a business case.",
            "constat": f"{col['valeurs_nulles']} empty value(s), i.e. {taux:g} %",
            "severity": "High",
            "seuil_tolerance_pct": 0.0,
            "remediation_action": "Fill the affected rows at the source.",
        })
    return out


def _unicite(df: pd.DataFrame, profil: list[dict]) -> list[dict]:
    """Cle candidate : presque unique, donc probablement unique par nature.

    Une colonne integralement unique ne prouve rien pour l'avenir ; une colonne
    unique a 99 % est plus interessante encore, car les 1 % restants sont
    justement les doublons a instruire.
    """
    lignes = len(df)
    out = []
    for col in profil:
        if col["type"] not in ("string", "integer"):
            continue
        part = col["valeurs_distinctes"] / lignes if lignes else 0
        if part < QUASI_UNIQUE:
            continue
        constat = ("no duplicate today" if col["unique"] else
                   f"{lignes - col['valeurs_distinctes']} doublon(s) sur {lignes} lignes")
        out.append({
            "dimension": "Uniqueness",
            "template": "UNIQUE_KEY",
            "params": {"columns": [col["colonne"]]},
            "control_name": f"Uniqueness of {col['colonne']}",
            "description": f"{col['colonne']} holds {part:.1%} distinct "
                           f"values: it behaves like a row identifier and should "
                           f"carry no duplicate.",
            "constat": constat,
            "severity": "High",
            "seuil_tolerance_pct": 0.0,
            "remediation_action": "De-duplicate at the source and check the key "
                                  "generation.",
        })
    return out


def _bornes(df: pd.DataFrame, profil: list[dict]) -> list[dict]:
    """Valeurs negatives isolees et aberrations hautes, par la regle de l'IQR."""
    out = []
    for col in _colonnes(profil, *NUMERIQUES):
        nom = col["colonne"]
        valeurs = pd.to_numeric(df[nom], errors="coerce").dropna()
        if len(valeurs) < MIN_LIGNES:
            continue
        q1, q3 = valeurs.quantile(0.25), valeurs.quantile(0.75)
        iqr = q3 - q1
        params: dict = {}
        constats = []

        negatives = int((valeurs < 0).sum())
        if 0 < negatives <= max(1, int(0.05 * len(valeurs))):
            params["min"] = 0
            constats.append(f"{negatives} negative value(s)")
        elif iqr > 0:
            bas = q1 - FACTEUR_IQR * iqr
            sous = int((valeurs < bas).sum())
            if sous:
                params["min"] = float(round(bas, 4))
                constats.append(f"{sous} abnormally low value(s)")

        if iqr > 0:
            haut = q3 + FACTEUR_IQR * iqr
            au_dessus = int((valeurs > haut).sum())
            if au_dessus:
                params["max"] = float(round(haut, 4))
                constats.append(f"{au_dessus} abnormally high value(s)")

        if not params:
            continue
        params["column"] = nom
        out.append({
            "dimension": "Validity",
            "template": "RANGE",
            "params": params,
            "control_name": f"Value range of {nom}",
            "description": f"The distribution of {nom} (median "
                           f"{valeurs.median():g}, écart interquartile {iqr:g}) "
                           f"shows isolated values, beyond three "
                           f"interquartile ranges.",
            "constat": ", ".join(constats),
            "severity": "Medium",
            "seuil_tolerance_pct": 0.0,
            "remediation_action": "Review how the extreme values were keyed; "
                                  "fix or justify each of them.",
        })
    return out


def _domaines(df: pd.DataFrame, profil: list[dict]) -> list[dict]:
    """Colonne a faible cardinalite : liste fermee de valeurs autorisees."""
    lignes = len(df)
    out = []
    for col in _colonnes(profil, "string"):
        nom = col["colonne"]
        if not 1 < col["valeurs_distinctes"] <= MAX_MODALITES or lignes < MIN_LIGNES:
            continue
        comptes = df[nom].value_counts(dropna=True)
        rare = (comptes / lignes < PART_RARE) | (comptes <= COMPTE_MARGINAL)
        frequentes, marginales = comptes[~rare], comptes[rare]
        if len(frequentes) < MIN_MODALITES_DOMAINE:
            continue
        retenues = sorted(str(v) for v in frequentes.index)
        if marginales.empty:
            constat = f"{len(retenues)} distinct value(s), none marginal"
            severite, description = "Low", (
                f"{nom} takes only {len(retenues)} values. Freezing that list "
                f"prevents a new modality from appearing silently.")
        else:
            perdues = ", ".join(str(v) for v in marginales.index[:5])
            constat = (f"{int(marginales.sum())} row(s) outside the "
                       f"{len(retenues)} common values: {perdues}")
            severite, description = "Medium", (
                f"{nom} concentrates on {len(retenues)} values covering "
                f"{frequentes.sum() / lignes:.1%} of rows; the rest is marginal "
                f"enough to look like keying errors.")
        out.append({
            "dimension": "Validity",
            "template": "IN_DOMAIN",
            "params": {"column": nom, "values": retenues},
            "control_name": f"Allowed domain of {nom}",
            "description": description,
            "constat": constat,
            "severity": severite,
            "seuil_tolerance_pct": 0.0,
            "remediation_action": "Map the out-of-list values to a known one, "
                                  "or extend the list if the new value is "
                                  "legitimate.",
        })
    return out


def _formats(df: pd.DataFrame, profil: list[dict]) -> list[dict]:
    """Codes de forme homogene : longueur et alternance lettres/chiffres."""
    out = []
    for col in _colonnes(profil, "string"):
        nom = col["colonne"]
        if col["valeurs_distinctes"] <= MAX_MODALITES:
            continue  # deja couvert, et mieux, par un domaine ferme
        valeurs = df[nom].dropna().astype(str)
        if len(valeurs) < MIN_LIGNES:
            continue
        formes = valeurs.map(_forme).value_counts()
        dominante = formes.index[0]
        part = formes.iloc[0] / len(valeurs)
        if part < PART_FORME or len(dominante) > 40:
            continue
        deviantes = len(valeurs) - int(formes.iloc[0])
        out.append({
            "dimension": "Validity",
            "template": "MATCHES_REGEX",
            "params": {"column": nom, "pattern": _regex_depuis_forme(dominante)},
            "control_name": f"Format of {nom}",
            "description": f"{part:.1%} of the values of {nom} share the same "
                           f"shape. Freezing that format catches upstream "
                           f"convention breaks.",
            "constat": ("uniform format on every row" if not deviantes
                        else f"{deviantes} value(s) with a different shape"),
            "severity": "Low" if not deviantes else "Medium",
            "seuil_tolerance_pct": 0.0,
            "remediation_action": "Fix the format at the source, or document "
                                  "the expected variant.",
        })
    return out


def _fraicheur(df: pd.DataFrame, profil: list[dict], as_of: dt.date) -> list[dict]:
    """Ancienneté de la donnee la plus recente de chaque colonne de date."""
    out = []
    for col in _colonnes(profil, "date"):
        nom = col["colonne"]
        dates = _dates(df, nom).dropna()
        if dates.empty:
            continue
        age = int((pd.Timestamp(as_of) - dates.max()).days)
        seuil = _seuil_fraicheur(max(age, 0))
        out.append({
            "dimension": "Timeliness",
            "template": "FRESHNESS",
            "params": {"column": nom, "max_lag_days": seuil},
            "control_name": f"Freshness of {nom}",
            "description": f"The most recent value of {nom} is {age} day(s) "
                           f"old. The proposed threshold ({seuil} days) leaves "
                           f"room above that observed gap and raises an alert if "
                           f"the feed dries up.",
            "constat": f"most recent value: {dates.max():%Y-%m-%d} "
                       f"({age} day(s))",
            "severity": "Medium",
            "seuil_tolerance_pct": 0.0,
            "remediation_action": "Check that the file feed has not been "
                                  "interrupted.",
        })
    return out


def _chronologie(df: pd.DataFrame, profil: list[dict]) -> list[dict]:
    """Ordre entre deux colonnes de date, deduit de l'ordre observe."""
    colonnes = [c["colonne"] for c in _colonnes(profil, "date")]
    if len(colonnes) < 2:
        return []
    series = {nom: _dates(df, nom) for nom in colonnes}
    out, vues = [], set()
    for avant in colonnes:
        for apres in colonnes:
            if avant == apres or (apres, avant) in vues:
                continue
            a, b = series[avant], series[apres]
            valides = a.notna() & b.notna()
            if valides.sum() < MIN_LIGNES:
                continue
            part = float((a[valides] <= b[valides]).mean())
            if part < PART_ORDRE:
                continue
            vues.add((avant, apres))
            violations = int((~(a[valides] <= b[valides])).sum())
            out.append({
                "dimension": "Consistency",
                "template": "DATE_ORDER",
                "params": {"before": avant, "after": apres},
                "control_name": f"Chronology {avant} → {apres}",
                "description": f"On {part:.1%} of rows, {avant} precedes "
                               f"{apres}. The reverse order betrays a keying or "
                               f"matching error.",
                "constat": ("order respected everywhere" if not violations
                            else f"{violations} row(s) out of order"),
                "severity": "High" if violations else "Low",
                "seuil_tolerance_pct": 0.0,
                "remediation_action": "Fix the reversed dates at the source.",
            })
            if len(out) >= MAX_PAIRES_DATES:
                return out
    return out


# --------------------------------------------------------------------------- #
# Assemblage
# --------------------------------------------------------------------------- #
GENERATEURS = [_completude_partielle, _unicite, _bornes, _domaines,
               _formats, _completude]


def suggerer(df: pd.DataFrame, profil: list[dict], fichier: str,
             as_of: dt.date | None = None, owner: str = "Data Steward",
             chiffrer: bool = True) -> list[dict]:
    """Rend les controles candidats deduits du fichier, les plus utiles d'abord.

    Chaque candidat est une ligne de catalogue complete, prete a etre acceptee.
    `impact` chiffre ce que la regle attraperait aujourd'hui : c'est ce qui
    permet a l'analyste de trancher sans lire une ligne de code.
    """
    as_of = as_of or dt.date.today()
    propositions: list[dict] = []
    for generateur in GENERATEURS:
        propositions += generateur(df, profil)
    propositions += _fraicheur(df, profil, as_of)
    propositions += _chronologie(df, profil)

    for i, p in enumerate(propositions):
        p["cle"] = f"{p['template']}_{i}_{p['params'].get('column', '')}"
        p["dataset_scope"] = fichier
        p["owner"] = owner
        p["frequency"] = "On demand"
        p["output_type"] = "Exception report"
        p["impact"] = _impact(df, profil, p, as_of) if chiffrer else None

    plafond = max(1, int(PART_ANOMALIE * len(df)))
    propositions = [p for p in propositions
                    if p["impact"] is None or p["impact"] <= plafond]

    # Ce qui attrape quelque chose aujourd'hui remonte en premier ; a impact
    # egal, la gravite tranche.
    ordre_severite = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    propositions.sort(key=lambda p: (-(p["impact"] or 0),
                                     ordre_severite.get(p["severity"], 9),
                                     p["control_name"]))
    return propositions


def _impact(df: pd.DataFrame, profil: list[dict], proposition: dict,
            as_of: dt.date) -> int | None:
    """Nombre de lignes que la regle signalerait sur ce fichier, aujourd'hui.

    On reutilise les executeurs du moteur plutot que de reimplementer la
    logique : une suggestion ne peut pas promettre autre chose que ce que
    l'execution produira.
    """
    import dq_engine as moteur

    executeur = moteur.EXECUTORS.get(proposition["template"])
    if executeur is None:
        return None
    params = proposition["params"]
    cibles = moteur.resolve_targets(proposition["template"], params, profil)
    if not cibles:
        return None
    contexte = {"load_ref": lambda _: pd.DataFrame(), "id_column": cle_candidate(profil),
                "as_of": as_of, "store": None, "dataset": ""}
    total = 0
    for cible in cibles:
        try:
            _, ko, _, _, _ = executeur(df, params, cible, contexte)
        except Exception:  # noqa: BLE001 - une suggestion ne doit jamais casser
            return None
        total += int(ko)
    return total


def en_controle(proposition: dict, rule_id: str) -> dict:
    """Traduit une proposition acceptee en ligne de catalogue aux 14 attributs."""
    import json

    return {
        "rule_id": rule_id,
        "control_name": proposition["control_name"],
        "control_type": proposition["dimension"],
        "description": proposition["description"],
        "template": proposition["template"],
        "params": json.dumps(proposition["params"], ensure_ascii=False),
        "logic_definition": "",
        "dataset_scope": proposition["dataset_scope"],
        "data_element": str(proposition["params"].get("column")
                            or ", ".join(proposition["params"].get("columns", []))
                            or f"{proposition['params'].get('before', '')} → "
                               f"{proposition['params'].get('after', '')}"),
        "seuil_tolerance_pct": proposition["seuil_tolerance_pct"],
        "severity": proposition["severity"],
        "frequency": proposition["frequency"],
        "owner": proposition["owner"],
        "output_type": proposition["output_type"],
        "kpi": "",
        "remediation_action": proposition["remediation_action"],
    }
