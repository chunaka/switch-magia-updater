from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers import load_release
from magia_updater.core.cache import CACHE_SCHEMA, CacheStore
from magia_updater.core.models import ComponentId


def test_cache_roundtrip(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache")
    release = load_release("atmosphere", ComponentId.ATMOSPHERE)
    fetched = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)

    cache.save_release(
        ComponentId.ATMOSPHERE,
        release,
        etag='"etag"',
        fetched_at=fetched,
    )
    loaded = cache.load_release(ComponentId.ATMOSPHERE)

    assert loaded is not None
    assert loaded.release.tag == "1.12.1"
    assert loaded.release.assets[0].sha256 is not None
    assert loaded.etag == '"etag"'
    assert loaded.fetched_at == fetched
    assert cache.release_path(ComponentId.ATMOSPHERE).is_file()
    assert not cache.release_path(ComponentId.ATMOSPHERE).with_suffix(".json.tmp").exists()


def test_cache_freshness(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache")
    release = load_release("hekate", ComponentId.HEKATE)
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    cached = cache.save_release(
        ComponentId.HEKATE,
        release,
        etag=None,
        fetched_at=now,
    )

    assert cache.is_fresh(cached, now=now + timedelta(hours=1)) is True
    assert cache.is_fresh(cached, now=now + timedelta(hours=7)) is False


@pytest.mark.parametrize(
    "content",
    [
        "no es json",
        '{"schema": 2, "release": {}}',
        '{"schema": 1, "release": {}}',
        '{"schema": 1, "fetched_at": "no-fecha", "release": {"tag": "1.0.0", "assets": []}}',
    ],
)
def test_cache_ignores_invalid_files(tmp_path: Path, content: str) -> None:
    cache = CacheStore(tmp_path / "cache")
    path = cache.release_path(ComponentId.ATMOSPHERE)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    assert cache.load_release(ComponentId.ATMOSPHERE) is None


def test_cache_schema_constant_is_used(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache")
    release = load_release("sys-patch", ComponentId.SYS_PATCH)
    cache.save_release(ComponentId.SYS_PATCH, release, etag=None)

    raw = cache.release_path(ComponentId.SYS_PATCH).read_text(encoding="utf-8")
    assert f'"schema": {CACHE_SCHEMA}' in raw


def test_asset_path_is_safe(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache")
    digest = "a" * 64

    path = cache.asset_path(digest, "../evil.bin")

    assert path == cache.root / "assets" / digest / "evil.bin"
    with pytest.raises(ValueError):
        cache.asset_path("no-es-sha", "archivo.zip")


def test_cache_clear(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache")
    release = load_release("atmosphere", ComponentId.ATMOSPHERE)
    cache.save_release(ComponentId.ATMOSPHERE, release, etag=None)
    assert cache.root.exists()

    cache.clear()
    assert not cache.root.exists()
