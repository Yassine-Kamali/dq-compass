"""
Harnais de test DQ Compass. Aucune dependance externe : lancez simplement

    .venv/Scripts/python.exe tests/test_dq.py

Couvre les quatre couches : store (gouvernance), validateur, exécuteurs,
moteur bout en bout, rapport Excel, et le rendu des quatre écrans Streamlit.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import shutil
import sys
import tempfile
import traceback

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "reporting"))

import dq_engine as E  # noqa: E402
from excel_report import build_workbook  # noqa: E402
from store import CatalogueStore  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(nom: str):
    def decorateur(fn):
        try:
            fn()
            RESULTS.append((nom, True, ""))
        except Exception as exc:  # noqa: BLE001
            RESULTS.append((nom, False, f"{type(exc).__name__}: {exc}\n"
                                        + traceback.format_exc(limit=3)))
        return fn
    return decorateur


def eq(got, want, quoi=""):
    if got != want:
        raise AssertionError(f"{quoi}: obtenu {got!r}, attendu {want!r}")


# --------------------------------------------------------------------------- #
# Fixtures : un store et un dataset synthetiques, independants du projet reel
# --------------------------------------------------------------------------- #
TMP = pathlib.Path(tempfile.mkdtemp(prefix="dq_tests_"))


def make_store() -> CatalogueStore:
    st = CatalogueStore(TMP / "store.json")
    st.data = {"meta": {"schema": "1.0"}, "templates": [], "datasets": {},
               "controls": [], "changelog": []}
    st.data["templates"] = [
        {"template_id": "NOT_NULL", "dimension": "Completeness",
         "params_requis": "column | role", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
        {"template_id": "RANGE", "dimension": "Validity",
         "params_requis": "column, min | max", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
        {"template_id": "MATCHES_REGEX", "dimension": "Validity",
         "params_requis": "column, pattern", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
        {"template_id": "FOREIGN_KEY", "dimension": "Consistency",
         "params_requis": "column, ref_dataset, ref_column", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
        {"template_id": "FRESHNESS", "dimension": "Timeliness",
         "params_requis": "column, max_lag_days", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "j", "exemple_params": ""},
        {"template_id": "UNIQUE_KEY", "dimension": "Uniqueness",
         "params_requis": "columns | role", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
    ]
    st.data["datasets"] = {
        "ventes": {
            "libelle": "Ventes de test", "source": "data/ventes.csv",
            "proprietaire": "Test",
            "colonnes": [
                {"colonne": "vente_id", "type": "string", "role": "identifiant",
                 "cle_primaire": "OUI", "obligatoire": "OUI", "description": "",
                 "fk_dataset": "", "fk_colonne": ""},
                {"colonne": "devise", "type": "string", "role": "code",
                 "cle_primaire": "NON", "obligatoire": "OUI", "description": "",
                 "fk_dataset": "ref_dev", "fk_colonne": "code"},
                {"colonne": "montant", "type": "decimal", "role": "mesure",
                 "cle_primaire": "NON", "obligatoire": "OUI", "description": "",
                 "fk_dataset": "", "fk_colonne": ""},
                {"colonne": "date_vente", "type": "date", "role": "date_evenement",
                 "cle_primaire": "NON", "obligatoire": "OUI", "description": "",
                 "fk_dataset": "", "fk_colonne": ""},
            ]},
        "ref_dev": {
            "libelle": "Devises de test", "source": "data/ref_dev.csv",
            "proprietaire": "Test",
            "colonnes": [
                {"colonne": "code", "type": "string", "role": "identifiant",
                 "cle_primaire": "OUI", "obligatoire": "OUI", "description": "",
                 "fk_dataset": "", "fk_colonne": ""}]},
    }
    return st


VENTES = pd.DataFrame({
    "vente_id": ["V1", "V2", "V3", "V4", "V4"],
    "devise":   ["EUR", "USD", "XXX", "EUR", "EUR"],
    "montant":  [100.0, -5.0, 50.0, None, 20.0],
    "date_vente": ["2026-01-01", "2026-06-30", "2026-06-30", "2026-06-30", "2026-06-30"],
})
REF_DEV = pd.DataFrame({"code": ["EUR", "USD"]})


def ctx(store: CatalogueStore, as_of="2026-09-07") -> dict:
    return {"load_dataset": lambda n: REF_DEV if n == "ref_dev" else VENTES,
            "id_column": "vente_id", "as_of": dt.date.fromisoformat(as_of),
            "store": store, "dataset": "ventes"}


# --------------------------------------------------------------------------- #
# 1. Gouvernance du store
# --------------------------------------------------------------------------- #
@check("store: creation d'un controle en version 1")
def _():
    st = make_store()
    c = st.add_control({"control_name": "Test", "template": "NOT_NULL",
                        "params": '{"column": "montant"}'}, "yassine")
    eq(c["version"], 1, "version initiale")
    eq(c["rule_id"], "DQ01", "identifiant attribue")
    eq(c["statut"], "Actif", "statut par defaut")
    eq(len(st.changelog), 1, "entree de journal")


@check("store: modification d'un champ executable -> version incrementee")
def _():
    st = make_store()
    st.add_control({"control_name": "Test", "template": "NOT_NULL",
                    "params": '{"column": "montant"}'}, "yassine")
    st.update_control("DQ01", {"seuil_tolerance_pct": 5}, "yassine", "recalibrage")
    eq(st.control("DQ01")["version"], 2, "version apres modification executable")


@check("store: modification d'un champ non executable -> version inchangee")
def _():
    st = make_store()
    st.add_control({"control_name": "Test", "template": "NOT_NULL",
                    "params": '{"column": "montant"}'}, "yassine")
    st.update_control("DQ01", {"owner": "Autre"}, "yassine")
    eq(st.control("DQ01")["version"], 1, "version apres modification documentaire")


@check("store: le journal enregistre l'auteur, le motif et avant/apres")
def _():
    st = make_store()
    st.add_control({"control_name": "Test", "template": "NOT_NULL",
                    "params": '{"column": "montant"}'}, "yassine")
    st.update_control("DQ01", {"severity": "High"}, "daniel", "escalade demandee")
    entry = next(e for e in st.changelog if e["champ"] == "severity")
    eq(entry["utilisateur"], "daniel", "auteur")
    eq(entry["motif"], "escalade demandee", "motif")
    eq(entry["apres"], "High", "valeur apres")


@check("store: la suppression est interdite")
def _():
    st = make_store()
    st.add_control({"control_name": "T", "template": "NOT_NULL",
                    "params": '{"column": "montant"}'}, "y")
    try:
        st.delete_control("DQ01")
    except PermissionError:
        return
    raise AssertionError("La suppression aurait du etre refusee")


@check("store: suspendre retire des controles actifs sans perdre la definition")
def _():
    st = make_store()
    st.add_control({"control_name": "T", "template": "NOT_NULL",
                    "params": '{"column": "montant"}', "dataset_scope": "ventes"}, "y")
    st.set_statut("DQ01", "Suspendu", "y", "faux positifs")
    eq(len(st.active_controls("ventes")), 0, "controles actifs")
    eq(st.control("DQ01")["control_name"], "T", "definition conservee")


@check("store: le perimetre accepte *, un nom et une liste")
def _():
    from store import scope_matches
    eq(scope_matches("*", "ventes"), True, "joker")
    eq(scope_matches("ventes", "ventes"), True, "nom exact")
    eq(scope_matches("ventes, ref_dev", "ref_dev"), True, "liste")
    eq(scope_matches("ventes", "ref_dev"), False, "hors perimetre")


# --------------------------------------------------------------------------- #
# 2. Validateur : un controle malforme est rejete avant execution
# --------------------------------------------------------------------------- #
@check("validateur: template inconnu rejete")
def _():
    st = make_store()
    errs = E.validate_control({"template": "N_EXISTE_PAS", "params": "{}"}, st, "ventes")
    assert errs and "inconnu" in errs[0].lower(), errs


@check("validateur: parametre requis manquant rejete")
def _():
    st = make_store()
    errs = E.validate_control(
        {"template": "MATCHES_REGEX", "params": '{"column": "devise"}'}, st, "ventes")
    assert any("pattern" in e for e in errs), errs


@check("validateur: colonne non declaree au contrat rejetee")
def _():
    st = make_store()
    errs = E.validate_control(
        {"template": "NOT_NULL", "params": '{"column": "colonne_fantome"}'}, st, "ventes")
    assert any("non declaree" in e for e in errs), errs


@check("validateur: type incompatible rejete (RANGE sur une colonne texte)")
def _():
    st = make_store()
    errs = E.validate_control(
        {"template": "RANGE", "params": '{"column": "devise", "min": 0}'}, st, "ventes")
    assert any("numerique" in e for e in errs), errs


@check("validateur: type incompatible rejete (FRESHNESS sur une mesure)")
def _():
    st = make_store()
    errs = E.validate_control(
        {"template": "FRESHNESS", "params": '{"column": "montant", "max_lag_days": 30}'},
        st, "ventes")
    assert any("date" in e for e in errs), errs


@check("validateur: JSON illisible rejete")
def _():
    st = make_store()
    errs = E.validate_control({"template": "NOT_NULL", "params": "{pas du json}"},
                              st, "ventes")
    assert any("JSON" in e for e in errs), errs


@check("validateur: regex invalide rejetee")
def _():
    st = make_store()
    errs = E.validate_control(
        {"template": "MATCHES_REGEX", "params": '{"column": "devise", "pattern": "([A-Z"}'},
        st, "ventes")
    assert any("reguliere" in e for e in errs), errs


@check("validateur: referentiel cible inconnu rejete")
def _():
    st = make_store()
    errs = E.validate_control(
        {"template": "FOREIGN_KEY",
         "params": '{"column": "devise", "ref_dataset": "absent", "ref_column": "code"}'},
        st, "ventes")
    assert any("Referentiel" in e for e in errs), errs


@check("validateur: un controle correct passe sans erreur")
def _():
    st = make_store()
    eq(E.validate_control({"template": "NOT_NULL", "params": '{"column": "montant"}'},
                          st, "ventes"), [], "controle valide")


# --------------------------------------------------------------------------- #
# 3. Resolution des cibles : une ligne de catalogue, N colonnes reelles
# --------------------------------------------------------------------------- #
@check("cibles: ciblage par colonne -> une cible")
def _():
    st = make_store()
    eq(E.resolve_targets("NOT_NULL", {"column": "montant"}, st, "ventes"),
       [["montant"]], "cible unique")


@check("cibles: ciblage par role -> une cible par colonne portant le role")
def _():
    st = make_store()
    eq(E.resolve_targets("NOT_NULL", {"role": "identifiant"}, st, "ventes"),
       [["vente_id"]], "role identifiant")


@check("cibles: role cle_primaire sur UNIQUE_KEY -> cle composee unique")
def _():
    st = make_store()
    eq(E.resolve_targets("UNIQUE_KEY", {"role": "cle_primaire"}, st, "ventes"),
       [["vente_id"]], "cle primaire")


@check("cibles: le meme controle se propage a un dataset inconnu du catalogue")
def _():
    st = make_store()
    st.upsert_dataset("inventaire", {"libelle": "x", "source": "s.csv", "colonnes": [
        {"colonne": "sku", "type": "string", "role": "identifiant", "cle_primaire": "OUI",
         "obligatoire": "OUI", "description": "", "fk_dataset": "", "fk_colonne": ""},
        {"colonne": "ean", "type": "string", "role": "identifiant", "cle_primaire": "NON",
         "obligatoire": "OUI", "description": "", "fk_dataset": "", "fk_colonne": ""}]},
        "y", "test")
    cibles = E.resolve_targets("NOT_NULL", {"role": "identifiant"}, st, "inventaire")
    eq(cibles, [["sku"], ["ean"]], "propagation par role sans modifier la regle")


# --------------------------------------------------------------------------- #
# 4. Executeurs : resultats attendus sur donnees connues
# --------------------------------------------------------------------------- #
@check("executeur NOT_NULL: 1 valeur absente sur 5")
def _():
    st = make_store()
    n, ko, kpi, _, exc = E.ex_not_null(VENTES, {}, ["montant"], ctx(st))
    eq((n, ko), (5, 1), "lignes testees / KO")
    eq(round(kpi, 1), 80.0, "completude")
    eq(len(exc), 1, "exceptions produites")


@check("executeur RANGE: 1 montant negatif detecte, les nulls ignores")
def _():
    st = make_store()
    n, ko, kpi, _, _ = E.ex_range(VENTES, {"min": 0}, ["montant"], ctx(st))
    eq((n, ko), (4, 1), "4 valeurs testees, 1 hors plage")
    eq(round(kpi, 1), 75.0, "% dans la plage")


@check("executeur MATCHES_REGEX: XXX respecte le format a trois lettres")
def _():
    st = make_store()
    n, ko, _, _, _ = E.ex_matches_regex(
        VENTES, {"pattern": "^[A-Z]{3}$"}, ["devise"], ctx(st))
    eq((n, ko), (5, 0), "le format est valide meme si la devise n'existe pas")


@check("executeur FOREIGN_KEY: XXX absent du referentiel est detecte")
def _():
    st = make_store()
    n, ko, _, _, exc = E.ex_foreign_key(
        VENTES, {"column": "devise", "ref_dataset": "ref_dev", "ref_column": "code"},
        ["devise"], ctx(st))
    eq((n, ko), (5, 1), "un orphelin")
    eq(exc.iloc[0]["valeur"], "XXX", "valeur orpheline")


@check("executeur UNIQUE_KEY: le doublon V4 est detecte deux fois")
def _():
    st = make_store()
    n, ko, kpi, _, _ = E.ex_unique_key(VENTES, {}, ["vente_id"], ctx(st))
    eq((n, ko), (5, 2), "les deux lignes du doublon sont remontees")
    eq(round(kpi, 1), 40.0, "taux de doublons")


@check("executeur IN_DOMAIN: valeur hors liste detectee")
def _():
    st = make_store()
    n, ko, _, _, _ = E.ex_in_domain(
        VENTES, {"values": ["EUR", "USD"]}, ["devise"], ctx(st))
    eq((n, ko), (5, 1), "XXX hors domaine")


@check("executeur FRESHNESS: PASS sous le seuil, FAIL au-dessus")
def _():
    st = make_store()
    _, ko_ok, age, _, _ = E.ex_freshness(
        VENTES, {"max_lag_days": 100}, ["date_vente"], ctx(st))
    _, ko_ko, _, _, exc = E.ex_freshness(
        VENTES, {"max_lag_days": 10}, ["date_vente"], ctx(st))
    eq(age, 69, "anciennete calculee au 2026-09-07 depuis le 2026-06-30")
    eq((ko_ok, ko_ko), (0, 1), "seuil respecte puis depasse")
    eq(len(exc), 1, "exception produite quand le seuil est depasse")


@check("executeur CUSTOM_EXPRESSION: expression fausse sur une ligne")
def _():
    st = make_store()
    n, ko, _, _, _ = E.ex_custom_expression(
        VENTES, {"expression": "devise != 'XXX'"}, [], ctx(st))
    eq((n, ko), (5, 1), "une ligne non conforme")


@check("executeur SUM_RECONCILIATION sens=parties_max: depassement detecte")
def _():
    df = pd.DataFrame({
        "zone": ["TOTAL", "FR", "DE", "TOTAL", "FR", "DE"],
        "an": [2026, 2026, 2026, 2025, 2025, 2025],
        "montant": [100.0, 60.0, 60.0, 100.0, 40.0, 50.0],
    })
    st = make_store()
    p = {"amount": "montant", "total_column": "zone", "total_value": "TOTAL",
         "group_by": ["an"], "sens": "parties_max", "tolerance_pct": 0.1}
    n, ko, kpi, nom, _ = E.ex_sum_reconciliation(df, p, ["montant"], ctx(st))
    eq((n, ko), (2, 1), "2026 depasse (120 > 100), 2025 non (90 < 100)")
    eq(round(kpi), 20, "depassement maximal de 20%")
    eq(nom, "depassement maximal %", "libelle du KPI")


@check("executeur SUM_RECONCILIATION sens=couverture_min: sous-couverture detectee")
def _():
    df = pd.DataFrame({
        "zone": ["TOTAL", "FR", "DE", "TOTAL", "FR", "DE"],
        "an": [2026, 2026, 2026, 2025, 2025, 2025],
        "montant": [100.0, 60.0, 39.0, 100.0, 40.0, 50.0],
    })
    st = make_store()
    p = {"amount": "montant", "total_column": "zone", "total_value": "TOTAL",
         "group_by": ["an"], "sens": "couverture_min", "couverture_min_pct": 95.0}
    n, ko, kpi, _, _ = E.ex_sum_reconciliation(df, p, ["montant"], ctx(st))
    eq((n, ko), (2, 1), "2025 couvre 90% seulement")
    eq(round(kpi, 1), 94.5, "couverture mediane")


# --------------------------------------------------------------------------- #
# 5. Moteur bout en bout sur le catalogue reel
# --------------------------------------------------------------------------- #
@check("moteur: run complet sur les donnees BIS, aucun rejet ni erreur")
def _():
    st = CatalogueStore()
    run = E.run_dq(datasets=["bis_turnover_demo", "ref_devises", "ref_pays"],
                   store=st, run_label="test", write_evidence=False)
    s = run.summary()
    eq(s["erreur"], 0, "aucune erreur d'execution")
    eq(s["rejets"], 0, "aucun controle rejete")
    assert s["controles"] >= 15, f"trop peu de controles executes : {s['controles']}"


@check("moteur: les 6 dimensions du brief sont couvertes")
def _():
    st = CatalogueStore()
    run = E.run_dq(datasets=["bis_turnover_demo"], store=st, write_evidence=False)
    couvertes = set(run.scorecard["dimension"])
    attendues = {"Completeness", "Validity", "Uniqueness", "Consistency",
                 "Timeliness", "Reconciliation"}
    manquantes = attendues - couvertes
    eq(manquantes, set(), "dimensions manquantes")


@check("moteur: un controle non actif n'est jamais execute")
def _():
    st = CatalogueStore()
    run = E.run_dq(datasets=["bis_turnover"], store=st, write_evidence=False)
    deprecies = {c["rule_id"] for c in st.controls if c["statut"] != "Actif"}
    executes = set(run.scorecard["rule_id"])
    eq(deprecies & executes, set(), "controles inactifs executes a tort")


@check("moteur: l'orphelin CLS est detecte sur la jambe 2")
def _():
    st = CatalogueStore()
    run = E.run_dq(datasets=["bis_turnover_demo"], store=st, write_evidence=False)
    exc = run.exceptions
    cls = exc[(exc["rule_id"] == "DQ09") & (exc["valeur"] == "CLS")]
    eq(len(cls), 32, "32 lignes portant un code devise CLS inexistant au referentiel")


@check("moteur: une erreur d'execution est capturee, jamais propagee")
def _():
    st = CatalogueStore()
    st.add_control({"rule_id": "DQZZ", "control_name": "Casse volontairement",
                    "template": "CUSTOM_EXPRESSION",
                    "params": '{"expression": "colonne_absente > 0"}',
                    "dataset_scope": "ref_devises", "severity": "Low",
                    "seuil_tolerance_pct": 0}, "test")
    run = E.run_dq(datasets=["ref_devises"], store=st, write_evidence=False)
    ligne = run.scorecard[run.scorecard["rule_id"] == "DQZZ"]
    eq(len(ligne), 1, "le controle produit une ligne de resultat")
    eq(ligne.iloc[0]["statut"], "ERREUR", "statut ERREUR et non un crash")
    assert ligne.iloc[0]["message"], "le message d'erreur doit etre conserve"


@check("moteur: reproductibilite - deux runs donnent les memes empreintes et KPI")
def _():
    st = CatalogueStore()
    a = E.run_dq(datasets=["bis_turnover_demo"], store=st, write_evidence=False)
    b = E.run_dq(datasets=["bis_turnover_demo"], store=st, write_evidence=False)
    eq(a.manifeste["catalogue_sha256"], b.manifeste["catalogue_sha256"], "hash catalogue")
    eq(a.manifeste["sources"]["bis_turnover_demo"]["sha256"],
       b.manifeste["sources"]["bis_turnover_demo"]["sha256"], "hash source")
    ka = a.scorecard[["rule_id", "cible", "statut", "lignes_ko"]].to_dict("records")
    kb = b.scorecard[["rule_id", "cible", "statut", "lignes_ko"]].to_dict("records")
    eq(ka, kb, "resultats identiques")
    assert a.run_id != b.run_id, "les run_id doivent differer"


@check("moteur: l'evidence pack contient les six pieces attendues")
def _():
    st = CatalogueStore()
    run = E.run_dq(datasets=["ref_devises"], store=st, write_evidence=True)
    attendus = ["manifest.json", "catalogue_snapshot.json", "results.json",
                "rejets.json", "exceptions.csv", "execution.log"]
    for nom in attendus:
        assert (run.evidence_path / nom).exists(), f"piece manquante : {nom}"
    manifeste = json.loads((run.evidence_path / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifeste["catalogue_sha256"]) == 64, "empreinte du catalogue"
    assert manifeste["sources"]["ref_devises"]["sha256"], "empreinte de la source"
    shutil.rmtree(run.evidence_path, ignore_errors=True)


@check("moteur: un dataset declare mais absent du disque est signale, pas fatal")
def _():
    st = CatalogueStore()
    st.upsert_dataset("fantome", {"libelle": "x", "source": "data/absent.csv",
                                  "colonnes": []}, "test", "test")
    run = E.run_dq(datasets=["fantome", "ref_devises"], store=st, write_evidence=False)
    assert any(r["dataset"] == "fantome" for r in run.rejets), "rejet attendu"
    assert len(run.scorecard) > 0, "les autres datasets doivent quand meme tourner"


# --------------------------------------------------------------------------- #
# 6. Rapport Excel
# --------------------------------------------------------------------------- #
@check("rapport: le classeur contient les six onglets attendus")
def _():
    st = CatalogueStore()
    run = E.run_dq(datasets=["ref_devises", "ref_pays"], store=st, write_evidence=False)
    chemin = build_workbook(run, st, TMP / "rapport.xlsx")
    onglets = pd.ExcelFile(chemin).sheet_names
    eq(onglets, ["SYNTHESE", "EXCEPTIONS", "COUVERTURE", "EVIDENCE",
                 "CATALOGUE_EXECUTE", "JOURNAL"], "onglets du classeur")


@check("rapport: l'onglet EVIDENCE porte l'empreinte SHA-256 des sources")
def _():
    st = CatalogueStore()
    run = E.run_dq(datasets=["ref_devises"], store=st, write_evidence=False)
    chemin = build_workbook(run, st, TMP / "rapport2.xlsx")
    texte = pd.read_excel(chemin, "EVIDENCE", header=None).astype(str).to_string()
    assert run.manifeste["sources"]["ref_devises"]["sha256"] in texte, "hash source absent"
    assert run.manifeste["catalogue_sha256"] in texte, "hash catalogue absent"


@check("rapport: l'onglet COUVERTURE liste les controles non executes")
def _():
    st = CatalogueStore()
    run = E.run_dq(datasets=["ref_devises"], store=st, write_evidence=False)
    chemin = build_workbook(run, st, TMP / "rapport3.xlsx")
    texte = pd.read_excel(chemin, "COUVERTURE", header=None).astype(str).to_string()
    assert "DQ15" in texte, "le controle deprecie doit apparaitre comme trou de couverture"


# --------------------------------------------------------------------------- #
# 7. Interface Streamlit : les quatre ecrans se rendent sans exception
# --------------------------------------------------------------------------- #
def _apptest():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=120)


@check("interface: l'ecran Catalogue se rend sans exception")
def _():
    at = _apptest().run()
    assert not at.exception, at.exception


@check("interface: l'ecran Datasets se rend sans exception")
def _():
    at = _apptest().run()
    at.sidebar.radio[0].set_value("Datasets").run()
    assert not at.exception, at.exception


@check("interface: l'ecran Execution se rend sans exception")
def _():
    at = _apptest().run()
    at.sidebar.radio[0].set_value("Execution").run()
    assert not at.exception, at.exception


@check("interface: l'ecran Journal se rend sans exception")
def _():
    at = _apptest().run()
    at.sidebar.radio[0].set_value("Journal").run()
    assert not at.exception, at.exception


@check("interface: l'editeur de regle se rend et propose les templates")
def _():
    at = _apptest().run()
    boutons = [b for b in at.button if "Ajouter" in b.label]
    assert boutons, "bouton d'ajout absent"
    boutons[0].click().run()
    assert not at.exception, at.exception
    labels = [s.label for s in at.selectbox]
    assert any("Template" in str(x) for x in labels), f"selecteur de template absent: {labels}"


# --------------------------------------------------------------------------- #
def main() -> int:
    largeur = max(len(n) for n, _, _ in RESULTS)
    ok = 0
    for nom, reussi, detail in RESULTS:
        marque = "PASS" if reussi else "FAIL"
        print(f"[{marque}] {nom.ljust(largeur)}")
        if not reussi:
            print("        " + detail.replace("\n", "\n        ").rstrip())
        ok += reussi
    print(f"\n{ok}/{len(RESULTS)} tests reussis")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
