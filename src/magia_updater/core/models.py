"""Modelos de dominio del actualizador.

Este módulo no depende de la GUI ni de la red. Las versiones se comparan de forma
numérica y tolerante a componentes finales en cero: ``1.12.1`` y ``1.12.1.0`` son
equivalentes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import total_ordering
from pathlib import Path
from types import MappingProxyType
from typing import Any

_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)(?:[-+].*)?$")


class ComponentId(StrEnum):
    """Identificadores estables usados en configuración, estado y CLI."""

    ATMOSPHERE = "atmosphere"
    HEKATE = "hekate"
    SYS_PATCH = "sys-patch"
    HBMENU = "hbmenu"


class CheckStatus(StrEnum):
    """Resultado de comparar lo instalado con la última release estable."""

    UPDATE_AVAILABLE = "update_available"
    UP_TO_DATE = "up_to_date"
    NOT_INSTALLED = "not_installed"
    INSTALLED_NEWER = "installed_newer"
    UNKNOWN = "unknown"


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    """Versión numérica con texto de presentación.

    ``parts`` se usa para comparar. ``text`` conserva la forma que se mostrará
    (por ejemplo ``1.6.3.0`` para sys-patch o ``1.12.1`` para Atmosphère).
    """

    parts: tuple[int, ...]
    text: str

    def __post_init__(self) -> None:
        if not self.parts:
            raise ValueError("La versión debe tener al menos un componente")
        normalized_parts = tuple(int(part) for part in self.parts)
        normalized_text = self.text.strip()
        if not normalized_text:
            normalized_text = ".".join(str(part) for part in normalized_parts)
        object.__setattr__(self, "parts", normalized_parts)
        object.__setattr__(self, "text", normalized_text)

    @classmethod
    def parse(cls, value: str) -> Version:
        """Parsea ``v1.2.3``, ``1.2.3.0`` o ``1.6.3.0-b962f75``."""
        match = _VERSION_RE.fullmatch(value.strip())
        if match is None:
            raise ValueError(f"Versión inválida: {value!r}")
        numeric = match.group(1)
        return cls(tuple(int(part) for part in numeric.split(".")), numeric)

    @classmethod
    def from_parts(cls, parts: tuple[int, ...], text: str | None = None) -> Version:
        return cls(tuple(parts), text or ".".join(str(part) for part in parts))

    @property
    def key(self) -> tuple[int, ...]:
        """Clave de igualdad: elimina ceros finales, conserva al menos uno."""
        parts = list(self.parts)
        while len(parts) > 1 and parts[-1] == 0:
            parts.pop()
        return tuple(parts)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return False
        length = max(len(self.parts), len(other.parts))
        left = self.parts + (0,) * (length - len(self.parts))
        right = other.parts + (0,) * (length - len(other.parts))
        return left == right

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        length = max(len(self.parts), len(other.parts))
        left = self.parts + (0,) * (length - len(self.parts))
        right = other.parts + (0,) * (length - len(other.parts))
        return left < right

    def __hash__(self) -> int:
        return hash(self.key)

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class Package3Info:
    """Información leída de ``/atmosphere/package3``."""

    ams_version: Version
    supported_firmware: Version
    size: int


@dataclass(frozen=True)
class SysPatchLog:
    """Valores útiles de ``/config/sys-patch/log.ini``."""

    firmware: Version | None = None
    ams_version: Version | None = None
    sys_patch_version: Version | None = None


@dataclass(frozen=True)
class FirmwareInfo:
    version: Version | None = None
    source: str | None = None
    ams_version: Version | None = None


@dataclass(frozen=True)
class InstalledComponent:
    """Estado detectado de un componente en una SD."""

    component: ComponentId
    present: bool = False
    version: Version | None = None
    evidence: str | None = None
    path: Path | None = None
    supported_firmware: Version | None = None

    @property
    def detected(self) -> bool:
        return self.present


@dataclass(frozen=True)
class ComponentDefinition:
    """Metadatos estáticos de un componente actualizable."""

    id: ComponentId
    name: str
    repo: str
    description: str
    archive_pattern: str = ""
    payload_patterns: tuple[str, ...] = ()
    required_files: tuple[str, ...] = ()
    install_roots: tuple[str, ...] = ()
    preserve: tuple[str, ...] = ()
    informational: bool = False
    optional: bool = False

    @property
    def asset_patterns(self) -> tuple[str, ...]:
        patterns: list[str] = []
        if self.archive_pattern:
            patterns.append(self.archive_pattern)
        patterns.extend(self.payload_patterns)
        return tuple(patterns)

    def asset_matches(self, name: str) -> bool:
        return any(re.fullmatch(pattern, name) for pattern in self.asset_patterns)


@dataclass(frozen=True)
class AssetInfo:
    """Asset publicado en una release de GitHub."""

    name: str
    size: int
    download_url: str
    digest: str | None = None

    @property
    def sha256(self) -> str | None:
        if self.digest is None:
            return None
        prefix = "sha256:"
        value = self.digest[len(prefix) :] if self.digest.startswith(prefix) else self.digest
        value = value.strip().lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            return None
        return value


@dataclass(frozen=True)
class ReleaseInfo:
    """Release estable normalizada e independiente del JSON de GitHub."""

    component: ComponentId
    tag: str
    version: Version
    name: str
    html_url: str
    published_at: str | None
    body: str
    prerelease: bool
    draft: bool
    assets: tuple[AssetInfo, ...]

    def asset(self, name: str) -> AssetInfo | None:
        return next((asset for asset in self.assets if asset.name == name), None)


@dataclass(frozen=True)
class AssetSelection:
    """Assets elegidos para un componente."""

    component: ComponentId
    archive: AssetInfo | None
    payloads: tuple[AssetInfo, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.archive is not None and not self.missing


@dataclass(frozen=True)
class ComponentCheck:
    """Comparación entre lo instalado y la última release."""

    component: ComponentId
    installed: Version | None
    latest: Version | None
    status: CheckStatus
    release: ReleaseInfo | None = None
    selection: AssetSelection | None = None
    from_cache: bool = False
    checked_at: datetime | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None
    republished: bool = False

    @property
    def update_available(self) -> bool:
        return self.status == CheckStatus.UPDATE_AVAILABLE


@dataclass(frozen=True)
class SdSnapshot:
    """Fotografía offline de una SD. No implica que se haya escrito nada."""

    root: Path
    components: Mapping[ComponentId, InstalledComponent]
    firmware: FirmwareInfo
    is_switch_sd: bool
    has_cfw: bool
    emummc_present: bool
    atmosphere_config_present: bool
    hekate_config_present: bool
    root_payload: Path | None
    reboot_payload: Path | None
    payloads: tuple[Path, ...]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", MappingProxyType(dict(self.components)))

    def component(self, component: ComponentId) -> InstalledComponent:
        return self.components[component]


def _relative(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def asset_to_dict(asset: AssetInfo) -> dict[str, Any]:
    return {
        "name": asset.name,
        "size": asset.size,
        "download_url": asset.download_url,
        "digest": asset.digest,
    }


def release_to_dict(release: ReleaseInfo) -> dict[str, Any]:
    return {
        "component": release.component.value,
        "tag": release.tag,
        "version": str(release.version),
        "name": release.name,
        "html_url": release.html_url,
        "published_at": release.published_at,
        "body": release.body,
        "prerelease": release.prerelease,
        "draft": release.draft,
        "assets": [asset_to_dict(asset) for asset in release.assets],
    }


def release_from_dict(component: ComponentId, data: Mapping[str, Any]) -> ReleaseInfo:
    tag = data.get("tag")
    if not isinstance(tag, str) or not tag:
        raise ValueError("Release sin tag válido")

    raw_assets = data.get("assets", [])
    if not isinstance(raw_assets, list):
        raise ValueError("Release con assets inválidos")

    assets: list[AssetInfo] = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, Mapping):
            raise ValueError("Asset inválido")
        name = raw_asset.get("name")
        size = raw_asset.get("size")
        download_url = raw_asset.get("download_url")
        digest = raw_asset.get("digest")
        if not isinstance(name, str) or not name:
            raise ValueError("Asset sin nombre")
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"Asset con tamaño inválido: {name}")
        if not isinstance(download_url, str) or not download_url:
            raise ValueError(f"Asset sin URL: {name}")
        if digest is not None and not isinstance(digest, str):
            raise ValueError(f"Asset con digest inválido: {name}")
        assets.append(AssetInfo(name=name, size=size, download_url=download_url, digest=digest))

    return ReleaseInfo(
        component=component,
        tag=tag,
        version=Version.parse(tag),
        name=str(data.get("name", tag)),
        html_url=str(data.get("html_url", "")),
        published_at=(str(data["published_at"]) if data.get("published_at") else None),
        body=str(data.get("body", "")),
        prerelease=bool(data.get("prerelease", False)),
        draft=bool(data.get("draft", False)),
        assets=tuple(assets),
    )


def component_check_to_dict(check: ComponentCheck) -> dict[str, Any]:
    selection: dict[str, Any] | None = None
    if check.selection is not None:
        selection = {
            "archive": (
                asset_to_dict(check.selection.archive) if check.selection.archive else None
            ),
            "payloads": [asset_to_dict(asset) for asset in check.selection.payloads],
            "missing": list(check.selection.missing),
        }
    return {
        "component": check.component.value,
        "installed": str(check.installed) if check.installed else None,
        "latest": str(check.latest) if check.latest else None,
        "status": check.status.value,
        "update_available": check.update_available,
        "republished": check.republished,
        "from_cache": check.from_cache,
        "checked_at": check.checked_at.isoformat() if check.checked_at else None,
        "error": check.error,
        "warnings": list(check.warnings),
        "release": release_to_dict(check.release) if check.release else None,
        "selection": selection,
    }


def snapshot_to_dict(snapshot: SdSnapshot) -> dict[str, Any]:
    """Serializa una fotografía para ``--json`` y para logs."""
    components: dict[str, Any] = {}
    for component_id, installed in snapshot.components.items():
        components[component_id.value] = {
            "present": installed.present,
            "version": str(installed.version) if installed.version else None,
            "evidence": installed.evidence,
            "path": _relative(installed.path, snapshot.root),
            "supported_firmware": (
                str(installed.supported_firmware) if installed.supported_firmware else None
            ),
        }

    return {
        "root": str(snapshot.root),
        "is_switch_sd": snapshot.is_switch_sd,
        "has_cfw": snapshot.has_cfw,
        "emummc_present": snapshot.emummc_present,
        "atmosphere_config_present": snapshot.atmosphere_config_present,
        "hekate_config_present": snapshot.hekate_config_present,
        "root_payload": _relative(snapshot.root_payload, snapshot.root),
        "reboot_payload": _relative(snapshot.reboot_payload, snapshot.root),
        "payloads": [_relative(path, snapshot.root) for path in snapshot.payloads],
        "firmware": {
            "version": str(snapshot.firmware.version) if snapshot.firmware.version else None,
            "source": snapshot.firmware.source,
            "ams_version": (
                str(snapshot.firmware.ams_version) if snapshot.firmware.ams_version else None
            ),
        },
        "components": components,
        "warnings": list(snapshot.warnings),
        "errors": list(snapshot.errors),
    }
