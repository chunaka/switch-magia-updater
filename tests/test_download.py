from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from magia_updater.core.cache import CacheStore
from magia_updater.core.download import (
    AssetDownloader,
    DownloadError,
    DownloadVerificationError,
    OfflineDownloadTransport,
    ProgressCallback,
    sha256_file,
    verify_asset,
)
from magia_updater.core.models import AssetInfo


class FakeDownloadTransport:
    def __init__(self, data: bytes, *, error: Exception | None = None) -> None:
        self.data = data
        self.error = error
        self.calls: list[tuple[str, Path, int]] = []

    def download(
        self,
        url: str,
        destination: Path,
        *,
        expected_size: int,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.calls.append((url, destination, expected_size))
        if self.error is not None:
            raise self.error
        destination.write_bytes(self.data)
        if progress is not None:
            progress(len(self.data), expected_size)


def make_asset(
    data: bytes,
    *,
    name: str = "archive.zip",
    size: int | None = None,
    digest: str | None = None,
) -> AssetInfo:
    if digest is None:
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
    return AssetInfo(
        name=name,
        size=len(data) if size is None else size,
        download_url=f"https://example.test/{name}",
        digest=digest,
    )


def test_download_verifies_and_reuses_cache(tmp_path: Path) -> None:
    data = b"contenido del zip"
    asset = make_asset(data)
    transport = FakeDownloadTransport(data)
    downloader = AssetDownloader(CacheStore(tmp_path / "cache"), transport=transport)

    first = downloader.download(asset)
    second = downloader.download(asset)

    assert first.from_cache is False
    assert second.from_cache is True
    assert first.path.is_file()
    assert first.path.read_bytes() == data
    assert first.sha256 == hashlib.sha256(data).hexdigest()
    assert len(transport.calls) == 1


def test_download_rejects_missing_digest(tmp_path: Path) -> None:
    data = b"sin digest"
    asset = make_asset(data, digest="not-a-digest")
    downloader = AssetDownloader(
        CacheStore(tmp_path / "cache"),
        transport=FakeDownloadTransport(data),
    )

    with pytest.raises(DownloadVerificationError, match="digest"):
        downloader.download(asset)


def test_download_rejects_size_mismatch(tmp_path: Path) -> None:
    data = b"corto"
    asset = make_asset(data, size=len(data) + 10)
    cache = CacheStore(tmp_path / "cache")
    downloader = AssetDownloader(cache, transport=FakeDownloadTransport(data))

    with pytest.raises(DownloadVerificationError, match="Tamaño"):
        downloader.download(asset)

    path = cache.asset_path(asset.sha256 or "", asset.name)
    assert not path.exists()
    assert not path.with_name(path.name + ".part").exists()


def test_download_rejects_hash_mismatch(tmp_path: Path) -> None:
    data = b"contenido real"
    wrong_digest = "sha256:" + "0" * 64
    asset = make_asset(data, digest=wrong_digest)
    cache = CacheStore(tmp_path / "cache")
    downloader = AssetDownloader(cache, transport=FakeDownloadTransport(data))

    with pytest.raises(DownloadVerificationError, match="SHA-256"):
        downloader.download(asset)

    assert not cache.asset_path(wrong_digest.removeprefix("sha256:"), asset.name).exists()


def test_corrupt_cache_is_replaced(tmp_path: Path) -> None:
    data = b"contenido correcto"
    asset = make_asset(data)
    cache = CacheStore(tmp_path / "cache")
    path = cache.asset_path(asset.sha256 or "", asset.name)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"corrupto")

    transport = FakeDownloadTransport(data)
    downloader = AssetDownloader(cache, transport=transport)
    result = downloader.download(asset)

    assert result.from_cache is False
    assert path.read_bytes() == data
    assert len(transport.calls) == 1


def test_download_progress_callback(tmp_path: Path) -> None:
    data = b"x" * 100
    asset = make_asset(data)
    seen: list[tuple[int, int]] = []
    downloader = AssetDownloader(
        CacheStore(tmp_path / "cache"),
        transport=FakeDownloadTransport(data),
    )

    downloader.download(asset, progress=lambda done, total: seen.append((done, total)))

    assert seen == [(100, 100)]


def test_download_wraps_transport_error(tmp_path: Path) -> None:
    data = b"data"
    asset = make_asset(data)
    downloader = AssetDownloader(
        CacheStore(tmp_path / "cache"),
        transport=FakeDownloadTransport(data, error=DownloadError("sin red")),
    )

    with pytest.raises(DownloadError, match="sin red"):
        downloader.download(asset)


def test_sha256_file_and_verify_asset(tmp_path: Path) -> None:
    data = b"abc"
    path = tmp_path / "file.bin"
    path.write_bytes(data)
    asset = make_asset(data)

    assert sha256_file(path) == hashlib.sha256(data).hexdigest()
    verify_asset(path, asset)


def test_offline_transport_refuses_missing_asset(tmp_path: Path) -> None:
    data = b"no descargar"
    asset = make_asset(data)
    downloader = AssetDownloader(
        CacheStore(tmp_path / "cache"),
        transport=OfflineDownloadTransport(),
    )

    with pytest.raises(DownloadError, match="offline"):
        downloader.download(asset)


def test_offline_transport_uses_valid_cache(tmp_path: Path) -> None:
    data = b"ya descargado"
    asset = make_asset(data)
    cache = CacheStore(tmp_path / "cache")
    path = cache.asset_path(asset.sha256 or "", asset.name)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    downloader = AssetDownloader(cache, transport=OfflineDownloadTransport())

    result = downloader.download(asset)

    assert result.from_cache is True
    assert result.path == path
