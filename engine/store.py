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
import json
import pathlib
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / "catalogue" / "store.json"

STATUTS = ["Actif", "Suspendu", "Deprecie"]
SEVERITES = ["Critical", "High", "Medium", "Low"]

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
            return {"meta": {"schema": "1.0", "updated_at": _now()},
                    "templates": [], "datasets": {}, "controls": [], "changelog": []}
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
    def datasets(self) -> dict:
        return self.data["datasets"]

    @property
    def changelog(self) -> list[dict]:
        return self.data["changelog"]

    def template(self, template_id: str) -> dict | None:
        return next((t for t in self.templates if t["template_id"] == template_id), None)

    def control(self, rule_id: str) -> dict | None:
        return next((c for c in self.controls if c["rule_id"] == rule_id), None)

    def dataset(self, name: str) -> dict | None:
        return self.datasets.get(name)

    def columns_of(self, dataset: str) -> list[dict]:
        ds = self.dataset(dataset)
        return ds["colonnes"] if ds else []

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
            raise ValueError(f"Le controle {rule_id} existe deja.")
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
            raise ValueError(f"Controle inconnu : {rule_id}")
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
            raise ValueError(f"Statut invalide : {statut}. Attendu : {STATUTS}")
        return self.update_control(rule_id, {"statut": statut}, user, motif)

    def delete_control(self, *_a, **_k):
        raise PermissionError(
            "Suppression interdite : l'audit exige de rejouer les runs historiques. "
            "Utilisez le statut Suspendu ou Deprecie."
        )

    # --------------------------------------------------------- dataset defs
    def upsert_dataset(self, name: str, definition: dict, user: str, motif: str = "") -> dict:
        action = "DATASET_MAJ" if name in self.datasets else "DATASET_AJOUT"
        self.datasets[name] = definition
        self._journal(action, name, "contrat", None,
                      f"{len(definition.get('colonnes', []))} colonnes", user, motif)
        return definition

    # ------------------------------------------------------------- helpers
    def active_controls(self, dataset: str | None = None) -> list[dict]:
        out = [c for c in self.controls if c.get("statut") == "Actif"]
        if dataset is not None:
            out = [c for c in out if scope_matches(c.get("dataset_scope", ""), dataset)]
        return out


def scope_matches(scope: str, dataset: str) -> bool:
    """dataset_scope accepts '*', a single name, or a comma-separated list."""
    scope = (scope or "").strip()
    if scope in ("", "*"):
        return True
    return dataset in [s.strip() for s in scope.split(",")]


def load_store(path: pathlib.Path | str = STORE_PATH) -> CatalogueStore:
    return CatalogueStore(path)
