"""
Excel reporting layer: the business deliverable.

Excel is an OUTPUT, not an input. The workbook is self-contained: a reviewer
opens it without any tool and finds the scorecard, the exceptions, the coverage
of the control plan, the evidence needed to replay the run, the exact rule text
that was executed, and the change journal of the catalogue.

    build_workbook(run_result, store, path) -> pathlib.Path
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engine"))
from store import (CatalogueStore, libelle_severite,  # noqa: E402
                   libelle_statut, phrase_controle)

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "reporting" / "sorties"

DIMENSIONS_ATTENDUES = ["Completeness", "Validity", "Uniqueness",
                        "Consistency", "Timeliness", "Reconciliation"]

VERT, ROUGE, ORANGE, GRIS = "#1E7B34", "#B3261E", "#B26A00", "#5F6368"
VERT_BG, ROUGE_BG, ORANGE_BG, GRIS_BG = "#E6F4EA", "#FCE8E6", "#FEF7E0", "#F1F3F4"


class _Fmt:
    """Workbook formats, created once."""

    def __init__(self, wb):
        self.titre = wb.add_format({"bold": True, "font_size": 16, "font_color": "#202124"})
        self.sous_titre = wb.add_format({"font_size": 10, "font_color": GRIS})
        self.section = wb.add_format({"bold": True, "font_size": 11, "font_color": "#202124",
                                      "bottom": 1, "border_color": "#DADCE0"})
        self.entete = wb.add_format({
            "bold": True, "font_color": "white", "bg_color": "#37474F",
            "border": 1, "border_color": "#CFD8DC", "text_wrap": True, "valign": "vcenter"})
        self.cell = wb.add_format({"border": 1, "border_color": "#E0E0E0", "valign": "top"})
        self.wrap = wb.add_format({"border": 1, "border_color": "#E0E0E0",
                                   "text_wrap": True, "valign": "top"})
        self.num = wb.add_format({"border": 1, "border_color": "#E0E0E0",
                                  "num_format": "#,##0"})
        self.pct = wb.add_format({"border": 1, "border_color": "#E0E0E0",
                                  "num_format": "0.00"})
        self.pass_ = wb.add_format({"bold": True, "font_color": VERT, "bg_color": VERT_BG,
                                    "border": 1, "border_color": "#E0E0E0", "align": "center"})
        self.fail = wb.add_format({"bold": True, "font_color": ROUGE, "bg_color": ROUGE_BG,
                                   "border": 1, "border_color": "#E0E0E0", "align": "center"})
        self.warn = wb.add_format({"bold": True, "font_color": ORANGE, "bg_color": ORANGE_BG,
                                   "border": 1, "border_color": "#E0E0E0", "align": "center"})
        self.neutre = wb.add_format({"font_color": GRIS, "bg_color": GRIS_BG,
                                     "border": 1, "border_color": "#E0E0E0", "align": "center"})
        self.kpi_val = wb.add_format({"bold": True, "font_size": 22, "align": "center",
                                      "valign": "vcenter", "border": 1,
                                      "border_color": "#DADCE0"})
        # Le KPI d'echec est la seule tuile qui declenche une action : elle est
        # plus grande que les autres et change de couleur selon le resultat.
        self.kpi_alerte = wb.add_format({
            "bold": True, "font_size": 34, "align": "center", "valign": "vcenter",
            "font_color": ROUGE, "bg_color": ROUGE_BG, "border": 2,
            "border_color": "#F2B8B5"})
        self.kpi_ok = wb.add_format({
            "bold": True, "font_size": 34, "align": "center", "valign": "vcenter",
            "font_color": VERT, "bg_color": VERT_BG, "border": 2,
            "border_color": "#A8DAB5"})
        self.kpi_lib_alerte = wb.add_format({
            "bold": True, "font_size": 10, "font_color": ROUGE, "bg_color": ROUGE_BG,
            "align": "center", "valign": "vcenter", "border": 2,
            "border_color": "#F2B8B5"})
        self.kpi_lib_ok = wb.add_format({
            "bold": True, "font_size": 10, "font_color": VERT, "bg_color": VERT_BG,
            "align": "center", "valign": "vcenter", "border": 2,
            "border_color": "#A8DAB5"})
        self.kpi_lib = wb.add_format({"font_size": 9, "font_color": GRIS, "align": "center",
                                      "valign": "vcenter", "border": 1,
                                      "border_color": "#DADCE0"})
        self.cle = wb.add_format({"bold": True, "border": 1, "border_color": "#E0E0E0",
                                  "bg_color": "#F8F9FA"})
        self.mono = wb.add_format({"border": 1, "border_color": "#E0E0E0",
                                   "font_name": "Consolas", "font_size": 9})


def _write_table(ws, fmt: _Fmt, df: pd.DataFrame, start_row: int,
                 widths: dict[str, int] | None = None,
                 wrap_cols: set[str] | None = None) -> int:
    """Write a dataframe as a bordered table with an autofilter. Returns next row."""
    widths, wrap_cols = widths or {}, wrap_cols or set()
    if df.empty:
        ws.write(start_row, 0, "No rows", fmt.cell)
        return start_row + 2

    for c, name in enumerate(df.columns):
        ws.write(start_row, c, name, fmt.entete)
        ws.set_column(c, c, widths.get(name, 16))
    ws.set_row(start_row, 30)

    for r, (_, row) in enumerate(df.iterrows(), start=start_row + 1):
        for c, name in enumerate(df.columns):
            value = row[name]
            if name == "statut":
                style = {"PASS": fmt.pass_, "FAIL": fmt.fail, "ERROR": fmt.warn}.get(
                    str(value), fmt.neutre)
            elif name in wrap_cols:
                style = fmt.wrap
            elif isinstance(value, (int,)) and not isinstance(value, bool):
                style = fmt.num
            elif isinstance(value, float):
                style = fmt.pct
            else:
                style = fmt.cell
            ws.write(r, c, "" if pd.isna(value) else value, style)

    last = start_row + len(df)
    ws.autofilter(start_row, 0, last, len(df.columns) - 1)
    ws.freeze_panes(start_row + 1, 0)
    return last + 3


def _kpi_band(ws, fmt: _Fmt, row: int, tuiles: list[tuple]) -> int:
    """A row of large KPI tiles, two columns wide each.

    A tile may carry its own pair of formats as a third element, so the tile
    that matters most can be louder than the rest.
    """
    for i, tuile in enumerate(tuiles):
        valeur, libelle = tuile[0], tuile[1]
        f_val, f_lib = tuile[2] if len(tuile) > 2 else (fmt.kpi_val, fmt.kpi_lib)
        c = i * 3
        ws.merge_range(row, c, row + 1, c + 1, valeur, f_val)
        ws.merge_range(row + 2, c, row + 2, c + 1, libelle, f_lib)
    ws.set_row(row, 30)
    ws.set_row(row + 1, 24)
    return row + 5


# --------------------------------------------------------------------------- #
# Sheets
# --------------------------------------------------------------------------- #
def _sheet_synthese(wb, fmt: _Fmt, run, sc: pd.DataFrame,
                    store: CatalogueStore) -> None:
    ws = wb.add_worksheet("SUMMARY")
    ws.hide_gridlines(2)
    ws.write(0, 0, "DQ Compass - Data quality scorecard", fmt.titre)
    ws.write(1, 0, f"File: {pathlib.Path(run.fichier).name or '-'}  |  "
                   f"Dataset: {run.contrat}  |  Run {run.run_id}  |  "
                   f"{run.horodatage}  |  Overall status: {run.rag}", fmt.sous_titre)

    s = run.summary()
    total = max(s["controls"], 1)
    conformite = 100.0 * s["pass"] / total
    echecs = s["fail"] + s["error"]
    critiques = int(((sc["statut"] == "FAIL") & (sc["severity"].isin(
        ["Critical", "High"]))).sum()) if not sc.empty else 0

    # Le nombre d'echecs vient en premier et en gros : c'est la seule chose qui
    # appelle une decision. Le taux de conformite ne fait que rassurer.
    alerte = (fmt.kpi_alerte, fmt.kpi_lib_alerte) if echecs else (
        fmt.kpi_ok, fmt.kpi_lib_ok)
    row = _kpi_band(ws, fmt, 3, [
        (str(echecs), "CONTROLS IN BREACH", alerte),
        (str(critiques), "of which Blocking / Major",
         alerte if critiques else (fmt.kpi_val, fmt.kpi_lib)),
        (f"{s['exceptions']:,}".replace(",", " "), "Rows to review"),
        (f"{conformite:.0f}%", "Compliance rate"),
        (str(s["controls"]), "Controls executed"),
        (str(s["rejected"]), "Rules rejected"),
    ])

    ws.write(row, 0, "Control-by-control detail", fmt.section)
    table = sc.copy()
    table.insert(2, "what_is_checked",
                 [phrase_controle(store.control(r) or {}, store)
                  for r in table["rule_id"]])
    table["gravite"] = table["severity"].map(libelle_severite)
    cols = ["rule_id", "control_name", "what_is_checked", "statut", "gravite",
            "lignes_ko", "lignes_testees", "taux_ko_pct", "kpi_nom", "kpi_valeur",
            "seuil_pct", "dimension", "cible", "owner", "frequency", "version",
            "message", "remediation_action"]
    table = table[[c for c in cols if c in table.columns]]
    table = table.sort_values(
        ["statut", "gravite", "rule_id"],
        key=lambda s: s.map({"FAIL": 0, "ERROR": 1, "SKIPPED": 2, "PASS": 3,
                             "Blocking": 0, "Major": 1, "Moderate": 2,
                             "Minor": 3}).fillna(9)
        if s.name in ("statut", "gravite") else s)
    _write_table(ws, fmt, table, row + 1,
                 widths={"control_name": 38, "what_is_checked": 62, "cible": 26,
                         "kpi_nom": 22, "remediation_action": 55, "owner": 22,
                         "message": 60},
                 wrap_cols={"remediation_action", "control_name", "message",
                            "what_is_checked"})


def _sheet_exceptions(wb, fmt: _Fmt, run) -> None:
    ws = wb.add_worksheet("EXCEPTIONS")
    ws.hide_gridlines(2)
    ws.write(0, 0, "Exception report", fmt.titre)
    ws.write(1, 0, f"{len(run.exceptions):,} exception rows. Filter by rule_id or "
                   f"severity to work through a batch.".replace(",", " "), fmt.sous_titre)
    exc = run.exceptions.copy()
    if not exc.empty and "index_source" in exc.columns:
        exc = exc.drop(columns=["index_source"])
    _write_table(ws, fmt, exc.head(50000), 3,
                 widths={"identifiant_ligne": 44, "motif": 52, "colonne": 24,
                         "valeur": 30, "dataset": 20},
                 wrap_cols={"motif"})


def _sheet_couverture(wb, fmt: _Fmt, run, sc: pd.DataFrame, store: CatalogueStore) -> None:
    ws = wb.add_worksheet("COVERAGE")
    ws.hide_gridlines(2)
    ws.write(0, 0, "Control coverage", fmt.titre)
    ws.write(1, 0, "What the framework covers, and what it does not cover yet.",
             fmt.sous_titre)

    row = 3
    ws.write(row, 0, "Dimensions x dataset (controls executed)", fmt.section)
    row += 1
    if sc.empty:
        pivot = pd.DataFrame()
    else:
        pivot = sc.pivot_table(index="dimension", columns="dataset", values="rule_id",
                               aggfunc="count", fill_value=0)
        for dim in DIMENSIONS_ATTENDUES:
            if dim not in pivot.index:
                pivot.loc[dim] = 0
        pivot = pivot.loc[[d for d in DIMENSIONS_ATTENDUES if d in pivot.index]
                          + [d for d in pivot.index if d not in DIMENSIONS_ATTENDUES]]
        pivot["TOTAL"] = pivot.sum(axis=1)
        pivot = pivot.reset_index()
    row = _write_table(ws, fmt, pivot, row, widths={"dimension": 22})

    ws.write(row, 0, "Coverage gaps", fmt.section)
    row += 1
    trous = []
    couvertes = set(sc["dimension"]) if not sc.empty else set()
    for dim in DIMENSIONS_ATTENDUES:
        if dim not in couvertes:
            trous.append({"objet": dim, "type": "Dimension",
                          "constat": "No active control executed on this dimension"})
    hors_portee = [c for c in store.active_controls()
                   if c["rule_id"] not in set(sc["rule_id"] if not sc.empty else [])]
    for c in hors_portee:
        trous.append({"objet": c["rule_id"], "type": "Out of scope",
                      "constat": f"{c['control_name']} - scope '{c['dataset_scope']}' "
                                 f"does not cover this file"})
    if not sc.empty:
        for _, r in sc[sc["statut"] == "SKIPPED"].iterrows():
            trous.append({"objet": r["rule_id"], "type": "Skipped",
                          "constat": f"{r['control_name']} - {r['message']}"})
    for c in store.controls:
        if c.get("statut") != "Actif":
            trous.append({"objet": c["rule_id"],
                          "type": f"Control {libelle_statut(c['statut'])}",
                          "constat": f"{c['control_name']} - not executed"})
    row = _write_table(ws, fmt, pd.DataFrame(trous), row,
                       widths={"objet": 26, "type": 22, "constat": 62},
                       wrap_cols={"constat"})

    ws.write(row, 0, "Breakdown by severity", fmt.section)
    row += 1
    if not sc.empty:
        sev = (sc.groupby(["severity", "statut"]).size().unstack(fill_value=0)
               .reindex(["Critical", "High", "Medium", "Low"]).dropna(how="all")
               .fillna(0).astype(int).reset_index())
    else:
        sev = pd.DataFrame()
    _write_table(ws, fmt, sev, row, widths={"severity": 18})


def _sheet_evidence(wb, fmt: _Fmt, run) -> None:
    ws = wb.add_worksheet("EVIDENCE")
    ws.hide_gridlines(2)
    ws.write(0, 0, "Execution evidence", fmt.titre)
    ws.write(1, 0, "Everything needed to replay this run identically "
                   "(brief, appendix B.2).", fmt.sous_titre)
    ws.set_column(0, 0, 30)
    ws.set_column(1, 1, 78)

    m = run.manifeste
    row = 3
    ws.write(row, 0, "Run identification", fmt.section)
    row += 1
    lignes = [
        ("Run ID", m["run_id"]),
        ("Label", m.get("libelle", "")),
        ("Execution timestamp", m["horodatage"]),
        ("As-of date", m["as_of"]),
        ("Overall status (RAG)", m.get("statut_global", "")),
        ("Engine version", m["moteur_version"]),
        ("Evidence pack version", m.get("evidence_version", "")),
        ("Catalogue SHA-256", m["catalogue_sha256"]),
        ("Executed rules SHA-256", m.get("regles_executees_sha256", "")),
        ("Rules executed", m.get("regles_executees", "")),
        ("Active controls in catalogue", m["controles_actifs"]),
        ("Exceptions complete", "yes" if m.get("exceptions_completes", True)
         else "NO - truncated for: "
              + ", ".join(m.get("exceptions_tronquees_pour", []))),
        ("Total duration (s)", m["duree_totale_s"]),
        ("Python / pandas", f"{m['python']} / {m['pandas']}"),
        ("Machine / user", f"{m['machine']} / {m.get('utilisateur_systeme', '')}"),
        ("Replay command", m.get("rejeu", "")),
    ]
    for cle, valeur in lignes:
        ws.write(row, 0, cle, fmt.cle)
        ws.write(row, 1, str(valeur),
                 fmt.mono if "SHA" in cle or "Replay" in cle else fmt.cell)
        row += 1

    row += 2
    ws.write(row, 0, "Input dataset references (hash)", fmt.section)
    row += 1
    sources = pd.DataFrame([
        {"dataset": k, "chemin": v["chemin"], "lignes": v["lignes"],
         "colonnes": v["colonnes"], "sha256": v["sha256"], "modifie_le": v["modifie_le"]}
        for k, v in m["sources"].items()])
    row = _write_table(ws, fmt, sources, row,
                       widths={"dataset": 22, "chemin": 40, "sha256": 68,
                               "modifie_le": 22})

    ws.write(row, 0, "Rules rejected at validation", fmt.section)
    row += 1
    _write_table(ws, fmt, pd.DataFrame(run.rejets), row,
                 widths={"motif": 70, "control_name": 34}, wrap_cols={"motif"})


def _sheet_catalogue(wb, fmt: _Fmt, store: CatalogueStore) -> None:
    ws = wb.add_worksheet("EXECUTED_CATALOGUE")
    ws.hide_gridlines(2)
    ws.write(0, 0, "Control catalogue - executed version", fmt.titre)
    ws.write(1, 0, "Frozen copy of the rules as executed. This is not a link to "
                   "the live catalogue.", fmt.sous_titre)
    df = pd.DataFrame(store.controls)
    if not df.empty and "statut" in df.columns:
        df["statut"] = df["statut"].map(libelle_statut)
    _write_table(ws, fmt, df, 3,
                 widths={"control_name": 36, "description": 55, "params": 60,
                         "logic_definition": 42, "remediation_action": 55,
                         "dataset_scope": 26, "data_element": 24, "owner": 22},
                 wrap_cols={"description", "params", "remediation_action",
                            "logic_definition"})


def _sheet_journal(wb, fmt: _Fmt, store: CatalogueStore) -> None:
    ws = wb.add_worksheet("CHANGELOG")
    ws.hide_gridlines(2)
    ws.write(0, 0, "Catalogue change log", fmt.titre)
    ws.write(1, 0, "Who changed what, when and why. Nothing is ever erased: a "
                   "retired control is paused or deprecated, never deleted "
                   "silently.", fmt.sous_titre)
    df = pd.DataFrame(store.changelog)
    if not df.empty:
        df = df.iloc[::-1].reset_index(drop=True)
    _write_table(ws, fmt, df, 3,
                 widths={"timestamp": 20, "utilisateur": 16, "action": 15,
                         "champ": 20, "avant": 40, "apres": 40, "motif": 60},
                 wrap_cols={"avant", "apres", "motif"})


# --------------------------------------------------------------------------- #
def build_workbook(run, store: CatalogueStore,
                   path: pathlib.Path | str | None = None) -> pathlib.Path:
    if path is None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"DQ_Rapport_{run.run_id}.xlsx"
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    sc = run.scorecard
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        wb = writer.book
        wb.set_properties({
            "title": f"DQ Compass - report {run.run_id}",
            "subject": "Data quality control",
            "comments": json.dumps(run.summary(), ensure_ascii=False),
            "created": dt.datetime.now(),
        })
        fmt = _Fmt(wb)
        _sheet_synthese(wb, fmt, run, sc, store)
        _sheet_exceptions(wb, fmt, run)
        _sheet_couverture(wb, fmt, run, sc, store)
        _sheet_evidence(wb, fmt, run)
        _sheet_catalogue(wb, fmt, store)
        _sheet_journal(wb, fmt, store)
    return path


def main() -> int:
    sys.path.insert(0, str(ROOT / "engine"))
    from dq_engine import run_dq  # noqa: PLC0415

    store = CatalogueStore()
    fichier = sys.argv[1] if len(sys.argv) > 1 else "data/prepared/bis_turnover.csv"
    nom = sys.argv[2] if len(sys.argv) > 2 else None
    run = run_dq(fichier, dataset=nom, store=store, run_label="Excel report")
    out = build_workbook(run, store)
    print(f"Workbook written : {out}")
    print(f"Summary          : {run.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
