"""Exportación de planes de actualización a carpetas o paquetes ZIP.

La exportación es siempre de solo lectura sobre la SD: únicamente lee los
archivos preparados en la caché y escribe el paquete en la ruta elegida.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from magia_updater import __version__
from magia_updater.core.download import sha256_file
from magia_updater.core.errors import MagiaError
from magia_updater.core.fs import (
    atomic_copy,
    atomic_write_json,
    atomic_write_text,
    validate_relative_path,
)
from magia_updater.core.models import (
    ComponentId,
    FileOperation,
    OperationKind,
    UpdatePlan,
)

_RESERVED_NAMES = frozenset({"manifest.json", "leeme.txt"})


class ExportError(MagiaError):
    """No se pudo construir el paquete de exportación."""


class ExportFormat(StrEnum):
    FOLDER = "folder"
    ZIP = "zip"


class ExportKind(StrEnum):
    FULL = "full"
    DELTA = "delta"


@dataclass(frozen=True)
class ExportFile:
    relative_path: str
    source: Path
    size: int
    sha256: str
    kind: OperationKind
    component: ComponentId
    reason: str
    destination_sha256: str | None
    requires_confirmation: bool


@dataclass(frozen=True)
class ExportResult:
    path: Path
    format: ExportFormat
    kind: ExportKind
    files: tuple[ExportFile, ...]
    manifest: Mapping[str, Any]

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(file.size for file in self.files)


def _validate_source(operation: FileOperation) -> tuple[Path, int, str]:
    if operation.source is None:
        raise ExportError(f"La operación {operation.relative_path} no tiene archivo de origen.")
    source = operation.source
    if not source.is_file():
        raise ExportError(f"No existe el archivo de origen: {source}")
    size = source.stat().st_size
    if operation.size is not None and operation.size != size:
        raise ExportError(
            f"El tamaño de {operation.relative_path} cambió desde el plan "
            f"({operation.size} != {size})."
        )
    digest = sha256_file(source)
    if operation.sha256 is not None and operation.sha256 != digest:
        raise ExportError(f"El hash de {operation.relative_path} cambió desde el plan.")
    return source, size, digest


def _collect_files(plan: UpdatePlan, kind: ExportKind) -> tuple[ExportFile, ...]:
    if plan.conflicts:
        paths = ", ".join(operation.relative_path for operation in plan.conflicts)
        raise ExportError(f"El plan contiene conflictos: {paths}")

    files: dict[str, ExportFile] = {}
    for component_plan in plan.components:
        for operation in component_plan.operations:
            if operation.kind == OperationKind.SKIP:
                continue
            if operation.kind == OperationKind.KEEP and (
                kind == ExportKind.DELTA or operation.source is None
            ):
                continue
            if operation.kind == OperationKind.CONFLICT:
                raise ExportError(f"Conflicto en {operation.relative_path}.")
            if operation.kind not in {
                OperationKind.CREATE,
                OperationKind.REPLACE,
                OperationKind.KEEP,
            }:
                raise ExportError(
                    f"Operación no exportable en {operation.relative_path}: {operation.kind}."
                )

            member = validate_relative_path(operation.relative_path)
            relative = member.as_posix()
            if relative.lower() in _RESERVED_NAMES:
                raise ExportError(f"La ruta {relative} está reservada por el paquete.")
            if relative in files:
                raise ExportError(f"Ruta duplicada en el plan: {relative}")

            source, size, digest = _validate_source(operation)
            files[relative] = ExportFile(
                relative_path=relative,
                source=source,
                size=size,
                sha256=digest,
                kind=operation.kind,
                component=component_plan.component,
                reason=operation.reason,
                destination_sha256=operation.destination_sha256,
                requires_confirmation=operation.requires_confirmation,
            )

    if not files:
        raise ExportError("El plan no contiene archivos para exportar.")
    return tuple(files[key] for key in sorted(files))


def _build_manifest(
    plan: UpdatePlan,
    files: tuple[ExportFile, ...],
    kind: ExportKind,
    created_at: datetime,
) -> dict[str, Any]:
    components = []
    for component_plan in plan.components:
        release = component_plan.release
        components.append(
            {
                "component": component_plan.component.value,
                "release_tag": release.tag if release is not None else None,
                "version": str(release.version) if release is not None else None,
                "name": release.name if release is not None else None,
            }
        )

    warnings = list(plan.warnings)
    for component_plan in plan.components:
        for warning in component_plan.warnings:
            if warning not in warnings:
                warnings.append(warning)

    return {
        "schema": 1,
        "generated_by": {"name": "magia-updater", "version": __version__},
        "created_at": created_at.isoformat(timespec="seconds"),
        "package_type": kind.value,
        "file_count": len(files),
        "total_size": sum(file.size for file in files),
        "requires_confirmation": plan.requires_confirmation,
        "components": components,
        "warnings": warnings,
        "files": [
            {
                "path": file.relative_path,
                "kind": file.kind.value,
                "component": file.component.value,
                "size": file.size,
                "sha256": file.sha256,
                "destination_sha256": file.destination_sha256,
                "reason": file.reason,
                "requires_confirmation": file.requires_confirmation,
            }
            for file in files
        ],
    }


def _build_readme(plan: UpdatePlan, files: tuple[ExportFile, ...], kind: ExportKind) -> str:
    label = "completo" if kind == ExportKind.FULL else "delta"
    lines = [
        "Magia Updater - Paquete de actualización",
        "=======================================",
        "",
        f"Tipo de paquete: {label}",
        f"Archivos incluidos: {len(files)}",
        "",
        "Componentes:",
    ]
    for component_plan in plan.components:
        release = component_plan.release
        if release is None:
            lines.append(f"- {component_plan.component.value}: sin release")
        else:
            lines.append(f"- {component_plan.component.value}: {release.version} ({release.tag})")

    lines.extend(
        [
            "",
            "Instrucciones:",
            "1. Haz una copia de seguridad de la SD antes de copiar archivos.",
            "2. Copia el CONTENIDO de esta carpeta en la raíz de la SD, no la carpeta en sí.",
            "3. Si has descargado un ZIP, extráelo y combina las carpetas con la SD.",
            "4. No sobrescribas configuraciones, sysmodules ni payloads que no conozcas.",
            "5. Revisa manifest.json para comprobar rutas, tamaños y hashes SHA-256.",
            "",
            "Este paquete no ha modificado la SD. La instalación es responsabilidad del usuario.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_folder(
    destination: Path,
    files: tuple[ExportFile, ...],
    manifest: Mapping[str, Any],
    readme: str,
) -> None:
    temporary = Path(tempfile.mkdtemp(prefix=".magia-export-", dir=destination.parent))
    try:
        for file in files:
            target = temporary / file.relative_path
            atomic_copy(file.source, target)
            if sha256_file(target) != file.sha256:
                raise ExportError(f"La copia de {file.relative_path} no coincide con su hash.")
        atomic_write_json(temporary / "manifest.json", manifest)
        atomic_write_text(temporary / "LEEME.txt", readme)
        os.replace(temporary, destination)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise


def _write_zip(
    destination: Path,
    files: tuple[ExportFile, ...],
    manifest: Mapping[str, Any],
    readme: str,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".magia-export-", suffix=".zip", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in files:
                archive.write(file.source, arcname=file.relative_path)
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            archive.writestr("LEEME.txt", readme)
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def export_plan(
    plan: UpdatePlan,
    destination: Path,
    *,
    kind: ExportKind = ExportKind.DELTA,
    format: ExportFormat = ExportFormat.ZIP,
    now: Callable[[], datetime] | None = None,
) -> ExportResult:
    """Exporta ``plan`` sin escribir en la SD.

    ``full`` incluye los archivos idénticos que tienen origen (``KEEP``), mientras
    que ``delta`` solo incluye creaciones y reemplazos. Las rutas preservadas y
    los payloads protegidos sin origen nunca se incluyen.
    """
    destination = Path(destination)
    if destination.exists():
        raise ExportError(f"El destino ya existe: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    files = _collect_files(plan, kind)
    created_at = now() if now is not None else datetime.now(UTC)
    manifest = _build_manifest(plan, files, kind, created_at)
    readme = _build_readme(plan, files, kind)

    if format == ExportFormat.FOLDER:
        _write_folder(destination, files, manifest, readme)
    elif format == ExportFormat.ZIP:
        _write_zip(destination, files, manifest, readme)
    else:  # pragma: no cover - protege futuras variantes del enum
        raise ExportError(f"Formato de exportación desconocido: {format}")

    return ExportResult(
        path=destination,
        format=format,
        kind=kind,
        files=files,
        manifest=manifest,
    )
