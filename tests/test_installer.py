from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from magia_updater.core import installer as installer_module
from magia_updater.core.backup import BackupManager
from magia_updater.core.download import sha256_file
from magia_updater.core.identity import get_sd_identity
from magia_updater.core.installer import (
    ApplyOptions,
    ConfirmationRequired,
    InstallError,
    PreflightError,
    RollbackError,
    UpdateInstaller,
)
from magia_updater.core.models import (
    ComponentId,
    ComponentPlan,
    FileOperation,
    FirmwareInfo,
    OperationKind,
    ReleaseInfo,
    SdSnapshot,
    UpdatePlan,
    Version,
)
from magia_updater.core.state import StateStore

FIXED_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def write_file(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_snapshot(root: Path) -> SdSnapshot:
    return SdSnapshot(
        root=root,
        components={},
        firmware=FirmwareInfo(),
        is_switch_sd=True,
        has_cfw=True,
        emummc_present=False,
        atmosphere_config_present=False,
        hekate_config_present=False,
        root_payload=None,
        reboot_payload=None,
        payloads=(),
    )


def make_plan(operations: tuple[FileOperation, ...]) -> UpdatePlan:
    release = ReleaseInfo(
        component=ComponentId.ATMOSPHERE,
        tag="1.12.1",
        version=Version.parse("1.12.1"),
        name="Atmosphere 1.12.1",
        html_url="",
        published_at=None,
        body="",
        prerelease=False,
        draft=False,
        assets=(),
    )
    return UpdatePlan(
        components=(
            ComponentPlan(
                component=ComponentId.ATMOSPHERE,
                release=release,
                operations=operations,
            ),
        )
    )


def make_operation(
    sd_root: Path,
    relative: str,
    kind: OperationKind,
    source: Path,
    *,
    requires_confirmation: bool = False,
) -> FileOperation:
    destination = sd_root / relative
    return FileOperation(
        relative_path=relative,
        kind=kind,
        source=source,
        size=source.stat().st_size,
        sha256=sha256_file(source),
        destination_sha256=(sha256_file(destination) if destination.is_file() else None),
        requires_confirmation=requires_confirmation,
    )


def make_installer(tmp_path: Path, *, keep: int = 3) -> tuple[UpdateInstaller, BackupManager]:
    manager = BackupManager(
        tmp_path / "backups",
        keep=keep,
        now=lambda: FIXED_NOW,
    )
    installer = UpdateInstaller(
        backup_manager=manager,
        state_store=StateStore(tmp_path / "state"),
    )
    return installer, manager


def test_apply_creates_replaces_and_saves_state(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    old = write_file(sd_root, "atmosphere/package3", b"viejo package3")
    new = write_file(tmp_path / "sources", "package3", b"nuevo package3")
    hbmenu = write_file(tmp_path / "sources", "hbmenu.nro", b"hbmenu")
    plan = make_plan(
        (
            make_operation(sd_root, "atmosphere/package3", OperationKind.REPLACE, new),
            make_operation(sd_root, "hbmenu.nro", OperationKind.CREATE, hbmenu),
        )
    )
    installer, manager = make_installer(tmp_path)
    snapshot = make_snapshot(sd_root)

    result = installer.apply(snapshot, plan, now=lambda: FIXED_NOW)

    assert (sd_root / "atmosphere/package3").read_bytes() == b"nuevo package3"
    assert (sd_root / "hbmenu.nro").read_bytes() == b"hbmenu"
    assert result.backup_dir.is_dir()
    assert (result.backup_dir / "files/atmosphere/package3").read_bytes() == b"viejo package3"
    assert result.state_path is not None
    assert result.applied[0].relative_path == "atmosphere/package3"

    journal = manager.read_journal(manager.load_session(result.backup_dir))
    assert journal["status"] == "applied"
    assert journal["pending"] is None
    assert journal["completed"] == ["atmosphere/package3", "hbmenu.nro"]

    state = StateStore(tmp_path / "state").load(result.sd_id)
    assert state is not None
    assert state.backup_dir == result.backup_dir
    assert state.components[0].version == "1.12.1"
    assert old.exists()


def test_apply_requires_confirmation_for_protected_write(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    write_file(sd_root, "payload.bin", b"payload viejo")
    source = write_file(tmp_path / "sources", "fusee.bin", b"payload nuevo")
    plan = make_plan(
        (
            make_operation(
                sd_root,
                "payload.bin",
                OperationKind.REPLACE,
                source,
                requires_confirmation=True,
            ),
        )
    )
    installer, _ = make_installer(tmp_path)
    snapshot = make_snapshot(sd_root)

    with pytest.raises(ConfirmationRequired, match="payload.bin"):
        installer.apply(snapshot, plan)

    assert (sd_root / "payload.bin").read_bytes() == b"payload viejo"
    assert not (tmp_path / "backups").exists()

    installer.apply(
        snapshot,
        plan,
        options=ApplyOptions(confirm_required_changes=True),
        now=lambda: FIXED_NOW,
    )
    assert (sd_root / "payload.bin").read_bytes() == b"payload nuevo"


def test_apply_rejects_conflict_operation(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    sd_root.mkdir()
    source = write_file(tmp_path / "sources", "file.bin", b"nuevo")
    plan = make_plan(
        (
            FileOperation(
                relative_path="file.bin",
                kind=OperationKind.CONFLICT,
                source=source,
                reason="conflicto",
            ),
        )
    )
    installer, _ = make_installer(tmp_path)

    with pytest.raises(PreflightError, match="Conflicto"):
        installer.apply(make_snapshot(sd_root), plan)


def test_apply_rejects_stale_destination(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    destination = write_file(sd_root, "atmosphere/package3", b"viejo")
    source = write_file(tmp_path / "sources", "package3", b"nuevo")
    plan = make_plan(
        (make_operation(sd_root, "atmosphere/package3", OperationKind.REPLACE, source),)
    )
    destination.write_bytes(b"cambiado por el usuario")
    installer, _ = make_installer(tmp_path)

    with pytest.raises(PreflightError, match="cambió"):
        installer.apply(make_snapshot(sd_root), plan)

    assert destination.read_bytes() == b"cambiado por el usuario"


def test_apply_rejects_source_hash_mismatch(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    write_file(sd_root, "atmosphere/package3", b"viejo")
    source = write_file(tmp_path / "sources", "package3", b"nuevo")
    plan = make_plan(
        (make_operation(sd_root, "atmosphere/package3", OperationKind.REPLACE, source),)
    )
    source.write_bytes(b"otro!")
    installer, _ = make_installer(tmp_path)

    with pytest.raises(PreflightError, match="SHA-256"):
        installer.apply(make_snapshot(sd_root), plan)


def test_apply_rejects_missing_or_existing_destination(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    sd_root.mkdir()
    source = write_file(tmp_path / "sources", "file.bin", b"nuevo")
    installer, _ = make_installer(tmp_path)

    missing = make_plan(
        (
            FileOperation(
                relative_path="file.bin",
                kind=OperationKind.REPLACE,
                source=source,
                size=source.stat().st_size,
                sha256=sha256_file(source),
                destination_sha256="0" * 64,
            ),
        )
    )
    with pytest.raises(PreflightError, match="ya no existe"):
        installer.apply(make_snapshot(sd_root), missing)

    write_file(sd_root, "file.bin", b"existente")
    existing = make_plan((make_operation(sd_root, "file.bin", OperationKind.CREATE, source),))
    with pytest.raises(PreflightError, match="ya existe"):
        installer.apply(make_snapshot(sd_root), existing)


def test_apply_rejects_unsafe_path(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    sd_root.mkdir()
    source = write_file(tmp_path / "sources", "file.bin", b"nuevo")
    plan = make_plan(
        (
            FileOperation(
                relative_path="../evil.bin",
                kind=OperationKind.CREATE,
                source=source,
                size=source.stat().st_size,
                sha256=sha256_file(source),
            ),
        )
    )
    installer, _ = make_installer(tmp_path)

    with pytest.raises(PreflightError, match="ambiguo|traversal|raíz"):
        installer.apply(make_snapshot(sd_root), plan)


def test_apply_rejects_empty_plan(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    sd_root.mkdir()
    installer, _ = make_installer(tmp_path)

    with pytest.raises(PreflightError, match="no contiene cambios"):
        installer.apply(make_snapshot(sd_root), make_plan(()))


def test_apply_rolls_back_on_failure(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    write_file(sd_root, "a.txt", b"viejo a")
    source_a = write_file(tmp_path / "sources", "a.txt", b"nuevo a")
    source_b = write_file(tmp_path / "sources", "b.txt", b"nuevo b")
    plan = make_plan(
        (
            make_operation(sd_root, "a.txt", OperationKind.REPLACE, source_a),
            make_operation(sd_root, "b.txt", OperationKind.CREATE, source_b),
        )
    )
    installer, manager = make_installer(tmp_path)
    real_atomic_copy = installer_module.atomic_copy

    def flaky_copy(source: Path, destination: Path) -> None:
        if destination == sd_root / "b.txt":
            raise OSError("fallo simulado")
        real_atomic_copy(source, destination)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(installer_module, "atomic_copy", flaky_copy)
    try:
        with pytest.raises(InstallError, match="No se pudo aplicar"):
            installer.apply(make_snapshot(sd_root), plan, now=lambda: FIXED_NOW)
    finally:
        monkeypatch.undo()

    assert (sd_root / "a.txt").read_bytes() == b"viejo a"
    assert not (sd_root / "b.txt").exists()
    sd_id = get_sd_identity(sd_root).key
    sessions = manager.list_sessions(sd_id)
    assert len(sessions) == 1
    journal = manager.read_journal(sessions[0])
    assert journal["status"] == "rolled_back"
    assert journal["completed"] == ["a.txt"]


def test_apply_reports_incomplete_rollback(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    write_file(sd_root, "a.txt", b"viejo a")
    source_a = write_file(tmp_path / "sources", "a.txt", b"nuevo a")
    source_b = write_file(tmp_path / "sources", "b.txt", b"nuevo b")
    plan = make_plan(
        (
            make_operation(sd_root, "a.txt", OperationKind.REPLACE, source_a),
            make_operation(sd_root, "b.txt", OperationKind.CREATE, source_b),
        )
    )
    installer, manager = make_installer(tmp_path)
    real_atomic_copy = installer_module.atomic_copy

    def flaky_copy(source: Path, destination: Path) -> None:
        if destination == sd_root / "b.txt":
            raise OSError("fallo simulado")
        if destination == sd_root / "a.txt" and "backups" in str(source):
            raise OSError("fallo de restauración")
        real_atomic_copy(source, destination)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(installer_module, "atomic_copy", flaky_copy)
    try:
        with pytest.raises(RollbackError) as exc_info:
            installer.apply(make_snapshot(sd_root), plan, now=lambda: FIXED_NOW)
    finally:
        monkeypatch.undo()

    assert exc_info.value.rollback_errors
    sd_id = get_sd_identity(sd_root).key
    journal = manager.read_journal(manager.list_sessions(sd_id)[0])
    assert journal["status"] == "rollback_failed"


def test_apply_mirrors_backup_to_sd(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    write_file(sd_root, "atmosphere/package3", b"viejo")
    source = write_file(tmp_path / "sources", "package3", b"nuevo")
    plan = make_plan(
        (make_operation(sd_root, "atmosphere/package3", OperationKind.REPLACE, source),)
    )
    installer, _ = make_installer(tmp_path)

    result = installer.apply(
        make_snapshot(sd_root),
        plan,
        options=ApplyOptions(mirror_backup_to_sd=True),
        now=lambda: FIXED_NOW,
    )

    mirror = sd_root / ".magia/backups" / result.backup_dir.name
    assert (mirror / "manifest.json").is_file()
    assert (mirror / "files/atmosphere/package3").read_bytes() == b"viejo"


def test_apply_prunes_old_backups(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    write_file(sd_root, "atmosphere/package3", b"viejo")
    source = write_file(tmp_path / "sources", "package3", b"nuevo")
    plan = make_plan(
        (make_operation(sd_root, "atmosphere/package3", OperationKind.REPLACE, source),)
    )
    sd_id = get_sd_identity(sd_root).key
    base = tmp_path / "backups" / sd_id
    for name in ("20250101T000000000000Z", "20250102T000000000000Z"):
        directory = base / name
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text("{}", encoding="utf-8")
    installer, manager = make_installer(tmp_path, keep=2)

    result = installer.apply(make_snapshot(sd_root), plan, now=lambda: FIXED_NOW)

    assert len([directory for directory in base.iterdir() if directory.is_dir()]) == 2
    assert (base / "20250101T000000000000Z").exists() is False
    assert result.pruned


def test_recover_rolls_back_incomplete_session(tmp_path: Path) -> None:
    sd_root = tmp_path / "sd"
    destination = write_file(sd_root, "a.txt", b"viejo a")
    installer, manager = make_installer(tmp_path)
    sd_id = get_sd_identity(sd_root).key
    operation = FileOperation(relative_path="a.txt", kind=OperationKind.REPLACE)
    session = manager.create_session(
        sd_root,
        sd_id,
        [operation],
        timestamp="20260101T120000000000Z",
    )
    destination.write_bytes(b"nuevo a")
    manager.write_journal(session, status="applying", pending="a.txt")

    recovered = installer.recover(make_snapshot(sd_root), now=lambda: FIXED_NOW)

    assert recovered == (session.backup_dir,)
    assert destination.read_bytes() == b"viejo a"
    journal = manager.read_journal(session)
    assert journal["status"] == "rolled_back"
