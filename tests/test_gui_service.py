from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from magia_updater.core.backup import BackupManager
from magia_updater.core.identity import get_sd_identity
from magia_updater.core.models import FileOperation, OperationKind
from magia_updater.core.settings import AppSettings
from magia_updater.gui.service import GuiService


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
