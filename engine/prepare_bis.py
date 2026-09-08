"""
Prepare BIS OTC derivatives turnover data for the DQ engine.

Source  : WS_DER_OTC_TOV_csv_col.csv (BIS Triennial Survey, wide format)
Produces:
  data/prepared/bis_turnover.csv        long format, one row per (key, year)
  data/prepared/bis_turnover_demo.csv   2022 survey only, for the live demo
  data/ref/ref_devises.csv              currency reference table (FK target)
  data/ref/ref_pays.csv                 country reference table (FK target)

The engine never reads this file. Preparation is deliberately separate from
control execution: the catalogue describes the *prepared* datasets.
"""
from __future__ import annotations

import hashlib
import pathlib
import zipfile

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "WS_DER_OTC_TOV_csv_col.csv"
RAW_ZIP = RAW.with_suffix(".zip")
PREPARED = ROOT / "data" / "prepared"
REF = ROOT / "data" / "ref"

# The 14 code columns that together form the observation key.
DIMENSIONS = [
    "FREQ", "DER_TYPE", "DER_INSTR", "DER_RISK", "DER_REP_CTY",
    "DER_SECTOR_CPY", "DER_CPC", "DER_SECTOR_UDL", "DER_CURR_LEG1",
    "DER_CURR_LEG2", "DER_ISSUE_MAT", "DER_RATING", "DER_EX_METHOD",
    "DER_BASIS",
]

# Label column paired with each code column, kept for readability of exceptions.
LABELS = {
    "DER_INSTR": "Instrument",
    "DER_RISK": "Risk category",
    "DER_REP_CTY": "Reporting country",
    "DER_SECTOR_CPY": "Counterparty sector",
    "DER_CURR_LEG1": "Currency leg 1",
    "DER_CURR_LEG2": "Currency leg 2",
    "DER_EX_METHOD": "Execution method",
    "DER_BASIS": "Basis",
}

# Codes that are aggregates, not real currencies. Kept in the reference table
# with type = AGREGAT so that legitimate totals do not surface as orphans.
CURRENCY_AGGREGATES = {"TO1": "Total (all currencies)"}

# Settlement mechanism, deliberately absent from the currency reference:
# CLS is not a currency and must surface as a referential integrity exception.
NOT_A_CURRENCY = {"CLS"}

COUNTRY_AGGREGATES = {"5J", "5Z", "1E", "1C", "5A"}


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_reference(df: pd.DataFrame, code_label_pairs, aggregates, exclude) -> pd.DataFrame:
    frames = []
    for code_col, label_col in code_label_pairs:
        frames.append(
            df[[code_col, label_col]]
            .dropna()
            .drop_duplicates()
            .rename(columns={code_col: "code", label_col: "libelle"})
        )
    ref = pd.concat(frames).drop_duplicates("code")
    ref = ref[~ref["code"].isin(exclude)].copy()
    ref["type"] = ref["code"].map(lambda c: "AGREGAT" if c in aggregates else "REEL")
    return ref.sort_values("code").reset_index(drop=True)


def ensure_source() -> None:
    """Only the zip is versioned; the 42 MB CSV is extracted on first use."""
    if RAW.exists():
        return
    if not RAW_ZIP.exists():
        raise FileNotFoundError(
            f"Source absente. Placez {RAW_ZIP.name} dans {RAW_ZIP.parent}.")
    print(f"Extraction de {RAW_ZIP.name}...")
    with zipfile.ZipFile(RAW_ZIP) as zf:
        zf.extract(RAW.name, RAW.parent)


def main() -> None:
    PREPARED.mkdir(parents=True, exist_ok=True)
    REF.mkdir(parents=True, exist_ok=True)
    ensure_source()

    print(f"Source        : {RAW.name}")
    print(f"SHA-256 source: {sha256(RAW)}")

    df = pd.read_csv(RAW, dtype=str)
    years = [c for c in df.columns if c.isdigit()]
    print(f"Lignes brutes : {len(df):,} | dimensions : {len(DIMENSIONS)} | annees : {len(years)}")

    # --- Reference tables ---------------------------------------------------
    devises = build_reference(
        df,
        [("DER_CURR_LEG1", "Currency leg 1"), ("DER_CURR_LEG2", "Currency leg 2")],
        CURRENCY_AGGREGATES,
        NOT_A_CURRENCY,
    ).rename(columns={"code": "code_devise", "libelle": "libelle_devise"})
    devises.to_csv(REF / "ref_devises.csv", index=False, encoding="utf-8")

    pays = build_reference(
        df,
        [("DER_REP_CTY", "Reporting country"), ("DER_CPC", "Counterparty country")],
        COUNTRY_AGGREGATES,
        set(),
    ).rename(columns={"code": "code_pays", "libelle": "libelle_pays"})
    pays.to_csv(REF / "ref_pays.csv", index=False, encoding="utf-8")
    print(f"Referentiels  : {len(devises)} devises, {len(pays)} pays")

    # --- Long format --------------------------------------------------------
    keep = DIMENSIONS + list(LABELS.values()) + years
    long = df[keep].melt(
        id_vars=DIMENSIONS + list(LABELS.values()),
        value_vars=years,
        var_name="annee",
        value_name="turnover_notionnel",
    )
    long = long[long["turnover_notionnel"].notna()].copy()
    long["annee"] = long["annee"].astype(int)
    long["turnover_notionnel"] = pd.to_numeric(long["turnover_notionnel"], errors="coerce")
    long["observation_id"] = (
        long[DIMENSIONS].fillna("").agg("|".join, axis=1) + "|" + long["annee"].astype(str)
    )
    long["date_observation"] = pd.to_datetime(long["annee"].astype(str) + "-12-31")

    cols = ["observation_id"] + DIMENSIONS + list(LABELS.values()) + [
        "annee", "date_observation", "turnover_notionnel",
    ]
    long = long[cols].sort_values(["annee", "observation_id"]).reset_index(drop=True)
    long.to_csv(PREPARED / "bis_turnover.csv", index=False, encoding="utf-8")
    print(f"Format long   : {len(long):,} observations non nulles")

    demo = long[long["annee"] == 2022].reset_index(drop=True)
    demo.to_csv(PREPARED / "bis_turnover_demo.csv", index=False, encoding="utf-8")
    print(f"Extrait demo  : {len(demo):,} observations (enquete 2022)")

    for path in (PREPARED / "bis_turnover.csv", PREPARED / "bis_turnover_demo.csv"):
        print(f"  {path.name:28s} sha256={sha256(path)[:16]}...")


if __name__ == "__main__":
    main()
