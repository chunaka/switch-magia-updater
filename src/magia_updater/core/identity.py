"""Identificación estable de una SD para separar backups y estado."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from magia_updater.core.errors import MagiaError


class IdentityError(MagiaError):
    """No se pudo identificar la SD."""


@dataclass(frozen=True)
class SdIdentity:
    key: str
    label: str


def _windows_volume_serial(root: Path) -> int | None:
    drive = root.drive
    if not drive:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = kernel32.GetVolumeInformationW
        function.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.LPDWORD,
            wintypes.LPDWORD,
            wintypes.LPDWORD,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        function.restype = wintypes.BOOL
        volume_name = ctypes.create_unicode_buffer(261)
        filesystem_name = ctypes.create_unicode_buffer(261)
        serial = wintypes.DWORD()
        max_component = wintypes.DWORD()
        flags = wintypes.DWORD()
        result = function(
            drive + "\\",
            volume_name,
            len(volume_name),
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(flags),
            filesystem_name,
            len(filesystem_name),
        )
        if not result:
            return None
        return int(serial.value)
    except (OSError, AttributeError, ValueError):
        return None


def get_sd_identity(root: Path) -> SdIdentity:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise IdentityError(f"La raíz de la SD no existe o no es una carpeta: {resolved}")

    if os.name == "nt":
        serial = _windows_volume_serial(resolved)
        if serial is not None:
            return SdIdentity(key=f"win-{serial:08x}", label=str(resolved))

    try:
        stat = resolved.stat()
    except OSError as exc:
        raise IdentityError(f"No se pudo identificar {resolved}: {exc}") from exc
    digest = hashlib.sha256(str(resolved).casefold().encode("utf-8")).hexdigest()[:12]
    return SdIdentity(key=f"dev-{stat.st_dev:x}-{digest}", label=str(resolved))
