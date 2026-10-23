"""Preferencias persistentes de Magia Updater."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from magia_updater.core.cache import default_cache_root
from magia_updater.core.components import UPDATABLE_COMPONENTS
from magia_updater.core.errors import MagiaError
from magia_updater.core.fs import (
    atomic_write_json,
    default_app_root,
    default_backup_root,
    default_state_root,
    read_json,
)
from magia_updater.core.models import ComponentId

SETTINGS_SCHEMA = 1


class SettingsError(MagiaError):
    """El archivo de preferencias no es válido."""


@dataclass(frozen=True)
class AppSettings:
    cache_dir: Path = field(default_factory=default_cache_root)
    backup_dir: Path = field(default_factory=default_backup_root)
    state_dir: Path = field(default_factory=default_state_root)
    keep_backups: int = 3
    mirror_backup: bool = False
    offline: bool = False
    install_payloads: bool = True
    allow_reboot_payload: bool = False
    allow_root_payload: bool = False
    components: tuple[ComponentId, ...] = field(default_factory=lambda: UPDATABLE_COMPONENTS)


def default_settings_path() -> Path:
    return default_app_root() / "settings.json"


def _path_value(raw: Mapping[str, Any], key: str, default: Path) -> Path:
    value = raw.get(key, str(default))
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"La ruta {key!r} debe ser un texto no vacío.")
    return Path(value).expanduser()


def _bool_value(raw: Mapping[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise SettingsError(f"La preferencia {key!r} debe ser booleana.")
    return value


def _components_value(raw: Mapping[str, Any]) -> tuple[ComponentId, ...]:
    value = raw.get("components", [component.value for component in UPDATABLE_COMPONENTS])
    if not isinstance(value, list) or not value:
        raise SettingsError("La preferencia 'components' debe ser una lista no vacía.")
    valid = {component.value: component for component in UPDATABLE_COMPONENTS}
    result: list[ComponentId] = []
    for item in value:
        if not isinstance(item, str):
            raise SettingsError("Cada componente debe ser un texto.")
        component = valid.get(item)
        if component is None:
            allowed = ", ".join(valid)
            raise SettingsError(f"Componente inválido: {item!r}. Válidos: {allowed}")
        if component not in result:
            result.append(component)
    return tuple(result)


def settings_from_dict(raw: Mapping[str, Any]) -> AppSettings:
    if raw.get("schema") != SETTINGS_SCHEMA:
        raise SettingsError("La versión del archivo de preferencias no es compatible.")
    keep_backups = raw.get("keep_backups", 3)
    if isinstance(keep_backups, bool) or not isinstance(keep_backups, int) or keep_backups < 1:
        raise SettingsError("'keep_backups' debe ser un entero mayor o igual a 1.")
    return AppSettings(
        cache_dir=_path_value(raw, "cache_dir", default_cache_root()),
        backup_dir=_path_value(raw, "backup_dir", default_backup_root()),
        state_dir=_path_value(raw, "state_dir", default_state_root()),
        keep_backups=keep_backups,
        mirror_backup=_bool_value(raw, "mirror_backup", False),
        offline=_bool_value(raw, "offline", False),
        install_payloads=_bool_value(raw, "install_payloads", True),
        allow_reboot_payload=_bool_value(raw, "allow_reboot_payload", False),
        allow_root_payload=_bool_value(raw, "allow_root_payload", False),
        components=_components_value(raw),
    )


def settings_to_dict(settings: AppSettings) -> dict[str, Any]:
    if settings.keep_backups < 1:
        raise SettingsError("'keep_backups' debe ser un entero mayor o igual a 1.")
    if not settings.components:
        raise SettingsError("Debe seleccionarse al menos un componente.")
    return {
        "schema": SETTINGS_SCHEMA,
        "cache_dir": str(settings.cache_dir),
        "backup_dir": str(settings.backup_dir),
        "state_dir": str(settings.state_dir),
        "keep_backups": settings.keep_backups,
        "mirror_backup": settings.mirror_backup,
        "offline": settings.offline,
        "install_payloads": settings.install_payloads,
        "allow_reboot_payload": settings.allow_reboot_payload,
        "allow_root_payload": settings.allow_root_payload,
        "components": [component.value for component in settings.components],
    }


class SettingsStore:
    """Lee y guarda preferencias en JSON con escritura atómica."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else default_settings_path()

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            raw = read_json(self.path)
        except (OSError, json.JSONDecodeError) as exc:
            raise SettingsError(f"No se pudo leer {self.path}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise SettingsError("El archivo de preferencias debe contener un objeto JSON.")
        return settings_from_dict(raw)

    def save(self, settings: AppSettings) -> None:
        atomic_write_json(self.path, settings_to_dict(settings))
