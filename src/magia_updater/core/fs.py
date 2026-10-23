"""Utilidades de sistema de archivos con escritura atómica y rutas seguras."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from magia_updater.core.errors import MagiaError

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_COPY_CHUNK = 1024 * 1024


class PathSecurityError(MagiaError):
    """Una ruta relativa intenta salir de la raíz o es ambigua."""


def default_app_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "MagiaUpdater"
    return Path.home() / ".local" / "share" / "MagiaUpdater"


def default_backup_root() -> Path:
    return default_app_root() / "backups"


def default_state_root() -> Path:
    return default_app_root() / "state"


def validate_relative_path(relative: str) -> PurePosixPath:
    """Valida una ruta POSIX relativa sin resolverla contra el sistema de archivos."""
    if not relative or "\x00" in relative:
        raise PathSecurityError(f"Ruta relativa vacía o inválida: {relative!r}")
    if relative.startswith(("/", "\\")) or "\\" in relative:
        raise PathSecurityError(f"Ruta absoluta o con separador de Windows: {relative!r}")
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        raise PathSecurityError(f"Ruta con componente ambiguo: {relative!r}")
    member = PurePosixPath(relative)
    if member.is_absolute() or not member.parts:
        raise PathSecurityError(f"Ruta absoluta o vacía: {relative!r}")
    if any(part in {"", ".", ".."} for part in member.parts):
        raise PathSecurityError(f"Ruta con traversal: {relative!r}")
    if _DRIVE_RE.match(member.parts[0]):
        raise PathSecurityError(f"Ruta con unidad de Windows: {relative!r}")
    return member


def resolve_within(root: Path, relative: str) -> Path:
    """Resuelve una ruta POSIX relativa y comprueba que quede dentro de ``root``."""
    member = validate_relative_path(relative)
    root_resolved = root.resolve()
    target = (root_resolved / member).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise PathSecurityError(f"La ruta sale de la raíz: {relative!r}")
    return target


def ensure_no_symlink_path(root: Path, relative: str) -> None:
    """Rechaza cualquier componente existente que sea un enlace simbólico."""
    current = root.resolve()
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise PathSecurityError(f"Componente symlink no permitido: {current}")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, text)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def atomic_copy(source: Path, destination: Path) -> None:
    """Copia un archivo a un temporal y lo reemplaza de forma atómica."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=_COPY_CHUNK)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
