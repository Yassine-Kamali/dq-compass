"""
Harnais de test DQ Compass. Aucune dependance externe : lancez simplement

    .venv/Scripts/python.exe tests/test_dq.py

Couvre les couches : store (gouvernance), profilage, validateur, exécuteurs,
moteur bout en bout, rapport Excel, et le rendu des trois écrans Streamlit.
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
import suggestions as S  # noqa: E402
from excel_report import build_workbook  # noqa: E402
from store import (CatalogueStore, libelle_severite,  # noqa: E402
                   phrase_controle)

RESULTS: list[tuple[str, bool, str]] = []

# Le fichier servant de reference aux tests bout en bout.
FICHIER_DEMO = ROOT / "data" / "prepared" / "bis_turnover_demo.csv"


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
    st.data = {"meta": {"schema": "2.0"}, "templates": [],
               "controls": [], "changelog": []}
    st.data["templates"] = [
        {"template_id": "NOT_NULL", "dimension": "Completeness",
         "params_requis": "column | colonnes_motif", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
        {"template_id": "RANGE", "dimension": "Validity",
         "params_requis": "column, min | max", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
        {"template_id": "MATCHES_REGEX", "dimension": "Validity",
         "params_requis": "column, pattern", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
        {"template_id": "FOREIGN_KEY", "dimension": "Consistency",
         "params_requis": "column, ref_fichier, ref_column", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
        {"template_id": "FRESHNESS", "dimension": "Timeliness",
         "params_requis": "column, max_lag_days", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "j", "exemple_params": ""},
        {"template_id": "UNIQUE_KEY", "dimension": "Uniqueness",
         "params_requis": "columns | colonnes_motif", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
    ]
    return st


VENTES = pd.DataFrame({
    "vente_id": ["V1", "V2", "V3", "V4", "V4"],
    "devise":   ["EUR", "USD", "XXX", "EUR", "EUR"],
    "montant":  [100.0, -5.0, 50.0, None, 20.0],
    "date_vente": ["2026-01-01", "2026-06-30", "2026-06-30", "2026-06-30", "2026-06-30"],
})
REF_DEV = pd.DataFrame({"code": ["EUR", "USD"]})
REF_DEV_CSV = TMP / "ref_dev.csv"
REF_DEV.to_csv(REF_DEV_CSV, index=False)

# Le profil remplace le contrat declare : il est deduit du DataFrame lui-meme.
PROFIL_VENTES = E.profiler(VENTES)


def store_bis() -> CatalogueStore:
    """Catalogue avec les regles propres au jeu BIS remises en service.

    Le catalogue livre ne contient que des regles generiques : celles qui
    nomment `turnover_notionnel` ou `DER_CURR_LEG2` sont conservees mais hors
    service. Les tests bout en bout de ce jeu de donnees les reactivent ici, en
    memoire uniquement - rien n'est ecrit sur disque.
    """
    st = CatalogueStore()
    for c in st.controls:
        if c["statut"] == "Deprecie":
            c["statut"] = "Actif"
    return st


def ctx(store: CatalogueStore, as_of="2026-09-07") -> dict:
    return {"load_ref": lambda chemin: REF_DEV,
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


@check("store: une suppression exige un motif")
def _():
    st = make_store()
    st.add_control({"rule_id": "DQX1", "control_name": "A supprimer",
                    "template": "NOT_NULL", "params": '{"column": "montant"}'}, "u")
    try:
        st.delete_control("DQX1", "u", "   ")
    except ValueError:
        return
    raise AssertionError("une suppression sans motif aurait du etre refusee")


@check("store: une regle supprimee laisse sa definition complete au journal")
def _():
    """La suppression est possible parce que rien ne se perd : le journal garde
    la definition, et chaque pack de preuves embarque le catalogue du moment."""
    st = make_store()
    st.add_control({"rule_id": "DQX2", "control_name": "Doublon a retirer",
                    "template": "NOT_NULL", "params": '{"column": "montant"}'}, "u")
    st.delete_control("DQX2", "claire", "fait double emploi avec DQX1")
    eq(st.control("DQX2"), None, "la regle a quitte le catalogue")
    traces = [e for e in st.changelog if e["action"] == "SUPPRESSION"]
    eq(len(traces), 1, "la suppression est journalisee")
    assert "Doublon a retirer" in traces[0]["avant"], \
        "la definition complete doit rester lisible au journal"
    eq(traces[0]["motif"], "fait double emploi avec DQX1", "motif conserve")
    eq(traces[0]["utilisateur"], "claire", "auteur conserve")


@check("store: supprimer une regle inconnue est refuse")
def _():
    st = make_store()
    try:
        st.delete_control("DQ404", "u", "motif")
    except ValueError:
        return
    raise AssertionError("une regle inconnue ne peut pas etre supprimee")


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
    assert errs and "unknown" in errs[0].lower(), errs


@check("validateur: parametre requis manquant rejete")
def _():
    st = make_store()
    errs = E.validate_control(
        {"template": "MATCHES_REGEX", "params": '{"column": "devise"}'}, st, "ventes")
    assert any("pattern" in e for e in errs), errs


def _raison(template: str, params: dict, profil=None) -> str | None:
    """Pose au profil la question : cette regle concerne-t-elle ce fichier ?"""
    profil = PROFIL_VENTES if profil is None else profil
    cibles = E.resolve_targets(template, params, profil)
    return E.applicabilite(template, params, cibles, profil)


@check("applicabilite: une colonne absente du fichier met la regle hors perimetre")
def _():
    raison = _raison("NOT_NULL", {"column": "colonne_fantome"})
    assert raison and "absent" in raison, raison


@check("applicabilite: RANGE sur une colonne texte met la regle hors perimetre")
def _():
    raison = _raison("RANGE", {"column": "devise", "min": 0})
    assert raison and "numeric" in raison, raison


@check("applicabilite: FRESHNESS sur une mesure met la regle hors perimetre")
def _():
    raison = _raison("FRESHNESS", {"column": "montant", "max_lag_days": 30})
    assert raison and "date" in raison, raison


@check("applicabilite: une regle dont la cible existe est bien applicable")
def _():
    eq(_raison("NOT_NULL", {"column": "montant"}), None, "cible presente")


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
    assert any("regular expression" in e for e in errs), errs


@check("validateur: fichier de reference introuvable rejete")
def _():
    st = make_store()
    errs = E.validate_control(
        {"template": "FOREIGN_KEY",
         "params": '{"column": "devise", "ref_fichier": "data/absent.csv", '
                   '"ref_column": "code"}'},
        st)
    assert any("not found" in e for e in errs), errs


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
    eq(E.resolve_targets("NOT_NULL", {"column": "montant"}, PROFIL_VENTES),
       [["montant"]], "cible unique")


@check("cibles: ciblage par motif -> une cible par colonne dont le nom correspond")
def _():
    eq(E.resolve_targets("NOT_NULL", {"colonnes_motif": "(?i)(^|_)id$"}, PROFIL_VENTES),
       [["vente_id"]], "motif d identifiant")


@check("cibles: UNIQUE_KEY sur cle composee -> une seule cible multi-colonnes")
def _():
    eq(E.resolve_targets("UNIQUE_KEY", {"columns": ["vente_id", "devise"]},
                         PROFIL_VENTES),
       [["vente_id", "devise"]], "cle composee")


@check("cibles: la meme regle se propage a un fichier jamais declare")
def _():
    autre = E.profiler(pd.DataFrame({"sku_id": ["A"], "ean_id": ["B"],
                                     "libelle": ["C"]}))
    eq(E.resolve_targets("NOT_NULL", {"colonnes_motif": "(?i)(^|_)id$"}, autre),
       [["sku_id"], ["ean_id"]],
       "une regle ecrite pour les ventes couvre un inventaire sans etre modifiee")


MOTIFS_DATES = {"motif_avant": "(?i)(^|_)(debut|start|order)(_|$)",
                "motif_apres": "(?i)(^|_)(fin|end|ship)(_|$)"}


@check("cibles: DATE_ORDER apparie deux dates sur leur radical commun")
def _():
    """`date_debut` va avec `date_fin` parce qu'il ne reste que « date » une
    fois le marqueur retire. Aucun de ces mots n'est ecrit dans le moteur : les
    deux marqueurs sont des parametres de la regle."""
    profil = E.profiler(pd.DataFrame({
        "date_debut": ["2026-01-01"], "date_fin": ["2026-01-05"],
        "start_sejour": ["2026-01-02"], "end_sejour": ["2026-01-04"]}))
    eq(E.resolve_targets("DATE_ORDER", MOTIFS_DATES, profil),
       [["date_debut", "date_fin"], ["start_sejour", "end_sejour"]],
       "chaque paire reste dans son evenement")


@check("cibles: DATE_ORDER n apparie que des colonnes de date")
def _():
    """`order_status` et `payment_status` forment une paire parfaite au regard
    de leurs seuls noms. Le type deduit est ce qui l'empeche."""
    profil = E.profiler(pd.DataFrame({
        "order_status": ["OUVERT"] * 3, "shipped_status": ["EXPEDIE"] * 3}))
    eq(E.resolve_targets("DATE_ORDER", MOTIFS_DATES, profil), [],
       "deux libelles ne sont pas deux dates")


@check("cibles: ROW_SUM_RECONCILIATION ecarte les composantes non sommables")
def _():
    """Un motif est un filet, pas une designation : ce qu'il ramene de non
    numerique est ecarte plutot que de faire echouer la regle."""
    profil = E.profiler(pd.DataFrame({
        "total_ttc": [120.0], "montant_ht": [100.0], "montant_tva": [20.0],
        "montant_libelle": ["vingt"]}))
    eq(E.resolve_targets("ROW_SUM_RECONCILIATION",
                         {"motif_total": "(?i)(^|_)total(_|$)",
                          "motif_parties": "(?i)^montant(_|$)"}, profil),
       [["total_ttc", "montant_ht", "montant_tva"]],
       "le total en tete, ses composantes chiffrees ensuite")


# --------------------------------------------------------------------------- #
# 2 bis. Genericite du catalogue livre
# --------------------------------------------------------------------------- #
@check("catalogue: aucune regle en service ne nomme une colonne particuliere")
def _():
    """Annexe A.4 : les controles doivent etre « independent of datasets
    (reusable) ». Une regle qui nomme `turnover_notionnel` ne l'est pas, quelle
    que soit sa portee. Le ciblage se fait par motif de nom de colonne."""
    st = CatalogueStore()
    nommantes = []
    for c in st.active_controls():
        params = E.parse_params(c.get("params"))
        cite = (params.get("column") or params.get("columns")
                or params.get("before") or params.get("after")
                or params.get("field_a") or params.get("amount")
                or params.get("expression"))
        if cite:
            nommantes.append(f"{c['rule_id']} ({cite})")
    eq(nommantes, [], "regles liees a un jeu de donnees particulier")


@check("catalogue: le socle livre couvre les six dimensions du brief")
def _():
    """§2.2 : les six dimensions sont le perimetre minimum. Le brief n'impose
    aucun NOMBRE de regles - il impose cette couverture, et les 14 attributs."""
    st = CatalogueStore()
    dimensions = {c["control_type"] for c in st.active_controls()}
    manquantes = [d for d in ["Completeness", "Validity", "Uniqueness",
                              "Consistency", "Timeliness", "Reconciliation"]
                  if d not in dimensions]
    eq(manquantes, [], f"dimensions sans aucune regle en service : {dimensions}")


@check("catalogue: le socle s applique a des fichiers de metiers differents")
def _():
    """Le meme catalogue, sans une ligne de configuration, doit trouver quelque
    chose sur des fichiers qui n'ont rien en commun."""
    st = CatalogueStore()
    sante = pd.DataFrame({
        "patient_id": ["P1", None, "P3"] * 10,
        "total_cost_eur": [10.0, -5.0, 20.0] * 10,
    })
    logistique = pd.DataFrame({
        "order_id": ["O1", "O2", None] * 10,
        "country_code": ["FR", "be", "DE"] * 10,
        "quantity": [1, -2, 3] * 10,
    })
    for nom, df in [("sante", sante), ("logistique", logistique)]:
        chemin = TMP / f"socle_{nom}.csv"
        df.to_csv(chemin, index=False)
        run = E.run_dq(chemin, store=st, write_evidence=False)
        resume = run.summary()
        assert resume["fail"] >= 2, f"{nom}: le socle doit trouver des ecarts"
        eq(resume["error"], 0, f"{nom}: aucune erreur technique")


@check("reconciliation: rapprocher deux sources fonctionne bout en bout")
def _():
    """La sixieme dimension ne peut pas etre proposee automatiquement - elle
    suppose une seconde source, que seul un humain peut designer. La capacite
    doit donc etre demontree ici : une regle qui pointe un second fichier est
    acceptee par le validateur, s execute, et chiffre l ecart."""
    source, cible = TMP / "recon_source.csv", TMP / "recon_cible.csv"
    pd.DataFrame({"id": [f"L{i}" for i in range(100)]}).to_csv(source, index=False)
    pd.DataFrame({"id": [f"L{i}" for i in range(97)]}).to_csv(cible, index=False)

    st = CatalogueStore()
    regle = {
        "rule_id": "DQ99", "control_name": "Rapprochement du nombre de lignes",
        "control_type": "Reconciliation", "template": "COUNT_RECONCILIATION",
        "params": json.dumps({"ref_fichier": str(cible).replace("\\", "/")}),
        "dataset_scope": "recon_source", "severity": "High",
        "seuil_tolerance_pct": 0, "description": "x", "remediation_action": "y",
        "owner": "z", "frequency": "A la demande",
        "output_type": "Exception report", "kpi": "", "logic_definition": "",
        "data_element": "",
    }
    eq(E.validate_control(regle, st, pathlib.Path("/")), [], "regle acceptee")
    st.add_control(regle, "test", "demonstration de la reconciliation")

    run = E.run_dq(source, store=st, write_evidence=False)
    ligne = run.scorecard[run.scorecard["rule_id"] == "DQ99"]
    eq(len(ligne), 1, "le controle produit une ligne de resultat")
    eq(ligne.iloc[0]["statut"], "FAIL", "trois lignes manquent a la cible")
    eq(int(ligne.iloc[0]["kpi_valeur"]), 3, "ecart chiffre")


# --------------------------------------------------------------------------- #
# 3 bis. Suggestions : un fichier inconnu propose ses propres controles
# --------------------------------------------------------------------------- #
def _sale() -> pd.DataFrame:
    """Fichier synthetique porteur d'un defaut par dimension."""
    lignes = 100
    return pd.DataFrame({
        # quasi unique : 2 doublons -> Uniqueness
        "ref_ligne": [f"R{i:04d}" for i in range(lignes - 2)] + ["R0000", "R0001"],
        # 2 trous -> Completeness
        "libelle": [None, None] + [f"produit {i}" for i in range(lignes - 2)],
        # 2 modalites marginales -> Validity (domaine)
        "categorie": ["A"] * 49 + ["B"] * 49 + ["ZZZ", "QQQ"],
        # 1 valeur negative -> Validity (bornes)
        "montant": [-42.0] + [float(10 + i % 30) for i in range(lignes - 1)],
        "debut": ["2026-01-01"] * lignes,
        # 3 sorties avant entree -> Consistency
        "fin": ["2025-01-01"] * 3 + ["2026-02-01"] * (lignes - 3),
    })


@check("suggestions: un fichier inconnu couvre cinq dimensions sans rien declarer")
def _():
    df = _sale()
    props = S.suggerer(df, E.profiler(df), "sale", as_of=dt.date(2026, 3, 1))
    dims = {p["dimension"] for p in props}
    for attendue in ["Completeness", "Validity", "Uniqueness", "Consistency",
                     "Timeliness"]:
        assert attendue in dims, f"{attendue} absente de {dims}"


@check("suggestions: chaque defaut seme est retrouve et chiffre")
def _():
    df = _sale()
    props = S.suggerer(df, E.profiler(df), "sale", as_of=dt.date(2026, 3, 1))
    par_template = {}
    for p in props:
        cible = p["params"].get("column") or ",".join(p["params"].get("columns", []))
        par_template[(p["template"], cible)] = p["impact"]
    eq(par_template.get(("NOT_NULL", "libelle")), 2, "deux libelles vides")
    eq(par_template.get(("UNIQUE_KEY", "ref_ligne")), 4, "deux doublons, quatre lignes")
    eq(par_template.get(("IN_DOMAIN", "categorie")), 2, "deux modalites marginales")
    eq(par_template.get(("RANGE", "montant")), 1, "un montant negatif")
    eq(par_template.get(("DATE_ORDER", "")), 3, "trois dates inversees")


@check("suggestions: une distribution etalee ne produit pas de fausse anomalie")
def _():
    # Une exponentielle a une longue traine : l'ecart interquartile en signale
    # beaucoup, mais ce n'est pas un defaut - c'est la forme de la loi.
    valeurs = [float(2 ** (i % 20)) for i in range(1000)]
    df = pd.DataFrame({"montant": valeurs})
    props = S.suggerer(df, E.profiler(df), "expo")
    bornes = [p for p in props if p["template"] == "RANGE"]
    assert not bornes, f"aucune borne ne devrait etre proposee, obtenu {bornes}"


@check("suggestions: le module ne decide rien, il ne touche pas au catalogue")
def _():
    st = CatalogueStore(TMP / "store_sug.json")
    st.data = {"meta": {"schema": "2.0"}, "templates": [], "controls": [],
               "changelog": []}
    df = _sale()
    S.suggerer(df, E.profiler(df), "sale")
    eq(len(st.controls), 0, "aucun controle ecrit sans acceptation humaine")


@check("suggestions: une proposition acceptee devient une ligne de catalogue valide")
def _():
    st = CatalogueStore()
    df = _sale()
    props = S.suggerer(df, E.profiler(df), "sale", as_of=dt.date(2026, 3, 1))
    for p in props:
        controle = S.en_controle(p, "DQ99")
        for attribut in ["rule_id", "control_name", "control_type", "description",
                         "dataset_scope", "data_element", "seuil_tolerance_pct",
                         "severity", "frequency", "owner", "output_type",
                         "remediation_action"]:
            assert attribut in controle, f"{attribut} manquant sur {p['cle']}"
        eq(E.validate_control(controle, st), [], f"proposition invalide : {p['cle']}")


@check("suggestions: le meme fichier donne toujours les memes propositions")
def _():
    df = _sale()
    a = S.suggerer(df, E.profiler(df), "sale", as_of=dt.date(2026, 3, 1))
    b = S.suggerer(df, E.profiler(df), "sale", as_of=dt.date(2026, 3, 1))
    eq([p["cle"] for p in a], [p["cle"] for p in b], "ordre reproductible")
    eq([p["impact"] for p in a], [p["impact"] for p in b], "impacts reproductibles")


@check("robustesse: la chaine complete tient sur des fichiers degeneres")
def _():
    """Sept fichiers pathologiques : aucun ne doit faire tomber la chaine.

    « Plug-and-play […] integrated into any EUC » se verifie sur les cas laids,
    pas sur le fichier de demonstration.
    """
    cas = {
        "une_seule_ligne": pd.DataFrame({"a": [1], "b": ["x"]}),
        "colonne_toute_vide": pd.DataFrame({"a": [None] * 50,
                                            "b": list(range(50))}),
        "une_seule_colonne": pd.DataFrame({"seule": [f"v{i}" for i in range(50)]}),
        "types_melanges": pd.DataFrame({"m": [1, "deux", 3.5, None,
                                              "2026-01-01"] * 10}),
        "noms_bizarres": pd.DataFrame({"a b/c": [1] * 30, "": [2] * 30,
                                       "é#$": [3] * 30}),
        "tout_identique": pd.DataFrame({"c": ["K"] * 40, "d": [7] * 40}),
        "sans_ligne": pd.DataFrame({"a": pd.Series(dtype=float),
                                    "b": pd.Series(dtype=object)}),
    }
    st = CatalogueStore()
    for nom, df in cas.items():
        profil = E.profiler(df)
        eq(len(profil), len(df.columns), f"{nom}: une ligne de profil par colonne")
        S.suggerer(df, profil, nom)
        chemin = TMP / f"{nom}.csv"
        df.to_csv(chemin, index=False)
        run = E.run_dq(chemin, store=st, write_evidence=False)
        eq(run.summary()["error"], 0, f"{nom}: aucune erreur technique")


@check("suggestions: un fichier trop court ne fait pas parler les statistiques")
def _():
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "zz"]})
    props = S.suggerer(df, E.profiler(df), "minuscule")
    interdits = {"IN_DOMAIN", "RANGE", "MATCHES_REGEX"}
    assert not [p for p in props if p["template"] in interdits], \
        "aucune statistique ne doit etre tiree de trois lignes"


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
        VENTES, {"column": "devise", "ref_fichier": "data/ref_dev.csv",
                 "ref_column": "code"},
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
    eq(nom, "maximum overshoot %", "libelle du KPI")


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


@check("executeur ROW_SUM_RECONCILIATION: le total faux est le seul en ecart")
def _():
    df = pd.DataFrame({
        "total_ttc": [120.0, 130.0, 120.005, 50.0],
        "montant_ht": [100.0, 100.0, 100.0, None],
        "montant_tva": [20.0, 20.0, 20.0, 10.0],
    })
    st = make_store()
    p = {"tolerance_pct": 0.01}
    n, ko, kpi, nom, exc = E.ex_row_sum_reconciliation(
        df, p, ["total_ttc", "montant_ht", "montant_tva"], ctx(st))
    eq((n, ko), (3, 1), "la ligne a composante manquante n'est pas testee")
    eq(round(kpi, 1), 66.7, "% de lignes rapprochees")
    eq(nom, "% reconciled rows", "libelle du KPI")
    eq(exc.iloc[0]["valeur"], "130.0", "le total declare faux")


@check("executeur ROW_SUM_RECONCILIATION: la tolerance absorbe l'arrondi")
def _():
    df = pd.DataFrame({"total": [100.004], "montant_a": [50.0],
                       "montant_b": [50.0]})
    st = make_store()
    _, sans, _, _, _ = E.ex_row_sum_reconciliation(
        df, {}, ["total", "montant_a", "montant_b"], ctx(st))
    _, avec, _, _, _ = E.ex_row_sum_reconciliation(
        df, {"tolerance_pct": 0.01}, ["total", "montant_a", "montant_b"], ctx(st))
    eq((sans, avec), (1, 0), "sans tolerance l'ecart compte, avec il est absorbe")


# --------------------------------------------------------------------------- #
# 5. Reconnaissance du fichier et langage metier
# --------------------------------------------------------------------------- #
@check("profilage: les types sont deduits du contenu, jamais declares")
def _():
    types = {c["colonne"]: c["type"] for c in PROFIL_VENTES}
    eq(types["montant"], "decimal", "montant")
    eq(types["date_vente"], "date", "date_vente")
    eq(types["devise"], "string", "devise")


@check("profilage: une colonne d annees n est pas prise pour une date")
def _():
    profil = E.profiler(pd.DataFrame({"annee": [1986, 2019, 2022]}))
    eq(profil[0]["type"], "integer", "une annee reste un entier")


@check("profilage: la cle candidate sert d identifiant de ligne")
def _():
    profil = E.profiler(pd.DataFrame({"id": ["A", "B"], "v": [1, 1]}))
    eq(E.cle_candidate(profil), "id", "colonne integralement unique")
    eq(E.cle_candidate(E.profiler(pd.DataFrame({"v": [1, 1]}))), None,
       "aucune colonne unique")


@check("plug-and-play: un fichier jamais vu est controle sans etre declare")
def _():
    st = CatalogueStore()
    inconnu = TMP / "inconnu.csv"
    pd.DataFrame({"aaa": [1], "bbb": [2]}).to_csv(inconnu, index=False)
    run = E.run_dq(inconnu, store=st, write_evidence=False)
    eq(run.contrat, "inconnu", "le nom du fichier tient lieu de nom de jeu")
    eq(set(run.scorecard["statut"]), {"SKIPPED"},
       "les regles universelles sont evaluees puis declarees hors perimetre")
    eq(run.summary()["error"], 0, "aucune erreur technique sur un fichier inconnu")


@check("portee: le nom du fichier peut etre impose plutot que deduit")
def _():
    st = CatalogueStore()
    run = E.run_dq(FICHIER_DEMO, dataset="bis_turnover", store=st,
                   write_evidence=False)
    eq(run.contrat, "bis_turnover", "nom impose")


@check("chargement: CSV et Excel sont acceptes, les autres formats refuses")
def _():
    chemin = TMP / "petit.xlsx"
    pd.DataFrame({"a": [1, 2]}).to_excel(chemin, index=False)
    eq(len(E.charger_fichier(chemin)), 2, "lecture Excel")
    mauvais = TMP / "note.docx"
    mauvais.write_text("x", encoding="utf-8")
    try:
        E.charger_fichier(mauvais)
    except ValueError:
        return
    raise AssertionError("un format non gere aurait du etre refuse")


@check("langage metier: chaque template porte un libelle comprehensible")
def _():
    st = CatalogueStore()
    sans = [t["template_id"] for t in st.templates if not t.get("libelle_metier")]
    eq(sans, [], "templates sans libelle metier")


@check("langage metier: chaque controle se rend en une phrase francaise")
def _():
    st = CatalogueStore()
    for c in st.controls:
        phrase = phrase_controle(c, st)
        assert phrase and len(phrase) > 15, f"{c['rule_id']} : phrase vide ou trop courte"
        # Un gabarit non rempli laisserait un nom de parametre entre accolades.
        # Les accolades venant d'une valeur -- une expression reguliere comme
        # ^([A-Z]{3}|TO1)$ -- sont legitimes et ne doivent pas alerter.
        tpl = st.template(c["template"])
        restants = [p for p in tpl.get("params_libelles", {}) if "{" + p + "}" in phrase]
        eq(restants, [], f"{c['rule_id']} : parametres non substitues dans « {phrase} »")
        assert "…" not in phrase, f"{c['rule_id']} : parametre manquant -> {phrase}"


@check("langage metier: la phrase s'adapte au ciblage et au mode d'execution")
def _():
    st = CatalogueStore()
    eq(phrase_controle(st.control("DQ02"), st),
       "Every column whose name matches « (?i)(^|_)id$ » must be filled on "
       "every row.", "par motif de colonnes")
    eq(phrase_controle(st.control("DQ03"), st),
       "Column « turnover_notionnel » must be greater than or equal to 0.",
       "borne minimale seule")
    assert "must never exceed" in phrase_controle(st.control("DQ13"), st)
    assert "must cover at least" in phrase_controle(st.control("DQ16"), st)


@check("langage metier: les severites sont traduites")
def _():
    eq(libelle_severite("Critical"), "Blocking", "Critical")
    eq(libelle_severite("Low"), "Minor", "Low")


# --------------------------------------------------------------------------- #
# 6. Moteur bout en bout sur le catalogue reel
# --------------------------------------------------------------------------- #
@check("moteur: run complet sur les donnees BIS, aucun rejet ni erreur")
def _():
    st = store_bis()
    run = E.run_dq(FICHIER_DEMO, store=st, run_label="test", write_evidence=False)
    s = run.summary()
    eq(s["error"], 0, "aucune erreur d'execution")
    eq(s["rejected"], 0, "aucun controle rejete")
    assert s["controls"] >= 12, f"trop peu de controles executes : {s['controls']}"


@check("moteur: les 6 dimensions du brief sont couvertes")
def _():
    st = store_bis()
    run = E.run_dq(FICHIER_DEMO, store=st, write_evidence=False)
    attendues = {"Completeness", "Validity", "Uniqueness", "Consistency",
                 "Timeliness", "Reconciliation"}
    eq(attendues - set(run.scorecard["dimension"]), set(), "dimensions manquantes")


@check("moteur: un controle non actif n'est jamais execute")
def _():
    st = CatalogueStore()
    run = E.run_dq(FICHIER_DEMO, store=st, write_evidence=False)
    inactifs = {c["rule_id"] for c in st.controls if c["statut"] != "Actif"}
    eq(inactifs & set(run.scorecard["rule_id"]), set(), "controles inactifs executes")


@check("moteur: toute regle est tentee sur tout fichier, l applicabilite tranche")
def _():
    """La portee ne filtre plus par nom de fichier : c'est la presence des
    colonnes qui decide, et une regle hors sujet le dit au lieu de disparaitre."""
    st = store_bis()
    run = E.run_dq(ROOT / "data" / "ref" / "ref_devises.csv", store=st,
                   write_evidence=False)
    eq(run.contrat, "ref_devises", "nom du fichier")
    statuts = run.scorecard.set_index("rule_id")["statut"].to_dict()
    assert "DQ01" in statuts, "une regle universelle doit etre tentee partout"
    eq(statuts["DQ01"], "SKIPPED",
       "faute de colonne, elle est hors perimetre : ni echec, ni erreur")
    eq(statuts.get("DQ14"), "PASS", "la regle qui trouve ses colonnes s execute")
    eq(statuts.get("DQ02"), "SKIPPED",
       "le referentiel des devises ne porte aucune colonne d identifiant")
    eq(run.summary()["error"], 0, "aucune erreur technique")


@check("moteur: le referentiel est charge tout seul pour l'integrite referentielle")
def _():
    st = store_bis()
    run = E.run_dq(FICHIER_DEMO, store=st, write_evidence=False)
    sources = run.manifeste["sources"]
    chemin = "data/ref/ref_devises.csv"
    assert chemin in sources, \
        "le referentiel doit etre charge sans que l'utilisateur le fournisse"
    assert sources[chemin]["sha256"], "et etre empreinte comme les autres"


@check("moteur: l'orphelin CLS est detecte sur la jambe 2")
def _():
    st = store_bis()
    run = E.run_dq(FICHIER_DEMO, store=st, write_evidence=False)
    exc = run.exceptions
    cls = exc[(exc["rule_id"] == "DQ09") & (exc["valeur"] == "CLS")]
    eq(len(cls), 32, "32 lignes portant un code devise CLS inexistant au referentiel")


@check("moteur: une erreur d'execution est capturee, jamais propagee")
def _():
    st = CatalogueStore()
    st.add_control({"rule_id": "DQZZ", "control_name": "Casse volontairement",
                    "template": "CUSTOM_EXPRESSION",
                    # La colonne existe, donc l applicabilite laisse passer :
                    # c est la comparaison d un texte a un nombre qui casse a
                    # l execution. Une colonne absente serait SKIPPED.
                    "params": '{"expression": "code_devise > 0"}',
                    "dataset_scope": "*", "severity": "Low",
                    "seuil_tolerance_pct": 0}, "test")
    run = E.run_dq(ROOT / "data" / "ref" / "ref_devises.csv", store=st,
                   write_evidence=False)
    ligne = run.scorecard[run.scorecard["rule_id"] == "DQZZ"]
    eq(len(ligne), 1, "le controle produit une ligne de resultat")
    eq(ligne.iloc[0]["statut"], "ERROR", "statut ERROR et non un crash")
    assert ligne.iloc[0]["message"], "le message d'erreur doit etre conserve"


@check("moteur: reproductibilite - deux runs donnent les memes empreintes et KPI")
def _():
    st = CatalogueStore()
    a = E.run_dq(FICHIER_DEMO, store=st, write_evidence=False)
    b = E.run_dq(FICHIER_DEMO, store=st, write_evidence=False)
    eq(a.manifeste["catalogue_sha256"], b.manifeste["catalogue_sha256"], "hash catalogue")
    eq(a.manifeste["sources"]["bis_turnover_demo"]["sha256"],
       b.manifeste["sources"]["bis_turnover_demo"]["sha256"], "hash source")
    colonnes = ["rule_id", "cible", "statut", "lignes_ko"]
    eq(a.scorecard[colonnes].to_dict("records"),
       b.scorecard[colonnes].to_dict("records"), "resultats identiques")
    assert a.run_id != b.run_id, "les run_id doivent differer"


@check("moteur: l'evidence pack porte les pieces de l'annexe B.2")
def _():
    st = CatalogueStore()
    run = E.run_dq(ROOT / "data" / "ref" / "ref_devises.csv", store=st,
                   write_evidence=True)
    for nom in ["manifest.json", "catalogue_snapshot.json", "results.json",
                "executed_rules.json", "summary.json", "rejected_rules.json",
                "exceptions.csv", "execution.log", "checksums.json"]:
        assert (run.evidence_path / nom).exists(), f"piece manquante : {nom}"
    manifeste = json.loads((run.evidence_path / "manifest.json").read_text(encoding="utf-8"))
    eq(len(manifeste["catalogue_sha256"]), 64, "empreinte du catalogue")
    assert manifeste["fichier_controle"].endswith("ref_devises.csv"), "fichier trace"
    eq(manifeste["fichier_nom"], "ref_devises", "nom du fichier trace")
    assert manifeste["profil_fichier"], "le profil deduit fait partie des preuves"
    eq([c["colonne"] for c in manifeste["profil_fichier"]],
       ["code_devise", "libelle_devise", "type"], "structure tracee dans les preuves")
    shutil.rmtree(run.evidence_path, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 6 bis. Audit : statut global, integrite du pack, rejeu
# --------------------------------------------------------------------------- #
def _resultat(statut: str, severity: str = "Medium") -> E.ControlResult:
    return E.ControlResult(
        rule_id="DQ99", control_name="T", dimension="Validity", template="RANGE",
        dataset="d", cible="c", statut=statut, lignes_testees=10, lignes_ko=1,
        taux_ko_pct=10.0, kpi_nom="%", kpi_valeur=90, seuil_pct=0.0,
        severity=severity, owner="o", frequency="On demand",
        remediation_action="r", version=1, duree_s=0.0, message="m")


@check("audit: le statut global suit les regles RED / AMBER / GREEN")
def _():
    eq(E.statut_global([_resultat("PASS")]), "GREEN", "aucun ecart")
    eq(E.statut_global([_resultat("ERROR", "Low")]), "RED", "une erreur technique")
    eq(E.statut_global([_resultat("FAIL", "High")]), "RED", "un echec bloquant")
    eq(E.statut_global([_resultat("FAIL", "Low")]), "AMBER", "un echec mineur")
    eq(E.statut_global([_resultat("PASS"), _resultat("SKIPPED")]), "AMBER",
       "un controle hors perimetre sans echec")


def _pack_de_test() -> tuple:
    st = CatalogueStore()
    run = E.run_dq(ROOT / "data" / "exemples" / "inventaire.csv", store=st,
                   write_evidence=True, run_label="test audit")
    return run, run.evidence_path


@check("audit: un pack fraichement ecrit est verifie conforme")
def _():
    run, pack = _pack_de_test()
    try:
        rapport = E.verifier_pack(pack)
        eq(rapport["verdict"], "VERIFIED",
           str([c for c in rapport["controles"] if c["statut"] == "GAP"]))
        assert len(rapport["controles"]) >= 8, "trop peu de controles d'audit"
    finally:
        shutil.rmtree(pack, ignore_errors=True)


@check("audit: une piece modifiee apres coup est detectee")
def _():
    """C'est tout l'objet de checksums.json : un pack altere ne passe plus."""
    run, pack = _pack_de_test()
    try:
        (pack / "results.json").write_text("[]", encoding="utf-8")
        rapport = E.verifier_pack(pack)
        eq(rapport["verdict"], "GAP", "un pack falsifie doit etre signale")
        integrite = next(c for c in rapport["controles"]
                         if c["controle"].startswith("Integrity"))
        assert "results.json" in integrite["detail"], integrite["detail"]
    finally:
        shutil.rmtree(pack, ignore_errors=True)


@check("audit: rejouer un pack rend exactement les memes resultats")
def _():
    run, pack = _pack_de_test()
    try:
        rejeu = E.rejouer_pack(pack)
        assert rejeu["rejouable"], rejeu.get("motif")
        eq(rejeu["identique"], True, str(rejeu["differences"][:3]))
        eq(rejeu["statut_global_rejeu"], rejeu["statut_global_origine"],
           "meme feu tricolore")
        eq(rejeu["exceptions_rejeu"], rejeu["exceptions_origine"],
           "meme volume d'exceptions")
    finally:
        shutil.rmtree(pack, ignore_errors=True)


@check("audit: chaque verdict porte son explication, aucune exception tronquee")
def _():
    st = CatalogueStore()
    run = E.run_dq(ROOT / "data" / "exemples" / "inventaire.csv", store=st,
                   write_evidence=False)
    muets = [r.rule_id for r in run.resultats
             if r.statut in ("FAIL", "SKIPPED", "ERROR") and not r.message.strip()]
    eq(muets, [], "un FAIL ou un SKIPPED sans raison n'est pas auditable")
    assert all(r.exceptions_completes for r in run.resultats), \
        "aucune exception ne doit etre tronquee par defaut"
    eq(run.manifeste["exceptions_completes"], True, "trace au manifeste")


@check("moteur: un fichier introuvable est signale clairement")
def _():
    st = CatalogueStore()
    try:
        E.run_dq(TMP / "jamais_vu.csv", store=st, write_evidence=False)
    except FileNotFoundError:
        return
    raise AssertionError("un fichier absent aurait du lever FileNotFoundError")


# --------------------------------------------------------------------------- #
# 7. Rapport Excel
# --------------------------------------------------------------------------- #
@check("rapport: le classeur contient les six onglets attendus")
def _():
    st = CatalogueStore()
    run = E.run_dq(ROOT / "data" / "ref" / "ref_devises.csv", store=st,
                   write_evidence=False)
    chemin = build_workbook(run, st, TMP / "rapport.xlsx")
    eq(pd.ExcelFile(chemin).sheet_names,
       ["SUMMARY", "EXCEPTIONS", "COVERAGE", "EVIDENCE", "EXECUTED_CATALOGUE",
        "CHANGELOG"], "onglets du classeur")


@check("rapport: le nombre d'echecs est le premier KPI de la synthese")
def _():
    st = CatalogueStore()
    run = E.run_dq(FICHIER_DEMO, store=st, write_evidence=False)
    chemin = build_workbook(run, st, TMP / "rapport_kpi.xlsx")
    sy = pd.read_excel(chemin, "SUMMARY", header=None)
    libelles = [v for v in sy.iloc[5].tolist() if pd.notna(v)]
    eq(libelles[0], "CONTROLS IN BREACH", "premier libelle du bandeau")
    valeurs = [v for v in sy.iloc[3].tolist() if pd.notna(v)]
    eq(str(valeurs[0]), str(run.summary()["fail"] + run.summary()["error"]),
       "premiere valeur du bandeau")


@check("rapport: la synthese porte une colonne en langage metier")
def _():
    st = CatalogueStore()
    run = E.run_dq(FICHIER_DEMO, store=st, write_evidence=False)
    chemin = build_workbook(run, st, TMP / "rapport_phrase.xlsx")
    texte = pd.read_excel(chemin, "SUMMARY", header=None).astype(str).to_string()
    assert "what_is_checked" in texte, "colonne en langage metier absente"
    assert "must be filled on every row" in texte, "phrase absente"


@check("rapport: l'onglet EVIDENCE porte l'empreinte SHA-256 des sources")
def _():
    st = CatalogueStore()
    run = E.run_dq(ROOT / "data" / "ref" / "ref_devises.csv", store=st,
                   write_evidence=False)
    chemin = build_workbook(run, st, TMP / "rapport2.xlsx")
    texte = pd.read_excel(chemin, "EVIDENCE", header=None).astype(str).to_string()
    assert run.manifeste["sources"]["ref_devises"]["sha256"] in texte, "hash source absent"
    assert run.manifeste["catalogue_sha256"] in texte, "hash catalogue absent"


@check("rapport: l'onglet COVERAGE liste les controles non executes")
def _():
    st = CatalogueStore()
    run = E.run_dq(ROOT / "data" / "ref" / "ref_devises.csv", store=st,
                   write_evidence=False)
    chemin = build_workbook(run, st, TMP / "rapport3.xlsx")
    texte = pd.read_excel(chemin, "COVERAGE", header=None).astype(str).to_string()
    assert "DQ15" in texte, "le controle deprecie doit apparaitre comme trou de couverture"


# --------------------------------------------------------------------------- #
# 8. Interface Streamlit : les ecrans se rendent sans exception
# --------------------------------------------------------------------------- #
def _apptest():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=180)


def _page(nom: str):
    at = _apptest().run()
    at.sidebar.radio[0].set_value(nom).run()
    assert not at.exception, at.exception
    return at


@check("interface: l ecran Catalogue de controles se rend sans exception")
def _():
    at = _apptest().run()
    assert not at.exception, at.exception


@check("interface: les ecrans suivent le cycle de vie du brief")
def _():
    at = _apptest().run()
    eq(list(at.sidebar.radio[0].options),
       ["① Control catalogue", "② Execution", "③ Reporting",
        "④ Audit trail"],
       "definit, execute, restitue, prouve")


@check("interface: l ecran Execution se rend sans exception")
def _():
    _page("② Execution")


@check("interface: l ecran Restitution se rend sans exception")
def _():
    _page("③ Reporting")


def _restitution():
    """Lance un controle depuis l ecran d execution, puis ouvre la restitution."""
    at = _apptest().run()
    at.sidebar.radio[0].set_value("② Execution").run()
    selecteur = [b for b in at.selectbox if b.label == "File"][0]
    cible = [o for o in selecteur.options if "inventaire" in str(o)][0]
    at = selecteur.set_value(cible).run()
    at = [b for b in at.button if "Run the controls" in b.label][0].click().run()
    assert not at.exception, at.exception
    at = at.sidebar.radio[0].set_value("③ Reporting").run()
    assert not at.exception, at.exception
    return at


@check("restitution: le tableau de bord porte ses graphiques et ses onglets")
def _():
    at = _restitution()
    graphiques = [e for e in at.main if e.type == "vega_lite_chart"]
    assert len(graphiques) >= 2, f"graphiques attendus, obtenu {len(graphiques)}"
    eq(len(at.tabs), 5, "detail, exceptions, couverture, historique, refusees")


@check("restitution: tout ce qui est affiche est exportable")
def _():
    at = _restitution()
    libelles = " ".join(b.label for b in at.get("download_button"))
    for attendu in ["Excel", "scorecard", "exceptions"]:
        assert attendu in libelles, f"export manquant : {attendu} dans {libelles}"


@check("restitution: vert et rouge ne sont jamais adjacents dans l empilement")
def _():
    """Le validateur de palette mesure un ecart CVD de 4,1 entre le vert et le
    rouge en deuteranopie : cote a cote, deux segments indistinguables. Le gris
    et le jaune s intercalent, ce qui porte le pire ecart adjacent a 10,7."""
    source = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")
    debut = source.index("STATUTS_VUE = [")
    espace = {}
    exec(compile(source[debut:source.index("]", debut) + 1], "app_ui", "exec"), espace)
    ordre = [code for code, _, _, _ in espace["STATUTS_VUE"]]
    eq(ordre.index("SKIPPED") - ordre.index("PASS"), 1,
       "le gris suit immediatement le vert")
    assert abs(ordre.index("PASS") - ordre.index("FAIL")) > 1, \
        "PASS et FAIL ne doivent jamais etre adjacents"
    for code, nom, puce, _ in espace["STATUTS_VUE"]:
        assert puce and nom, f"{code} doit porter une pastille et un libelle"


@check("interface: l ecran Piste d audit se rend sans exception")
def _():
    _page("④ Audit trail")


@check("interface: aucun ecran ne demande de declarer un fichier")
def _():
    at = _apptest().run()
    textes = " ".join(str(m.value) for m in at.markdown)
    assert "Describe a new file" not in textes, \
        "aucun formulaire de declaration ne doit subsister"


@check("interface: l'editeur propose les regles en langage metier, pas en template_id")
def _():
    at = _page("① Control catalogue")
    boutons = [b for b in at.button if "Create a rule" in b.label]
    assert boutons, f"bouton de creation absent : {[b.label for b in at.button]}"
    boutons[0].click().run()
    assert not at.exception, at.exception
    options = [str(o) for r in at.radio for o in (r.options or [])]
    assert any("Never be empty" in o for o in options), \
        f"libelles metier absents des options : {options[:6]}"
    assert not any("NOT_NULL" == o for o in options), \
        "les identifiants techniques ne doivent pas etre proposes a l'utilisateur"


@check("interface: l editeur sait lire les colonnes d un fichier")
def _():
    """Sans schema declare, l editeur n'a aucune colonne a proposer tant qu'un
    fichier n'a pas ete lu. Lire l'en-tete suffit : inutile de profiler."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("app_ui", ROOT / "ui" / "app.py")
    # Le module s'execute au chargement (il rend l'interface) : on ne teste que
    # la fonction, extraite du source pour ne pas declencher Streamlit.
    source = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")
    debut = source.index("def entetes_du_fichier(")
    fin = source.index("def retenir_entetes(")
    espace = {"pd": pd, "pathlib": pathlib}
    exec(compile(source[debut:fin], "app_ui", "exec"), espace)
    entetes = espace["entetes_du_fichier"]

    csv = TMP / "entetes.csv"
    pd.DataFrame({"a": [1], "b c": [2], "é": [3]}).to_csv(csv, index=False)
    eq(entetes(csv), ["a", "b c", "é"], "colonnes du CSV")

    xlsx = TMP / "entetes.xlsx"
    pd.DataFrame({"x": [1], "y": [2]}).to_excel(xlsx, index=False)
    eq(entetes(xlsx), ["x", "y"], "colonnes du classeur")
    assert spec is not None


@check("interface: le bouton de creation est le premier de l ecran")
def _():
    at = _page("① Control catalogue")
    eq(at.button[0].label, "➕ Create a rule",
       "la creation doit venir avant le tableau, pas apres")


@check("interface: l editeur propose de charger un fichier quand il ne sait rien")
def _():
    """Sans schema declare, l editeur doit offrir de lire un fichier : sinon une
    regle ciblant une colonne nommee est inconstruisible depuis le catalogue."""
    at = _page("① Control catalogue")
    at.button[0].click().run()          # Creer une regle
    assert not at.exception, at.exception
    titres = [str(e.label) for e in at.expander]
    assert any("column" in t.lower() for t in titres), \
        f"aucun selecteur de colonnes : {titres}"
    assert "A file already present" in [b.label for b in at.selectbox], \
        "l editeur doit proposer les fichiers presents"


@check("interface: charger un fichier depuis l editeur alimente les listes de colonnes")
def _():
    """Le parcours complet sans passer par l ecran d execution : on ouvre le
    catalogue, on cree une regle, on charge un fichier, ses colonnes sont
    proposees. Sans cela, une regle ciblant une colonne nommee serait
    inconstruisible depuis le catalogue."""
    at = _page("① Control catalogue")
    at.button[0].click().run()

    selecteur = [b for b in at.selectbox if b.label == "A file already present"][0]
    cible = [o for o in selecteur.options if "health" in str(o)][0]
    at = selecteur.set_value(cible).run()
    assert not at.exception, at.exception

    profils = at.session_state["profils_vus"]
    assert "health_test_dataset" in profils, f"fichier non memorise : {list(profils)}"
    colonnes = [c["colonne"] for c in profils["health_test_dataset"]]
    eq(len(colonnes), 12, "les douze colonnes du fichier")
    assert "patient_id" in colonnes, colonnes

    listes = [b for b in at.selectbox if "always be filled" in str(b.label)]
    assert listes, "la liste des colonnes cibles doit apparaitre"
    assert "patient_id" in [str(o) for o in listes[0].options], \
        "les colonnes du fichier doivent etre proposees comme cible"


@check("interface: l'editeur refuse d'enregistrer une regle incomplete")
def _():
    at = _page("① Control catalogue")
    [b for b in at.button if "Create a rule" in b.label][0].click().run()
    enregistrer = [b for b in at.button if "Save the rule" in b.label]
    assert enregistrer, "bouton d'enregistrement absent"
    assert enregistrer[0].disabled, \
        "un formulaire vide ne doit pas pouvoir etre enregistre"
    avertissements = " ".join(str(w.value) for w in at.warning)
    assert "still missing" in avertissements.lower(), \
        f"l'utilisateur doit savoir ce qui manque : {avertissements}"


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
