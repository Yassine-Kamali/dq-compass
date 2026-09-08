"""
DQ Compass engine.

Reads control definitions from the catalogue store and executes them against
any file at all. The engine knows no column name, no threshold and no file
structure: everything comes from the catalogue, and the structure is deduced at
read time by engine/profiler.py.

One run controls ONE file. Nothing is declared beforehand: every active rule
whose scope covers the file is attempted, and a rule whose columns are absent is
reported NON_APPLICABLE with its reason - never as a technical error. Reference
tables are not files the user picks: a reconciliation rule carries its own
source path, which the engine loads and fingerprints.

Execution is a three-phase pipeline, in this order and never merged:
  1. VALIDATE  - reject malformed controls before touching any data
  2. EXECUTE   - run the accepted controls, collect KPIs and exceptions
  3. EVIDENCE  - write a self-contained, replayable evidence pack

Public entry point:
    run_dq("data/prepared/bis_turnover.csv", store=..., run_label="...") -> RunResult
"""
from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import json
import pathlib
import platform
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from profiler import cle_candidate, profiler  # noqa: E402
from store import CatalogueStore, load_store, scope_matches  # noqa: E402

ENGINE_VERSION = "2.1.0"
EVIDENCE_VERSION = "2.0"
ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "evidence"

# Column types the catalogue may declare, grouped by what executors need.
NUMERIC_TYPES = {"integer", "decimal", "float", "number"}
DATE_TYPES = {"date", "datetime", "timestamp"}

# Les quatre issues possibles d'un controle. SKIPPED n'est pas un echec : la
# regle est active mais ses colonnes n'existent pas dans ce fichier. ERROR n'en
# est pas un non plus : aucun verdict n'a pu etre rendu.
STATUTS_RUN = ("PASS", "FAIL", "ERROR", "SKIPPED")

# Les exceptions ne sont plus tronquees : un pack de preuves incomplet n'est pas
# une preuve. Un appelant peut imposer une limite (run_dq(limite_exceptions=N)) ;
# elle est alors tracee controle par controle dans results.json et au manifeste.
MAX_EXCEPTIONS_PER_CONTROL = None


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #
@dataclass
class ControlResult:
    rule_id: str
    control_name: str
    dimension: str
    template: str
    dataset: str
    cible: str
    statut: str                     # PASS / FAIL / ERROR / SKIPPED
    lignes_testees: int
    lignes_ko: int
    taux_ko_pct: float
    kpi_nom: str
    kpi_valeur: float | str
    seuil_pct: float
    severity: str
    owner: str
    frequency: str
    remediation_action: str
    version: int
    duree_s: float
    message: str = ""
    params: str = ""                # parametres exacts appliques (audit)
    exceptions_completes: bool = True

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class RunResult:
    run_id: str
    horodatage: str
    libelle: str
    datasets: list[str]
    resultats: list[ControlResult]
    exceptions: pd.DataFrame
    rejets: list[dict]
    manifeste: dict
    evidence_path: pathlib.Path | None = None
    journal: list[str] = field(default_factory=list)
    fichier: str = ""                # le fichier controle par ce run
    contrat: str = ""                # le contrat de dataset applique

    @property
    def scorecard(self) -> pd.DataFrame:
        if not self.resultats:
            return pd.DataFrame()
        return pd.DataFrame([r.as_dict() for r in self.resultats])

    def summary(self) -> dict:
        sc = self.scorecard
        if sc.empty:
            return {"controls": 0, "pass": 0, "fail": 0, "error": 0,
                    "skipped": 0, "rejected": len(self.rejets), "exceptions": 0,
                    "rag": "GREEN"}
        return {
            "controls": len(sc),
            "pass": int((sc["statut"] == "PASS").sum()),
            "fail": int((sc["statut"] == "FAIL").sum()),
            "error": int((sc["statut"] == "ERROR").sum()),
            "skipped": int((sc["statut"] == "SKIPPED").sum()),
            "rejected": len(self.rejets),
            "exceptions": int(len(self.exceptions)),
            "rag": self.rag,
        }

    @property
    def rag(self) -> str:
        return statut_global(self.resultats)


# --------------------------------------------------------------------------- #
# Statut global du run : un feu tricolore, des regles ecrites une seule fois
# --------------------------------------------------------------------------- #
GRAVITES_BLOQUANTES = {"Critical", "High"}


def statut_global(resultats: list[ControlResult]) -> str:
    """Rend RED / AMBER / GREEN pour l'ensemble d'un run.

    L'ordre des regles est celui du pire cas :
      - une erreur technique est rouge : un controle qui n'a pas rendu de
        verdict ne peut pas etre presume passant ;
      - un echec de gravite Critical ou High est rouge ;
      - tout autre echec est orange ;
      - un controle hors perimetre, sans echec, est orange : la couverture est
        incomplete, ce n'est ni un succes franc ni une alerte ;
      - le reste est vert.
    """
    statuts = [r.statut for r in resultats]
    if "ERROR" in statuts:
        return "RED"
    echecs = [r for r in resultats if r.statut == "FAIL"]
    if any(r.severity in GRAVITES_BLOQUANTES for r in echecs):
        return "RED"
    if echecs:
        return "AMBER"
    if "SKIPPED" in statuts:
        return "AMBER"
    return "GREEN"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_obj(obj: Any) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_params(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw in (None, "", float("nan")):
        return {}
    return json.loads(str(raw))


def required_param_groups(params_requis: str) -> list[list[str]]:
    """'column, min | max' -> [['column'], ['min', 'max']] (at least one per group)."""
    groups = []
    for part in str(params_requis or "").split(","):
        part = part.strip()
        if not part:
            continue
        groups.append([alt.strip() for alt in part.split("|") if alt.strip()])
    return groups


def colonne_du_profil(profil: list[dict], column: str) -> dict | None:
    return next((c for c in profil if c["colonne"] == column), None)


# --------------------------------------------------------------------------- #
# Resolution de cible : une ligne de catalogue peut viser N colonnes reelles
# --------------------------------------------------------------------------- #
def resolve_targets(template: str, params: dict, profil: list[dict]) -> list[list[str]]:
    """Rend la liste des cibles ; chaque cible est une liste de colonnes.

    Une regle designee par `column` produit une cible d'une colonne. Une regle
    designee par `colonnes_motif` produit une cible par colonne dont le nom
    correspond au motif : c'est ce qui permet a une seule ligne de catalogue de
    s'appliquer a des fichiers qui n'existent pas encore, sans rien declarer.
    """
    noms = [c["colonne"] for c in profil]

    def par_motif(motif: str) -> list[str]:
        try:
            regex = re.compile(motif)
        except re.error:
            return []
        return [n for n in noms if regex.search(n)]

    if template == "UNIQUE_KEY":
        if "columns" in params:
            return [list(params["columns"])]
        if "colonnes_motif" in params:
            return [[n] for n in par_motif(params["colonnes_motif"])]
        return []

    if template == "FIELD_EQUALS":
        return [[params["field_a"], params["field_b"]]]

    if template == "DATE_ORDER":
        if "before" in params and "after" in params:
            return [[params["before"], params["after"]]]
        # Seules les colonnes de date sont appariables : sans ce filtre,
        # `order_status` et `payment_status` formeraient une paire parfaite au
        # regard de leurs noms. Un motif est un filet, pas une designation.
        dates = [c["colonne"] for c in profil if c["type"] in DATE_TYPES]
        return apparier(dates, params.get("motif_avant", ""),
                        params.get("motif_apres", ""))

    if template == "ROW_SUM_RECONCILIATION":
        numeriques = {c["colonne"] for c in profil if c["type"] in NUMERIC_TYPES}
        if "total_column" in params:
            totaux = [params["total_column"]]
        else:
            totaux = [n for n in par_motif(params.get("motif_total", ""))
                      if n in numeriques]
        if params.get("columns"):
            parties = list(params["columns"])
        else:
            # Un motif est un filet, pas une designation : ce qu'il ramene de
            # non sommable est ecarte sans faire echouer la regle.
            parties = [n for n in par_motif(params.get("motif_parties", ""))
                       if n in numeriques]
        return [[t] + [p for p in parties if p != t] for t in totaux]
    if template in ("CUSTOM_EXPRESSION", "COUNT_RECONCILIATION"):
        return [[]]
    if template == "SUM_RECONCILIATION":
        return [[params["amount"]]]

    if "column" in params:
        return [[params["column"]]]
    if "colonnes_motif" in params:
        return [[n] for n in par_motif(params["colonnes_motif"])]
    return []


def _radical(nom: str, marqueur: re.Pattern) -> str:
    """Le nom d'une colonne prive du marqueur qui l'a designee."""
    return re.sub(r"[^a-z0-9]", "", marqueur.sub("", nom).lower())


def apparier(noms: list[str], motif_avant: str, motif_apres: str) -> list[list[str]]:
    """Apparie une colonne « avant » a une colonne « apres », sans les nommer.

    `date_debut` et `date_fin` decrivent le meme evenement : leurs noms sont
    identiques une fois le marqueur retire. C'est ce reste - le radical - qui
    fait la paire. Aucun vocabulaire n'est ecrit ici : les deux marqueurs sont
    les motifs portes par la regle, donc le catalogue reste seul a decider ce
    qu'est un debut et ce qu'est une fin.

    A defaut de radical commun, une seule colonne de chaque cote suffit a lever
    l'ambiguite : c'est la seule paire possible.
    """
    if not motif_avant or not motif_apres:
        return []
    try:
        avant, apres = re.compile(motif_avant), re.compile(motif_apres)
    except re.error:
        return []
    debuts = [n for n in noms if avant.search(n)]
    fins = [n for n in noms if apres.search(n) and not avant.search(n)]
    paires = [[d, f] for d in debuts for f in fins
              if d != f and _radical(d, avant) == _radical(f, apres)]
    if not paires and len(debuts) == 1 and len(fins) == 1:
        paires = [[debuts[0], fins[0]]]
    return paires


# --------------------------------------------------------------------------- #
# Phase 1 - Validation. Une regle mal definie est rejetee, jamais executee.
# --------------------------------------------------------------------------- #
def validate_control(control: dict, store: CatalogueStore,
                     root: pathlib.Path = ROOT) -> list[str]:
    """Rejette une regle mal *definie*, independamment de tout fichier.

    Une regle n'est plus validee contre un schema declare : il n'y en a plus.
    Ce qui se verifie ici tient a la regle seule : template connu et implemente,
    parametres requis presents, expressions compilables, fichier de reference
    atteignable. Qu'une colonne existe ou non depend du fichier controle : c'est
    une question d'applicabilite, tranchee a l'execution.
    """
    errors: list[str] = []
    tid = control.get("template")
    tpl = store.template(tid)
    if tpl is None:
        return [f"Unknown template: '{tid}'"]
    if tid not in EXECUTORS:
        return [f"Template '{tid}' declared in the catalogue but not implemented "
                f"by the engine"]

    try:
        params = parse_params(control.get("params"))
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"Unreadable JSON parameters: {exc}"]

    for group in required_param_groups(tpl.get("params_requis", "")):
        if not any(alt in params for alt in group):
            errors.append(f"Required parameter missing: {' | '.join(group)}")
    if errors:
        return errors

    for nom, valeur in params.items():
        if nom == "colonnes_motif" or nom.startswith("motif_"):
            try:
                re.compile(str(valeur))
            except re.error as exc:
                errors.append(f"Invalid column pattern ({nom}): {exc}")

    if tid == "MATCHES_REGEX":
        try:
            re.compile(params["pattern"])
        except re.error as exc:
            errors.append(f"Invalid regular expression: {exc}")

    if tid in ("FOREIGN_KEY", "COUNT_RECONCILIATION"):
        ref = params.get("ref_fichier")
        if not ref:
            errors.append("Reference file not set")
        elif not (pathlib.Path(root) / str(ref)).exists():
            errors.append(f"Reference file not found: '{ref}'")

    return errors


# Mots qui appartiennent au langage de l'expression, pas au fichier.
MOTS_EXPRESSION = {"and", "or", "not", "in", "is", "None", "True", "False",
                   "if", "else", "abs", "len", "str", "int", "float", "index"}


def colonnes_d_expression(expression: str) -> set[str]:
    """Colonnes citees par une expression sur mesure.

    Les litteraux de chaine sont retires d'abord : dans
    `DER_CURR_LEG1 == 'TO1'`, seul le premier nom designe une colonne. Les
    identifiants precedes d'un point sont ignores : ce sont des methodes.
    """
    sans_chaines = re.sub(r"'[^']*'|\"[^\"]*\"", " ", str(expression or ""))
    entre_accents = set(re.findall(r"`([^`]+)`", sans_chaines))
    sans_accents = re.sub(r"`[^`]*`", " ", sans_chaines)
    nus = set(re.findall(r"(?<![\w.])([A-Za-z_]\w*)", sans_accents))
    return (entre_accents | nus) - MOTS_EXPRESSION


def applicabilite(template: str, params: dict, targets: list[list[str]],
                  profil: list[dict]) -> str | None:
    """Rend la raison pour laquelle la regle ne s'applique pas a ce fichier.

    Une regle hors perimetre n'est ni un echec ni une erreur technique. Les
    separer est ce qui rend lisible la couverture de controle : sur 15 regles,
    savoir que 9 ne concernent pas ce fichier n'a rien a voir avec 9 plantages.
    """
    presentes = {c["colonne"] for c in profil}

    if template == "CUSTOM_EXPRESSION":
        manquantes = sorted(colonnes_d_expression(params.get("expression")) - presentes)
        if manquantes:
            return "column(s) absent from the file: " + ", ".join(manquantes)
        return None

    if template == "COUNT_RECONCILIATION":
        manquantes = sorted(set(params.get("group_by") or []) - presentes)
        if manquantes:
            return "column(s) absent from the file: " + ", ".join(manquantes)
        return None

    if template == "DATE_ORDER" and not targets and "motif_avant" in params:
        return (f"no pair of dates matches "
                f"'{params['motif_avant']}' then '{params.get('motif_apres', '')}'")

    if not targets or all(not t for t in targets):
        vise = (params.get("colonnes_motif") or params.get("column")
                or params.get("motif_total") or params.get("total_column")
                or "the target")
        return f"no column in the file matches {vise}"

    for target in targets:
        if template == "ROW_SUM_RECONCILIATION" and len(target) < 2:
            return (f"'{target[0]}' has no detail column to add up in this "
                    f"file")
        for column in target:
            if column not in presentes:
                return f"column absent from the file: '{column}'"
            typ = (colonne_du_profil(profil, column) or {}).get("type", "")
            if (template in ("RANGE", "SUM_RECONCILIATION",
                             "ROW_SUM_RECONCILIATION")
                    and typ not in NUMERIC_TYPES):
                return (f"'{column}' is not numeric in this file "
                        f"(inferred type: {typ})")
            if template in ("FRESHNESS", "DATE_ORDER") and typ not in DATE_TYPES:
                return (f"'{column}' is not a date in this file "
                        f"(inferred type: {typ})")
    return None


# --------------------------------------------------------------------------- #
# Phase 2 - Executors
# Each returns (lignes_testees, lignes_ko, kpi_valeur, kpi_nom, exceptions_df)
# --------------------------------------------------------------------------- #
def _exceptions(df: pd.DataFrame, mask: pd.Series, ctx: dict,
                colonne: str, motif: str, valeur_col: str | None = None) -> pd.DataFrame:
    ko = df.loc[mask]
    if ko.empty:
        return pd.DataFrame()
    limite = ctx.get("limite_exceptions", MAX_EXCEPTIONS_PER_CONTROL)
    if limite and len(ko) > limite:
        # Une troncature n'est jamais silencieuse : elle est remontee au
        # resultat du controle, puis au manifeste du pack de preuves.
        ctx["exceptions_tronquees"] = True
        ko = ko.head(limite)
    id_col = ctx.get("id_column")
    out = pd.DataFrame({
        "identifiant_ligne": ko[id_col] if id_col in ko.columns else ko.index.astype(str),
        "colonne": colonne,
        "valeur": ko[valeur_col].astype(str) if valeur_col and valeur_col in ko.columns else "",
        "motif": motif,
    })
    out.insert(0, "index_source", ko.index)
    return out.reset_index(drop=True)


def ex_not_null(df, params, target, ctx):
    col = target[0]
    s = df[col]
    mask = s.isna() | (s.astype(str).str.strip() == "")
    ko = int(mask.sum())
    n = len(df)
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% completeness", _exceptions(df, mask, ctx, col, "Value missing", col)


def ex_matches_regex(df, params, target, ctx):
    col = target[0]
    pattern = params["pattern"]
    s = df[col].astype(str)
    notna = df[col].notna()
    ok = s.str.match(pattern, na=False)
    mask = notna & ~ok
    n = int(notna.sum())
    ko = int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% valid values", _exceptions(
        df, mask, ctx, col, f"Does not match {pattern}", col)


def ex_in_domain(df, params, target, ctx):
    col = target[0]
    values = set(params["values"])
    notna = df[col].notna()
    mask = notna & ~df[col].isin(values)
    n, ko = int(notna.sum()), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% valid values", _exceptions(
        df, mask, ctx, col, f"Outside the domain {sorted(values)}", col)


def ex_range(df, params, target, ctx):
    col = target[0]
    s = pd.to_numeric(df[col], errors="coerce")
    notna = s.notna()
    mask = pd.Series(False, index=df.index)
    bornes = []
    if "min" in params:
        mask |= notna & (s < float(params["min"]))
        bornes.append(f">= {params['min']}")
    if "max" in params:
        mask |= notna & (s > float(params["max"]))
        bornes.append(f"<= {params['max']}")
    n, ko = int(notna.sum()), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% values in range", _exceptions(
        df, mask, ctx, col, f"Out of range ({' and '.join(bornes)})", col)


def ex_unique_key(df, params, target, ctx):
    mask = df.duplicated(subset=target, keep=False)
    n, ko = len(df), int(mask.sum())
    kpi = round(100.0 * ko / n, 4) if n else 0.0
    return n, ko, kpi, "duplicate rate %", _exceptions(
        df, mask, ctx, " + ".join(target), "Key present more than once", target[0])


def ex_foreign_key(df, params, target, ctx):
    col = target[0]
    ref = ctx["load_ref"](params["ref_fichier"])
    valid = set(ref[params["ref_column"]].dropna().astype(str))
    notna = df[col].notna()
    mask = notna & ~df[col].astype(str).isin(valid)
    n, ko = int(notna.sum()), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    motif = f"Absent from {params['ref_fichier']}.{params['ref_column']}"
    return n, ko, kpi, "% values matched", _exceptions(df, mask, ctx, col, motif, col)


def ex_field_equals(df, params, target, ctx):
    a, b = target
    notna = df[a].notna() & df[b].notna()
    mask = notna & (df[a].astype(str) != df[b].astype(str))
    n, ko = int(notna.sum()), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% consistent rows", _exceptions(
        df, mask, ctx, f"{a} = {b}", "Fields diverge", a)


def ex_date_order(df, params, target, ctx):
    before, after = target
    d1 = pd.to_datetime(df[before], errors="coerce")
    d2 = pd.to_datetime(df[after], errors="coerce")
    notna = d1.notna() & d2.notna()
    mask = notna & (d1 > d2)
    n, ko = int(notna.sum()), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% consistent rows", _exceptions(
        df, mask, ctx, f"{before} <= {after}", "Dates out of order", before)


def ex_freshness(df, params, target, ctx):
    col = target[0]
    dates = pd.to_datetime(df[col], errors="coerce")
    if dates.notna().sum() == 0:
        return 1, 1, -1, "maximum age in days", pd.DataFrame(
            [{"index_source": -1, "identifiant_ligne": "", "colonne": col,
              "valeur": "", "motif": "No usable date"}])
    age = (pd.Timestamp(ctx["as_of"]) - dates.max()).days
    limite = int(params["max_lag_days"])
    ko = 1 if age > limite else 0
    exc = pd.DataFrame()
    if ko:
        exc = pd.DataFrame([{
            "index_source": -1, "identifiant_ligne": f"max({col})",
            "colonne": col, "valeur": str(dates.max().date()),
            "motif": f"Age {age} d > threshold {limite} d"}])
    return 1, ko, age, "maximum age in days", exc


def ex_sum_reconciliation(df, params, target, ctx):
    """Compare a declared total against the sum of its published components.

    `sens` decides what a breach is, because the two directions carry very
    different meanings:
      - parties_max    : components must never EXCEED the total. A breach is an
                         arithmetic error or double counting.
      - couverture_min : components must cover at least N% of the total. A
                         breach means the published breakdown is incomplete.
      - bilateral      : any gap beyond tolerance, in either direction.
    """
    amount = params["amount"]
    total_col, total_val = params["total_column"], params["total_value"]
    group_by = [c for c in params["group_by"] if c in df.columns]
    tolerance = float(params.get("tolerance_pct", 1.0))
    sens = params.get("sens", "bilateral")
    couverture_min = float(params.get("couverture_min_pct", 95.0))

    work = df.copy()
    work[amount] = pd.to_numeric(work[amount], errors="coerce")
    totaux = work[work[total_col] == total_val]
    parties = work[work[total_col] != total_val]
    if totaux.empty or not group_by:
        return 0, 0, 0.0, "relative gap %", pd.DataFrame()

    agg_total = totaux.groupby(group_by, dropna=False)[amount].sum().rename("total_declare")
    agg_parts = parties.groupby(group_by, dropna=False)[amount].sum().rename("somme_composantes")
    comp = pd.concat([agg_total, agg_parts], axis=1, join="inner").reset_index()
    comp["ecart"] = comp["somme_composantes"] - comp["total_declare"]
    denom = comp["total_declare"].abs().replace(0, pd.NA)
    comp["ecart_pct"] = (comp["ecart"] / denom * 100).astype(float).fillna(0.0)
    comp["couverture_pct"] = (
        comp["somme_composantes"] / denom * 100).astype(float).fillna(100.0)

    if sens == "parties_max":
        mask = comp["ecart_pct"] > tolerance
        kpi = round(float(comp["ecart_pct"].max()) if len(comp) else 0.0, 4)
        kpi_nom = "maximum overshoot %"
        motif = comp["ecart_pct"].map(
            lambda v: f"Parts exceed the total by {v:.2f}% (tolerance {tolerance}%)")
    elif sens == "couverture_min":
        mask = comp["couverture_pct"] < couverture_min
        kpi = round(float(comp["couverture_pct"].median()) if len(comp) else 100.0, 4)
        kpi_nom = "median coverage %"
        motif = comp["couverture_pct"].map(
            lambda v: f"Coverage {v:.2f}% < minimum {couverture_min}%")
    else:
        mask = comp["ecart_pct"].abs() > tolerance
        kpi = round(float(comp["ecart_pct"].abs().median()) if len(comp) else 0.0, 4)
        kpi_nom = "median relative gap %"
        motif = comp["ecart_pct"].map(
            lambda v: f"Gap {abs(v):.2f}% > tolerance {tolerance}%")

    n, ko = len(comp), int(mask.sum())
    exc = pd.DataFrame()
    if ko:
        bad = comp.loc[mask].head(MAX_EXCEPTIONS_PER_CONTROL)
        exc = pd.DataFrame({
            "index_source": bad.index,
            "identifiant_ligne": bad[group_by].astype(str).agg("|".join, axis=1),
            "colonne": amount,
            "valeur": bad.apply(
                lambda r: f"total={r['total_declare']:.1f} somme={r['somme_composantes']:.1f}",
                axis=1),
            "motif": motif.loc[mask].head(MAX_EXCEPTIONS_PER_CONTROL),
        }).reset_index(drop=True)
    return n, ko, kpi, kpi_nom, exc


def ex_row_sum_reconciliation(df, params, target, ctx):
    """Rapproche, sur chaque ligne, un total declare et ses composantes.

    C'est la reconciliation interne a un fichier : elle ne suppose aucune
    seconde source, seulement une convention de nommage entre la colonne de
    total et celles du detail. Une ligne dont une composante manque n'est pas
    testee : additionner un trou reviendrait a inventer un ecart.
    """
    total, parties = target[0], target[1:]
    tolerance = float(params.get("tolerance_pct", 0.0) or 0.0)
    declare = pd.to_numeric(df[total], errors="coerce")
    somme = pd.to_numeric(df[parties[0]], errors="coerce")
    for col in parties[1:]:
        somme = somme + pd.to_numeric(df[col], errors="coerce")

    testable = declare.notna() & somme.notna()
    ecart = (declare - somme).abs()
    marge = declare.abs() * tolerance / 100.0
    mask = testable & (ecart > marge + 1e-9)
    n, ko = int(testable.sum()), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    motif = (f"'{total}' differs from the sum of {' + '.join(parties)}"
             + (f" beyond {tolerance:g} %" if tolerance else ""))
    return n, ko, kpi, "% reconciled rows", _exceptions(
        df, mask, ctx, total, motif, total)


def ex_count_reconciliation(df, params, target, ctx):
    ref = ctx["load_ref"](params["ref_fichier"])
    group_by = params.get("group_by")
    if not group_by:
        ecart = abs(len(df) - len(ref))
        ko = 1 if ecart else 0
        exc = pd.DataFrame()
        if ko:
            exc = pd.DataFrame([{
                "index_source": -1, "identifiant_ligne": "total",
                "colonne": "COUNT(*)", "valeur": f"{len(df)} vs {len(ref)}",
                "motif": f"Gap of {ecart} rows"}])
        return 1, ko, ecart, "row count gap", exc

    left = df.groupby(group_by, dropna=False).size().rename("n_source")
    right = ref.groupby(group_by, dropna=False).size().rename("n_ref")
    comp = pd.concat([left, right], axis=1).fillna(0).reset_index()
    comp["ecart"] = (comp["n_source"] - comp["n_ref"]).abs()
    mask = comp["ecart"] > 0
    n, ko = len(comp), int(mask.sum())
    exc = pd.DataFrame()
    if ko:
        bad = comp.loc[mask].head(MAX_EXCEPTIONS_PER_CONTROL)
        exc = pd.DataFrame({
            "index_source": bad.index,
            "identifiant_ligne": bad[group_by].astype(str).agg("|".join, axis=1),
            "colonne": "COUNT(*)",
            "valeur": bad.apply(lambda r: f"{int(r['n_source'])} vs {int(r['n_ref'])}", axis=1),
            "motif": "Effectifs divergents",
        }).reset_index(drop=True)
    return n, ko, int(comp["ecart"].sum()), "row count gap", exc


def ex_custom_expression(df, params, target, ctx):
    expr = params["expression"]
    ok = df.eval(expr, engine="python")
    if not isinstance(ok, pd.Series):
        raise ValueError("L'expression doit produire un booleen par ligne")
    mask = ~ok.fillna(False)
    n, ko = len(df), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% compliant rows", _exceptions(
        df, mask, ctx, expr, f"Expression false: {expr}", ctx.get("id_column"))


EXECUTORS: dict[str, Callable] = {
    "NOT_NULL": ex_not_null,
    "MATCHES_REGEX": ex_matches_regex,
    "IN_DOMAIN": ex_in_domain,
    "RANGE": ex_range,
    "UNIQUE_KEY": ex_unique_key,
    "FOREIGN_KEY": ex_foreign_key,
    "FIELD_EQUALS": ex_field_equals,
    "DATE_ORDER": ex_date_order,
    "FRESHNESS": ex_freshness,
    "SUM_RECONCILIATION": ex_sum_reconciliation,
    "ROW_SUM_RECONCILIATION": ex_row_sum_reconciliation,
    "COUNT_RECONCILIATION": ex_count_reconciliation,
    "CUSTOM_EXPRESSION": ex_custom_expression,
}


# --------------------------------------------------------------------------- #
# Chargement du fichier a controler
#
# Un run porte sur UN fichier. Les referentiels ne sont pas des fichiers a
# choisir : ce sont des tables de support que le moteur va chercher tout seul
# quand un controle d'integrite referentielle en a besoin.
# --------------------------------------------------------------------------- #
FORMATS_SUPPORTES = {".csv", ".txt", ".xlsx", ".xlsm", ".xls"}


def charger_fichier(path: pathlib.Path | str) -> pd.DataFrame:
    """Lit un CSV ou un classeur Excel. Le format vient de l'extension."""
    path = pathlib.Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    suffixe = path.suffix.lower()
    if suffixe not in FORMATS_SUPPORTES:
        raise ValueError(
            f"Format '{suffixe}' is not supported. "
            f"Expected: {', '.join(sorted(FORMATS_SUPPORTES))}")
    if suffixe in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path, low_memory=False)


class SourceLoader:
    """Charge les fichiers lus par un run et les empreinte.

    Toute source touchee - le fichier controle comme les fichiers de reference -
    laisse ici son chemin, son SHA-256, sa taille et sa date. C'est la
    « Dataset Reference : version / snapshot / hash » exigee par l'annexe B.2 du
    brief, et ce qui permet de rejouer un run a l'identique.
    """

    def __init__(self, root: pathlib.Path = ROOT):
        self.root = pathlib.Path(root)
        self._cache: dict[str, pd.DataFrame] = {}
        self.hashes: dict[str, dict] = {}

    def enregistrer(self, nom: str, df: pd.DataFrame, path: pathlib.Path) -> None:
        self._cache[nom] = df
        self.hashes[nom] = {
            "chemin": str(path).replace("\\", "/"),
            "sha256": sha256_file(path),
            "lignes": len(df),
            "colonnes": len(df.columns),
            "modifie_le": dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                timespec="seconds"),
        }

    def load_ref(self, chemin: str) -> pd.DataFrame:
        """Charge un fichier de reference designe par son chemin.

        Le chemin vient des parametres de la regle, pas d'un registre de
        fichiers : une regle de rapprochement porte sa propre source.
        """
        cle = str(chemin)
        if cle in self._cache:
            return self._cache[cle]
        path = self.root / cle
        if not path.exists():
            raise FileNotFoundError(f"Reference file not found: {path}")
        df = charger_fichier(path)
        self.enregistrer(cle, df, path)
        return df


# --------------------------------------------------------------------------- #
# Phase 2/3 - Runner : un fichier, un contrat, un rapport
# --------------------------------------------------------------------------- #
def explication(statut: str, cible: str, n: int, ko: int, taux: float,
                seuil: float, kpi_nom: str, kpi) -> str:
    """Rend le verdict en une phrase chiffree, jamais vide.

    L'audit exige qu'un echec porte son explication : « 12 lignes sur 1 500
    (0,8 %) au-dela d'une tolerance de 0 % » se verifie a la main, « FAIL » non.
    """
    if statut == "SKIPPED":
        return (f"no testable row for '{cible}' (every value is empty "
                f"or unusable)")
    if statut == "FAIL":
        return (f"{ko:,} of {n:,} rows breach the rule on '{cible}' "
                f"({taux:g} %), above the {seuil:g} % tolerance — "
                f"{kpi_nom} = {kpi}").replace(",", " ")
    return (f"{ko:,} of {n:,} rows breach the rule on '{cible}' ({taux:g} %), "
            f"within the {seuil:g} % tolerance — {kpi_nom} = {kpi}"
            ).replace(",", " ")


def en_erreur(control: dict, message: str, dataset: str) -> ControlResult:
    """Une regle qui casse avant meme d'etre executee reste tracee."""
    return ControlResult(
        rule_id=control["rule_id"],
        control_name=control.get("control_name", ""),
        dimension=control.get("control_type", ""),
        template=control.get("template", ""),
        dataset=dataset,
        cible="-",
        statut="ERROR",
        lignes_testees=0,
        lignes_ko=0,
        taux_ko_pct=0.0,
        kpi_nom="-",
        kpi_valeur=-1,
        seuil_pct=float(control.get("seuil_tolerance_pct") or 0),
        severity=control.get("severity", ""),
        owner=control.get("owner", ""),
        frequency=control.get("frequency", ""),
        remediation_action=control.get("remediation_action", ""),
        version=int(control.get("version", 1)),
        duree_s=0.0,
        message=message,
        params=str(control.get("params") or ""),
    )


# Les attributs d'une regle tels qu'ils sont figes dans le pack de preuves :
# la configuration exacte appliquee, parametres et seuils compris (annexe B.2,
# « Rule Configuration » et « Execution Parameters »).
CHAMPS_REGLE_EXECUTEE = [
    "rule_id", "control_name", "control_type", "description", "template",
    "params", "logic_definition", "dataset_scope", "data_element",
    "seuil_tolerance_pct", "severity", "frequency", "owner", "output_type",
    "kpi", "remediation_action", "statut", "version", "effective_from",
]


def regles_executees(store: CatalogueStore, resultats: list[ControlResult],
                     rejets: list[dict]) -> list[dict]:
    """Fige la definition integrale des regles reellement passees par le moteur.

    Le snapshot du catalogue contient TOUT le catalogue ; ce fichier-ci ne
    contient que ce qui a tourne, avec l'empreinte de chaque regle. C'est lui
    qui repond a « quelle regle, avec quels parametres et quel seuil ? » sans
    obliger l'auditeur a trier.
    """
    vues = [r.rule_id for r in resultats] + [r.get("rule_id") for r in rejets]
    ordonnees = list(dict.fromkeys(v for v in vues if v))
    figees = []
    for rule_id in ordonnees:
        ctrl = store.control(rule_id)
        if ctrl is None:
            continue
        regle = {champ: ctrl.get(champ, "") for champ in CHAMPS_REGLE_EXECUTEE}
        regle["params_resolus"] = _params_lisibles(ctrl)
        regle["rule_sha256"] = sha256_obj(regle)
        figees.append(regle)
    return figees


def _params_lisibles(control: dict) -> dict:
    try:
        return parse_params(control.get("params"))
    except (json.JSONDecodeError, ValueError):
        return {}



def run_dq(fichier: pathlib.Path | str,
           dataset: str | None = None,
           store: CatalogueStore | None = None,
           run_label: str = "",
           write_evidence: bool = True,
           as_of: dt.date | None = None,
           root: pathlib.Path = ROOT,
           limite_exceptions: int | None = MAX_EXCEPTIONS_PER_CONTROL) -> RunResult:
    """Controle un fichier unique, quel qu'il soit.

    Aucun contrat n'est requis et aucun n'est detecte : le fichier est profile
    a la lecture, et chaque regle active du catalogue s'applique si les colonnes
    qu'elle vise existent. `dataset` ne sert qu'a nommer le fichier pour le
    ciblage par portee et pour les preuves ; a defaut, le nom du fichier suffit.
    """
    store = store or load_store()
    as_of = as_of or dt.date.today()
    fichier = pathlib.Path(fichier)
    run_id = f"RUN-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    started = time.perf_counter()
    journal: list[str] = []

    def log(msg: str) -> None:
        journal.append(f"{dt.datetime.now():%H:%M:%S} | {msg}")

    log(f"Demarrage {run_id} | moteur {ENGINE_VERSION}")
    log(f"Fichier : {fichier.name}")

    df = charger_fichier(fichier)
    log(f"Charge : {len(df):,} lignes, {len(df.columns)} colonnes")

    profil = profiler(df)
    nom = dataset or fichier.stem
    id_ligne = cle_candidate(profil)
    log(f"Profil deduit : {len(profil)} colonnes | "
        f"identifiant de ligne : {id_ligne or 'index du fichier'}")

    loader = SourceLoader(root)
    loader.enregistrer(nom, df, fichier)
    resultats: list[ControlResult] = []
    rejets: list[dict] = []
    frames: list[pd.DataFrame] = []

    ctx = {
        "load_ref": loader.load_ref,
        "id_column": id_ligne,
        "as_of": as_of,
        "store": store,
        "dataset": nom,
        "limite_exceptions": limite_exceptions,
    }

    applicables = [c for c in store.controls
                   if c.get("statut") == "Actif"
                   and scope_matches(c.get("dataset_scope", ""), nom)]
    log(f"{len(applicables)} controle(s) actif(s) dans la portee de ce fichier")

    def hors_perimetre(control: dict, raison: str) -> ControlResult:
        """Une regle qui ne concerne pas ce fichier est tracee, pas tue."""
        return ControlResult(
            rule_id=control["rule_id"],
            control_name=control.get("control_name", ""),
            dimension=control.get("control_type", ""),
            template=control["template"],
            dataset=nom,
            cible="-",
            statut="SKIPPED",
            lignes_testees=0,
            lignes_ko=0,
            taux_ko_pct=0.0,
            kpi_nom="-",
            kpi_valeur=-1,
            seuil_pct=float(control.get("seuil_tolerance_pct") or 0),
            severity=control.get("severity", ""),
            owner=control.get("owner", ""),
            frequency=control.get("frequency", ""),
            remediation_action=control.get("remediation_action", ""),
            version=int(control.get("version", 1)),
            duree_s=0.0,
            message=raison,
            params=str(control.get("params") or ""),
        )

    for control in applicables:
        errors = validate_control(control, store, root)
        if errors:
            for err in errors:
                rejets.append({"rule_id": control["rule_id"], "dataset": nom,
                               "control_name": control.get("control_name", ""),
                               "motif": err})
            log(f"{control['rule_id']} REJETE : {errors[0]}")
            continue

        # Le diagnostic d'applicabilite est lui aussi execute sous filet : une
        # regle qui casse ici ne doit pas emporter les autres controles du run.
        try:
            params = parse_params(control["params"])
            targets = resolve_targets(control["template"], params, profil)
            raison = applicabilite(control["template"], params, targets, profil)
        except Exception as exc_obj:  # noqa: BLE001 - reporte, jamais avale
            message = f"{type(exc_obj).__name__}: {exc_obj}"
            resultats.append(en_erreur(control, message, nom))
            log(f"{control['rule_id']} ERROR (resolution de cible) : {message}")
            continue

        if raison:
            resultats.append(hors_perimetre(control, raison))
            log(f"{control['rule_id']} SKIPPED : {raison}")
            continue

        executor = EXECUTORS[control["template"]]

        for target in targets:
            t0 = time.perf_counter()
            cible = " + ".join(target) if target else params.get("expression", "-")
            ctx.pop("exceptions_tronquees", None)
            try:
                n, ko, kpi, kpi_nom, exc = executor(df, params, target, ctx)
                seuil = float(control.get("seuil_tolerance_pct") or 0)
                taux = round(100.0 * ko / n, 4) if n else 0.0
                statut = "SKIPPED" if n == 0 else ("PASS" if taux <= seuil else "FAIL")
                message = explication(statut, cible, n, ko, taux, seuil,
                                      kpi_nom, kpi)
            except Exception as exc_obj:  # noqa: BLE001 - reporte, jamais avale
                n = ko = 0
                kpi, kpi_nom, taux, seuil = -1, "-", 0.0, 0.0
                statut, message = "ERROR", f"{type(exc_obj).__name__}: {exc_obj}"
                exc = pd.DataFrame()
                log(f"{control['rule_id']} ERROR : {message}")
            completes = not ctx.pop("exceptions_tronquees", False)

            if not exc.empty:
                exc.insert(0, "dataset", nom)
                exc.insert(0, "rule_id", control["rule_id"])
                exc.insert(2, "severity", control.get("severity", ""))
                frames.append(exc)

            resultats.append(ControlResult(
                rule_id=control["rule_id"],
                control_name=control.get("control_name", ""),
                dimension=control.get("control_type", ""),
                template=control["template"],
                dataset=nom,
                cible=cible,
                statut=statut,
                lignes_testees=int(n),
                lignes_ko=int(ko),
                taux_ko_pct=taux,
                kpi_nom=kpi_nom,
                kpi_valeur=kpi,
                seuil_pct=float(control.get("seuil_tolerance_pct") or 0),
                severity=control.get("severity", ""),
                owner=control.get("owner", ""),
                frequency=control.get("frequency", ""),
                remediation_action=control.get("remediation_action", ""),
                version=int(control.get("version", 1)),
                duree_s=round(time.perf_counter() - t0, 3),
                message=message,
                params=str(control.get("params") or ""),
                exceptions_completes=completes,
            ))
            log(f"{control['rule_id']} {cible[:40]:<40} {statut:<14} "
                f"{kpi_nom}={kpi} ({ko}/{n} KO)")

    exceptions = (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["rule_id", "dataset", "severity", "index_source",
                 "identifiant_ligne", "colonne", "valeur", "motif"]))

    catalogue_snapshot = {
        "meta": store.data.get("meta", {}),
        "templates": store.templates,
        "controls": store.controls,
    }
    executees = regles_executees(store, resultats, rejets)
    tronques = [r.rule_id for r in resultats if not r.exceptions_completes]
    manifeste = {
        "evidence_version": EVIDENCE_VERSION,
        "run_id": run_id,
        "libelle": run_label,
        "horodatage": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "statut_global": statut_global(resultats),
        "moteur_version": ENGINE_VERSION,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "machine": platform.node(),
        "utilisateur_systeme": getpass.getuser(),
        "fichier_controle": str(fichier).replace("\\", "/"),
        "fichier_nom": nom,
        "profil_fichier": profil,
        "identifiant_de_ligne": id_ligne,
        "sources": loader.hashes,
        "catalogue_sha256": sha256_obj(catalogue_snapshot),
        "regles_executees_sha256": sha256_obj(executees),
        "regles_executees": len(executees),
        "controles_actifs": len(store.active_controls()),
        "limite_exceptions": limite_exceptions,
        "exceptions_completes": not tronques,
        "exceptions_tronquees_pour": tronques,
        "duree_totale_s": round(time.perf_counter() - started, 3),
        "rejeu": ("engine/dq_engine.py --replay evidence/" + run_id),
    }

    result = RunResult(
        run_id=run_id,
        horodatage=manifeste["horodatage"],
        libelle=run_label,
        datasets=[nom],
        resultats=resultats,
        exceptions=exceptions,
        rejets=rejets,
        manifeste=manifeste,
        journal=journal,
        fichier=str(fichier),
        contrat=nom,
    )
    log(f"Termine en {manifeste['duree_totale_s']}s | {result.summary()}")

    if write_evidence:
        # Le journal est clos AVANT l'ecriture du pack : sans cela, la derniere
        # ligne arriverait apres le calcul de son empreinte, et `checksums.json`
        # signalerait un journal falsifie a chaque run.
        log(f"Evidence pack : {EVIDENCE_DIR / run_id}")
        result.evidence_path = write_evidence_pack(result, catalogue_snapshot,
                                                   executees)
    return result


def resume_du_run(result: RunResult) -> dict:
    """Le « Control Results » de l'annexe B.2 : metriques et KPI, par controle."""
    resume = result.summary()
    return {
        "run_id": result.run_id,
        "horodatage": result.horodatage,
        "fichier": result.manifeste.get("fichier_nom", ""),
        "statut_global": resume.get("rag", statut_global(result.resultats)),
        "totaux": resume,
        "par_dimension": _compter(result.resultats, lambda r: r.dimension),
        "par_severite": _compter(result.resultats, lambda r: r.severity),
        "controles": [
            {"rule_id": r.rule_id, "control_name": r.control_name,
             "cible": r.cible, "statut": r.statut, "severity": r.severity,
             "seuil_pct": r.seuil_pct, "taux_ko_pct": r.taux_ko_pct,
             "lignes_testees": r.lignes_testees, "lignes_ko": r.lignes_ko,
             "kpi_nom": r.kpi_nom, "kpi_valeur": r.kpi_valeur,
             "message": r.message, "version": r.version,
             "exceptions_completes": r.exceptions_completes}
            for r in result.resultats],
    }


def _compter(resultats: list[ControlResult], cle) -> dict:
    compte: dict[str, dict[str, int]] = {}
    for r in resultats:
        ligne = compte.setdefault(str(cle(r)), {s: 0 for s in STATUTS_RUN})
        ligne[r.statut] = ligne.get(r.statut, 0) + 1
    return compte


def write_evidence_pack(result: RunResult, catalogue_snapshot: dict,
                        executees: list[dict] | None = None) -> pathlib.Path:
    """Ecrit les dix pieces de l'annexe B.2, puis leurs empreintes.

    `checksums.json` est ecrit en dernier et couvre tous les autres fichiers :
    il permet a un auditeur de detecter la modification d'une piece apres coup,
    sans rien connaitre du moteur.
    """
    out = EVIDENCE_DIR / result.run_id
    out.mkdir(parents=True, exist_ok=True)

    def ecrire(nom: str, contenu) -> None:
        (out / nom).write_text(
            json.dumps(contenu, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")

    ecrire("manifest.json", result.manifeste)
    ecrire("catalogue_snapshot.json", catalogue_snapshot)
    ecrire("executed_rules.json", executees or [])
    ecrire("results.json", [r.as_dict() for r in result.resultats])
    ecrire("summary.json", resume_du_run(result))
    ecrire("rejected_rules.json", result.rejets)
    result.exceptions.to_csv(out / "exceptions.csv", index=False,
                             encoding="utf-8-sig")
    (out / "execution.log").write_text("\n".join(result.journal), encoding="utf-8")

    ecrire("checksums.json", {
        "sha256": {f.name: sha256_file(f) for f in sorted(out.iterdir())
                   if f.is_file() and f.name != "checksums.json"},
        "genere_le": dt.datetime.now().isoformat(timespec="seconds"),
    })
    return out


# --------------------------------------------------------------------------- #
# Phase 4 - Audit : verifier un pack, puis le rejouer
#
# Ces deux fonctions sont ecrites pour un tiers : elles ne supposent rien de la
# session qui a produit le pack, seulement les fichiers presents sur disque.
# --------------------------------------------------------------------------- #
PIECES_OBLIGATOIRES = [
    "manifest.json", "catalogue_snapshot.json", "executed_rules.json",
    "results.json", "summary.json", "exceptions.csv", "execution.log",
    "checksums.json",
]


def _lire_json(chemin: pathlib.Path):
    return json.loads(chemin.read_text(encoding="utf-8"))


def verifier_pack(pack: pathlib.Path | str, root: pathlib.Path = ROOT) -> dict:
    """Verifie qu'un pack de preuves est complet, intact et coherent.

    Sept controles, dans l'ordre ou un auditeur les poserait : les pieces
    sont-elles la, n'ont-elles pas bouge, le fichier controle est-il toujours
    celui qui a ete lu, les regles figees sont-elles bien celles qui ont tourne,
    et les resultats concordent-ils avec les exceptions livrees.

    Rend un rapport structure ; ne leve jamais.
    """
    pack = pathlib.Path(pack)
    controles: list[dict] = []

    def noter(nom: str, ok: bool | None, detail: str) -> None:
        controles.append({"controle": nom, "statut": "OK" if ok else
                          ("N/A" if ok is None else "GAP"), "detail": detail})

    manquantes = [p for p in PIECES_OBLIGATOIRES if not (pack / p).exists()]
    noter("Mandatory components (appendix B.2)", not manquantes,
          "all required files are present" if not manquantes
          else "missing: " + ", ".join(manquantes))
    if not (pack / "manifest.json").exists():
        return {"pack": pack.name, "verdict": "GAP", "controles": controles}

    manifeste = _lire_json(pack / "manifest.json")

    # 1. Integrite interne : les pieces n'ont pas ete modifiees apres coup.
    if (pack / "checksums.json").exists():
        attendus = _lire_json(pack / "checksums.json").get("sha256", {})
        alteres = [nom for nom, empreinte in attendus.items()
                   if not (pack / nom).exists()
                   or sha256_file(pack / nom) != empreinte]
        noter("Integrity of the pack files", not alteres,
              f"{len(attendus)} file(s) checked, none altered" if not alteres
              else "fingerprint differs: " + ", ".join(alteres))
    else:
        noter("Integrity of the pack files", None, "checksums.json missing")

    # 2. Le fichier controle est-il toujours celui qui a ete lu ?
    for nom, source in (manifeste.get("sources") or {}).items():
        chemin = pathlib.Path(source.get("chemin", ""))
        if not chemin.is_absolute():
            chemin = root / chemin
        if not chemin.exists():
            noter(f"Input dataset « {nom} »", None,
                  f"file no longer on disk: {source.get('chemin')}")
        else:
            reel = sha256_file(chemin)
            noter(f"Input dataset « {nom} »", reel == source.get("sha256"),
                  f"SHA-256 matches ({reel[:16]}…)" if reel == source.get("sha256")
                  else f"the file changed since the run ({reel[:16]}… "
                       f"instead of {str(source.get('sha256'))[:16]}…)")

    # 3. Le catalogue fige est-il bien celui dont l'empreinte est au manifeste ?
    if (pack / "catalogue_snapshot.json").exists():
        reel = sha256_obj(_lire_json(pack / "catalogue_snapshot.json"))
        noter("Catalogue fingerprint", reel == manifeste.get("catalogue_sha256"),
              "the snapshot matches the fingerprint in the manifest" if
              reel == manifeste.get("catalogue_sha256") else
              "the snapshot does not match the declared fingerprint")

    # 4. Les regles figees sont-elles celles qui ont tourne ?
    if (pack / "executed_rules.json").exists():
        executees = _lire_json(pack / "executed_rules.json")
        reel = sha256_obj(executees)
        noter("Executed rules fingerprint",
              reel == manifeste.get("regles_executees_sha256"),
              f"{len(executees)} rule(s) frozen, fingerprint matches" if
              reel == manifeste.get("regles_executees_sha256") else
              "the frozen rules do not match the declared fingerprint")
        if (pack / "results.json").exists():
            resultats = _lire_json(pack / "results.json")
            connues = {r["rule_id"] for r in executees}
            orphelines = sorted({r["rule_id"] for r in resultats} - connues)
            noter("Traceability rule -> result", not orphelines,
                  "every result maps back to a frozen rule" if not orphelines
                  else "results with no frozen rule: " + ", ".join(orphelines))

    # 5. Les exceptions livrees correspondent-elles aux resultats ?
    if (pack / "results.json").exists() and (pack / "exceptions.csv").exists():
        resultats = _lire_json(pack / "results.json")
        try:
            exceptions = pd.read_csv(pack / "exceptions.csv")
        except (pd.errors.EmptyDataError, OSError):
            exceptions = pd.DataFrame(columns=["rule_id"])
        complet = all(r.get("exceptions_completes", True) for r in resultats)
        echecs = {r["rule_id"] for r in resultats if r["statut"] == "FAIL"}
        documentes = set(exceptions["rule_id"]) if len(exceptions) else set()
        sans_preuve = sorted(echecs - documentes)
        noter("Exception dataset complete", complet,
              f"{len(exceptions)} row(s) in breach, nothing truncated" if complet
              else "exceptions were truncated for: "
                   + ", ".join(manifeste.get("exceptions_tronquees_pour", [])))
        noter("Every breach carries its rows", not sans_preuve,
              "every failed control has its rows in the exception report"
              if not sans_preuve else
              "breaches with no exception row: " + ", ".join(sans_preuve))

    # 6. Chaque verdict porte-t-il une explication exploitable ?
    if (pack / "results.json").exists():
        resultats = _lire_json(pack / "results.json")
        muets = [r["rule_id"] for r in resultats
                 if r["statut"] in ("FAIL", "SKIPPED", "ERROR")
                 and not str(r.get("message") or "").strip()]
        noter("Explanation of every outcome", not muets,
              "every FAIL, SKIPPED and ERROR carries its reason" if not muets
              else "no explanation for: " + ", ".join(muets))

    verdict = ("GAP" if any(c["statut"] == "GAP" for c in controles)
               else "VERIFIED")
    return {"pack": pack.name, "verdict": verdict,
            "run_id": manifeste.get("run_id", pack.name),
            "statut_global": manifeste.get("statut_global", ""),
            "controles": controles}


def store_du_snapshot(snapshot: dict) -> CatalogueStore:
    """Reconstruit un catalogue en memoire depuis un snapshot de pack.

    Rejouer un run avec le catalogue d'aujourd'hui ne prouverait rien : c'est
    la definition figee au moment du run qu'il faut reappliquer.
    """
    store = CatalogueStore(EVIDENCE_DIR / "_snapshot_en_memoire.json")
    store.data = {"meta": snapshot.get("meta", {}),
                  "templates": snapshot.get("templates", []),
                  "controls": snapshot.get("controls", []),
                  "changelog": []}
    return store


CHAMPS_COMPARES = ["statut", "lignes_testees", "lignes_ko", "taux_ko_pct",
                   "kpi_valeur"]


def rejouer_pack(pack: pathlib.Path | str, root: pathlib.Path = ROOT) -> dict:
    """Rejoue un run a partir de son seul pack, et compare les resultats.

    Le rejeu reprend le fichier d'origine, le catalogue fige et la date de
    reference du run. Deux executions identiques doivent rendre exactement les
    memes verdicts, les memes volumes et les memes KPI ; seuls le `run_id` et
    l'horodatage different, par construction.
    """
    pack = pathlib.Path(pack)
    manifeste = _lire_json(pack / "manifest.json")
    chemin = pathlib.Path(manifeste["fichier_controle"])
    if not chemin.is_absolute():
        chemin = root / chemin
    if not chemin.exists():
        return {"rejouable": False,
                "motif": f"the original file cannot be found: {chemin}"}

    empreinte = sha256_file(chemin)
    attendue = (manifeste.get("sources", {})
                .get(manifeste.get("fichier_nom", ""), {}).get("sha256"))
    if attendue and empreinte != attendue:
        return {"rejouable": False,
                "motif": ("the original file changed since the run: "
                          f"{empreinte[:16]}… instead of {attendue[:16]}…")}

    store = store_du_snapshot(_lire_json(pack / "catalogue_snapshot.json"))
    rejeu = run_dq(chemin, dataset=manifeste.get("fichier_nom"), store=store,
                   run_label=f"Replay of {manifeste.get('run_id')}",
                   write_evidence=False,
                   as_of=dt.date.fromisoformat(manifeste["as_of"]),
                   root=root,
                   limite_exceptions=manifeste.get("limite_exceptions"))

    avant = {(r["rule_id"], r["cible"]): r for r in _lire_json(pack / "results.json")}
    apres = {(r.rule_id, r.cible): r.as_dict() for r in rejeu.resultats}
    differences = []
    for cle in sorted(set(avant) | set(apres), key=str):
        a, b = avant.get(cle), apres.get(cle)
        if a is None or b is None:
            differences.append({"controle": " · ".join(cle),
                                "champ": "presence",
                                "origine": "missing" if a is None else "present",
                                "rejeu": "missing" if b is None else "present"})
            continue
        for champ in CHAMPS_COMPARES:
            if str(a.get(champ)) != str(b.get(champ)):
                differences.append({"controle": " · ".join(cle), "champ": champ,
                                    "origine": a.get(champ), "rejeu": b.get(champ)})
    return {
        "rejouable": True,
        "identique": not differences,
        "controles_compares": len(set(avant) | set(apres)),
        "differences": differences,
        "statut_global_origine": manifeste.get("statut_global", ""),
        "statut_global_rejeu": rejeu.rag,
        "exceptions_origine": int(len(pd.read_csv(pack / "exceptions.csv"))
                                  if (pack / "exceptions.csv").stat().st_size > 3
                                  else 0),
        "exceptions_rejeu": int(len(rejeu.exceptions)),
    }


def main(argv: list[str]) -> int:
    if len(argv) > 2 and argv[1] == "--verify":
        print(json.dumps(verifier_pack(argv[2]), ensure_ascii=False, indent=2))
        return 0
    if len(argv) > 2 and argv[1] == "--replay":
        print(json.dumps(rejouer_pack(argv[2]), ensure_ascii=False, indent=2))
        return 0
    fichier = argv[1] if len(argv) > 1 else "data/prepared/bis_turnover.csv"
    nom = argv[2] if len(argv) > 2 else None
    res = run_dq(fichier, dataset=nom, run_label="Execution en ligne de commande")
    print("\n".join(res.journal))
    print("\n--- SYNTHESE ---")
    print(json.dumps(res.summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
