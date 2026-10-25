from __future__ import annotations

import json
from pathlib import Path

import pytest

from magia_updater.core.models import ComponentId
from magia_updater.core.settings import (
    AppSettings,
    SettingsError,
    SettingsStore,
    default_settings_path,
    settings_from_dict,
    settings_to_dict,
)


def test_default_settings_path_follows_localappdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert default_settings_path() == tmp_path / "MagiaUpdater" / "settings.json"


def test_settings_store_returns_defaults_when_file_is_missing(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")

    settings = store.load()

    assert settings.keep_backups == 3
    assert settings.components == (
        ComponentId.ATMOSPHERE,
        ComponentId.HEKATE,
        ComponentId.SYS_PATCH,
    )


def test_settings_store_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "settings.json"
    store = SettingsStore(path)
    settings = AppSettings(
        cache_dir=tmp_path / "cache",
        backup_dir=tmp_path / "backups",
        state_dir=tmp_path / "state",
        keep_backups=5,
        mirror_backup=True,
        offline=True,
        install_payloads=False,
        allow_reboot_payload=True,
        allow_root_payload=True,
        components=(ComponentId.ATMOSPHERE, ComponentId.SYS_PATCH),
        last_sd_root=tmp_path / "sd",
    )

    store.save(settings)

    assert store.load() == settings
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert not list(path.parent.glob("*.tmp"))


def test_settings_dict_uses_relative_components() -> None:
    payload = settings_to_dict(AppSettings())

    assert payload["schema"] == 1
    assert payload["components"] == ["atmosphere", "hekate", "sys-patch"]
    assert payload["last_sd_root"] is None


def test_settings_from_dict_without_last_sd_root_is_compatible() -> None:
    settings = settings_from_dict({"schema": 1})

    assert settings.last_sd_root is None


def test_settings_from_dict_rejects_invalid_schema() -> None:
    with pytest.raises(SettingsError, match="versión"):
        settings_from_dict({"schema": 99})


def test_settings_from_dict_rejects_invalid_keep_backups() -> None:
    with pytest.raises(SettingsError, match="keep_backups"):
        settings_from_dict({"schema": 1, "keep_backups": 0})


def test_settings_from_dict_rejects_invalid_component() -> None:
    with pytest.raises(SettingsError, match="Componente"):
        settings_from_dict({"schema": 1, "components": ["atmosphere", "desconocido"]})


def test_settings_from_dict_rejects_empty_components() -> None:
    with pytest.raises(SettingsError, match="components"):
        settings_from_dict({"schema": 1, "components": []})


def test_settings_store_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{no-json", encoding="utf-8")

    with pytest.raises(SettingsError, match="No se pudo leer"):
        SettingsStore(path).load()


def test_settings_store_rejects_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps([1, 2]), encoding="utf-8")

    with pytest.raises(SettingsError, match="objeto JSON"):
        SettingsStore(path).load()


def test_settings_to_dict_rejects_invalid_values() -> None:
    with pytest.raises(SettingsError, match="keep_backups"):
        settings_to_dict(AppSettings(keep_backups=0))
    with pytest.raises(SettingsError, match="componente"):
        settings_to_dict(AppSettings(components=()))
