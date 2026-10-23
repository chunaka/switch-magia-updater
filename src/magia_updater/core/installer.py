"""Aplicación transaccional de un plan: preflight, backup, escritura y rollback."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from magia_updater.core.backup import BackupError, BackupManager, BackupSession
from magia_updater.core.download import sha256_file
from magia_updater.core.errors import MagiaError
from magia_updater.core.fs import (
    PathSecurityError,
    atomic_copy,
    ensure_no_symlink_path,
    resolve_within,
)
from magia_updater.core.identity import IdentityError, get_sd_identity
from magia_updater.core.models import FileOperation, OperationKind, SdSnapshot, UpdatePlan
from magia_updater.core.state import StateError, StateStore, state_from_plan

_WRITE_KINDS = {OperationKind.CREATE, OperationKind.REPLACE}


class InstallError(MagiaError):
    """No se pudo aplicar el plan."""


class PreflightError(InstallError):
    """El plan o la SD no están en un estado seguro para aplicar."""


class ConfirmationRequired(InstallError):
    """El plan toca rutas protegidas y falta confirmación explícita."""


class RollbackError(InstallError):
    """La escritura falló y además no se pudo restaurar todo."""

    def __init__(self, message: str, *, rollback_errors: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.rollback_errors = rollback_errors


@dataclass(frozen=True)
class ApplyOptions:
    confirm_required_changes: bool = False
    mirror_backup_to_sd: bool = False
    backup_root: Path | None = None
    state_root: Path | None = None
    keep_backups: int = 3


@dataclass(frozen=True)
class ApplyResult:
    sd_id: str
    backup_dir: Path
    state_path: Path | None
    applied: tuple[FileOperation, ...]
    pruned: tuple[Path, ...]
    warnings: tuple[str, ...] = ()


class UpdateInstaller:
    def __init__(
        self,
        *,
        backup_manager: BackupManager | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self.backup_manager = backup_manager
        self.state_store = state_store

    def _manager(self, options: ApplyOptions, now: Callable[[], datetime] | None) -> BackupManager:
        if self.backup_manager is not None:
            return self.backup_manager
        return BackupManager(options.backup_root, keep=options.keep_backups, now=now)

    def _store(self, options: ApplyOptions) -> StateStore:
        if self.state_store is not None:
            return self.state_store
        return StateStore(options.state_root)

    def _preflight(
        self,
        snapshot: SdSnapshot,
        plan: UpdatePlan,
        *,
        options: ApplyOptions,
    ) -> tuple[FileOperation, ...]:
        sd_root = snapshot.root
        if not sd_root.is_dir():
            raise PreflightError(f"La raíz de la SD no existe: {sd_root}")

        writes: list[FileOperation] = []
        seen: set[str] = set()
        confirmation_paths: list[str] = []

        for operation in plan.operations:
            if operation.kind == OperationKind.CONFLICT:
                raise PreflightError(f"Conflicto en {operation.relative_path}: {operation.reason}")
            if operation.kind not in _WRITE_KINDS:
                continue
            if operation.relative_path in seen:
                raise PreflightError(f"Operación duplicada para {operation.relative_path!r}.")
            seen.add(operation.relative_path)

            try:
                destination = resolve_within(sd_root, operation.relative_path)
                ensure_no_symlink_path(sd_root, operation.relative_path)
            except PathSecurityError as exc:
                raise PreflightError(str(exc)) from exc

            source = operation.source
            if source is None or not source.is_file():
                raise PreflightError(f"Falta el archivo fuente de {operation.relative_path!r}.")
            source_size = source.stat().st_size
            if operation.size is not None and source_size != operation.size:
                raise PreflightError(f"El tamaño de {source} no coincide con el plan.")
            if operation.sha256 is not None and sha256_file(source) != operation.sha256:
                raise PreflightError(f"El SHA-256 de {source} no coincide con el plan.")

            if destination.is_symlink():
                raise PreflightError(f"El destino es un symlink: {destination}")
            if destination.exists() and not destination.is_file():
                raise PreflightError(f"El destino no es un archivo: {destination}")

            if operation.kind == OperationKind.CREATE:
                if destination.exists():
                    raise PreflightError(
                        f"El destino ya existe y el plan lo marca como nuevo: {destination}"
                    )
            else:
                if not destination.is_file():
                    raise PreflightError(f"El destino a reemplazar ya no existe: {destination}")
                if operation.destination_sha256 is None:
                    raise PreflightError(
                        f"El plan no tiene el hash del destino para {operation.relative_path!r}."
                    )
                if sha256_file(destination) != operation.destination_sha256:
                    raise PreflightError(
                        f"El destino cambió desde que se creó el plan: {destination}"
                    )

            if operation.requires_confirmation:
                confirmation_paths.append(operation.relative_path)
            writes.append(operation)

        if not writes:
            raise PreflightError("El plan no contiene cambios para aplicar.")
        if confirmation_paths and not options.confirm_required_changes:
            paths = ", ".join(confirmation_paths)
            raise ConfirmationRequired(
                f"El plan requiere confirmación para rutas protegidas: {paths}"
            )

        total_size = sum(
            operation.source.stat().st_size for operation in writes if operation.source is not None
        )
        free = shutil.disk_usage(sd_root).free
        if total_size > free:
            raise PreflightError(
                f"No hay espacio suficiente en la SD: {total_size} bytes necesarios, "
                f"{free} disponibles."
            )
        return tuple(sorted(writes, key=lambda operation: operation.relative_path))

    def _rollback(
        self,
        session: BackupSession,
        sd_root: Path,
        *,
        completed: tuple[str, ...],
        pending: str | None,
    ) -> tuple[str, ...]:
        entries = {entry.relative_path: entry for entry in session.entries}
        targets = list(reversed(completed))
        if pending is not None and pending not in targets:
            targets.append(pending)

        errors: list[str] = []
        for relative in targets:
            entry = entries.get(relative)
            if entry is None:
                errors.append(f"{relative}: no hay entrada de backup")
                continue
            try:
                destination = resolve_within(sd_root, relative)
                ensure_no_symlink_path(sd_root, relative)
                if entry.existed:
                    if entry.backup_path is None or not entry.backup_path.is_file():
                        raise InstallError(f"backup ausente para {relative}")
                    atomic_copy(entry.backup_path, destination)
                elif destination.is_file():
                    destination.unlink()
                elif destination.exists():
                    raise InstallError(f"el destino no es un archivo: {destination}")
            except Exception as exc:
                errors.append(f"{relative}: {exc}")
        return tuple(errors)

    def apply(
        self,
        snapshot: SdSnapshot,
        plan: UpdatePlan,
        *,
        options: ApplyOptions | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> ApplyResult:
        options = options if options is not None else ApplyOptions()
        writes = self._preflight(snapshot, plan, options=options)
        manager = self._manager(options, now)
        store = self._store(options)
        try:
            identity = get_sd_identity(snapshot.root)
        except IdentityError as exc:
            raise PreflightError(str(exc)) from exc

        try:
            session = manager.create_session(snapshot.root, identity.key, writes)
        except BackupError as exc:
            raise InstallError(f"No se pudo crear el backup: {exc}") from exc

        warnings: list[str] = []
        if options.mirror_backup_to_sd:
            try:
                manager.mirror_to_sd(session, snapshot.root)
            except BackupError as exc:
                raise InstallError(f"No se pudo espejar el backup en la SD: {exc}") from exc

        completed: list[str] = []
        pending: str | None = None
        try:
            for operation in writes:
                pending = operation.relative_path
                manager.write_journal(
                    session,
                    status="applying",
                    pending=pending,
                    completed=completed,
                )
                destination = resolve_within(snapshot.root, operation.relative_path)
                ensure_no_symlink_path(snapshot.root, operation.relative_path)

                if operation.kind == OperationKind.REPLACE:
                    if not destination.is_file() or (
                        sha256_file(destination) != operation.destination_sha256
                    ):
                        raise InstallError(
                            f"El destino cambió durante la aplicación: {destination}"
                        )
                elif destination.exists():
                    raise InstallError(f"El destino apareció durante la aplicación: {destination}")

                if operation.source is None:
                    raise InstallError(f"Falta la fuente de {operation.relative_path!r}.")
                atomic_copy(operation.source, destination)
                completed.append(operation.relative_path)
                pending = None
                manager.write_journal(
                    session,
                    status="applying",
                    pending=None,
                    completed=completed,
                )
        except Exception as exc:
            rollback_errors = self._rollback(
                session,
                snapshot.root,
                completed=tuple(completed),
                pending=pending,
            )
            if rollback_errors:
                manager.write_journal(
                    session,
                    status="rollback_failed",
                    pending=pending,
                    completed=completed,
                    error=f"{exc}; rollback: {'; '.join(rollback_errors)}",
                )
                raise RollbackError(
                    f"Falló la aplicación y el rollback quedó incompleto: {exc}",
                    rollback_errors=rollback_errors,
                ) from exc
            manager.write_journal(
                session,
                status="rolled_back",
                pending=pending,
                completed=completed,
                error=str(exc),
            )
            raise InstallError(f"No se pudo aplicar el plan: {exc}") from exc

        manager.write_journal(session, status="applied", pending=None, completed=completed)

        state_path: Path | None = None
        try:
            state = state_from_plan(
                plan,
                sd_id=identity.key,
                backup_dir=session.backup_dir,
                applied_at=(now().isoformat(timespec="seconds") if now is not None else None),
            )
            state_path = store.save(state)
        except StateError as exc:
            warnings.append(f"No se pudo guardar el estado: {exc}")

        pruned: list[Path] = []
        try:
            pruned.extend(manager.prune(identity.key))
        except OSError as exc:
            warnings.append(f"No se pudieron podar backups locales: {exc}")
        if options.mirror_backup_to_sd:
            try:
                pruned.extend(manager.prune_mirror(snapshot.root))
            except OSError as exc:
                warnings.append(f"No se pudieron podar backups en la SD: {exc}")

        return ApplyResult(
            sd_id=identity.key,
            backup_dir=session.backup_dir,
            state_path=state_path,
            applied=writes,
            pruned=tuple(pruned),
            warnings=tuple(warnings),
        )

    def recover(
        self,
        snapshot: SdSnapshot,
        *,
        options: ApplyOptions | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> tuple[Path, ...]:
        options = options if options is not None else ApplyOptions()
        manager = self._manager(options, now)
        try:
            identity = get_sd_identity(snapshot.root)
        except IdentityError as exc:
            raise PreflightError(str(exc)) from exc

        recovered: list[Path] = []
        errors: list[str] = []
        for session in manager.find_incomplete(identity.key):
            journal = manager.read_journal(session)
            raw_completed = journal.get("completed", [])
            completed = (
                tuple(str(item) for item in raw_completed)
                if isinstance(raw_completed, list)
                else ()
            )
            raw_pending = journal.get("pending")
            pending = str(raw_pending) if raw_pending is not None else None
            rollback_errors = self._rollback(
                session,
                snapshot.root,
                completed=completed,
                pending=pending,
            )
            if rollback_errors:
                errors.extend(rollback_errors)
                manager.write_journal(
                    session,
                    status="rollback_failed",
                    pending=pending,
                    completed=completed,
                    error="; ".join(rollback_errors),
                )
            else:
                manager.write_journal(
                    session,
                    status="rolled_back",
                    pending=pending,
                    completed=completed,
                    error="recuperación al iniciar",
                )
                recovered.append(session.backup_dir)
        if errors:
            raise RollbackError(
                "No se pudieron recuperar todas las transacciones incompletas.",
                rollback_errors=tuple(errors),
            )
        return tuple(recovered)
