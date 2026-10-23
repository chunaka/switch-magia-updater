"""Descarga de assets a la caché con verificación de tamaño y SHA-256."""

from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from magia_updater.core.cache import CacheStore
from magia_updater.core.errors import MagiaError
from magia_updater.core.github import USER_AGENT
from magia_updater.core.models import AssetInfo

ProgressCallback = Callable[[int, int], None]
DEFAULT_CHUNK_SIZE = 1024 * 1024


class DownloadError(MagiaError):
    """No se pudo descargar un asset."""


class DownloadVerificationError(DownloadError):
    """El asset descargado no coincide con lo publicado."""


def sha256_file(path: Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class DownloadTransport(Protocol):
    def download(
        self,
        url: str,
        destination: Path,
        *,
        expected_size: int,
        progress: ProgressCallback | None = None,
    ) -> None: ...


class UrllibDownloadTransport:
    """Transporte real, con escritura en streaming y callback de progreso."""

    def __init__(self, *, timeout: float = 60.0, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        self.timeout = timeout
        self.chunk_size = chunk_size

    def download(
        self,
        url: str,
        destination: Path,
        *,
        expected_size: int,
        progress: ProgressCallback | None = None,
    ) -> None:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content_length = response.headers.get("Content-Length")
                total = expected_size
                if content_length is not None and content_length.isdigit():
                    total = int(content_length)
                downloaded = 0
                with destination.open("wb") as stream:
                    while chunk := response.read(self.chunk_size):
                        stream.write(chunk)
                        downloaded += len(chunk)
                        if progress is not None:
                            progress(downloaded, total)
        except urllib.error.HTTPError as exc:
            raise DownloadError(f"GitHub respondió HTTP {exc.code} al descargar {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DownloadError(f"No se pudo descargar {url}: {exc}") from exc


class OfflineDownloadTransport:
    """Transporte que falla si el asset no está ya en la caché."""

    def download(
        self,
        url: str,
        destination: Path,
        *,
        expected_size: int,
        progress: ProgressCallback | None = None,
    ) -> None:
        raise DownloadError(f"Modo offline: el asset no está en la caché ({url}).")


@dataclass(frozen=True)
class DownloadResult:
    asset: AssetInfo
    path: Path
    size: int
    sha256: str
    from_cache: bool


def verify_asset(path: Path, asset: AssetInfo) -> None:
    """Verifica tamaño y SHA-256. Lanza ``DownloadVerificationError`` si falla."""
    expected_sha = asset.sha256
    if expected_sha is None:
        raise DownloadVerificationError(
            f"El asset {asset.name!r} no tiene un digest SHA-256 utilizable."
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DownloadVerificationError(f"No se pudo leer {path}: {exc}") from exc
    if size != asset.size:
        raise DownloadVerificationError(
            f"Tamaño incorrecto para {asset.name}: esperado {asset.size}, obtenido {size}."
        )
    actual_sha = sha256_file(path)
    if actual_sha != expected_sha:
        raise DownloadVerificationError(
            f"SHA-256 incorrecto para {asset.name}: esperado {expected_sha}, obtenido {actual_sha}."
        )


class AssetDownloader:
    """Descarga assets a la caché y evita repetir descargas válidas."""

    def __init__(
        self,
        cache: CacheStore,
        *,
        transport: DownloadTransport | None = None,
    ) -> None:
        self.cache = cache
        self.transport = transport if transport is not None else UrllibDownloadTransport()

    def download(
        self,
        asset: AssetInfo,
        *,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        expected_sha = asset.sha256
        if expected_sha is None:
            raise DownloadVerificationError(
                f"El asset {asset.name!r} no tiene un digest SHA-256 utilizable."
            )

        path = self.cache.asset_path(expected_sha, asset.name)
        if path.is_file():
            try:
                verify_asset(path, asset)
            except DownloadVerificationError:
                path.unlink(missing_ok=True)
            else:
                return DownloadResult(
                    asset=asset,
                    path=path,
                    size=asset.size,
                    sha256=expected_sha,
                    from_cache=True,
                )

        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        try:
            self.transport.download(
                asset.download_url,
                partial,
                expected_size=asset.size,
                progress=progress,
            )
            verify_asset(partial, asset)
            os.replace(partial, path)
        finally:
            partial.unlink(missing_ok=True)

        return DownloadResult(
            asset=asset,
            path=path,
            size=asset.size,
            sha256=expected_sha,
            from_cache=False,
        )
