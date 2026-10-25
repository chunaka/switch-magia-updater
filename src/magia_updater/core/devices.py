"""Detección de unidades que contienen una SD de Nintendo Switch.

La detección es de solo lectura y no depende de PySide6: la usan tanto la CLI
como la GUI. En Windows se enumeran las unidades lógicas y se descartan las
ópticas, las de red y la unidad del sistema. También se admiten unidades fijas
porque algunos lectores de SD se presentan así.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SWITCH_MARKERS: tuple[str, ...] = (
    "atmosphere/package3",
    "bootloader/update.bin",
    "hbmenu.nro",
    "Nintendo",
    "emuMMC",
)

_DRIVE_REMOVABLE = 2
_DRIVE_FIXED = 3
_DRIVE_TYPE_LABELS = {
    _DRIVE_REMOVABLE: "extraíble",
    _DRIVE_FIXED: "fija",
}


@dataclass(frozen=True)
class SdCandidate:
    """Una unidad o carpeta que contiene marcadores de una SD de Switch."""

    root: Path
    drive_type: str
    markers: tuple[str, ...]

    @property
    def display_name(self) -> str:
        return f"{self.root} ({self.drive_type})"


def switch_markers(root: Path) -> tuple[str, ...]:
    """Devuelve los marcadores de Switch encontrados en ``root``."""
    found: list[str] = []
    for marker in SWITCH_MARKERS:
        try:
            if (root / marker).exists():
                found.append(marker)
        except OSError:
            continue
    return tuple(found)


def _windows_drive_roots() -> tuple[tuple[Path, str], ...]:
    if os.name != "nt":
        return ()
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_logical_drives = kernel32.GetLogicalDrives
        get_logical_drives.argtypes = []
        get_logical_drives.restype = wintypes.DWORD
        get_drive_type = kernel32.GetDriveTypeW
        get_drive_type.argtypes = [wintypes.LPCWSTR]
        get_drive_type.restype = wintypes.UINT
        mask = int(get_logical_drives())
    except (AttributeError, OSError, ValueError):
        return ()

    system_drive = os.environ.get("SYSTEMDRIVE", "C:").rstrip("\\/").upper()
    roots: list[tuple[Path, str]] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        drive = f"{chr(ord('A') + index)}:"
        if drive.upper() == system_drive:
            continue
        drive_type = int(get_drive_type(f"{drive}\\"))
        label = _DRIVE_TYPE_LABELS.get(drive_type)
        if label is None:
            continue
        roots.append((Path(f"{drive}\\"), label))
    return tuple(roots)


def _unix_drive_roots() -> tuple[tuple[Path, str], ...]:
    bases = [Path("/Volumes"), Path("/media")]
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if user:
        bases.extend((Path("/media") / user, Path("/run/media") / user))

    roots: list[tuple[Path, str]] = []
    for base in bases:
        try:
            children = sorted(base.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if child.is_dir():
                    roots.append((child, "montaje"))
            except OSError:
                continue
    return tuple(roots)


def find_switch_sds(
    *,
    roots: Iterable[Path] | None = None,
) -> tuple[SdCandidate, ...]:
    """Busca SD de Switch en las unidades conectadas.

    ``roots`` permite inyectar rutas en pruebas sin depender del hardware real.
    """
    if roots is None:
        entries = _windows_drive_roots() if os.name == "nt" else _unix_drive_roots()
    else:
        entries = tuple((Path(root), "ruta") for root in roots)

    candidates: list[SdCandidate] = []
    seen: set[str] = set()
    for root, drive_type in entries:
        try:
            normalized = Path(os.path.abspath(root.expanduser()))
        except (OSError, RuntimeError, ValueError):
            continue
        key = str(normalized).casefold()
        if key in seen:
            continue
        seen.add(key)
        markers = switch_markers(normalized)
        if not markers:
            continue
        candidates.append(
            SdCandidate(root=normalized, drive_type=drive_type, markers=markers)
        )
    return tuple(sorted(candidates, key=lambda candidate: str(candidate.root).casefold()))
