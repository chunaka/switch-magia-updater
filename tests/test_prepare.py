from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from magia_updater.core.cache import CacheStore
from magia_updater.core.download import AssetDownloader, ProgressCallback
from magia_updater.core.models import (
    AssetInfo,
    AssetSelection,
    CheckStatus,
    ComponentCheck,
    ComponentId,
    ReleaseInfo,
    Version,
)
from magia_updater.core.prepare import PreparationError, UpdatePreparer


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def make_asset(data: bytes, name: str, url: str) -> AssetInfo:
    return AssetInfo(
        name=name,
        size=len(data),
        download_url=url,
        digest="sha256:" + hashlib.sha256(data).hexdigest(),
    )


class MapTransport:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.calls: list[str] = []

    def download(
        self,
        url: str,
        destination: Path,
        *,
        expected_size: int,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.calls.append(url)
        data = self.files[url]
        destination.write_bytes(data)
        if progress is not None:
            progress(len(data), expected_size)


def make_check(
    *,
    archive_data: bytes | None = None,
    payload_data: bytes | None = None,
    archive_digest: str | None = None,
    include_archive: bool = True,
) -> tuple[ComponentCheck, MapTransport]:
    archive_data = archive_data or zip_bytes(
        {
            "atmosphere/package3": b"package3",
            "switch/daybreak.nro": b"daybreak",
        }
    )
    payload_data = payload_data or b"fusee"
    archive_asset = make_asset(
        archive_data,
        "atmosphere-test.zip",
        "https://example.test/atmosphere.zip",
    )
    if archive_digest is not None:
        archive_asset = AssetInfo(
            name=archive_asset.name,
            size=archive_asset.size,
            download_url=archive_asset.download_url,
            digest=archive_digest,
        )
    payload_asset = make_asset(
        payload_data,
        "fusee.bin",
        "https://example.test/fusee.bin",
    )
    release = ReleaseInfo(
        component=ComponentId.ATMOSPHERE,
        tag="1.12.1",
        version=Version.parse("1.12.1"),
        name="Atmosphere 1.12.1",
        html_url="",
        published_at=None,
        body="",
        prerelease=False,
        draft=False,
        assets=(archive_asset, payload_asset),
    )
    selection = AssetSelection(
        component=ComponentId.ATMOSPHERE,
        archive=archive_asset if include_archive else None,
        payloads=(payload_asset,),
    )
    check = ComponentCheck(
        component=ComponentId.ATMOSPHERE,
        installed=Version.parse("1.11.2"),
        latest=Version.parse("1.12.1"),
        status=CheckStatus.UPDATE_AVAILABLE,
        release=release,
        selection=selection,
    )
    transport = MapTransport(
        {
            archive_asset.download_url: archive_data,
            payload_asset.download_url: payload_data,
        }
    )
    return check, transport


def test_prepare_downloads_extracts_and_stages(tmp_path: Path) -> None:
    check, transport = make_check()
    cache = CacheStore(tmp_path / "cache")
    preparer = UpdatePreparer(AssetDownloader(cache, transport=transport), cache)

    prepared = preparer.prepare(check)

    assert prepared.component == ComponentId.ATMOSPHERE
    assert (prepared.archive_root / "atmosphere/package3").read_bytes() == b"package3"
    assert (prepared.archive_root / "switch/daybreak.nro").read_bytes() == b"daybreak"
    assert prepared.payloads["fusee.bin"].read_bytes() == b"fusee"
    assert cache.extraction_marker(check.selection.archive.sha256 or "").is_file()  # type: ignore[union-attr]
    assert len(transport.calls) == 2


def test_prepare_reuses_cache(tmp_path: Path) -> None:
    check, transport = make_check()
    cache = CacheStore(tmp_path / "cache")
    preparer = UpdatePreparer(AssetDownloader(cache, transport=transport), cache)

    preparer.prepare(check)
    preparer.prepare(check)

    assert len(transport.calls) == 2


def test_prepare_reextracts_if_marker_is_missing(tmp_path: Path) -> None:
    check, transport = make_check()
    cache = CacheStore(tmp_path / "cache")
    preparer = UpdatePreparer(AssetDownloader(cache, transport=transport), cache)
    prepared = preparer.prepare(check)
    sha = check.selection.archive.sha256  # type: ignore[union-attr]
    marker = cache.extraction_marker(sha)
    marker.unlink()
    (prepared.archive_root / "basura.txt").write_text("basura", encoding="utf-8")

    prepared_again = preparer.prepare(check)

    assert not (prepared_again.archive_root / "basura.txt").exists()
    assert (prepared_again.archive_root / "atmosphere/package3").is_file()


def test_prepare_rejects_missing_selection(tmp_path: Path) -> None:
    check, transport = make_check()
    check = ComponentCheck(
        component=check.component,
        installed=check.installed,
        latest=check.latest,
        status=check.status,
        release=check.release,
        selection=None,
    )
    cache = CacheStore(tmp_path / "cache")
    preparer = UpdatePreparer(AssetDownloader(cache, transport=transport), cache)

    with pytest.raises(PreparationError, match="selección"):
        preparer.prepare(check)


def test_prepare_rejects_missing_archive(tmp_path: Path) -> None:
    check, transport = make_check(include_archive=False)
    cache = CacheStore(tmp_path / "cache")
    preparer = UpdatePreparer(AssetDownloader(cache, transport=transport), cache)

    with pytest.raises(PreparationError, match="ZIP"):
        preparer.prepare(check)


def test_prepare_rejects_missing_archive_digest(tmp_path: Path) -> None:
    check, transport = make_check(archive_digest="invalido")
    cache = CacheStore(tmp_path / "cache")
    preparer = UpdatePreparer(AssetDownloader(cache, transport=transport), cache)

    with pytest.raises(PreparationError, match="digest"):
        preparer.prepare(check)
