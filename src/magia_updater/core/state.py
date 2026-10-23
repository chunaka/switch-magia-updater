"""Estado local de la última actualización aplicada por SD."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from magia_updater.core.errors import MagiaError
from magia_updater.core.fs import atomic_write_json, default_state_root, read_json
from magia_updater.core.models import ComponentId, UpdatePlan, update_plan_to_dict

SCHEMA_VERSION = 1
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class StateError(MagiaError):
    """No se pudo leer o escribir el estado de la SD."""


@dataclass(frozen=True)
class AppliedComponent:
    component: ComponentId
    version: str
    release_tag: str


@dataclass(frozen=True)
class UpdateState:
    sd_id: str
    applied_at: str
    backup_dir: Path
    plan_sha256: str
    components: tuple[AppliedComponent, ...]
    status: str = "applied"


def plan_digest(plan: UpdatePlan) -> str:
    payload = json.dumps(
        update_plan_to_dict(plan),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def state_from_plan(
    plan: UpdatePlan,
    *,
    sd_id: str,
    backup_dir: Path,
    applied_at: str | None = None,
) -> UpdateState:
    components = tuple(
        AppliedComponent(
            component=component.component,
            version=str(component.release.version) if component.release is not None else "",
            release_tag=component.release.tag if component.release is not None else "",
        )
        for component in plan.components
    )
    return UpdateState(
        sd_id=sd_id,
        applied_at=applied_at
        if applied_at is not None
        else datetime.now(UTC).isoformat(timespec="seconds"),
        backup_dir=backup_dir,
        plan_sha256=plan_digest(plan),
        components=components,
    )


def _validate_key(value: str) -> str:
    if not value or not _SAFE_KEY_RE.fullmatch(value) or ".." in value:
        raise StateError(f"sd_id inválido: {value!r}")
    return value


class StateStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root if root is not None else default_state_root()).resolve()

    def path(self, sd_id: str) -> Path:
        return self.root / f"{_validate_key(sd_id)}.json"

    def save(self, state: UpdateState) -> Path:
        path = self.path(state.sd_id)
        payload = {
            "schema": SCHEMA_VERSION,
            "sd_id": state.sd_id,
            "applied_at": state.applied_at,
            "backup_dir": str(state.backup_dir),
            "plan_sha256": state.plan_sha256,
            "status": state.status,
            "components": [
                {
                    "component": component.component.value,
                    "version": component.version,
                    "release_tag": component.release_tag,
                }
                for component in state.components
            ],
        }
        atomic_write_json(path, payload)
        return path

    def load(self, sd_id: str) -> UpdateState | None:
        path = self.path(sd_id)
        if not path.is_file():
            return None
        try:
            payload: Any = read_json(path)
        except (OSError, ValueError) as exc:
            raise StateError(f"No se pudo leer el estado {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise StateError("El estado no es un objeto JSON.")
        try:
            components_raw = payload["components"]
            if not isinstance(components_raw, list):
                raise TypeError("components no es una lista")
            components = tuple(
                AppliedComponent(
                    component=ComponentId(str(item["component"])),
                    version=str(item["version"]),
                    release_tag=str(item["release_tag"]),
                )
                for item in components_raw
                if isinstance(item, dict)
            )
            return UpdateState(
                sd_id=str(payload["sd_id"]),
                applied_at=str(payload["applied_at"]),
                backup_dir=Path(str(payload["backup_dir"])),
                plan_sha256=str(payload["plan_sha256"]),
                components=components,
                status=str(payload.get("status", "applied")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError(f"Estado inválido en {path}: {exc}") from exc

    def clear(self, sd_id: str) -> bool:
        path = self.path(sd_id)
        if not path.exists():
            return False
        path.unlink()
        return True
