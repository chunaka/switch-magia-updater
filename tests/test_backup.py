from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from magia_updater.core.backup import BackupError, BackupManager
from magia_updater.core.models import FileOperation, OperationKind


def write_file(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def replace_op(relative: str) -> FileOperation:
    return FileOperation(relative_path=relative, kind=OperationKind.REPLACE)


def create_op(relative: str) -> FileOperation:
    return FileOperation(relative_path=relative, kind=OperationKind.CREATE)


def make_manager(tmp_path: Path) -> BackupManager:
    return BackupManager(
        tmp_path / "backups",
        keep=3,
        now=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


def test_create_session_backs_up_existing_and_records_created(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    write_file(sd_root, "atmosphere/package3", b"viejo package3")
    manager = make_manager(tmp_path)

    session = manager.create_session(
        sd_root,
        "sd-1",
        [replace_op("atmosphere/package3"), create_op("hbmenu.nro")],
        timestamp="20260101T120000000000Z",
    )

    assert session.backup_dir == tmp_path / "backups/sd-1/20260101T120000000000Z"
    assert session.manifest_path.is_file()
    assert session.journal_path.is_file()
    assert len(session.entries) == 2
    existing = session.entries[0]
    assert existing.existed is True
    assert existing.backup_path is not None
    assert existing.backup_path.read_bytes() == b"viejo package3"
    assert existing.sha256 is not None
    created = session.entries[1]
    assert created.existed is False
    assert created.backup_path is None

    journal = manager.read_journal(session)
    assert journal["status"] == "prepared"
    assert journal["pending"] is None
    assert journal["completed"] == []


def test_create_session_rejects_traversal(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    sd_root = tmp_path / "sd"
    sd_root.mkdir()

    with pytest.raises(BackupError, match="ambiguo|traversal|raíz"):
        manager.create_session(sd_root, "sd-1", [create_op("../evil.bin")])


def test_create_session_rejects_duplicate_operations(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    sd_root = tmp_path / "sd"
    sd_root.mkdir()

    with pytest.raises(BackupError, match="duplicada"):
        manager.create_session(
            sd_root,
            "sd-1",
            [create_op("hbmenu.nro"), replace_op("hbmenu.nro")],
        )


def test_create_session_rejects_destination_directory(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    sd_root = tmp_path / "sd"
    (sd_root / "hbmenu.nro").mkdir(parents=True)

    with pytest.raises(BackupError, match="no es un archivo"):
        manager.create_session(sd_root, "sd-1", [replace_op("hbmenu.nro")])


def test_load_session_roundtrip(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    write_file(sd_root, "atmosphere/package3", b"viejo")
    manager = make_manager(tmp_path)
    session = manager.create_session(
        sd_root,
        "sd-1",
        [replace_op("atmosphere/package3")],
        timestamp="20260101T120000000000Z",
    )

    loaded = manager.load_session(session.backup_dir)

    assert loaded.sd_id == "sd-1"
    assert loaded.sd_root == sd_root.resolve()
    assert loaded.entries[0].backup_path is not None
    assert loaded.entries[0].backup_path.read_bytes() == b"viejo"


def test_find_incomplete_uses_journal_status(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    sd_root.mkdir()
    manager = make_manager(tmp_path)
    session = manager.create_session(
        sd_root,
        "sd-1",
        [create_op("hbmenu.nro")],
        timestamp="20260101T120000000000Z",
    )

    assert manager.find_incomplete("sd-1") == ()

    manager.write_journal(session, status="applying", pending="hbmenu.nro")
    incomplete = manager.find_incomplete("sd-1")

    assert incomplete == (session,)

    manager.write_journal(session, status="applied")
    assert manager.find_incomplete("sd-1") == ()


def test_prune_keeps_newest_and_ignores_unknown_dirs(tmp_path: Path) -> None:
    base = tmp_path / "backups/sd-1"
    for name in ("20260101T000000000000Z", "20260102T000000000000Z", "20260103T000000000000Z"):
        directory = base / name
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text("{}", encoding="utf-8")
    unknown = base / "no-es-backup"
    unknown.mkdir()

    manager = make_manager(tmp_path)
    removed = manager.prune("sd-1", keep=2)

    assert len(removed) == 1
    assert not (base / "20260101T000000000000Z").exists()
    assert (base / "20260102T000000000000Z").is_dir()
    assert (base / "20260103T000000000000Z").is_dir()
    assert unknown.is_dir()


def test_mirror_backup_to_sd_and_prune_mirror(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    write_file(sd_root, "atmosphere/package3", b"viejo")
    manager = make_manager(tmp_path)
    session = manager.create_session(
        sd_root,
        "sd-1",
        [replace_op("atmosphere/package3")],
        timestamp="20260101T120000000000Z",
    )

    target = manager.mirror_to_sd(session, sd_root)

    assert target == sd_root / ".magia/backups/20260101T120000000000Z"
    assert (target / "manifest.json").is_file()
    assert (target / "files/atmosphere/package3").read_bytes() == b"viejo"

    removed = manager.prune_mirror(sd_root, keep=1)
    assert removed == ()


def test_prune_mirror_removes_old_backups(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    base = sd_root / ".magia/backups"
    for name in ("20260101T000000000000Z", "20260102T000000000000Z"):
        directory = base / name
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text("{}", encoding="utf-8")

    manager = make_manager(tmp_path)
    removed = manager.prune_mirror(sd_root, keep=1)

    assert len(removed) == 1
    assert not (base / "20260101T000000000000Z").exists()
    assert (base / "20260102T000000000000Z").is_dir()
