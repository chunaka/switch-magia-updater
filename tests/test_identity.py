from __future__ import annotations

from pathlib import Path

import pytest

from magia_updater.core import identity
from magia_updater.core.identity import IdentityError, get_sd_identity


def test_identity_uses_windows_serial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "_windows_volume_serial", lambda root: 0x12345678)

    result = get_sd_identity(tmp_path)

    assert result.key == "win-12345678"
    assert result.label == str(tmp_path.resolve())


def test_identity_falls_back_to_device_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(identity, "_windows_volume_serial", lambda root: None)
    first = tmp_path / "sd-a"
    second = tmp_path / "sd-b"
    first.mkdir()
    second.mkdir()

    first_identity = get_sd_identity(first)
    second_identity = get_sd_identity(second)

    assert first_identity.key.startswith("dev-")
    assert first_identity.key != second_identity.key


def test_identity_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(IdentityError):
        get_sd_identity(tmp_path / "missing")
