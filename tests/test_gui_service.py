from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from magia_updater.core.backup import BackupManager
from magia_updater.core.download import sha256_file
from magia_updater.core.export import ExportFormat, ExportKind
from magia_updater.core.identity import get_sd_identity
from magia_updater.core.models import (
    ComponentId,
    ComponentPlan,
    FileOperation,
    OperationKind,
    UpdatePlan,
)
from magia_updater.core.settings import AppSettings
from magia_updater.gui.service import GuiService, UpdateSession


def test_service_detects_sd_and_lists_empty_backups(fake_sd: Path, tmp_path: Path) -> None:
    service = GuiService(
        AppSettings(
            cache_dir=tmp_path / "cache",
            backup_dir=tmp_path / "backups",
            state_dir=tmp_path / "state",
        )
    )

    snapshot = service.detect(fake_sd)

    assert snapshot.is_switch_sd is True
    assert service.list_backups(snapshot) == ()


def test_service_deletes_local_backup(fake_sd: Path, tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    manager = BackupManager(backup_dir, now=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    sd_id = get_sd_identity(fake_sd).key
    session = manager.create_session(
        fake_sd,
        sd_id,
        [FileOperation(relative_path="hbmenu.nro", kind=OperationKind.CREATE)],
        timestamp="20260101T120000000000Z",
    )
    service = GuiService(
        AppSettings(
            cache_dir=tmp_path / "cache",
            backup_dir=backup_dir,
            state_dir=tmp_path / "state",
        )
    )

    backups = service.list_backups(service.detect(fake_sd))

    assert len(backups) == 1
    assert backups[0].size > 0
    service.delete_backup(backups[0])
    assert not session.backup_dir.exists()


def _session_with_create(fake_sd: Path, tmp_path: Path) -> UpdateSession:
    source = tmp_path / "source.txt"
    source.write_bytes(b"contenido")
    operation = FileOperation(
        relative_path="switch/prueba.txt",
        kind=OperationKind.CREATE,
        source=source,
        size=source.stat().st_size,
        sha256=sha256_file(source),
    )
    plan = UpdatePlan(
        components=(
            ComponentPlan(
                component=ComponentId.ATMOSPHERE,
                release=None,
                operations=(operation,),
            ),
        )
    )
    service = GuiService(
        AppSettings(
            cache_dir=tmp_path / "cache",
            backup_dir=tmp_path / "backups",
            state_dir=tmp_path / "state",
        )
    )
    return UpdateSession(snapshot=service.detect(fake_sd), checks=(), plan=plan)


def test_service_applies_and_exports_session(fake_sd: Path, tmp_path: Path) -> None:
    service = GuiService(
        AppSettings(
            cache_dir=tmp_path / "cache",
            backup_dir=tmp_path / "backups",
            state_dir=tmp_path / "state",
        )
    )
    session = _session_with_create(fake_sd, tmp_path)

    result = service.apply(session)

    assert (fake_sd / "switch/prueba.txt").read_bytes() == b"contenido"
    assert result.backup_dir.is_dir()
    assert result.state_path is not None and result.state_path.is_file()

    output = tmp_path / "package.zip"
    exported = service.export(
        session,
        output,
        kind=ExportKind.DELTA,
        format=ExportFormat.ZIP,
    )

    assert exported.file_count == 1
    assert output.is_file()
