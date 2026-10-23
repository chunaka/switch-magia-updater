from __future__ import annotations

import json
from pathlib import Path

import pytest

from magia_updater.core.fs import (
    PathSecurityError,
    atomic_copy,
    atomic_write_bytes,
    atomic_write_json,
    default_app_root,
    default_backup_root,
    default_state_root,
    ensure_no_symlink_path,
    read_json,
    resolve_within,
    validate_relative_path,
)


def test_validate_relative_path_accepts_nested_path() -> None:
    assert validate_relative_path("atmosphere/package3").as_posix() == "atmosphere/package3"


def test_resolve_within_accepts_nested_path(tmp_path: Path) -> None:
    assert resolve_within(tmp_path, "atmosphere/package3") == tmp_path / "atmosphere/package3"


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "../evil.bin",
        "a/../../evil.bin",
        "/evil.bin",
        "\\\\evil.bin",
        "a\\evil.bin",
        "C:/evil.bin",
        "a/./b.bin",
    ],
)
def test_resolve_within_rejects_unsafe_paths(tmp_path: Path, relative: str) -> None:
    with pytest.raises(PathSecurityError):
        resolve_within(tmp_path, relative)


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "../evil.bin",
        "a/../../evil.bin",
        "/evil.bin",
        "\\\\evil.bin",
        "a\\evil.bin",
        "C:/evil.bin",
    ],
)
def test_validate_relative_path_rejects_unsafe_paths(relative: str) -> None:
    with pytest.raises(PathSecurityError):
        validate_relative_path(relative)


def test_atomic_write_and_read_json(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"

    atomic_write_json(path, {"b": 2, "a": 1})

    assert read_json(path) == {"a": 1, "b": 2}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert not list(path.parent.glob("*.tmp"))


def test_atomic_write_bytes_replaces_content(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"viejo")

    atomic_write_bytes(path, b"nuevo")

    assert path.read_bytes() == b"nuevo"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_copy_preserves_content(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "nested" / "destination.bin"
    source.write_bytes(b"contenido")
    destination.parent.mkdir()
    destination.write_bytes(b"viejo")

    atomic_copy(source, destination)

    assert destination.read_bytes() == b"contenido"
    assert not list(destination.parent.glob("*.tmp"))


def test_ensure_no_symlink_path_accepts_regular_path(tmp_path: Path) -> None:
    (tmp_path / "atmosphere").mkdir()
    (tmp_path / "atmosphere/package3").write_bytes(b"data")

    ensure_no_symlink_path(tmp_path, "atmosphere/package3")


def test_ensure_no_symlink_path_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"No se pudieron crear symlinks: {exc}")

    with pytest.raises(PathSecurityError):
        ensure_no_symlink_path(tmp_path, "link/archivo.bin")


def test_default_roots_follow_localappdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert default_app_root() == tmp_path / "MagiaUpdater"
    assert default_backup_root() == tmp_path / "MagiaUpdater" / "backups"
    assert default_state_root() == tmp_path / "MagiaUpdater" / "state"


def test_atomic_write_json_is_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    atomic_write_json(path, {"emoji": "ñ"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"emoji": "ñ"}
