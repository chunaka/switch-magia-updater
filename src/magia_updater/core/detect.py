"""Detección offline de versiones instaladas en una SD de Nintendo Switch.

La detección no escribe nada. Prioriza archivos reales del sistema sobre el estado
de la aplicación, de modo que una SD actualizada manualmente también se detecte.
"""

from __future__ import annotations

import re
from pathlib import Path

from magia_updater.core.components import COMPONENTS, SYS_PATCH_TID
from magia_updater.core.errors import SdNotFoundError
from magia_updater.core.models import (
    ComponentId,
    FirmwareInfo,
    InstalledComponent,
    Package3Info,
    SdSnapshot,
    SysPatchLog,
    Version,
)

PACKAGE3_MAGIC = b"PK31"
PACKAGE3_EXPECTED_SIZE = 8 * 1024 * 1024
PACKAGE3_HEADER_SIZE = 0x3C
PACKAGE3_HOS_OFFSET = 0x35
PACKAGE3_AMS_OFFSET = 0x38

PACKAGE3_PATH = Path("atmosphere/package3")
HEKATE_UPDATE_PATH = Path("bootloader/update.bin")
HBMENU_PATH = Path("hbmenu.nro")
SYS_PATCH_EXEFS_PATH = Path(f"atmosphere/contents/{SYS_PATCH_TID}/exefs.nsp")
SYS_PATCH_LOG_PATH = Path("config/sys-patch/log.ini")

MAX_SCAN_SIZE = 64 * 1024 * 1024

_PREFIXED_VERSION_RE = re.compile(rb"v(\d+\.\d+\.\d+)")
_SYS_PATCH_VERSION_RE = re.compile(rb"(\d+\.\d+\.\d+\.\d+)(?:-[0-9a-fA-F]+)?")
_HEKATE_FILENAME_RE = re.compile(r"hekate_ctcaer_(\d+\.\d+\.\d+)(?:__ram8GB)?\.bin")


def _read_bytes(path: Path, *, limit: int = MAX_SCAN_SIZE) -> bytes | None:
    try:
        with path.open("rb") as stream:
            return stream.read(limit)
    except OSError:
        return None


def parse_package3_header(header: bytes, *, size: int) -> Package3Info | None:
    """Interpreta la cabecera de ``package3``.

    Layout verificado con Atmosphère 1.12.1:
    - ``0x35..0x37``: firmware soportado, en orden micro/menor/mayor.
    - ``0x38..0x3B``: versión AMS, en orden parche/micro/menor/mayor.
    """
    if len(header) < PACKAGE3_HEADER_SIZE or not header.startswith(PACKAGE3_MAGIC):
        return None

    hos = header[PACKAGE3_HOS_OFFSET : PACKAGE3_HOS_OFFSET + 3]
    ams = header[PACKAGE3_AMS_OFFSET : PACKAGE3_AMS_OFFSET + 4]
    hos_major, hos_minor, hos_micro = hos[2], hos[1], hos[0]
    ams_major, ams_minor, ams_micro, ams_patch = ams[3], ams[2], ams[1], ams[0]

    # Una cabecera válida no debería tener versión mayor cero.
    if hos_major == 0 or ams_major == 0:
        return None

    supported_firmware = Version.from_parts(
        (hos_major, hos_minor, hos_micro),
        f"{hos_major}.{hos_minor}.{hos_micro}",
    )
    ams_parts = (ams_major, ams_minor, ams_micro)
    ams_text = ".".join(str(part) for part in ams_parts)
    if ams_patch:
        ams_text = f"{ams_text}.{ams_patch}"

    return Package3Info(
        ams_version=Version.from_parts((*ams_parts, ams_patch), ams_text),
        supported_firmware=supported_firmware,
        size=size,
    )


def parse_package3(path: Path) -> Package3Info | None:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            header = stream.read(PACKAGE3_HEADER_SIZE)
    except OSError:
        return None
    return parse_package3_header(header, size=size)


def _find_prefixed_version(path: Path) -> Version | None:
    data = _read_bytes(path)
    if data is None:
        return None
    match = _PREFIXED_VERSION_RE.search(data)
    if match is None:
        return None
    try:
        return Version.parse(match.group(1).decode("ascii"))
    except ValueError:
        return None


def _find_sys_patch_version(path: Path) -> Version | None:
    data = _read_bytes(path)
    if data is None:
        return None
    match = _SYS_PATCH_VERSION_RE.search(data)
    if match is None:
        return None
    try:
        return Version.parse(match.group(1).decode("ascii"))
    except ValueError:
        return None


def parse_sys_patch_log(path: Path) -> SysPatchLog | None:
    """Parsea de forma tolerante el log de sys-patch, sin depender de configparser."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    values: dict[str, str] = {}
    section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "stats" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().lower()] = value.strip()

    def parse(key: str) -> Version | None:
        value = values.get(key)
        if not value:
            return None
        try:
            return Version.parse(value)
        except ValueError:
            return None

    return SysPatchLog(
        firmware=parse("fw_version"),
        ams_version=parse("ams_version"),
        sys_patch_version=parse("version"),
    )


def detect_atmosphere(root: Path) -> InstalledComponent:
    path = root / PACKAGE3_PATH
    if not path.is_file():
        return InstalledComponent(component=ComponentId.ATMOSPHERE)

    info = parse_package3(path)
    if info is None:
        return InstalledComponent(
            component=ComponentId.ATMOSPHERE,
            present=True,
            evidence="package3 (cabecera no reconocida)",
            path=path,
        )
    return InstalledComponent(
        component=ComponentId.ATMOSPHERE,
        present=True,
        version=info.ams_version,
        evidence="package3",
        path=path,
        supported_firmware=info.supported_firmware,
    )


def _hekate_fallback(root: Path) -> tuple[Path, Version] | None:
    candidates: list[Path] = []
    candidates.extend(sorted(root.glob("hekate_ctcaer_*.bin")))
    payloads_dir = root / "bootloader/payloads"
    if payloads_dir.is_dir():
        candidates.extend(sorted(payloads_dir.glob("hekate_ctcaer_*.bin")))

    for candidate in candidates:
        if "__ram8GB" in candidate.name:
            continue
        match = _HEKATE_FILENAME_RE.fullmatch(candidate.name)
        if match is None:
            continue
        try:
            return candidate, Version.parse(match.group(1))
        except ValueError:
            continue
    return None


def detect_hekate(root: Path) -> InstalledComponent:
    path = root / HEKATE_UPDATE_PATH
    if path.is_file():
        version = _find_prefixed_version(path)
        return InstalledComponent(
            component=ComponentId.HEKATE,
            present=True,
            version=version,
            evidence="bootloader/update.bin" if version else "update.bin (versión no reconocida)",
            path=path,
        )

    fallback = _hekate_fallback(root)
    if fallback is None:
        return InstalledComponent(component=ComponentId.HEKATE)
    fallback_path, version = fallback
    return InstalledComponent(
        component=ComponentId.HEKATE,
        present=True,
        version=version,
        evidence="nombre de payload",
        path=fallback_path,
    )


def detect_hbmenu(root: Path) -> InstalledComponent:
    path = root / HBMENU_PATH
    if not path.is_file():
        return InstalledComponent(component=ComponentId.HBMENU)
    version = _find_prefixed_version(path)
    return InstalledComponent(
        component=ComponentId.HBMENU,
        present=True,
        version=version,
        evidence="hbmenu.nro" if version else "hbmenu.nro (versión no reconocida)",
        path=path,
    )


def detect_sys_patch(root: Path, log: SysPatchLog | None = None) -> InstalledComponent:
    path = root / SYS_PATCH_EXEFS_PATH
    log = log if log is not None else parse_sys_patch_log(root / SYS_PATCH_LOG_PATH)

    if path.is_file():
        version = _find_sys_patch_version(path)
        evidence = "exefs.nsp"
        if version is None and log is not None and log.sys_patch_version is not None:
            version = log.sys_patch_version
            evidence = "log.ini"
        return InstalledComponent(
            component=ComponentId.SYS_PATCH,
            present=True,
            version=version,
            evidence=evidence if version else "exefs.nsp (versión no reconocida)",
            path=path,
        )

    if log is not None and log.sys_patch_version is not None:
        return InstalledComponent(
            component=ComponentId.SYS_PATCH,
            present=True,
            version=log.sys_patch_version,
            evidence="log.ini",
            path=root / SYS_PATCH_LOG_PATH,
        )

    return InstalledComponent(component=ComponentId.SYS_PATCH)


def detect_sd(root: str | Path) -> SdSnapshot:
    """Detecta el estado de una SD sin modificarla."""
    sd_root = Path(root).expanduser()
    if not sd_root.exists():
        raise SdNotFoundError(f"No existe la ruta: {sd_root}")
    if not sd_root.is_dir():
        raise SdNotFoundError(f"La ruta no es una carpeta: {sd_root}")
    sd_root = sd_root.resolve()

    log = parse_sys_patch_log(sd_root / SYS_PATCH_LOG_PATH)
    atmosphere = detect_atmosphere(sd_root)
    hekate = detect_hekate(sd_root)
    sys_patch = detect_sys_patch(sd_root, log)
    hbmenu = detect_hbmenu(sd_root)

    components = {
        ComponentId.ATMOSPHERE: atmosphere,
        ComponentId.HEKATE: hekate,
        ComponentId.SYS_PATCH: sys_patch,
        ComponentId.HBMENU: hbmenu,
    }

    markers = (
        sd_root / PACKAGE3_PATH,
        sd_root / HEKATE_UPDATE_PATH,
        sd_root / HBMENU_PATH,
        sd_root / "Nintendo",
        sd_root / "emuMMC",
    )
    is_switch_sd = any(marker.exists() for marker in markers)
    has_cfw = atmosphere.present or hekate.present

    emummc_present = (sd_root / "emuMMC").is_dir()
    atmosphere_config_present = (sd_root / "atmosphere/config").is_dir()
    hekate_config_present = (sd_root / "bootloader/hekate_ipl.ini").is_file()

    root_payload: Path | None = sd_root / "payload.bin"
    if root_payload is not None and not root_payload.is_file():
        root_payload = None

    reboot_payload: Path | None = sd_root / "atmosphere/reboot_payload.bin"
    if reboot_payload is not None and not reboot_payload.is_file():
        reboot_payload = None

    payloads_dir = sd_root / "bootloader/payloads"
    payloads: tuple[Path, ...] = ()
    if payloads_dir.is_dir():
        try:
            payloads = tuple(sorted(path for path in payloads_dir.iterdir() if path.is_file()))
        except OSError:
            payloads = ()

    warnings: list[str] = []
    if not is_switch_sd:
        warnings.append("La carpeta no parece una SD de Switch (no se encontraron marcadores).")
    if not atmosphere.present:
        warnings.append("No se detectó Atmosphère en /atmosphere/package3.")
    elif atmosphere.version is None:
        warnings.append("Atmosphère está presente, pero no se pudo leer su versión.")
    if not hekate.present:
        warnings.append("No se detectó Hekate; se asume un arranque con fusee.")
    elif hekate.version is None:
        warnings.append("Hekate está presente, pero no se pudo leer su versión.")
    if not sys_patch.present:
        warnings.append("No se detectó sys-patch; los juegos pueden no arrancar sin parches.")
    elif sys_patch.version is None:
        warnings.append("sys-patch está presente, pero no se pudo leer su versión.")
    if hbmenu.present and hbmenu.version is None:
        warnings.append("hbmenu está presente, pero no se pudo leer su versión.")
    if log is None or log.firmware is None:
        warnings.append(
            "No se pudo determinar el firmware; falta /config/sys-patch/log.ini o su clave fw_version."
        )
    if not has_cfw:
        warnings.append("No se detectó un CFW instalado; esta versión de la app solo actualiza.")
    if emummc_present:
        warnings.append(
            "EmuMMC detectado: se actualizarán los archivos compartidos, pero /emuMMC no se tocará."
        )
    if root_payload is not None:
        warnings.append("Hay /payload.bin en la raíz; no se modificará sin confirmación explícita.")
    if atmosphere.present:
        package3 = sd_root / PACKAGE3_PATH
        try:
            if package3.stat().st_size != PACKAGE3_EXPECTED_SIZE:
                warnings.append(
                    "package3 no tiene el tamaño esperado (8 MiB); la versión puede ser imprecisa."
                )
        except OSError:
            pass

    firmware = FirmwareInfo(
        version=log.firmware if log is not None else None,
        source="config/sys-patch/log.ini" if log is not None and log.firmware else None,
        ams_version=log.ams_version if log is not None else None,
    )

    return SdSnapshot(
        root=sd_root,
        components=components,
        firmware=firmware,
        is_switch_sd=is_switch_sd,
        has_cfw=has_cfw,
        emummc_present=emummc_present,
        atmosphere_config_present=atmosphere_config_present,
        hekate_config_present=hekate_config_present,
        root_payload=root_payload,
        reboot_payload=reboot_payload,
        payloads=payloads,
        warnings=tuple(warnings),
    )


def component_names() -> dict[ComponentId, str]:
    """Nombres de presentación, centralizados para CLI y GUI."""
    return {component_id: definition.name for component_id, definition in COMPONENTS.items()}
