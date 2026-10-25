"""Lógica de aplicación reutilizable por la GUI (sin dependencias de PySide6)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from magia_updater.core.backup import BackupManager, BackupSession
from magia_updater.core.cache import CacheStore
from magia_updater.core.detect import detect_sd
from magia_updater.core.download import ProgressCallback
from magia_updater.core.export import ExportFormat, ExportKind, ExportResult, export_plan
from magia_updater.core.identity import get_sd_identity
from magia_updater.core.installer import ApplyOptions, ApplyResult, UpdateInstaller
from magia_updater.core.models import ComponentCheck, ComponentId, SdSnapshot, UpdatePlan
from magia_updater.core.settings import AppSettings
from magia_updater.core.workflow import build_update_plan


@dataclass(frozen=True)
class UpdateSession:
    snapshot: SdSnapshot
    checks: tuple[ComponentCheck, ...]
    plan: UpdatePlan

    @property
    def change_count(self) -> int:
        return len(self.plan.changes)


@dataclass(frozen=True)
class BackupInfo:
    session: BackupSession
    size: int


class GuiService:
    """Orquesta detección, planificación, instalación, exportación y backups."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def set_settings(self, settings: AppSettings) -> None:
        self.settings = settings

    def detect(self, root: Path | str) -> SdSnapshot:
        return detect_sd(root)

    def build_plan(
        self,
        snapshot: SdSnapshot,
        *,
        components: tuple[ComponentId, ...] | None = None,
        refresh: bool = False,
        progress: ProgressCallback | None = None,
    ) -> UpdateSession:
        checks, plan = build_update_plan(
            snapshot,
            components if components is not None else self.settings.components,
            cache=CacheStore(self.settings.cache_dir),
            offline=self.settings.offline,
            refresh=refresh,
            no_payloads=not self.settings.install_payloads,
            allow_reboot_payload=self.settings.allow_reboot_payload,
            allow_root_payload=self.settings.allow_root_payload,
            progress=progress,
        )
        return UpdateSession(snapshot=snapshot, checks=checks, plan=plan)

    def _apply_options(self) -> ApplyOptions:
        return ApplyOptions(
            confirm_required_changes=True,
            mirror_backup_to_sd=self.settings.mirror_backup,
            backup_root=self.settings.backup_dir,
            state_root=self.settings.state_dir,
            keep_backups=self.settings.keep_backups,
        )

    def recover(self, snapshot: SdSnapshot) -> tuple[Path, ...]:
        return UpdateInstaller().recover(snapshot, options=self._apply_options())

    def apply(self, session: UpdateSession) -> ApplyResult:
        return UpdateInstaller().apply(
            session.snapshot, session.plan, options=self._apply_options()
        )

    def export(
        self,
        session: UpdateSession,
        output: Path,
        *,
        kind: ExportKind,
        format: ExportFormat,
    ) -> ExportResult:
        return export_plan(session.plan, output, kind=kind, format=format)

    def list_backups(self, snapshot: SdSnapshot) -> tuple[BackupInfo, ...]:
        sd_id = get_sd_identity(snapshot.root).key
        manager = BackupManager(self.settings.backup_dir, keep=self.settings.keep_backups)
        return tuple(
            BackupInfo(session=session, size=manager.session_size(session))
            for session in manager.list_sessions(sd_id)
        )

    def delete_backup(self, info: BackupInfo) -> None:
        manager = BackupManager(self.settings.backup_dir, keep=self.settings.keep_backups)
        manager.delete_session(info.session)
