"""Preparación de un componente: descarga, extracción y staging en caché."""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from magia_updater.core.archive import extract_zip_safely
from magia_updater.core.cache import CacheStore
from magia_updater.core.download import AssetDownloader, ProgressCallback
from magia_updater.core.errors import MagiaError
from magia_updater.core.models import ComponentCheck, ComponentId


class PreparationError(MagiaError):
    """No se pudo preparar un componente para el planner."""


@dataclass(frozen=True)
class PreparedComponent:
    check: ComponentCheck
    archive_root: Path
    payloads: Mapping[str, Path]

    @property
    def component(self) -> ComponentId:
        return self.check.component


class UpdatePreparer:
    """Deja el ZIP extraído y los payloads descargados en la caché."""

    def __init__(self, downloader: AssetDownloader, cache: CacheStore) -> None:
        self.downloader = downloader
        self.cache = cache

    def prepare(
        self,
        check: ComponentCheck,
        *,
        progress: ProgressCallback | None = None,
    ) -> PreparedComponent:
        if check.release is None or check.selection is None:
            raise PreparationError(f"No hay release ni selección de assets para {check.component}.")
        archive_asset = check.selection.archive
        if archive_asset is None:
            raise PreparationError(f"{check.component} no tiene un ZIP seleccionado.")
        archive_sha = archive_asset.sha256
        if archive_sha is None:
            raise PreparationError(
                f"El ZIP de {check.component} no tiene digest SHA-256 utilizable."
            )

        archive_result = self.downloader.download(archive_asset, progress=progress)
        marker = self.cache.extraction_marker(archive_sha)
        tree = self.cache.extraction_path(archive_sha)
        if not marker.is_file() or not tree.is_dir():
            if tree.exists():
                shutil.rmtree(tree)
            extract_zip_safely(archive_result.path, tree)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("ok\n", encoding="utf-8")

        payloads: dict[str, Path] = {}
        for asset in check.selection.payloads:
            result = self.downloader.download(asset, progress=progress)
            payloads[asset.name] = result.path

        return PreparedComponent(check=check, archive_root=tree, payloads=payloads)

    def prepare_all(
        self,
        checks: Iterable[ComponentCheck],
        *,
        progress: ProgressCallback | None = None,
    ) -> tuple[PreparedComponent, ...]:
        return tuple(self.prepare(check, progress=progress) for check in checks)
