"""
DQ Compass engine.

Reads control definitions from the catalogue store and executes them against
any declared dataset. The engine knows no column name, no threshold and no
dataset: everything comes from the catalogue.

Execution is a three-phase pipeline, in this order and never merged:
  1. VALIDATE  - reject malformed controls before touching any data
  2. EXECUTE   - run the accepted controls, collect KPIs and exceptions
  3. EVIDENCE  - write a self-contained, replayable evidence pack

Public entry point:
    run_dq(datasets=["bis_turnover"], store=..., run_label="...") -> RunResult
"""
from __future__ import annotations

import datetime as dt
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
from store import CatalogueStore, load_store, scope_matches  # noqa: E402

ENGINE_VERSION = "2.0.0"
ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "evidence"

# Column types the catalogue may declare, grouped by what executors need.
NUMERIC_TYPES = {"integer", "decimal", "float", "number"}
DATE_TYPES = {"date", "datetime", "timestamp"}

MAX_EXCEPTIONS_PER_CONTROL = 5000


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
    statut: str                     # PASS / FAIL / ERREUR / NON_APPLICABLE
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

    @property
    def scorecard(self) -> pd.DataFrame:
        if not self.resultats:
            return pd.DataFrame()
        return pd.DataFrame([r.as_dict() for r in self.resultats])

    def summary(self) -> dict:
        sc = self.scorecard
        if sc.empty:
            return {"controles": 0, "pass": 0, "fail": 0, "erreur": 0}
        return {
            "controles": len(sc),
            "pass": int((sc["statut"] == "PASS").sum()),
            "fail": int((sc["statut"] == "FAIL").sum()),
            "erreur": int((sc["statut"] == "ERREUR").sum()),
            "non_applicable": int((sc["statut"] == "NON_APPLICABLE").sum()),
            "rejets": len(self.rejets),
            "exceptions": int(len(self.exceptions)),
        }


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


def column_meta(store: CatalogueStore, dataset: str, column: str) -> dict | None:
    return next((c for c in store.columns_of(dataset) if c["colonne"] == column), None)


# --------------------------------------------------------------------------- #
# Target resolution: one catalogue row can address N real columns
# --------------------------------------------------------------------------- #
def resolve_targets(template: str, params: dict, store: CatalogueStore,
                    dataset: str) -> list[list[str]]:
    """Return a list of targets; each target is a list of column names.

    A control addressed by `column` yields one single-column target.
    A control addressed by `role` yields one target per matching column,
    which is what makes a single catalogue row apply to any future dataset.
    """
    cols = store.columns_of(dataset)

    if template == "UNIQUE_KEY":
        if "columns" in params:
            return [list(params["columns"])]
        role = params.get("role")
        if role == "cle_primaire":
            pk = [c["colonne"] for c in cols if str(c.get("cle_primaire", "")).upper() == "OUI"]
            return [pk] if pk else []
        matched = [c["colonne"] for c in cols if c.get("role") == role]
        return [matched] if matched else []

    if template == "FIELD_EQUALS":
        return [[params["field_a"], params["field_b"]]]
    if template == "DATE_ORDER":
        return [[params["before"], params["after"]]]
    if template in ("CUSTOM_EXPRESSION", "COUNT_RECONCILIATION"):
        return [[]]
    if template == "SUM_RECONCILIATION":
        return [[params["amount"]]]

    if "column" in params:
        return [[params["column"]]]
    if "role" in params:
        role = params["role"]
        if role == "cle_primaire":
            return [[c["colonne"]] for c in cols
                    if str(c.get("cle_primaire", "")).upper() == "OUI"]
        return [[c["colonne"]] for c in cols if c.get("role") == role]
    return []


# --------------------------------------------------------------------------- #
# Phase 1 - Validation. A malformed control is rejected, never executed.
# --------------------------------------------------------------------------- #
def validate_control(control: dict, store: CatalogueStore, dataset: str) -> list[str]:
    errors: list[str] = []
    tid = control.get("template")
    tpl = store.template(tid)
    if tpl is None:
        return [f"Template inconnu : '{tid}'"]
    if tid not in EXECUTORS:
        return [f"Template '{tid}' declare au catalogue mais non implemente par le moteur"]

    try:
        params = parse_params(control.get("params"))
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"Parametres JSON illisibles : {exc}"]

    for group in required_param_groups(tpl.get("params_requis", "")):
        if not any(alt in params for alt in group):
            errors.append(f"Parametre requis manquant : {' | '.join(group)}")
    if errors:
        return errors

    declared = {c["colonne"] for c in store.columns_of(dataset)}
    if not declared:
        return [f"Dataset '{dataset}' absent du contrat (onglet DATASETS)"]

    try:
        targets = resolve_targets(tid, params, store, dataset)
    except KeyError as exc:
        return [f"Parametre manquant pour la resolution de cible : {exc}"]

    if not targets or all(len(t) == 0 for t in targets) and tid not in (
            "CUSTOM_EXPRESSION", "COUNT_RECONCILIATION"):
        errors.append(
            f"Aucune colonne cible dans '{dataset}' pour {params.get('role') or params}")

    for target in targets:
        for column in target:
            if column not in declared:
                errors.append(f"Colonne '{column}' non declaree au contrat de '{dataset}'")
                continue
            meta = column_meta(store, dataset, column) or {}
            ctype = str(meta.get("type", "")).lower()
            if tid in ("RANGE", "SUM_RECONCILIATION") and ctype not in NUMERIC_TYPES:
                errors.append(
                    f"{tid} exige une colonne numerique ; '{column}' est declaree '{ctype}'")
            if tid in ("FRESHNESS", "DATE_ORDER") and ctype not in DATE_TYPES:
                errors.append(
                    f"{tid} exige une colonne date ; '{column}' est declaree '{ctype}'")

    if tid == "FOREIGN_KEY":
        ref_ds, ref_col = params.get("ref_dataset"), params.get("ref_column")
        if store.dataset(ref_ds) is None:
            errors.append(f"Referentiel cible inconnu : '{ref_ds}'")
        elif column_meta(store, ref_ds, ref_col) is None:
            errors.append(f"Colonne '{ref_col}' non declaree au contrat de '{ref_ds}'")

    if tid == "COUNT_RECONCILIATION" and store.dataset(params.get("ref_dataset")) is None:
        errors.append(f"Dataset de reference inconnu : '{params.get('ref_dataset')}'")

    if tid == "MATCHES_REGEX":
        try:
            re.compile(params["pattern"])
        except re.error as exc:
            errors.append(f"Expression reguliere invalide : {exc}")

    return errors


# --------------------------------------------------------------------------- #
# Phase 2 - Executors
# Each returns (lignes_testees, lignes_ko, kpi_valeur, kpi_nom, exceptions_df)
# --------------------------------------------------------------------------- #
def _exceptions(df: pd.DataFrame, mask: pd.Series, ctx: dict,
                colonne: str, motif: str, valeur_col: str | None = None) -> pd.DataFrame:
    ko = df.loc[mask]
    if ko.empty:
        return pd.DataFrame()
    ko = ko.head(MAX_EXCEPTIONS_PER_CONTROL)
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
    return n, ko, kpi, "% de completude", _exceptions(df, mask, ctx, col, "Valeur absente", col)


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
    return n, ko, kpi, "% de valeurs valides", _exceptions(
        df, mask, ctx, col, f"Ne respecte pas {pattern}", col)


def ex_in_domain(df, params, target, ctx):
    col = target[0]
    values = set(params["values"])
    notna = df[col].notna()
    mask = notna & ~df[col].isin(values)
    n, ko = int(notna.sum()), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% de valeurs valides", _exceptions(
        df, mask, ctx, col, f"Hors domaine {sorted(values)}", col)


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
    return n, ko, kpi, "% de valeurs dans la plage", _exceptions(
        df, mask, ctx, col, f"Hors plage ({' et '.join(bornes)})", col)


def ex_unique_key(df, params, target, ctx):
    mask = df.duplicated(subset=target, keep=False)
    n, ko = len(df), int(mask.sum())
    kpi = round(100.0 * ko / n, 4) if n else 0.0
    return n, ko, kpi, "taux de doublons %", _exceptions(
        df, mask, ctx, " + ".join(target), "Cle presente plusieurs fois", target[0])


def ex_foreign_key(df, params, target, ctx):
    col = target[0]
    ref = ctx["load_dataset"](params["ref_dataset"])
    valid = set(ref[params["ref_column"]].dropna().astype(str))
    notna = df[col].notna()
    mask = notna & ~df[col].astype(str).isin(valid)
    n, ko = int(notna.sum()), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    motif = f"Absent de {params['ref_dataset']}.{params['ref_column']}"
    return n, ko, kpi, "% de valeurs rattachees", _exceptions(df, mask, ctx, col, motif, col)


def ex_field_equals(df, params, target, ctx):
    a, b = target
    notna = df[a].notna() & df[b].notna()
    mask = notna & (df[a].astype(str) != df[b].astype(str))
    n, ko = int(notna.sum()), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% de lignes coherentes", _exceptions(
        df, mask, ctx, f"{a} = {b}", "Champs divergents", a)


def ex_date_order(df, params, target, ctx):
    before, after = target
    d1 = pd.to_datetime(df[before], errors="coerce")
    d2 = pd.to_datetime(df[after], errors="coerce")
    notna = d1.notna() & d2.notna()
    mask = notna & (d1 > d2)
    n, ko = int(notna.sum()), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% de lignes coherentes", _exceptions(
        df, mask, ctx, f"{before} <= {after}", "Dates dans le desordre", before)


def ex_freshness(df, params, target, ctx):
    col = target[0]
    dates = pd.to_datetime(df[col], errors="coerce")
    if dates.notna().sum() == 0:
        return 1, 1, -1, "age maximum en jours", pd.DataFrame(
            [{"index_source": -1, "identifiant_ligne": "", "colonne": col,
              "valeur": "", "motif": "Aucune date exploitable"}])
    age = (pd.Timestamp(ctx["as_of"]) - dates.max()).days
    limite = int(params["max_lag_days"])
    ko = 1 if age > limite else 0
    exc = pd.DataFrame()
    if ko:
        exc = pd.DataFrame([{
            "index_source": -1, "identifiant_ligne": f"max({col})",
            "colonne": col, "valeur": str(dates.max().date()),
            "motif": f"Anciennete {age} j > seuil {limite} j"}])
    return 1, ko, age, "age maximum en jours", exc


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
        return 0, 0, 0.0, "ecart relatif %", pd.DataFrame()

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
        kpi_nom = "depassement maximal %"
        motif = comp["ecart_pct"].map(
            lambda v: f"Composantes superieures au total de {v:.2f}% (tolerance {tolerance}%)")
    elif sens == "couverture_min":
        mask = comp["couverture_pct"] < couverture_min
        kpi = round(float(comp["couverture_pct"].median()) if len(comp) else 100.0, 4)
        kpi_nom = "couverture mediane %"
        motif = comp["couverture_pct"].map(
            lambda v: f"Couverture {v:.2f}% < minimum {couverture_min}%")
    else:
        mask = comp["ecart_pct"].abs() > tolerance
        kpi = round(float(comp["ecart_pct"].abs().median()) if len(comp) else 0.0, 4)
        kpi_nom = "ecart relatif median %"
        motif = comp["ecart_pct"].map(
            lambda v: f"Ecart {abs(v):.2f}% > tolerance {tolerance}%")

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


def ex_count_reconciliation(df, params, target, ctx):
    ref = ctx["load_dataset"](params["ref_dataset"])
    group_by = params.get("group_by")
    if not group_by:
        ecart = abs(len(df) - len(ref))
        ko = 1 if ecart else 0
        exc = pd.DataFrame()
        if ko:
            exc = pd.DataFrame([{
                "index_source": -1, "identifiant_ligne": "total",
                "colonne": "COUNT(*)", "valeur": f"{len(df)} vs {len(ref)}",
                "motif": f"Ecart de {ecart} lignes"}])
        return 1, ko, ecart, "ecart en nombre de lignes", exc

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
    return n, ko, int(comp["ecart"].sum()), "ecart en nombre de lignes", exc


def ex_custom_expression(df, params, target, ctx):
    expr = params["expression"]
    ok = df.eval(expr, engine="python")
    if not isinstance(ok, pd.Series):
        raise ValueError("L'expression doit produire un booleen par ligne")
    mask = ~ok.fillna(False)
    n, ko = len(df), int(mask.sum())
    kpi = round(100.0 * (n - ko) / n, 4) if n else 100.0
    return n, ko, kpi, "% de lignes conformes", _exceptions(
        df, mask, ctx, expr, f"Expression fausse : {expr}", ctx.get("id_column"))


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
    "COUNT_RECONCILIATION": ex_count_reconciliation,
    "CUSTOM_EXPRESSION": ex_custom_expression,
}


# --------------------------------------------------------------------------- #
# Dataset loading
# --------------------------------------------------------------------------- #
class DatasetLoader:
    """Loads a dataset declared in the catalogue, once, and hashes its source."""

    def __init__(self, store: CatalogueStore, root: pathlib.Path = ROOT):
        self.store, self.root = store, root
        self._cache: dict[str, pd.DataFrame] = {}
        self.hashes: dict[str, dict] = {}

    def path_of(self, name: str) -> pathlib.Path:
        definition = self.store.dataset(name)
        if definition is None:
            raise KeyError(f"Dataset '{name}' absent du catalogue")
        return self.root / definition["source"]

    def load(self, name: str) -> pd.DataFrame:
        if name in self._cache:
            return self._cache[name]
        path = self.path_of(name)
        if not path.exists():
            raise FileNotFoundError(f"Source introuvable pour '{name}' : {path}")
        df = pd.read_csv(path, low_memory=False)
        self._cache[name] = df
        self.hashes[name] = {
            "chemin": str(path.relative_to(self.root)).replace("\\", "/"),
            "sha256": sha256_file(path),
            "lignes": len(df),
            "colonnes": len(df.columns),
            "modifie_le": dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                timespec="seconds"),
        }
        return df

    def id_column(self, name: str) -> str | None:
        for c in self.store.columns_of(name):
            if str(c.get("cle_primaire", "")).upper() == "OUI":
                return c["colonne"]
        return None


# --------------------------------------------------------------------------- #
# Phase 2/3 - Runner
# --------------------------------------------------------------------------- #
def run_dq(datasets: list[str] | None = None,
           store: CatalogueStore | None = None,
           run_label: str = "",
           write_evidence: bool = True,
           as_of: dt.date | None = None,
           root: pathlib.Path = ROOT) -> RunResult:
    store = store or load_store()
    datasets = datasets or list(store.datasets.keys())
    as_of = as_of or dt.date.today()
    run_id = f"RUN-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    started = time.perf_counter()
    journal: list[str] = []

    def log(msg: str) -> None:
        line = f"{dt.datetime.now():%H:%M:%S} | {msg}"
        journal.append(line)

    log(f"Demarrage {run_id} | moteur {ENGINE_VERSION} | datasets={datasets}")
    loader = DatasetLoader(store, root)
    resultats: list[ControlResult] = []
    rejets: list[dict] = []
    frames: list[pd.DataFrame] = []

    for dataset in datasets:
        try:
            df = loader.load(dataset)
        except (KeyError, FileNotFoundError) as exc:
            log(f"[{dataset}] IGNORE : {exc}")
            rejets.append({"rule_id": "-", "dataset": dataset, "motif": str(exc)})
            continue
        log(f"[{dataset}] charge : {len(df):,} lignes, {len(df.columns)} colonnes")

        ctx = {
            "load_dataset": loader.load,
            "id_column": loader.id_column(dataset),
            "as_of": as_of,
            "store": store,
            "dataset": dataset,
        }

        for control in store.controls:
            if control.get("statut") != "Actif":
                continue
            if not scope_matches(control.get("dataset_scope", ""), dataset):
                continue

            errors = validate_control(control, store, dataset)
            if errors:
                for err in errors:
                    rejets.append({"rule_id": control["rule_id"], "dataset": dataset,
                                   "control_name": control.get("control_name", ""),
                                   "motif": err})
                log(f"[{dataset}] {control['rule_id']} REJETE : {errors[0]}")
                continue

            params = parse_params(control["params"])
            targets = resolve_targets(control["template"], params, store, dataset)
            executor = EXECUTORS[control["template"]]

            for target in targets:
                t0 = time.perf_counter()
                cible = " + ".join(target) if target else params.get("expression", "-")
                try:
                    n, ko, kpi, kpi_nom, exc = executor(df, params, target, ctx)
                    seuil = float(control.get("seuil_tolerance_pct") or 0)
                    taux = round(100.0 * ko / n, 4) if n else 0.0
                    statut = "NON_APPLICABLE" if n == 0 else (
                        "PASS" if taux <= seuil else "FAIL")
                    message = ""
                except Exception as exc_obj:  # noqa: BLE001 - reported, never swallowed
                    n = ko = 0
                    kpi, kpi_nom, taux, seuil = -1, "-", 0.0, 0.0
                    statut, message, exc = "ERREUR", f"{type(exc_obj).__name__}: {exc_obj}", pd.DataFrame()
                    log(f"[{dataset}] {control['rule_id']} ERREUR : {message}")

                if not exc.empty:
                    exc.insert(0, "dataset", dataset)
                    exc.insert(0, "rule_id", control["rule_id"])
                    exc.insert(2, "severity", control.get("severity", ""))
                    frames.append(exc)

                resultats.append(ControlResult(
                    rule_id=control["rule_id"],
                    control_name=control.get("control_name", ""),
                    dimension=control.get("control_type", ""),
                    template=control["template"],
                    dataset=dataset,
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
                ))
                log(f"[{dataset}] {control['rule_id']} {cible[:40]:<40} {statut:<14} "
                    f"{kpi_nom}={kpi} ({ko}/{n} KO)")

    exceptions = (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["rule_id", "dataset", "severity", "index_source",
                 "identifiant_ligne", "colonne", "valeur", "motif"]))

    catalogue_snapshot = {
        "meta": store.data.get("meta", {}),
        "templates": store.templates,
        "datasets": store.datasets,
        "controls": store.controls,
    }
    manifeste = {
        "run_id": run_id,
        "libelle": run_label,
        "horodatage": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "moteur_version": ENGINE_VERSION,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "machine": platform.node(),
        "datasets_executes": datasets,
        "sources": loader.hashes,
        "catalogue_sha256": sha256_obj(catalogue_snapshot),
        "controles_actifs": len(store.active_controls()),
        "duree_totale_s": round(time.perf_counter() - started, 3),
    }

    result = RunResult(
        run_id=run_id,
        horodatage=manifeste["horodatage"],
        libelle=run_label,
        datasets=datasets,
        resultats=resultats,
        exceptions=exceptions,
        rejets=rejets,
        manifeste=manifeste,
        journal=journal,
    )
    log(f"Termine en {manifeste['duree_totale_s']}s | {result.summary()}")

    if write_evidence:
        result.evidence_path = write_evidence_pack(result, catalogue_snapshot)
        log(f"Evidence pack : {result.evidence_path}")
    return result


def write_evidence_pack(result: RunResult, catalogue_snapshot: dict) -> pathlib.Path:
    out = EVIDENCE_DIR / result.run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(
        json.dumps(result.manifeste, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "catalogue_snapshot.json").write_text(
        json.dumps(catalogue_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "results.json").write_text(
        json.dumps([r.as_dict() for r in result.resultats], ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out / "rejets.json").write_text(
        json.dumps(result.rejets, ensure_ascii=False, indent=2), encoding="utf-8")
    result.exceptions.to_csv(out / "exceptions.csv", index=False, encoding="utf-8-sig")
    (out / "execution.log").write_text("\n".join(result.journal), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    datasets = argv[1:] or ["bis_turnover_demo", "ref_devises", "ref_pays"]
    res = run_dq(datasets=datasets, run_label="Execution en ligne de commande")
    print("\n".join(res.journal))
    print("\n--- SYNTHESE ---")
    print(json.dumps(res.summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
