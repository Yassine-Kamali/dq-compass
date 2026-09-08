"""
Harnais de test de l'assistant IA du catalogue. Aucune dependance externe,
aucun appel reseau : la reponse du modele est toujours injectee.

    .venv/Scripts/python.exe tests/test_catalogue_ai.py

Ce que ces tests defendent, dans l'ordre d'importance :

  1. aucune ligne du fichier ne part chez Anthropic ;
  2. une reponse de modele est une entree hostile - inconnue, incoherente ou
     illisible, elle est ecartee sans casser l'ecran ;
  3. l'assistant n'ecrit jamais au catalogue : il produit un brouillon, que le
     validateur et l'humain gardent le droit de refuser ;
  4. sans cle Anthropic, le deterministe et le moteur continuent de tourner.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import pathlib
import shutil
import sys
import tempfile
import traceback

import pandas as pd

# Plusieurs tests provoquent volontairement une panne du fournisseur. Le module
# journalise alors la trace complete - c'est le comportement attendu, mais elle
# n'a rien a faire dans la sortie du harnais.
logging.disable(logging.CRITICAL)

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "reporting"))

import catalogue_ai as IA  # noqa: E402
import dq_engine as E  # noqa: E402
import suggestions as S  # noqa: E402
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
# Fixtures. Un catalogue et un fichier synthetiques, hors du projet reel.
# --------------------------------------------------------------------------- #
TMP = pathlib.Path(tempfile.mkdtemp(prefix="dq_tests_ia_"))


def make_store() -> CatalogueStore:
    st = CatalogueStore(TMP / "store.json")
    st.data = {"meta": {"schema": "2.0"}, "templates": [],
               "controls": [], "changelog": []}
    st.data["templates"] = [
        {"template_id": "NOT_NULL", "dimension": "Completeness",
         "params_requis": "column | colonnes_motif", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "% completeness",
         "exemple_params": ""},
        {"template_id": "UNIQUE_KEY", "dimension": "Uniqueness",
         "params_requis": "columns | colonnes_motif", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "% uniqueness",
         "exemple_params": ""},
        {"template_id": "DATE_ORDER", "dimension": "Consistency",
         "params_requis": "before | motif_avant, after | motif_apres",
         "params_optionnels": "", "description_logique": "",
         "kpi_produit": "% ordered", "exemple_params": ""},
        {"template_id": "FRESHNESS", "dimension": "Timeliness",
         "params_requis": "column | colonnes_motif, max_lag_days",
         "params_optionnels": "", "description_logique": "",
         "kpi_produit": "days", "exemple_params": ""},
        {"template_id": "CUSTOM_EXPRESSION", "dimension": "Multi",
         "params_requis": "expression", "params_optionnels": "",
         "description_logique": "", "kpi_produit": "%", "exemple_params": ""},
    ]
    return st


# Un jeu hospitalier synthetique. Les valeurs sont des sentinelles reperables :
# si l'une d'elles apparait dans la charge utile, la fuite est prouvee.
SENTINELLES = ["ZZSECRETALPHA", "ZZSECRETBETA", "ZZSECRETGAMMA"]

HOPITAL = pd.DataFrame({
    "patient_id": [f"PAT{i:04d}" for i in range(30)],
    "encounter_id": [f"ENC{i:04d}" for i in range(30)],
    "diagnosis_code": [SENTINELLES[i % 3] for i in range(30)],
    "admission_date": ["2026-01-%02d" % (i % 28 + 1) for i in range(30)],
    "discharge_date": ["2026-02-%02d" % (i % 28 + 1) for i in range(30)],
    "last_update_date": ["2026-09-01"] * 30,
})
PROFIL = E.profiler(HOPITAL)
COLONNES = [c["colonne"] for c in PROFIL]


def reponse(proposals: list[dict]) -> str:
    return json.dumps({"proposals": proposals}, ensure_ascii=False)


def proposition_valide(**surcharge) -> dict:
    base = {
        "control_name": "Unique encounter identifier",
        "control_type": "Uniqueness",
        "template": "UNIQUE_KEY",
        "data_element": ["encounter_id"],
        "params": {"columns": ["encounter_id"]},
        "description": "An encounter identifier must not repeat.",
        "logic_definition": "encounter_id carries no duplicate.",
        "suggested_severity": "High",
        "suggested_kpi": "% uniqueness",
        "suggested_remediation_action": "De-duplicate at the source.",
        "reason": "The column name and its near-total cardinality both point "
                  "to a row identifier.",
        "confidence": 0.95,
        "business_input_required": False,
        "missing_business_inputs": [],
    }
    base.update(surcharge)
    return base


@contextlib.contextmanager
def sans_cle():
    """Neutralise les deux sources de cle : environnement ET secrets Streamlit.

    Retirer la variable d'environnement ne suffit pas : depuis qu'un
    `.streamlit/secrets.toml` peut exister sur la machine du developpeur,
    `cle_api()` y retomberait et le test passerait ou echouerait selon la
    machine. On coupe donc aussi cette seconde source.
    """
    ancienne = os.environ.pop(IA.VAR_CLE, None)
    vrai_secrets = IA._depuis_secrets
    IA._depuis_secrets = lambda nom: None
    try:
        yield
    finally:
        IA._depuis_secrets = vrai_secrets
        if ancienne is not None:
            os.environ[IA.VAR_CLE] = ancienne


# --------------------------------------------------------------------------- #
# 1. Analyse d'une reponse bien formee
# --------------------------------------------------------------------------- #
@check("ia: une proposition structuree valide est acceptee par l'analyseur")
def _():
    st = make_store()
    out = IA.suggerer_ia(PROFIL, "hopital", st, lignes=len(HOPITAL),
                         appelant=lambda _: reponse([proposition_valide()]))
    eq(len(out["propositions"]), 1, "une proposition retenue")
    eq(out["rejets"], [], "aucun rejet")
    p = out["propositions"][0]
    eq(p["template"], "UNIQUE_KEY", "template")
    eq(p["control_type"], "Uniqueness", "dimension")
    eq(p["source"], "AI", "origine tracee")
    eq(p["confidence"], 0.95, "confiance")
    assert p["reason"], "chaque proposition doit porter son explication"


@check("ia: une reponse enrobee dans une cloture markdown reste lisible")
def _():
    st = make_store()
    brut = "```json\n" + reponse([proposition_valide()]) + "\n```"
    out = IA.suggerer_ia(PROFIL, "hopital", st, appelant=lambda _: brut)
    eq(len(out["propositions"]), 1, "la cloture ne doit pas gener")


@check("ia: la confiance se traduit en trois paliers lisibles")
def _():
    eq(IA.niveau_de_confiance(0.95), "high", "haute")
    eq(IA.niveau_de_confiance(0.70), "medium", "moyenne")
    eq(IA.niveau_de_confiance(0.20), "low", "basse")
    eq(IA.niveau_de_confiance(None), "unknown", "illisible")


# --------------------------------------------------------------------------- #
# 2. La reponse du modele est traitee comme une entree hostile
# --------------------------------------------------------------------------- #
@check("ia: un template inconnu du catalogue est rejete")
def _():
    st = make_store()
    out = IA.suggerer_ia(
        PROFIL, "hopital", st,
        appelant=lambda _: reponse([proposition_valide(
            template="BENFORD_LAW", control_name="Benford check")]))
    eq(out["propositions"], [], "rien ne doit passer")
    assert any("BENFORD_LAW" in r for r in out["rejets"]), \
        f"le motif doit nommer le template inconnu : {out['rejets']}"


@check("ia: le modele ne peut pas ecrire de CUSTOM_EXPRESSION executable")
def _():
    st = make_store()
    # Le template existe bien au catalogue : c'est l'assistant qui n'y a pas droit.
    assert st.template("CUSTOM_EXPRESSION") is not None, "fixture"
    offerts = {t["template"] for t in IA.templates_offerts(st)}
    assert "CUSTOM_EXPRESSION" not in offerts, \
        "le template ne doit meme pas etre propose au modele"
    out = IA.suggerer_ia(
        PROFIL, "hopital", st,
        appelant=lambda _: reponse([proposition_valide(
            template="CUSTOM_EXPRESSION", control_type="Validity",
            params={"expression": "__import__('os').system('rm -rf /')"},
            data_element=[])]))
    eq(out["propositions"], [], "aucune expression executable ne doit passer")
    assert any("expert" in r.lower() for r in out["rejets"]), \
        f"le motif doit renvoyer a une configuration experte : {out['rejets']}"


@check("ia: une proposition citant une colonne inexistante est rejetee")
def _():
    st = make_store()
    out = IA.suggerer_ia(
        PROFIL, "hopital", st,
        appelant=lambda _: reponse([proposition_valide(
            control_name="Completeness of ssn", template="NOT_NULL",
            control_type="Completeness", data_element=["social_security_no"],
            params={"column": "social_security_no"})]))
    eq(out["propositions"], [], "une colonne absente invalide la proposition")
    assert any("social_security_no" in r for r in out["rejets"]), \
        f"le motif doit nommer la colonne absente : {out['rejets']}"


@check("ia: une dimension hors des six du brief est rejetee")
def _():
    st = make_store()
    out = IA.suggerer_ia(PROFIL, "hopital", st,
                         appelant=lambda _: reponse([proposition_valide(
                             control_type="Plausibility")]))
    eq(out["propositions"], [], "les six dimensions du brief font foi")


@check("ia: une confiance hors de l'intervalle 0-1 est rejetee")
def _():
    st = make_store()
    for valeur in (1.4, -0.2, "beaucoup"):
        out = IA.suggerer_ia(PROFIL, "hopital", st,
                             appelant=lambda _, v=valeur: reponse(
                                 [proposition_valide(confidence=v)]))
        eq(out["propositions"], [], f"confiance {valeur!r} doit etre refusee")


@check("ia: un JSON illisible ne casse pas l'application")
def _():
    st = make_store()
    for brut in ("", "   ", "je ne suis pas du JSON",
                 '{"proposals": [', "```json\n{oops}\n```", "null"):
        try:
            IA.suggerer_ia(PROFIL, "hopital", st, appelant=lambda _, b=brut: b)
        except IA.AssistantIndisponible as exc:
            assert str(exc), "l'ecran doit recevoir une phrase"
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"{brut!r} a leve {type(exc).__name__} au lieu de "
                f"AssistantIndisponible") from exc
        else:
            raise AssertionError(f"{brut!r} aurait du etre refuse")


@check("ia: une charge JSON valide mais de forme inattendue ne casse rien")
def _():
    st = make_store()
    # `proposals` absent, ou d'un type impossible, ou peuple d'objets bancals.
    for brut in ('{"resultat": "ok"}', '{"proposals": "beaucoup"}'):
        out_ou_exc = None
        try:
            out_ou_exc = IA.suggerer_ia(PROFIL, "hopital", st,
                                        appelant=lambda _, b=brut: b)
        except IA.AssistantIndisponible:
            continue
        eq(out_ou_exc["propositions"], [], f"{brut} ne propose rien")
        assert out_ou_exc["rejets"], "le motif doit etre conserve"

    out = IA.suggerer_ia(
        PROFIL, "hopital", st,
        appelant=lambda _: json.dumps({"proposals": [
            None, 42, "texte", {}, proposition_valide()]}))
    eq(len(out["propositions"]), 1, "seule la proposition saine survit")
    eq(len(out["rejets"]), 4, "les quatre autres sont tracees")


@check("ia: toute panne du fournisseur devient une phrase d'ecran")
def _():
    st = make_store()

    def explose(_):
        raise ConnectionResetError("socket closed by peer")

    try:
        IA.suggerer_ia(PROFIL, "hopital", st, appelant=explose)
    except IA.AssistantIndisponible as exc:
        assert "socket" not in str(exc).lower(), \
            "le detail technique ne doit pas remonter a l'ecran"
    else:
        raise AssertionError("une panne reseau doit etre signalee")


# --------------------------------------------------------------------------- #
# 3. Frontiere de confidentialite : rien du fichier ne sort
# --------------------------------------------------------------------------- #
@check("ia: aucune ligne du fichier ne figure dans la requete Anthropic")
def _():
    st = make_store()
    deterministes = S.suggerer(HOPITAL, PROFIL, "hopital", chiffrer=False)

    # Le deterministe transporte bien les valeurs : c'est ce qu'on doit filtrer.
    valeurs_deterministes = json.dumps(deterministes, default=str)
    assert any(s in valeurs_deterministes for s in SENTINELLES), \
        ("la fixture doit produire une suggestion portant des valeurs reelles, "
         "sinon ce test ne prouve rien")

    charge = IA.construire_charge(
        profil=PROFIL, nom_dataset="hopital", store=st,
        deterministes=deterministes, contexte_metier="Hospital admissions",
        lignes=len(HOPITAL), controles_existants=st.controls)
    envoye = IA.construire_message(charge)

    for sentinelle in SENTINELLES:
        assert sentinelle not in envoye, \
            f"valeur du fichier envoyee au modele : {sentinelle}"

    # Aucune valeur de cellule, quelle qu'elle soit.
    for colonne in HOPITAL.columns:
        for valeur in HOPITAL[colonne].astype(str).unique():
            assert valeur not in envoye, \
                f"valeur de cellule envoyee : {colonne}={valeur!r}"

    # Ce qui doit sortir, en revanche, sort bien.
    assert "encounter_id" in envoye, "les noms de colonnes sont attendus"
    assert "Hospital admissions" in envoye, "le contexte metier est attendu"
    assert "UNIQUE_KEY" in envoye, "les templates du store sont attendus"


@check("ia: le sanitiseur travaille en liste blanche, pas en liste noire")
def _():
    # Un profileur qui ajouterait demain un echantillon ne doit pas fuiter.
    profil_enrichi = [dict(c) for c in PROFIL]
    profil_enrichi[0]["echantillon"] = ["ZZFUITE1", "ZZFUITE2"]
    profil_enrichi[0]["valeur_min"] = "ZZFUITE3"
    propre = IA.sanitiser_profil(profil_enrichi, lignes=len(HOPITAL))
    texte = json.dumps(propre, default=str)
    for fuite in ("ZZFUITE1", "ZZFUITE2", "ZZFUITE3"):
        assert fuite not in texte, f"champ non autorise recopie : {fuite}"
    assert "echantillon" not in texte, "le champ lui-meme ne doit pas passer"
    eq(propre[0]["colonne"], PROFIL[0]["colonne"], "le nom de colonne reste")


@check("ia: les modalites d'un domaine ferme ne quittent jamais la machine")
def _():
    deterministes = S.suggerer(HOPITAL, PROFIL, "hopital", chiffrer=False)
    domaines = [d for d in deterministes if d["template"] == "IN_DOMAIN"]
    assert domaines, "la fixture doit produire au moins un IN_DOMAIN"
    assert domaines[0]["params"].get("values"), "il porte bien des valeurs"

    propre = json.dumps(IA.sanitiser_suggestions(deterministes), default=str)
    for sentinelle in SENTINELLES:
        assert sentinelle not in propre, f"modalite fuitee : {sentinelle}"
    assert "IN_DOMAIN" in propre, "le template, lui, doit etre annonce"


@check("ia: une cle etrangere sans referentiel connu n'est PAS rejetee")
def _():
    """Regression. `ref_column` nomme une colonne du fichier de reference, pas
    du fichier analyse. Le controle d'existence l'a longtemps confondue avec
    une colonne du dataset : toute proposition FOREIGN_KEY correctement formee
    - celles ou le modele avoue ne pas connaitre le referentiel - etait donc
    rejetee, et l'ecran restait vide."""
    st = make_store()
    st.data["templates"].append(
        {"template_id": "FOREIGN_KEY", "dimension": "Consistency",
         "params_requis": "column, ref_fichier, ref_column",
         "params_optionnels": "", "description_logique": "",
         "kpi_produit": "% found", "exemple_params": ""})
    out = IA.suggerer_ia(
        PROFIL, "hopital", st,
        appelant=lambda _: reponse([proposition_valide(
            control_name="Diagnosis code reference validation",
            control_type="Consistency", template="FOREIGN_KEY",
            data_element=["diagnosis_code"],
            params={"column": "diagnosis_code",
                    "ref_fichier": "TO_CONFIRM_reference_table",
                    "ref_column": "TO_CONFIRM_code_column"},
            confidence=0.82, business_input_required=True,
            missing_business_inputs=["official diagnosis-code reference"])]))
    eq(out["rejets"], [], "aucun rejet attendu")
    eq(len(out["propositions"]), 1, "la proposition doit survivre")
    p = out["propositions"][0]
    eq(p["business_input_required"], True, "le besoin metier est conserve")
    assert p["missing_business_inputs"], "ce qui manque doit etre dit"


@check("ia: un referentiel introuvable n'est pas recopie dans l'editeur")
def _():
    """Un marqueur « TO_CONFIRM_… » pre-remplirait le formulaire avec une
    valeur fausse - pire qu'un champ vide, car l'humain ne verrait pas qu'il
    reste a le renseigner."""
    st = make_store()
    brouillon = IA.en_brouillon(proposition_valide(
        template="FOREIGN_KEY", control_type="Consistency",
        data_element=["diagnosis_code"],
        params={"column": "diagnosis_code",
                "ref_fichier": "TO_CONFIRM_reference_table",
                "ref_column": "TO_CONFIRM_code_column"}), store=st)
    params = json.loads(brouillon["params"])
    eq("ref_fichier" in params, False, "le referentiel fantome est retire")
    eq("ref_column" in params, False, "sa colonne aussi")
    eq(params["column"], "diagnosis_code", "la colonne reelle, elle, reste")


@check("ia: un referentiel qui existe vraiment est conserve")
def _():
    st = make_store()
    reel = "data/ref/ref_devises.csv"
    assert (ROOT / reel).exists(), "fixture : ce referentiel doit exister"
    brouillon = IA.en_brouillon(proposition_valide(
        template="FOREIGN_KEY", control_type="Consistency",
        data_element=["diagnosis_code"],
        params={"column": "diagnosis_code", "ref_fichier": reel,
                "ref_column": "code"}), store=st)
    params = json.loads(brouillon["params"])
    eq(params.get("ref_fichier"), reel, "un vrai referentiel est garde")
    eq(params.get("ref_column"), "code", "sa colonne aussi")


# --------------------------------------------------------------------------- #
# 4. Dedoublonnage avec la couche deterministe
# --------------------------------------------------------------------------- #
@check("ia: une proposition identique a une suggestion deterministe est detectee")
def _():
    st = make_store()
    deterministe = {
        "template": "UNIQUE_KEY", "dimension": "Uniqueness",
        "params": {"columns": ["encounter_id"]},
        "control_name": "Uniqueness of encounter_id", "dataset_scope": "*",
    }
    out = IA.suggerer_ia(PROFIL, "hopital", st, deterministes=[deterministe],
                         appelant=lambda _: reponse([proposition_valide()]))
    eq(out["propositions"], [], "le doublon ne doit pas etre affiche deux fois")
    eq(len(out["doublons"]), 1, "il est compte comme doublon")
    assert deterministe.get("ia_reason"), (
        "l'enrichissement est prefere a la duplication : la suggestion "
        "deterministe doit recevoir l'explication de l'assistant")


@check("ia: un controle deja au catalogue n'est pas propose une seconde fois")
def _():
    st = make_store()
    st.add_control({
        "control_name": "Uniqueness of encounter_id", "template": "UNIQUE_KEY",
        "control_type": "Uniqueness",
        "params": json.dumps({"columns": ["encounter_id"]}),
        "dataset_scope": "*"}, "tests")
    out = IA.suggerer_ia(PROFIL, "hopital", st, controles_existants=st.controls,
                         appelant=lambda _: reponse([proposition_valide()]))
    eq(out["propositions"], [], "le catalogue le couvre deja")


@check("ia: une proposition sur une autre colonne n'est pas un doublon")
def _():
    st = make_store()
    deterministe = {
        "template": "UNIQUE_KEY", "dimension": "Uniqueness",
        "params": {"columns": ["patient_id"]}, "dataset_scope": "*",
    }
    out = IA.suggerer_ia(PROFIL, "hopital", st, deterministes=[deterministe],
                         appelant=lambda _: reponse([proposition_valide()]))
    eq(len(out["propositions"]), 1, "cibles differentes, propositions distinctes")


@check("ia: le modele ne peut pas se repeter lui-meme")
def _():
    st = make_store()
    out = IA.suggerer_ia(PROFIL, "hopital", st,
                         appelant=lambda _: reponse(
                             [proposition_valide(), proposition_valide()]))
    eq(len(out["propositions"]), 1, "deux fois la meme regle = une seule carte")


# --------------------------------------------------------------------------- #
# 5. Passage au catalogue : brouillon, validateur, humain
# --------------------------------------------------------------------------- #
@check("ia: une proposition acceptee se convertit au format du catalogue")
def _():
    from store import CONTROL_FIELDS
    st = make_store()
    brouillon = IA.en_brouillon(proposition_valide(), scope="hopital",
                                owner="data.steward", store=st)
    inconnus = set(brouillon) - set(CONTROL_FIELDS)
    eq(inconnus, set(), "aucun champ etranger au schema du catalogue")
    eq(brouillon["control_name"], "Unique encounter identifier", "nom")
    eq(brouillon["template"], "UNIQUE_KEY", "template")
    eq(brouillon["dataset_scope"], "hopital", "portee")
    eq(json.loads(brouillon["params"]), {"columns": ["encounter_id"]}, "params")
    eq(brouillon["kpi"], "% uniqueness", "le KPI vient du template, pas du modele")


@check("ia: le modele n'invente jamais l'identifiant d'une regle")
def _():
    st = make_store()
    brouillon = IA.en_brouillon(
        proposition_valide(rule_id="DQ99", control_name="Tentative"), store=st)
    assert "rule_id" not in brouillon or not brouillon.get("rule_id"), \
        "le rule_id appartient au store, jamais au modele"
    cree = st.add_control(brouillon, "tests")
    eq(cree["rule_id"], st.controls[0]["rule_id"], "attribue par le store")
    assert cree["rule_id"].startswith("DQ"), "au format du store"
    eq(cree["rule_id"] == "DQ99", False, "surtout pas celui souffle par le modele")


@check("ia: une proposition acceptee doit encore passer validate_control()")
def _():
    st = make_store()
    brouillon = IA.en_brouillon(proposition_valide(), store=st)
    eq(E.validate_control(brouillon, st), [], "la regle saine doit passer")

    # Une proposition dont les parametres ne satisfont pas le template est
    # arretee par le validateur, pas par l'assistant : la chaine reste la meme
    # que pour une regle saisie a la main.
    bancale = IA.en_brouillon(proposition_valide(
        template="FRESHNESS", control_type="Timeliness",
        data_element=["last_update_date"],
        params={"column": "last_update_date"}), store=st)   # max_lag_days manquant
    erreurs = E.validate_control(bancale, st)
    assert erreurs, "le validateur doit refuser une regle incomplete"
    assert any("max_lag_days" in e for e in erreurs), \
        f"le motif doit nommer le parametre manquant : {erreurs}"


@check("ia: une proposition n'ecrit jamais dans catalogue/store.json")
def _():
    reel = ROOT / "catalogue" / "store.json"
    avant = hashlib.sha256(reel.read_bytes()).hexdigest()
    mtime = reel.stat().st_mtime

    st = CatalogueStore()          # le vrai catalogue, en lecture
    out = IA.suggerer_ia(PROFIL, "hopital", st, lignes=len(HOPITAL),
                         controles_existants=st.controls,
                         appelant=lambda _: reponse([proposition_valide()]))
    assert out["propositions"], "la fixture doit proposer quelque chose"
    IA.en_brouillon(out["propositions"][0], store=st)

    eq(hashlib.sha256(reel.read_bytes()).hexdigest(), avant,
       "le catalogue ne doit pas avoir bouge")
    eq(reel.stat().st_mtime, mtime, "ni sa date de modification")
    eq(len(st.changelog), len(CatalogueStore().changelog),
       "aucune entree de journal n'a ete ajoutee")


@check("ia: la trace de session dit le modele, la date et le fournisseur")
def _():
    st = make_store()
    out = IA.suggerer_ia(PROFIL, "hopital", st, nom_modele="claude-sonnet-5",
                         appelant=lambda _: reponse([proposition_valide()]))
    trace = out["trace"]
    eq(trace["fournisseur"], "Anthropic", "fournisseur")
    eq(trace["modele"], "claude-sonnet-5", "modele")
    eq(trace["dataset"], "hopital", "dataset")
    eq(trace["propositions"], 1, "compte des propositions")
    dt.datetime.fromisoformat(trace["timestamp"])   # leve si mal forme


# --------------------------------------------------------------------------- #
# 6. Sans cle Anthropic, DQ Compass fonctionne
# --------------------------------------------------------------------------- #
@check("ia: sans cle, l'assistant se declare indisponible et l'explique")
def _():
    with sans_cle():
        # On ne compare jamais la cle elle-meme : un echec afficherait sa
        # valeur. Seul un booleen circule.
        eq(IA.cle_api() is None, True, "aucune cle ne doit etre trouvee")
        eq(IA.disponible(), False, "l'assistant est hors service")
        raison = IA.raison_indisponible()
        assert raison and len(raison) > 20, "l'ecran doit recevoir une phrase"


@check("ia: sans cle, les suggestions deterministes continuent de fonctionner")
def _():
    with sans_cle():
        propositions = S.suggerer(HOPITAL, PROFIL, "hopital", chiffrer=False)
        assert propositions, "le deterministe ne depend pas d'Anthropic"
        templates = {p["template"] for p in propositions}
        assert "UNIQUE_KEY" in templates, \
            f"les cles candidates restent detectees : {templates}"


@check("ia: sans cle, le moteur DQ rend toujours ses verdicts")
def _():
    with sans_cle():
        st = make_store()
        st.add_control({
            "control_name": "Completeness of patient_id",
            "control_type": "Completeness", "template": "NOT_NULL",
            "params": json.dumps({"column": "patient_id"}),
            "dataset_scope": "*", "severity": "High",
            "seuil_tolerance_pct": 0.0}, "tests")
        fichier = TMP / "hopital.csv"
        HOPITAL.to_csv(fichier, index=False)
        run = E.run_dq(fichier, store=st, write_evidence=False)
        statuts = {r.statut for r in run.resultats}
        assert statuts <= set(E.STATUTS_RUN), f"statuts inattendus : {statuts}"
        assert "PASS" in statuts, \
            f"le moteur doit rendre son verdict sans Anthropic : {statuts}"


@check("ia: le modele est configurable, avec un defaut Sonnet")
def _():
    ancien = os.environ.pop(IA.VAR_MODELE, None)
    try:
        assert IA.modele().startswith("claude-"), "un defaut doit exister"
        assert "sonnet" in IA.MODELE_DEFAUT, \
            f"le defaut attendu est un Sonnet : {IA.MODELE_DEFAUT}"
        os.environ[IA.VAR_MODELE] = "claude-opus-5"
        eq(IA.modele(), "claude-opus-5", "la variable d'environnement prime")
    finally:
        os.environ.pop(IA.VAR_MODELE, None)
        if ancien is not None:
            os.environ[IA.VAR_MODELE] = ancien


@check("ia: aucune cle ne transite par le message d'erreur affiche")
def _():
    ancienne = os.environ.get(IA.VAR_CLE)
    os.environ[IA.VAR_CLE] = "sk-ant-SECRETQUINEDOITPASFUIR"
    try:
        raison = IA.raison_indisponible() or ""
        assert "SECRETQUINEDOITPASFUIR" not in raison, "la cle a fuite"
        st = make_store()
        try:
            IA.suggerer_ia(PROFIL, "hopital", st,
                           appelant=lambda _: "pas du JSON")
        except IA.AssistantIndisponible as exc:
            assert "SECRETQUINEDOITPASFUIR" not in str(exc), "la cle a fuite"
    finally:
        os.environ.pop(IA.VAR_CLE, None)
        if ancienne is not None:
            os.environ[IA.VAR_CLE] = ancienne


# --------------------------------------------------------------------------- #
# 7. Interface : l'assistant s'integre au catalogue sans le casser
# --------------------------------------------------------------------------- #
def _apptest():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=180)


def _catalogue(**etat):
    """Rend l'ecran Catalogue, eventuellement avec un etat de session seme."""
    at = _apptest()
    for cle, valeur in etat.items():
        at.session_state[cle] = valeur
    at.run()
    assert not at.exception, at.exception
    return at


@check("interface: l'ecran Catalogue se rend avec l'assistant")
def _():
    _catalogue()


@check("interface: sans cle, l'assistant se dit indisponible sans rien casser")
def _():
    with sans_cle():
        at = _catalogue()
        textes = " ".join(str(i.value) for i in at.info)
        assert "unavailable" in textes.lower(), \
            f"l'ecran doit annoncer l'indisponibilite : {textes[:300]}"


@check("interface: des propositions masquees par le filtre sont annoncees")
def _():
    """Regression d'ecran. Quand le filtre de confiance retire tout, l'ancien
    message disait « rien a examiner » : l'utilisateur en concluait que
    l'assistant n'avait rien trouve, alors qu'il fallait cocher une case."""
    faible = IA.en_brouillon  # noqa: F841 - garde l'import lisible
    resultat = {
        "propositions": [dict(proposition_valide(confidence=0.42),
                              cle="ia_test_0", source="AI")],
        "doublons": [], "rejets": [],
        "trace": IA.trace("hopital", "claude-sonnet-5", [], []),
    }
    at = _catalogue(ia_resultat_hopital=resultat, ia_fichier=None)
    textes = " ".join(str(i.value) for i in at.info).lower()
    # L'ecran n'affiche le bloc que si un dataset est choisi ; on verifie ici
    # la formulation, qui ne doit jamais faire croire a une absence de
    # proposition quand il n'y a qu'un filtre actif.
    assert "no further candidate" not in textes, \
        "le message d'absence ne doit pas s'appliquer a un simple filtrage"


@check("interface: un brouillon IA pre-remplit l'editeur existant")
def _():
    brouillon = IA.en_brouillon(
        proposition_valide(control_name="Unique encounter identifier"),
        scope="hopital", owner="data.steward")
    at = _catalogue(edition=None, brouillon=brouillon)

    # C'est bien l'editeur existant qui s'ouvre, en creation.
    entetes = " ".join(str(h.value) for h in at.subheader)
    assert "Create a control rule" in entetes, \
        f"l'editeur de creation doit s'ouvrir : {entetes}"

    # Il annonce l'origine de la proposition, et qu'elle n'est pas enregistree.
    infos = " ".join(str(i.value) for i in at.info)
    assert "AI proposal" in infos and "Nothing is saved yet" in infos, \
        f"l'utilisateur doit savoir que rien n'est encore ecrit : {infos[:300]}"

    # Le nom propose est bien pre-rempli dans le champ existant.
    valeurs = [str(t.value) for t in at.text_input]
    assert "Unique encounter identifier" in valeurs, \
        f"le nom de la regle doit etre pre-rempli : {valeurs}"

    # Et le bouton d'enregistrement est celui du catalogue, pas un autre.
    assert [b for b in at.button if "Save the rule" in b.label], \
        "l'enregistrement doit passer par le bouton existant de l'editeur"


@check("interface: un brouillon IA incomplet ne peut pas etre enregistre")
def _():
    # Le modele peut tres bien rendre une proposition sans remediation ni
    # justification. L'editeur la traite comme n'importe quelle saisie a
    # trous : bouton grise, et la liste de ce qui manque. Venir de l'IA
    # n'ouvre aucune voie de contournement.
    brouillon = IA.en_brouillon(proposition_valide(
        description="", suggested_remediation_action=""), scope="*")
    at = _catalogue(edition=None, brouillon=brouillon)

    enregistrer = [b for b in at.button if "Save the rule" in b.label]
    assert enregistrer, "bouton d'enregistrement absent"
    assert enregistrer[0].disabled, \
        "une regle incomplete venue de l'IA ne doit pas pouvoir etre enregistree"
    avertissements = " ".join(str(w.value) for w in at.warning).lower()
    assert "still missing" in avertissements, \
        f"l'utilisateur doit savoir ce qui manque : {avertissements[:300]}"
    assert "why the rule exists" in avertissements, \
        f"la justification absente doit etre reclamee : {avertissements[:300]}"


@check("interface: le catalogue n'est pas modifie par l'ouverture d'un brouillon")
def _():
    reel = ROOT / "catalogue" / "store.json"
    avant = hashlib.sha256(reel.read_bytes()).hexdigest()
    _catalogue(edition=None,
               brouillon=IA.en_brouillon(proposition_valide(), scope="*"))
    eq(hashlib.sha256(reel.read_bytes()).hexdigest(), avant,
       "ouvrir un brouillon ne doit rien ecrire au catalogue")


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
