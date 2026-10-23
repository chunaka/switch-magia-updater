"""Backups locales por SD y journal de rollback para las actualizaciones."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from magia_updater.core.download import sha256_file
from magia_updater.core.errors import MagiaError
from magia_updater.core.fs import (
    PathSecurityError,
    atomic_copy,
    atomic_write_json,
    default_backup_root,
    ensure_no_symlink_path,
    read_json,
    resolve_within,
)
from magia_updater.core.models import FileOperation, OperationKind

SCHEMA_VERSION = 1
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class BackupError(MagiaError):
    """No se pudo crear, leer o podar un backup."""


@dataclass(frozen=True)
class BackupEntry:
    relative_path: str
    existed: bool
    backup_path: Path | None
    size: int | None
    sha256: str | None


@dataclass(frozen=True)
class BackupSession:
    sd_id: str
    sd_root: Path
    backup_dir: Path
    manifest_path: Path
    journal_path: Path
    created_at: str
    entries: tuple[BackupEntry, ...]


def _validate_key(value: str, *, name: str) -> str:
    if not value or not _SAFE_KEY_RE.fullmatch(value) or ".." in value:
        raise BackupError(f"{name} inválido: {value!r}")
    return value


class BackupManager:
    def __init__(
        self,
        root: Path | None = None,
        *,
        keep: int = 3,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if keep < 1:
            raise ValueError("keep debe ser al menos 1")
        self.root = (root if root is not None else default_backup_root()).resolve()
        self.keep = keep
        self._now = now if now is not None else lambda: datetime.now(UTC)

    def create_session(
        self,
        sd_root: Path,
        sd_id: str,
        operations: Iterable[FileOperation],
        *,
        timestamp: str | None = None,
    ) -> BackupSession:
        _validate_key(sd_id, name="sd_id")
        sd_root = sd_root.resolve()
        created_at = self._now().isoformat(timespec="seconds")
        folder_name = (
            timestamp if timestamp is not None else self._now().strftime("%Y%m%dT%H%M%S%fZ")
        )
        _validate_key(folder_name, name="timestamp")

        writes = tuple(
            operation
            for operation in operations
            if operation.kind in {OperationKind.CREATE, OperationKind.REPLACE}
        )
        seen: set[str] = set()
        for operation in writes:
            if operation.relative_path in seen:
                raise BackupError(f"Operación duplicada para {operation.relative_path!r}.")
            seen.add(operation.relative_path)

        base = self.root / sd_id
        base.mkdir(parents=True, exist_ok=True)
        backup_dir = base / folder_name
        counter = 1
        while backup_dir.exists():
            backup_dir = base / f"{folder_name}-{counter}"
            counter += 1
        files_dir = backup_dir / "files"
        files_dir.mkdir(parents=True)

        entries: list[BackupEntry] = []
        for operation in writes:
            try:
                destination = resolve_within(sd_root, operation.relative_path)
                ensure_no_symlink_path(sd_root, operation.relative_path)
            except PathSecurityError as exc:
                raise BackupError(str(exc)) from exc
            if destination.is_symlink():
                raise BackupError(f"El destino es un symlink: {destination}")
            if destination.exists() and not destination.is_file():
                raise BackupError(f"El destino no es un archivo: {destination}")

            if destination.is_file():
                backup_path = files_dir / PurePosixPath(operation.relative_path)
                atomic_copy(destination, backup_path)
                size = destination.stat().st_size
                digest = sha256_file(backup_path)
                entries.append(
                    BackupEntry(
                        relative_path=operation.relative_path,
                        existed=True,
                        backup_path=backup_path,
                        size=size,
                        sha256=digest,
                    )
                )
            else:
                entries.append(
                    BackupEntry(
                        relative_path=operation.relative_path,
                        existed=False,
                        backup_path=None,
                        size=None,
                        sha256=None,
                    )
                )

        manifest = {
            "schema": SCHEMA_VERSION,
            "sd_id": sd_id,
            "sd_root": str(sd_root),
            "created_at": created_at,
            "files": [
                {
                    "path": entry.relative_path,
                    "existed": entry.existed,
                    "backup_path": (
                        entry.backup_path.relative_to(backup_dir).as_posix()
                        if entry.backup_path is not None
                        else None
                    ),
                    "size": entry.size,
                    "sha256": entry.sha256,
                }
                for entry in entries
            ],
        }
        manifest_path = backup_dir / "manifest.json"
        atomic_write_json(manifest_path, manifest)

        session = BackupSession(
            sd_id=sd_id,
            sd_root=sd_root,
            backup_dir=backup_dir,
            manifest_path=manifest_path,
            journal_path=backup_dir / "journal.json",
            created_at=created_at,
            entries=tuple(entries),
        )
        self.write_journal(session, status="prepared")
        return session

    def write_journal(
        self,
        session: BackupSession,
        *,
        status: str,
        pending: str | None = None,
        completed: Iterable[str] = (),
        error: str | None = None,
    ) -> None:
        payload = {
            "schema": SCHEMA_VERSION,
            "sd_id": session.sd_id,
            "status": status,
            "created_at": session.created_at,
            "pending": pending,
            "completed": list(completed),
            "error": error,
        }
        atomic_write_json(session.journal_path, payload)

    def read_journal(self, session: BackupSession) -> dict[str, Any]:
        try:
            payload = read_json(session.journal_path)
        except (OSError, ValueError) as exc:
            raise BackupError(f"No se pudo leer el journal: {exc}") from exc
        if not isinstance(payload, dict):
            raise BackupError("El journal no es un objeto JSON.")
        return payload

    def load_session(self, backup_dir: Path) -> BackupSession:
        backup_dir = backup_dir.resolve()
        manifest_path = backup_dir / "manifest.json"
        journal_path = backup_dir / "journal.json"
        try:
            manifest = read_json(manifest_path)
        except (OSError, ValueError) as exc:
            raise BackupError(f"No se pudo leer el manifest: {exc}") from exc
        if not isinstance(manifest, dict):
            raise BackupError("El manifest no es un objeto JSON.")
        try:
            sd_id = str(manifest["sd_id"])
            sd_root = Path(str(manifest["sd_root"]))
            created_at = str(manifest["created_at"])
            raw_files = manifest["files"]
        except (KeyError, TypeError) as exc:
            raise BackupError(f"Manifest inválido: {exc}") from exc
        if not isinstance(raw_files, list):
            raise BackupError("Manifest inválido: files no es una lista.")

        entries: list[BackupEntry] = []
        for raw in raw_files:
            if not isinstance(raw, dict):
                raise BackupError("Manifest inválido: entrada de archivo no es un objeto.")
            try:
                relative_path = str(raw["path"])
                existed = bool(raw["existed"])
                raw_backup = raw.get("backup_path")
                backup_path = resolve_within(backup_dir, str(raw_backup)) if raw_backup else None
                size = raw.get("size")
                digest = raw.get("sha256")
            except (KeyError, TypeError) as exc:
                raise BackupError(f"Manifest inválido: {exc}") from exc
            entries.append(
                BackupEntry(
                    relative_path=relative_path,
                    existed=existed,
                    backup_path=backup_path,
                    size=int(size) if size is not None else None,
                    sha256=str(digest) if digest is not None else None,
                )
            )

        return BackupSession(
            sd_id=sd_id,
            sd_root=sd_root,
            backup_dir=backup_dir,
            manifest_path=manifest_path,
            journal_path=journal_path,
            created_at=created_at,
            entries=tuple(entries),
        )

    def list_sessions(self, sd_id: str) -> tuple[BackupSession, ...]:
        _validate_key(sd_id, name="sd_id")
        base = self.root / sd_id
        if not base.is_dir():
            return ()
        sessions: list[BackupSession] = []
        for directory in sorted(base.iterdir()):
            if not directory.is_dir() or not (directory / "manifest.json").is_file():
                continue
            sessions.append(self.load_session(directory))
        return tuple(sessions)

    def find_incomplete(self, sd_id: str) -> tuple[BackupSession, ...]:
        incomplete: list[BackupSession] = []
        for session in self.list_sessions(sd_id):
            journal = self.read_journal(session)
            if journal.get("status") == "applying":
                incomplete.append(session)
        return tuple(incomplete)

    def mirror_to_sd(self, session: BackupSession, sd_root: Path) -> Path:
        sd_root = sd_root.resolve()
        target = sd_root / ".magia" / "backups" / session.backup_dir.name
        ensure_no_symlink_path(sd_root, ".magia/backups")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        if temporary.exists():
            shutil.rmtree(temporary)
        if target.exists():
            raise BackupError(f"El backup espejo ya existe: {target}")
        try:
            shutil.copytree(session.backup_dir, temporary)
            os.replace(temporary, target)
        except OSError as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            raise BackupError(f"No se pudo espejar el backup en la SD: {exc}") from exc
        return target

    def prune(self, sd_id: str, *, keep: int | None = None) -> tuple[Path, ...]:
        _validate_key(sd_id, name="sd_id")
        limit = self.keep if keep is None else keep
        if limit < 1:
            raise ValueError("keep debe ser al menos 1")
        base = self.root / sd_id
        if not base.is_dir():
            return ()
        candidates = [
            directory
            for directory in base.iterdir()
            if directory.is_dir() and (directory / "manifest.json").is_file()
        ]
        candidates.sort(key=lambda path: path.name, reverse=True)
        removed: list[Path] = []
        for directory in candidates[limit:]:
            shutil.rmtree(directory)
            removed.append(directory)
        return tuple(removed)

    def prune_mirror(self, sd_root: Path, *, keep: int | None = None) -> tuple[Path, ...]:
        limit = self.keep if keep is None else keep
        if limit < 1:
            raise ValueError("keep debe ser al menos 1")
        base = sd_root.resolve() / ".magia" / "backups"
        if not base.is_dir():
            return ()
        candidates = [
            directory
            for directory in base.iterdir()
            if directory.is_dir() and (directory / "manifest.json").is_file()
        ]
        candidates.sort(key=lambda path: path.name, reverse=True)
        removed: list[Path] = []
        for directory in candidates[limit:]:
            shutil.rmtree(directory)
            removed.append(directory)
        return tuple(removed)
