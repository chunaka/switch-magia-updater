"""Extracción segura de ZIPs, sin rutas fuera del destino ni enlaces simbólicos."""

from __future__ import annotations

import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from magia_updater.core.errors import MagiaError

MAX_ENTRIES = 10_000
MAX_TOTAL_SIZE = 1024 * 1024 * 1024
MAX_ENTRY_SIZE = 512 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class ArchiveError(MagiaError):
    """El ZIP no se pudo abrir o extraer."""


class UnsafeArchiveError(ArchiveError):
    """El ZIP contiene una entrada peligrosa."""


class ArchiveValidationError(ArchiveError):
    """El ZIP excede los límites permitidos o el destino no es válido."""


@dataclass(frozen=True)
class ExtractionResult:
    root: Path
    file_count: int
    total_size: int
    files: tuple[str, ...]


def _safe_member(name: str) -> PurePosixPath:
    if not name or "\x00" in name:
        raise UnsafeArchiveError("Entrada ZIP sin nombre válido.")
    if name.startswith(("/", "\\")) or "\\" in name:
        raise UnsafeArchiveError(f"Ruta absoluta o con separador de Windows: {name!r}")
    member = PurePosixPath(name)
    if member.is_absolute() or not member.parts:
        raise UnsafeArchiveError(f"Ruta absoluta o vacía: {name!r}")
    if any(part in {"", ".", ".."} for part in member.parts):
        raise UnsafeArchiveError(f"Ruta con traversal: {name!r}")
    if _DRIVE_RE.match(member.parts[0]):
        raise UnsafeArchiveError(f"Ruta con unidad de Windows: {name!r}")
    return member


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def extract_zip_safely(
    archive: Path,
    destination: Path,
    *,
    max_entries: int = MAX_ENTRIES,
    max_total_size: int = MAX_TOTAL_SIZE,
    max_entry_size: int = MAX_ENTRY_SIZE,
) -> ExtractionResult:
    """Extrae un ZIP en ``destination``, que no debe existir.

    La extracción se hace en un directorio temporal y solo se renombra al final,
    para no dejar una carpeta parcial si el archivo es inválido.
    """
    if destination.exists():
        raise ArchiveValidationError(f"El destino ya existe: {destination}")
    if max_entries <= 0 or max_total_size <= 0 or max_entry_size <= 0:
        raise ArchiveValidationError("Los límites de extracción deben ser positivos.")

    try:
        zip_file = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveError(f"No se pudo abrir el ZIP {archive}: {exc}") from exc

    with zip_file:
        infos = zip_file.infolist()
        if len(infos) > max_entries:
            raise ArchiveValidationError(
                f"El ZIP tiene demasiadas entradas ({len(infos)} > {max_entries})."
            )

        seen: set[str] = set()
        declared_size = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise UnsafeArchiveError(f"Entrada ZIP cifrada: {info.filename!r}")
            if _is_symlink(info):
                raise UnsafeArchiveError(f"Entrada ZIP es un enlace simbólico: {info.filename!r}")
            member = _safe_member(info.filename)
            key = member.as_posix()
            if key in seen:
                raise UnsafeArchiveError(f"Entrada ZIP duplicada: {info.filename!r}")
            seen.add(key)
            if not info.is_dir():
                if info.file_size > max_entry_size:
                    raise ArchiveValidationError(f"Entrada demasiado grande: {info.filename!r}")
                declared_size += info.file_size
                if declared_size > max_total_size:
                    raise ArchiveValidationError("El ZIP supera el tamaño total permitido.")

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".magia-extract-", dir=destination.parent))
        files: list[str] = []
        total_size = 0
        try:
            for info in infos:
                member = _safe_member(info.filename)
                target = temporary / member
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                entry_size = 0
                with zip_file.open(info) as source, target.open("xb") as output:
                    while chunk := source.read(CHUNK_SIZE):
                        entry_size += len(chunk)
                        total_size += len(chunk)
                        if entry_size > max_entry_size:
                            raise ArchiveValidationError(
                                f"Entrada demasiado grande al descomprimir: {info.filename!r}"
                            )
                        if total_size > max_total_size:
                            raise ArchiveValidationError(
                                "El ZIP supera el tamaño total permitido al descomprimir."
                            )
                        output.write(chunk)
                files.append(member.as_posix())

            temporary.rename(destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    return ExtractionResult(
        root=destination,
        file_count=len(files),
        total_size=total_size,
        files=tuple(files),
    )
