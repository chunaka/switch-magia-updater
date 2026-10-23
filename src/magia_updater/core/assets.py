"""Selección determinista de assets de una release."""

from __future__ import annotations

import re

from magia_updater.core.errors import MagiaError
from magia_updater.core.models import (
    AssetInfo,
    AssetSelection,
    ComponentDefinition,
    ReleaseInfo,
)


class AssetSelectionError(MagiaError):
    """La release no contiene exactamente los assets esperados."""


def _select_one(pattern: str, release: ReleaseInfo) -> AssetInfo | None:
    matches = [asset for asset in release.assets if re.fullmatch(pattern, asset.name) is not None]
    if len(matches) > 1:
        names = ", ".join(asset.name for asset in matches)
        raise AssetSelectionError(
            f"La release {release.tag} tiene más de un asset para {pattern!r}: {names}"
        )
    return matches[0] if matches else None


def select_assets(definition: ComponentDefinition, release: ReleaseInfo) -> AssetSelection:
    """Elige el ZIP y los payloads. Falla si falta un asset requerido o hay ambigüedad."""
    if release.component != definition.id:
        raise AssetSelectionError(
            f"La release pertenece a {release.component}, no a {definition.id}."
        )

    if definition.informational or not definition.archive_pattern:
        return AssetSelection(component=definition.id, archive=None)

    missing: list[str] = []
    archive = _select_one(definition.archive_pattern, release)
    if archive is None:
        missing.append(definition.archive_pattern)

    payloads: list[AssetInfo] = []
    for pattern in definition.payload_patterns:
        payload = _select_one(pattern, release)
        if payload is None:
            missing.append(pattern)
        else:
            payloads.append(payload)

    if missing:
        patterns = ", ".join(repr(pattern) for pattern in missing)
        raise AssetSelectionError(
            f"La release {release.tag} no contiene los assets requeridos: {patterns}"
        )

    return AssetSelection(
        component=definition.id,
        archive=archive,
        payloads=tuple(payloads),
    )
