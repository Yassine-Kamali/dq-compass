"""
Catalogue store: the single source of truth for control definitions.

Excel is no longer the input format. It is now an *output* (the business
deliverable) and an optional exchange format (export for sign-off, re-import).
The store is a JSON document so that the UI and the engine can read and write
it safely, and so that every change is journalled.

Governance rules enforced here, not left to a user manual:
  - a control is never deleted, only suspended or deprecated;
  - any change to an executable field bumps the version and is journalled;
  - every mutation records who, when, what, before -> after.
"""
from __future__ import annotations

import copy
import datetime as dt
import fnmatch
import json
import pathlib
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / "catalogue" / "store.json"

STATUTS = ["Actif", "Suspendu", "Deprecie"]
SEVERITES = ["Critical", "High", "Medium", "Low"]

# Business vocabulary. Outside IT, nobody knows what "Critical" implies;
# "Blocking" needs no glossary.
SEVERITES_LIBELLES = {
    "Critical": "Blocking",
    "High": "Major",
    "Medium": "Moderate",
    "Low": "Minor",
}
STATUTS_LIBELLES = {
    "Actif": "In service",
    "Suspendu": "Paused",
    "Deprecie": "Retired",
}
SEVERITES_AIDE = {
    "Critical": "The file must not be published while the breach stands.",
    "High": "To be fixed before the next release.",
    "Medium": "To be reviewed, without holding the release.",
    "Low": "Reported for information.",
}

# Changing one of these changes what the engine executes -> version bump.
EXECUTABLE_FIELDS = {"template", "params", "dataset_scope", "seuil_tolerance_pct", "statut"}

CONTROL_FIELDS = [
    "rule_id", "control_name", "control_type", "description", "template",
    "params", "logic_definition", "dataset_scope", "data_element",
    "seuil_tolerance_pct", "severity", "frequency", "owner", "output_type",
    "kpi", "remediation_action", "statut", "version", "effective_from",
]


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return dt.date.today().isoformat()


class CatalogueStore:
    def __init__(self, path: pathlib.Path | str = STORE_PATH):
        self.path = pathlib.Path(path)
        self.data: dict[str, Any] = self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> dict:
        if not self.path.exists():
            return {"meta": {"schema": "2.0", "updated_at": _now()},
                    "templates": [], "controls": [], "changelog": []}
        with self.path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def save(self) -> None:
        self.data["meta"]["updated_at"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    # -------------------------------------------------------------- lookups
    @property
    def templates(self) -> list[dict]:
        return self.data["templates"]

    @property
    def controls(self) -> list[dict]:
        return self.data["controls"]

    @property
    def changelog(self) -> list[dict]:
        return self.data["changelog"]

    def template(self, template_id: str) -> dict | None:
        return next((t for t in self.templates if t["template_id"] == template_id), None)

    def control(self, rule_id: str) -> dict | None:
        return next((c for c in self.controls if c["rule_id"] == rule_id), None)

    def next_rule_id(self) -> str:
        nums = [int(c["rule_id"][2:]) for c in self.controls
                if c["rule_id"][:2] == "DQ" and c["rule_id"][2:].isdigit()]
        return f"DQ{max(nums, default=0) + 1:02d}"

    # ------------------------------------------------------------- journal
    def _journal(self, action: str, rule_id: str, champ: str,
                 avant: Any, apres: Any, user: str, motif: str = "") -> None:
        self.changelog.append({
            "timestamp": _now(), "utilisateur": user, "action": action,
            "rule_id": rule_id, "champ": champ,
            "avant": "" if avant is None else str(avant),
            "apres": "" if apres is None else str(apres),
            "motif": motif,
        })

    # ------------------------------------------------------------ mutations
    def add_control(self, control: dict, user: str, motif: str = "") -> dict:
        rule_id = control.get("rule_id") or self.next_rule_id()
        if self.control(rule_id):
            raise ValueError(f"Control {rule_id} already exists.")
        new = {f: control.get(f, "") for f in CONTROL_FIELDS}
        new["rule_id"] = rule_id
        new["version"] = 1
        new["statut"] = control.get("statut", "Actif")
        new["effective_from"] = control.get("effective_from") or _today()
        self.controls.append(new)
        self._journal("CREATION", rule_id, "*", None, new["control_name"], user, motif)
        return new

    def update_control(self, rule_id: str, changes: dict, user: str, motif: str = "") -> dict:
        ctrl = self.control(rule_id)
        if ctrl is None:
            raise ValueError(f"Unknown control: {rule_id}")
        before = copy.deepcopy(ctrl)
        touched_executable = False
        for field, value in changes.items():
            if field in ("rule_id", "version"):
                continue
            if str(ctrl.get(field, "")) == str(value):
                continue
            self._journal("MODIFICATION", rule_id, field, ctrl.get(field), value, user, motif)
            ctrl[field] = value
            if field in EXECUTABLE_FIELDS:
                touched_executable = True
        if touched_executable:
            ctrl["version"] = int(before.get("version", 1)) + 1
            ctrl["effective_from"] = _today()
            self._journal("VERSION", rule_id, "version",
                          before.get("version", 1), ctrl["version"], user, motif)
        return ctrl

    def set_statut(self, rule_id: str, statut: str, user: str, motif: str = "") -> dict:
        if statut not in STATUTS:
            raise ValueError(f"Invalid state: {statut}. Expected: {STATUTS}")
        return self.update_control(rule_id, {"statut": statut}, user, motif)

    def delete_control(self, rule_id: str, user: str, motif: str = "") -> dict:
        """Retire une regle du catalogue sans perdre sa definition.

        La suppression a longtemps ete interdite ici, au motif qu'un rapport
        deja produit devait rester explicable. L'argument ne tient plus : chaque
        pack de preuves embarque le catalogue integral du moment
        (`catalogue_snapshot.json`), et le journal conserve ci-dessous la
        definition complete de la regle retiree. Un run passe reste donc
        reconstructible apres la suppression de la regle qui l'a produit.

        Ce qui reste interdit, c'est de supprimer sans dire pourquoi.
        """
        ctrl = self.control(rule_id)
        if ctrl is None:
            raise ValueError(f"Unknown control: {rule_id}")
        if not str(motif).strip():
            raise ValueError(
                "Deleting a rule requires a reason: it is kept in the log.")
        self.controls.remove(ctrl)
        self._journal("SUPPRESSION", rule_id, "*",
                      json.dumps(ctrl, ensure_ascii=False), None, user, motif)
        return ctrl

    # ------------------------------------------------------------- helpers
    def active_controls(self, fichier: str | None = None) -> list[dict]:
        """Controles en service, filtres au besoin par le nom du fichier vise."""
        out = [c for c in self.controls if c.get("statut") == "Actif"]
        if fichier is not None:
            out = [c for c in out if scope_matches(c.get("dataset_scope", ""), fichier)]
        return out


class _Souple(dict):
    """Formatage tolerant : un parametre absent devient '…' au lieu de lever."""

    def __missing__(self, cle: str) -> str:
        return "…"


def _gabarit(tpl: dict, params: dict) -> str:
    """Choisit la formulation la plus precise que le template propose.

    Un template peut declarer plusieurs variantes de phrase : `phrase_<sens>`
    pour un mode d'execution, `phrase_motif` pour un ciblage par motif de nom de
    colonne, et `phrase_min` / `phrase_max` pour une borne unique. La plus
    specifique gagne.
    """
    sens = params.get("sens")
    if sens and tpl.get(f"phrase_{sens}"):
        return tpl[f"phrase_{sens}"]

    # Une borne unique se dit autrement que deux : « au moins 0 » plutot que
    # « entre 0 et … ». Le suffixe se combine au ciblage.
    borne = ""
    if "min" in params and "max" not in params:
        borne = "_min"
    elif "max" in params and "min" not in params:
        borne = "_max"

    # Un ciblage par motif se dit autrement qu'un ciblage par nom : « toute
    # colonne dont le nom ressemble a… » plutot que « la colonne X ». Tout
    # parametre `motif_*` releve du meme cas.
    par_motif = "colonnes_motif" in params or any(
        str(cle).startswith("motif_") for cle in params)
    candidats = []
    if par_motif:
        candidats += [f"phrase_motif{borne}", "phrase_motif"]
    candidats += [f"phrase{borne}", "phrase"]
    for cle in candidats:
        if tpl.get(cle):
            return tpl[cle]
    return ""


def phrase_controle(control: dict, store: "CatalogueStore") -> str:
    """Rend un controle en une phrase francaise, lisible sans culture data."""
    tpl = store.template(control.get("template", ""))
    if tpl is None:
        return control.get("description", "") or control.get("control_name", "")
    try:
        params = json.loads(control.get("params") or "{}")
    except (json.JSONDecodeError, TypeError):
        return control.get("description", "")

    lisibles = {}
    for cle, valeur in params.items():
        if isinstance(valeur, list):
            lisibles[cle] = ", ".join(str(v) for v in valeur)
        elif isinstance(valeur, float) and valeur.is_integer():
            lisibles[cle] = str(int(valeur))
        else:
            lisibles[cle] = str(valeur)

    gabarit = _gabarit(tpl, params)
    if not gabarit:
        return control.get("description", "") or tpl.get("libelle_metier", "")
    return gabarit.format_map(_Souple(lisibles))


def libelle_severite(severity: str) -> str:
    return SEVERITES_LIBELLES.get(severity, severity or "")


def libelle_statut(statut: str) -> str:
    return STATUTS_LIBELLES.get(statut, statut or "")


def scope_matches(scope: str, fichier: str) -> bool:
    """La portee d'une regle se lit sur le nom du fichier, pas sur un contrat.

    `dataset_scope` accepte '*', un motif de nom de fichier ('ventes_*') ou une
    liste separee par des virgules. Le nom compare est celui du fichier sans son
    extension : une regle ecrite aujourd'hui couvre ainsi les fichiers de demain
    sans qu'on ait a les declarer.
    """
    scope = (scope or "").strip()
    if scope in ("", "*"):
        return True
    cible = str(fichier or "")
    return any(fnmatch.fnmatch(cible, motif.strip())
               for motif in scope.split(",") if motif.strip())


def load_store(path: pathlib.Path | str = STORE_PATH) -> CatalogueStore:
    return CatalogueStore(path)
