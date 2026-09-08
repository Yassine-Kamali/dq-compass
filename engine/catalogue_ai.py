"""
Assistant IA du catalogue : propose des controles candidats, n'en decide aucun.

Le brief autorise l'assistance a la redaction des regles (§14, « Suggesting
candidate rules from a dataset profile […] Assists the analyst, never issues
the control verdict »). Ce module tient ce role et rien d'autre.

Ce qu'il fait :
  - il reduit le profil d'un fichier a des metadonnees non identifiantes ;
  - il demande a Claude des controles candidats, dans les seuls templates que
    le catalogue expose aujourd'hui ;
  - il valide durement la reponse, ecarte ce qui est faux ou inconnu, et rend
    des objets propres a l'interface.

Ce qu'il ne fait pas, et ne doit jamais faire :
  - aucun verdict PASS / FAIL / ERROR / SKIPPED - c'est le moteur, lui seul ;
  - aucune execution de controle ;
  - aucune ecriture au catalogue - une proposition passe par l'editeur, le
    validateur et `store.add_control()`, comme une regle saisie a la main ;
  - aucune invention de template hors de ceux du store ;
  - aucun seuil metier presente comme un fait.

FRONTIERE DE CONFIDENTIALITE. Aucune ligne du fichier ne sort d'ici. Le
sanitiseur travaille par liste blanche : il recopie les champs de profil
explicitement autorises, et rien d'autre. Un champ que le profileur ajouterait
demain - un echantillon, une valeur mini/maxi, une modalite - ne franchira pas
cette porte sans qu'on l'y inscrive.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import pathlib
import re
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))

LOG = logging.getLogger("dq_compass.catalogue_ai")

# --------------------------------------------------------------------------- #
# Configuration. Rien n'est code en dur : ni cle, ni modele.
# --------------------------------------------------------------------------- #
MODELE_DEFAUT = "claude-sonnet-5"
VAR_CLE = "ANTHROPIC_API_KEY"
VAR_MODELE = "ANTHROPIC_MODEL"

FOURNISSEUR = "Anthropic"
MAX_TOKENS = 8000
TIMEOUT_S = 90.0

# Les six dimensions du brief. Une proposition hors de cette liste est ecartee.
DIMENSIONS_VALIDES = {"Completeness", "Validity", "Uniqueness", "Consistency",
                      "Timeliness", "Reconciliation"}

SEVERITES_VALIDES = {"Critical", "High", "Medium", "Low"}

# Un template dont le parametre est du code executable ne peut pas etre
# redige par un modele : l'expression entrerait au catalogue puis a l'execution
# sans qu'aucun humain n'ait lu ce qu'elle fait. Il reste accessible a la main,
# dans l'editeur, sous le regard d'un expert.
TEMPLATES_INTERDITS = {"CUSTOM_EXPRESSION"}

# Seuils d'affichage de la confiance (§17 du cahier des charges).
CONFIANCE_HAUTE = 0.80
CONFIANCE_MOYENNE = 0.60

# Champs du profil que l'assistant a le droit de voir. Liste blanche stricte :
# ce sont des mesures de forme, jamais des valeurs du fichier.
CHAMPS_PROFIL_AUTORISES = ("colonne", "type", "taux_nuls_pct",
                           "valeurs_nulles", "valeurs_distinctes", "unique")


class AssistantIndisponible(RuntimeError):
    """L'assistant ne peut pas repondre : cle absente, reseau, quota, format.

    Le message porte est destine a l'ecran : il ne contient ni cle, ni trace
    d'exception, ni fragment de prompt.
    """


# --------------------------------------------------------------------------- #
# Configuration : cle et modele
# --------------------------------------------------------------------------- #
def _depuis_secrets(nom: str) -> str | None:
    """Lit un secret Streamlit sans imposer Streamlit au reste du moteur.

    L'import est local : le module reste testable et importable hors interface.
    Toute erreur vaut « pas de secret » - un secrets.toml absent est le cas
    nominal en developpement.
    """
    try:
        import streamlit as st
        valeur = st.secrets.get(nom)
    except Exception:  # noqa: BLE001 - absence de secrets = cas nominal
        return None
    return str(valeur) if valeur else None


def cle_api() -> str | None:
    """Cle Anthropic, depuis l'environnement puis les secrets Streamlit.

    La valeur n'est jamais journalisee ni renvoyee a l'interface : seul son
    caractere present/absent circule.
    """
    return (os.environ.get(VAR_CLE) or "").strip() or _depuis_secrets(VAR_CLE)


def modele() -> str:
    """Modele a interroger. Configurable, avec un defaut Sonnet raisonnable."""
    return ((os.environ.get(VAR_MODELE) or "").strip()
            or _depuis_secrets(VAR_MODELE) or MODELE_DEFAUT)


def sdk_disponible() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def disponible() -> bool:
    """Vrai si l'assistant peut etre sollicite : SDK installe et cle presente."""
    return sdk_disponible() and bool(cle_api())


def raison_indisponible() -> str | None:
    """Phrase d'ecran expliquant pourquoi l'assistant est hors service."""
    if not sdk_disponible():
        return ("The Anthropic SDK is not installed. Run "
                "`pip install -r requirements.txt` to enable the assistant. "
                "Everything else in DQ Compass works without it.")
    if not cle_api():
        return (f"No Anthropic API key found. Set the `{VAR_CLE}` environment "
                f"variable, or add it to `.streamlit/secrets.toml`. "
                f"Deterministic suggestions and the DQ engine work without it.")
    return None


# --------------------------------------------------------------------------- #
# Frontiere de confidentialite : ce qui a le droit de sortir
# --------------------------------------------------------------------------- #
def sanitiser_profil(profil: list[dict], lignes: int | None = None) -> list[dict]:
    """Reduit le profil aux seules metadonnees de forme.

    Liste blanche : on recopie les champs autorises, on ignore tout le reste.
    Le taux d'unicite est calcule ici plutot que transmis : c'est un ratio, il
    ne porte aucune valeur du fichier.
    """
    out: list[dict] = []
    for colonne in profil or []:
        propre = {champ: colonne.get(champ) for champ in CHAMPS_PROFIL_AUTORISES
                  if champ in colonne}
        distinctes = colonne.get("valeurs_distinctes")
        if lignes and isinstance(distinctes, (int, float)) and lignes > 0:
            propre["taux_unicite_pct"] = round(100.0 * distinctes / lignes, 2)
        out.append(propre)
    return out


def colonnes_visees(params: dict) -> list[str]:
    """Colonnes designees par des parametres, quel que soit le template.

    Sert au dedoublonnage et au controle d'existence. Les parametres qui ne
    designent pas une colonne (valeurs, seuils, motifs) sont ignores.
    """
    noms: list[str] = []
    for cle in ("column", "field_a", "field_b", "before", "after", "amount",
                "total_column", "ref_column"):
        valeur = (params or {}).get(cle)
        if isinstance(valeur, str) and valeur.strip():
            noms.append(valeur.strip())
    for cle in ("columns", "group_by"):
        valeur = (params or {}).get(cle)
        if isinstance(valeur, list):
            noms += [str(v).strip() for v in valeur if str(v).strip()]
    return noms


def sanitiser_suggestions(propositions: list[dict]) -> list[dict]:
    """Reduit les suggestions deterministes a leur forme, sans leurs valeurs.

    Une proposition deterministe transporte des donnees du fichier : les
    modalites d'un IN_DOMAIN sont des valeurs reelles, et `constat` comme
    `description` citent des exemples de lignes. Rien de tout cela ne sort.
    Seuls circulent le template, la dimension et les colonnes visees - de quoi
    dire a l'assistant « ceci est deja couvert », pas de quoi reconstituer une
    ligne.
    """
    out: list[dict] = []
    for p in propositions or []:
        out.append({
            "template": p.get("template"),
            "dimension": p.get("dimension"),
            "colonnes": colonnes_visees(p.get("params") or {}),
        })
    return out


def _params_lisibles(control: dict) -> dict:
    try:
        params = json.loads(control.get("params") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return params if isinstance(params, dict) else {}


def templates_offerts(store) -> list[dict]:
    """Templates que l'assistant a le droit de proposer, lus dans le store.

    Aucune liste n'est recopiee ici : ce que le catalogue expose aujourd'hui
    est exactement ce que l'assistant voit. Un template ajoute demain lui sera
    offert sans toucher a ce fichier.
    """
    out = []
    for tpl in getattr(store, "templates", []) or []:
        tid = tpl.get("template_id")
        if not tid or tid in TEMPLATES_INTERDITS:
            continue
        out.append({
            "template": tid,
            "dimension": tpl.get("dimension", ""),
            "params_requis": tpl.get("params_requis", ""),
            "params_optionnels": tpl.get("params_optionnels", ""),
            "description": tpl.get("description_logique", ""),
            "exemple_params": tpl.get("exemple_params", ""),
        })
    return out


def construire_charge(profil: list[dict], nom_dataset: str, store,
                      deterministes: list[dict] | None = None,
                      contexte_metier: str = "",
                      lignes: int | None = None,
                      controles_existants: list[dict] | None = None) -> dict:
    """Assemble la charge utile envoyee au modele.

    C'est le seul endroit qui decide de ce qui sort. Un test verifie qu'aucune
    valeur du fichier ne s'y trouve : si vous ajoutez un champ ici, ajoutez la
    preuve qu'il ne transporte pas de donnee.
    """
    return {
        "dataset": {
            "nom": str(nom_dataset or ""),
            "nombre_de_lignes": int(lignes) if lignes else None,
            "nombre_de_colonnes": len(profil or []),
        },
        "contexte_metier": str(contexte_metier or "").strip(),
        "profil_des_colonnes": sanitiser_profil(profil, lignes),
        "templates_disponibles": templates_offerts(store),
        "suggestions_deterministes_deja_couvertes":
            sanitiser_suggestions(deterministes or []),
        "controles_deja_au_catalogue": [
            {"template": c.get("template"),
             "colonnes": colonnes_visees(_params_lisibles(c)),
             "nom": c.get("control_name", "")}
            for c in (controles_existants or [])],
    }


# --------------------------------------------------------------------------- #
# Consigne interne. Elle est ecrite ici, pas laissee a l'utilisateur.
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """\
You are a Data Quality Catalogue Assistant working inside a governed control \
catalogue. You assist a human Data Steward; you are not a decision engine.

YOUR ROLE
Propose candidate Data Quality controls from dataset metadata, the list of \
available control templates, and optional business context.

HARD RULES
- You never issue PASS, FAIL, ERROR or SKIPPED decisions. A separate \
deterministic engine owns every verdict.
- You never execute a control.
- You may ONLY use templates from the `templates_disponibles` list supplied to \
you. Never invent a template name, and never propose one that is absent from \
that list.
- You never invent business thresholds, tolerances, reference datasets, \
regulatory rules, owners, frequencies or reconciliation sources as if they \
were facts. When a value cannot be derived from the metadata, propose it only \
as a value requiring human confirmation and list what is missing in \
`missing_business_inputs`.
- You only reference columns that appear in `profil_des_colonnes`.
- Prefer a small number of useful, defensible controls over many weak ones.
- Explain why every control is proposed, in one or two sentences an analyst \
can check against the metadata.
- Do not duplicate the entries in \
`suggestions_deterministes_deja_couvertes` or `controles_deja_au_catalogue` \
unless you add real semantic value that the deterministic profiler cannot see \
(for example: recognising that a column is a business code needing a \
reference table). If you do, say what you add in `reason`.
- Use semantic reasoning mainly where profiling alone is insufficient: what a \
column name means in business terms, which columns relate to each other, \
which fields are identifiers, dates of record, or controlled codes.
- When `contexte_metier` is empty, be more conservative: lower your confidence \
on any semantic assumption, and mark business input as required more readily.

OUTPUT
Return structured JSON only. No prose, no markdown, no code fences. The exact \
shape is:

{"proposals": [{
  "control_name": "short human name",
  "control_type": "Completeness|Validity|Uniqueness|Consistency|Timeliness|Reconciliation",
  "template": "one of templates_disponibles",
  "data_element": ["column names from the profile"],
  "params": {"...": "template parameters, using the template's own parameter names"},
  "description": "why this control exists, for a business reader",
  "logic_definition": "what the control checks, in one sentence",
  "suggested_severity": "High|Medium|Low",
  "suggested_kpi": "the indicator produced",
  "suggested_remediation_action": "what the owner must do when it breaches",
  "reason": "why you propose this, grounded in the metadata",
  "confidence": 0.0,
  "business_input_required": true,
  "missing_business_inputs": ["what a human must supply"]
}]}

If you have nothing defensible to propose, return {"proposals": []}."""


def construire_message(charge: dict) -> str:
    """Le message utilisateur : la charge, telle quelle, en JSON."""
    return (
        "Here is the metadata of a dataset a Data Steward is onboarding. "
        "Propose candidate Data Quality controls, following your rules.\n\n"
        "No row of the dataset is included: you are seeing profiling "
        "metadata only, by design.\n\n"
        + json.dumps(charge, ensure_ascii=False, indent=2, default=str))


# --------------------------------------------------------------------------- #
# Appel au fournisseur
# --------------------------------------------------------------------------- #
def _texte_de_la_reponse(reponse) -> str:
    """Concatene les blocs de texte, en ignorant les blocs de raisonnement."""
    morceaux = []
    for bloc in getattr(reponse, "content", None) or []:
        if getattr(bloc, "type", None) == "text":
            morceaux.append(getattr(bloc, "text", ""))
    return "\n".join(morceaux).strip()


def interroger(charge: dict, cle: str | None = None,
               nom_modele: str | None = None) -> str:
    """Envoie la charge au modele et rend le texte brut de sa reponse.

    Toute erreur du fournisseur est retraduite en `AssistantIndisponible` avec
    un message d'ecran. Les details techniques partent au journal, jamais a
    l'interface, et jamais avec la cle.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise AssistantIndisponible(
            "The Anthropic SDK is not installed.") from exc

    cle = cle or cle_api()
    if not cle:
        raise AssistantIndisponible("No Anthropic API key configured.")

    client = anthropic.Anthropic(api_key=cle, timeout=TIMEOUT_S)
    try:
        reponse = client.messages.create(
            model=nom_modele or modele(),
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": construire_message(charge)}],
        )
    except anthropic.AuthenticationError as exc:
        LOG.warning("catalogue_ai: authentification refusee")
        raise AssistantIndisponible(
            "The Anthropic API key was refused. Check its value.") from exc
    except anthropic.RateLimitError as exc:
        LOG.warning("catalogue_ai: quota atteint")
        raise AssistantIndisponible(
            "The Anthropic API is rate-limited right now. Try again in a "
            "moment.") from exc
    except anthropic.APITimeoutError as exc:
        LOG.warning("catalogue_ai: delai depasse")
        raise AssistantIndisponible(
            "The Anthropic API did not answer in time. Try again.") from exc
    except anthropic.APIConnectionError as exc:
        LOG.warning("catalogue_ai: reseau indisponible")
        raise AssistantIndisponible(
            "Could not reach the Anthropic API. Check the network "
            "connection.") from exc
    except anthropic.APIStatusError as exc:
        LOG.warning("catalogue_ai: statut %s", getattr(exc, "status_code", "?"))
        raise AssistantIndisponible(
            "The Anthropic API returned an error. The assistant is "
            "unavailable for now.") from exc
    except Exception as exc:  # noqa: BLE001 - l'ecran ne doit jamais casser
        LOG.exception("catalogue_ai: echec inattendu")
        raise AssistantIndisponible(
            "The assistant failed unexpectedly. Deterministic suggestions are "
            "unaffected.") from exc

    texte = _texte_de_la_reponse(reponse)
    if not texte:
        raise AssistantIndisponible("The assistant returned an empty response.")
    return texte


# --------------------------------------------------------------------------- #
# Lecture de la reponse. Elle est traitee comme une entree hostile.
# --------------------------------------------------------------------------- #
def extraire_json(texte: str) -> dict:
    """Lit le JSON d'une reponse de modele, cloture de code ou non.

    Une sortie mal formee est un cas nominal, pas un incident : elle leve
    `AssistantIndisponible`, que l'appelant affiche proprement.
    """
    brut = (texte or "").strip()
    if not brut:
        raise AssistantIndisponible("The assistant returned an empty response.")

    # Cloture markdown eventuelle : ```json … ```
    cloture = re.match(r"^```(?:json)?\s*(.*?)\s*```$", brut,
                       re.DOTALL | re.IGNORECASE)
    if cloture:
        brut = cloture.group(1).strip()

    try:
        charge = json.loads(brut)
    except json.JSONDecodeError:
        debut, fin = brut.find("{"), brut.rfind("}")
        if debut == -1 or fin <= debut:
            raise AssistantIndisponible(
                "The assistant did not return readable JSON.") from None
        try:
            charge = json.loads(brut[debut:fin + 1])
        except json.JSONDecodeError:
            raise AssistantIndisponible(
                "The assistant did not return readable JSON.") from None

    if not isinstance(charge, dict):
        raise AssistantIndisponible(
            "The assistant did not return the expected JSON object.")
    return charge


def _texte(valeur, defaut: str = "") -> str:
    if isinstance(valeur, str):
        return valeur.strip()
    if valeur is None:
        return defaut
    return str(valeur).strip()


def valider_proposition(brute: Any, templates_connus: set[str],
                        colonnes_connues: set[str]) -> tuple[dict | None, str]:
    """Valide une proposition. Rend (proposition propre, motif de rejet).

    Le motif de rejet est conserve : une proposition ecartee silencieusement
    est une proposition qu'on ne peut pas auditer.
    """
    if not isinstance(brute, dict):
        return None, "the proposal is not a JSON object"

    template = _texte(brute.get("template"))
    if not template:
        return None, "no template named"
    if template in TEMPLATES_INTERDITS:
        return None, (f"template '{template}' requires expert configuration "
                      f"and cannot be generated automatically")
    if template not in templates_connus:
        return None, f"unknown template '{template}'"

    dimension = _texte(brute.get("control_type"))
    if dimension not in DIMENSIONS_VALIDES:
        return None, f"unknown data quality dimension '{dimension}'"

    params = brute.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None, "the parameters are not a JSON object"

    # Colonnes : celles annoncees et celles reellement citees en parametre.
    annoncees = brute.get("data_element")
    if isinstance(annoncees, str):
        annoncees = [annoncees]
    if not isinstance(annoncees, list):
        annoncees = []
    annoncees = [_texte(c) for c in annoncees if _texte(c)]

    citees = colonnes_visees(params)
    inconnues = sorted({c for c in list(annoncees) + citees
                        if c and c not in colonnes_connues})
    if inconnues:
        return None, ("column(s) absent from the dataset: "
                      + ", ".join(inconnues))

    nom = _texte(brute.get("control_name"))
    if not nom:
        return None, "the control has no name"

    try:
        confiance = float(brute.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None, "unreadable confidence score"
    if not 0.0 <= confiance <= 1.0:
        return None, f"confidence out of the 0-1 range ({confiance})"

    severite = _texte(brute.get("suggested_severity")) or "Medium"
    if severite not in SEVERITES_VALIDES:
        severite = "Medium"

    manquants = brute.get("missing_business_inputs")
    if isinstance(manquants, str):
        manquants = [manquants]
    if not isinstance(manquants, list):
        manquants = []
    manquants = [_texte(m) for m in manquants if _texte(m)]

    besoin = brute.get("business_input_required")
    besoin = bool(manquants) if besoin is None else bool(besoin)

    return {
        "control_name": nom,
        "control_type": dimension,
        "template": template,
        "data_element": annoncees or citees,
        "params": params,
        "description": _texte(brute.get("description")),
        "logic_definition": _texte(brute.get("logic_definition")),
        "suggested_severity": severite,
        "suggested_kpi": _texte(brute.get("suggested_kpi")),
        "suggested_remediation_action":
            _texte(brute.get("suggested_remediation_action")),
        "reason": _texte(brute.get("reason")),
        "confidence": confiance,
        "business_input_required": besoin,
        "missing_business_inputs": manquants,
        "source": "AI",
    }, ""


def valider_reponse(charge: dict, store, profil: list[dict]
                    ) -> tuple[list[dict], list[str]]:
    """Valide la reponse entiere. Rend (propositions retenues, rejets).

    Rien de ce qui sort d'ici n'a echappe au controle : template connu du store,
    dimension du brief, colonnes presentes dans le fichier, confiance chiffree.
    """
    templates_connus = {t["template"] for t in templates_offerts(store)}
    colonnes_connues = {c.get("colonne") for c in (profil or [])}

    brutes = charge.get("proposals")
    if brutes is None:
        return [], ["the response carries no 'proposals' list"]
    if not isinstance(brutes, list):
        return [], ["'proposals' is not a list"]

    retenues, rejets = [], []
    for i, brute in enumerate(brutes, start=1):
        propre, motif = valider_proposition(brute, templates_connus,
                                            colonnes_connues)
        if propre is None:
            nom = ""
            if isinstance(brute, dict):
                nom = _texte(brute.get("control_name"))
            rejets.append(f"{nom or f'proposal {i}'}: {motif}")
            continue
        retenues.append(propre)
    return retenues, rejets


# --------------------------------------------------------------------------- #
# Dedoublonnage : ne pas noyer l'ecran sous ce que le deterministe dit deja
# --------------------------------------------------------------------------- #
def signature(template: str, params: dict, scope: str = "*") -> tuple:
    """Identite d'un controle pour la comparaison : template, cibles, portee."""
    return (str(template or "").upper(),
            tuple(sorted(c.lower() for c in colonnes_visees(params or {}))),
            (str(scope or "*").strip() or "*").lower())


def dedoublonner(propositions: list[dict], deterministes: list[dict],
                 existants: list[dict] | None = None,
                 scope: str = "*") -> tuple[list[dict], list[dict]]:
    """Separe les propositions inedites de celles qui doublonnent.

    L'enrichissement est prefere a la duplication : une proposition qui recouvre
    exactement une suggestion deterministe n'est pas affichee une seconde fois,
    son explication est accrochee a la suggestion existante (`ia_reason`).
    """
    connues: dict[tuple, dict] = {}
    for d in deterministes or []:
        connues[signature(d.get("template"), d.get("params"),
                          d.get("dataset_scope", scope))] = d
    for c in existants or []:
        connues.setdefault(
            signature(c.get("template"), _params_lisibles(c),
                      c.get("dataset_scope", "*")), c)

    inedites, doublons = [], []
    for p in propositions:
        sig = signature(p["template"], p["params"], scope)
        cible = connues.get(sig)
        if cible is None:
            # Une meme signature ne doit pas non plus se repeter entre
            # propositions du modele.
            connues[sig] = p
            inedites.append(p)
            continue
        if p.get("reason"):
            cible["ia_reason"] = p["reason"]
            cible["ia_confidence"] = p.get("confidence")
        doublons.append(p)
    return inedites, doublons


# --------------------------------------------------------------------------- #
# Passage a l'editeur : une proposition devient un brouillon de regle
# --------------------------------------------------------------------------- #
def en_brouillon(proposition: dict, scope: str = "*",
                 owner: str = "Data Steward", store=None) -> dict:
    """Traduit une proposition en brouillon au schema du catalogue.

    Le brouillon suit exactement les champs de `store.CONTROL_FIELDS` : le
    catalogue n'a pas a savoir d'ou vient une regle. Deux absences sont
    voulues :

      - `rule_id` n'est pas fixe ici. Il est attribue par le store au moment de
        l'enregistrement (`next_rule_id()`), jamais par le modele ;
      - `statut`, `version` et `effective_from` appartiennent au store.

    Rien n'est ecrit : c'est l'editeur qui affiche ce brouillon, le validateur
    qui l'accepte, et l'humain qui enregistre.
    """
    params = proposition.get("params") or {}
    kpi = proposition.get("suggested_kpi", "")
    if store is not None:
        tpl = store.template(proposition.get("template", ""))
        if tpl and tpl.get("kpi_produit"):
            kpi = tpl["kpi_produit"]

    cibles = proposition.get("data_element") or colonnes_visees(params)
    return {
        "control_name": proposition.get("control_name", ""),
        "control_type": proposition.get("control_type", ""),
        "description": proposition.get("description", ""),
        "template": proposition.get("template", ""),
        "params": json.dumps(params, ensure_ascii=False),
        "logic_definition": proposition.get("logic_definition", ""),
        "dataset_scope": scope or "*",
        "data_element": ", ".join(str(c) for c in cibles),
        "seuil_tolerance_pct": 0.0,
        "severity": proposition.get("suggested_severity", "Medium"),
        "frequency": "On demand",
        "owner": owner or "Data Steward",
        "output_type": "Exception report",
        "kpi": kpi,
        "remediation_action": proposition.get(
            "suggested_remediation_action", ""),
    }


def niveau_de_confiance(valeur: float | None) -> str:
    """Traduit une confiance en palier lisible (§17)."""
    try:
        v = float(valeur)
    except (TypeError, ValueError):
        return "unknown"
    if v >= CONFIANCE_HAUTE:
        return "high"
    if v >= CONFIANCE_MOYENNE:
        return "medium"
    return "low"


# --------------------------------------------------------------------------- #
# Tracabilite de session. Rien n'entre au catalogue avant approbation.
# --------------------------------------------------------------------------- #
def trace(nom_dataset: str, nom_modele: str, propositions: list[dict],
          rejets: list[str], doublons: int = 0) -> dict:
    """Rend la trace d'une consultation, pour la session courante.

    Ce n'est pas un enregistrement de catalogue : une proposition n'a aucune
    valeur de gouvernance tant qu'un humain ne l'a pas approuvee. Le journal du
    store prend le relais a l'enregistrement, via `store.add_control()`.
    """
    return {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "fournisseur": FOURNISSEUR,
        "modele": nom_modele,
        "dataset": nom_dataset,
        "propositions": len(propositions),
        "rejetees": len(rejets),
        "doublons": doublons,
        "motifs_de_rejet": list(rejets),
        "decisions": {},
    }


# --------------------------------------------------------------------------- #
# Point d'entree unique de l'interface
# --------------------------------------------------------------------------- #
def suggerer_ia(profil: list[dict], nom_dataset: str, store,
                deterministes: list[dict] | None = None,
                contexte_metier: str = "", lignes: int | None = None,
                scope: str = "*", controles_existants: list[dict] | None = None,
                cle: str | None = None, nom_modele: str | None = None,
                appelant=None) -> dict:
    """Chaine complete : metadonnees -> Claude -> JSON valide -> candidats.

    `appelant` permet aux tests d'injecter une reponse sans reseau. En
    production il reste None et l'appel passe par `interroger()`.

    Leve `AssistantIndisponible` - jamais autre chose : l'interface doit
    pouvoir tout attraper en une clause.
    """
    charge = construire_charge(
        profil=profil, nom_dataset=nom_dataset, store=store,
        deterministes=deterministes, contexte_metier=contexte_metier,
        lignes=lignes, controles_existants=controles_existants)

    nom_modele = nom_modele or modele()
    appel = appelant or (lambda c: interroger(c, cle=cle, nom_modele=nom_modele))

    try:
        texte = appel(charge)
    except AssistantIndisponible:
        raise
    except Exception as exc:  # noqa: BLE001 - l'ecran ne doit jamais casser
        LOG.exception("catalogue_ai: echec de l'appel")
        raise AssistantIndisponible(
            "The assistant could not be reached. Deterministic suggestions are "
            "unaffected.") from exc

    brut = extraire_json(texte)
    retenues, rejets = valider_reponse(brut, store, profil)
    inedites, doublons = dedoublonner(retenues, deterministes or [],
                                      controles_existants, scope)

    for i, p in enumerate(inedites):
        p["cle"] = f"ia_{p['template']}_{i}_" + "_".join(
            str(c) for c in (p.get("data_element") or []))[:60]

    inedites.sort(key=lambda p: -float(p.get("confidence") or 0))
    return {
        "propositions": inedites,
        "doublons": doublons,
        "rejets": rejets,
        "trace": trace(nom_dataset, nom_modele, inedites, rejets,
                       len(doublons)),
    }
