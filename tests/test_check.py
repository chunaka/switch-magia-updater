from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from helpers import (
    FakeTransport,
    json_response,
    json_response_data,
    load_release,
    load_release_data,
)
from magia_updater.core.cache import CacheStore
from magia_updater.core.check import ReleaseService
from magia_updater.core.github import GitHubClient, GitHubNetworkError, HttpResponse
from magia_updater.core.models import (
    CheckStatus,
    ComponentId,
    InstalledComponent,
    Version,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def installed(component: ComponentId, version: str) -> InstalledComponent:
    return InstalledComponent(
        component=component,
        present=True,
        version=Version.parse(version),
    )


def make_service(
    tmp_path: Path,
    transport: FakeTransport,
    *,
    max_age: timedelta = timedelta(hours=6),
) -> ReleaseService:
    return ReleaseService(
        GitHubClient(transport=transport),
        CacheStore(tmp_path / "cache"),
        max_age=max_age,
        now=lambda: NOW,
    )


def test_update_available(tmp_path: Path) -> None:
    transport = FakeTransport(json_response("atmosphere", etag='"new"'))
    service = make_service(tmp_path, transport)

    check = service.check_component(
        ComponentId.ATMOSPHERE,
        installed(ComponentId.ATMOSPHERE, "1.11.2"),
    )

    assert check.status == CheckStatus.UPDATE_AVAILABLE
    assert str(check.installed) == "1.11.2"
    assert str(check.latest) == "1.12.1"
    assert check.selection is not None
    assert check.selection.archive is not None
    assert check.from_cache is False
    assert check.checked_at == NOW
    assert service.cache.load_release(ComponentId.ATMOSPHERE) is not None


def test_up_to_date(tmp_path: Path) -> None:
    transport = FakeTransport(json_response("hekate"))
    service = make_service(tmp_path, transport)

    check = service.check_component(
        ComponentId.HEKATE,
        installed(ComponentId.HEKATE, "6.5.4"),
    )

    assert check.status == CheckStatus.UP_TO_DATE
    assert check.republished is False


def test_not_installed(tmp_path: Path) -> None:
    transport = FakeTransport(json_response("sys-patch"))
    service = make_service(tmp_path, transport)

    check = service.check_component(
        ComponentId.SYS_PATCH,
        InstalledComponent(component=ComponentId.SYS_PATCH),
    )

    assert check.status == CheckStatus.NOT_INSTALLED
    assert check.installed is None
    assert str(check.latest) == "1.6.3.0"


def test_installed_newer_does_not_downgrade(tmp_path: Path) -> None:
    transport = FakeTransport(json_response("atmosphere"))
    service = make_service(tmp_path, transport)

    check = service.check_component(
        ComponentId.ATMOSPHERE,
        installed(ComponentId.ATMOSPHERE, "1.13.0"),
    )

    assert check.status == CheckStatus.INSTALLED_NEWER
    assert any("no se bajará" in warning for warning in check.warnings)


def test_unknown_installed_version(tmp_path: Path) -> None:
    transport = FakeTransport(json_response("atmosphere"))
    service = make_service(tmp_path, transport)

    check = service.check_component(
        ComponentId.ATMOSPHERE,
        InstalledComponent(component=ComponentId.ATMOSPHERE, present=True),
    )

    assert check.status == CheckStatus.UNKNOWN
    assert check.error is not None
    assert "no se pudo detectar" in check.error


def test_republished_same_tag_is_update(tmp_path: Path) -> None:
    release = load_release("atmosphere", ComponentId.ATMOSPHERE)
    old_assets = tuple(
        replace(asset, digest="sha256:" + "0" * 64) if asset.name.endswith(".zip") else asset
        for asset in release.assets
    )
    old_release = replace(release, assets=old_assets)
    cache = CacheStore(tmp_path / "cache")
    cache.save_release(
        ComponentId.ATMOSPHERE,
        old_release,
        etag='"old"',
        fetched_at=NOW - timedelta(days=1),
    )

    service = ReleaseService(
        GitHubClient(transport=FakeTransport(json_response("atmosphere", etag='"new"'))),
        cache,
        now=lambda: NOW,
    )
    check = service.check_component(
        ComponentId.ATMOSPHERE,
        installed(ComponentId.ATMOSPHERE, "1.12.1"),
        force=True,
    )

    assert check.status == CheckStatus.UPDATE_AVAILABLE
    assert check.republished is True
    assert str(check.latest) == "1.12.1"
    assert any("re-subida" in warning for warning in check.warnings)


def test_offline_without_cache(tmp_path: Path) -> None:
    service = make_service(tmp_path, FakeTransport(json_response("atmosphere")))

    check = service.check_component(
        ComponentId.ATMOSPHERE,
        installed(ComponentId.ATMOSPHERE, "1.11.2"),
        offline=True,
    )

    assert check.status == CheckStatus.UNKNOWN
    assert check.error is not None
    assert "caché" in check.error


def test_offline_with_cache(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache")
    cache.save_release(
        ComponentId.ATMOSPHERE,
        load_release("atmosphere", ComponentId.ATMOSPHERE),
        etag='"etag"',
        fetched_at=NOW - timedelta(days=1),
    )
    service = ReleaseService(
        GitHubClient(transport=FakeTransport(error=AssertionError("no debe llamar"))),
        cache,
        now=lambda: NOW,
    )

    check = service.check_component(
        ComponentId.ATMOSPHERE,
        installed(ComponentId.ATMOSPHERE, "1.11.2"),
        offline=True,
    )

    assert check.status == CheckStatus.UPDATE_AVAILABLE
    assert check.from_cache is True
    assert any("Modo offline" in warning for warning in check.warnings)


def test_fresh_cache_skips_network(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache")
    cache.save_release(
        ComponentId.ATMOSPHERE,
        load_release("atmosphere", ComponentId.ATMOSPHERE),
        etag='"etag"',
        fetched_at=NOW,
    )
    service = ReleaseService(
        GitHubClient(transport=FakeTransport(error=AssertionError("no debe llamar"))),
        cache,
        now=lambda: NOW,
    )

    check = service.check_component(
        ComponentId.ATMOSPHERE,
        installed(ComponentId.ATMOSPHERE, "1.12.1"),
    )

    assert check.status == CheckStatus.UP_TO_DATE
    assert check.from_cache is True


def test_network_error_falls_back_to_cache(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache")
    cache.save_release(
        ComponentId.ATMOSPHERE,
        load_release("atmosphere", ComponentId.ATMOSPHERE),
        etag='"etag"',
        fetched_at=NOW - timedelta(days=1),
    )
    service = ReleaseService(
        GitHubClient(transport=FakeTransport(error=GitHubNetworkError("sin red"))),
        cache,
        now=lambda: NOW,
    )

    check = service.check_component(
        ComponentId.ATMOSPHERE,
        installed(ComponentId.ATMOSPHERE, "1.11.2"),
        force=True,
    )

    assert check.status == CheckStatus.UPDATE_AVAILABLE
    assert check.from_cache is True
    assert any("sin red" in warning for warning in check.warnings)


def test_304_touches_cache(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache")
    cache.save_release(
        ComponentId.ATMOSPHERE,
        load_release("atmosphere", ComponentId.ATMOSPHERE),
        etag='"etag"',
        fetched_at=NOW - timedelta(hours=1),
    )
    service = ReleaseService(
        GitHubClient(transport=FakeTransport(HttpResponse(status=304, headers={}, body=b""))),
        cache,
        now=lambda: NOW,
    )

    check = service.check_component(
        ComponentId.ATMOSPHERE,
        installed(ComponentId.ATMOSPHERE, "1.12.1"),
        force=True,
    )

    assert check.status == CheckStatus.UP_TO_DATE
    assert check.from_cache is True
    loaded = cache.load_release(ComponentId.ATMOSPHERE)
    assert loaded is not None
    assert loaded.fetched_at == NOW


def test_missing_assets_returns_unknown(tmp_path: Path) -> None:
    data = load_release_data("atmosphere")
    data["assets"] = [asset for asset in data["assets"] if not asset["name"].endswith(".zip")]
    service = make_service(
        tmp_path,
        FakeTransport(json_response_data(data)),
    )

    check = service.check_component(
        ComponentId.ATMOSPHERE,
        installed(ComponentId.ATMOSPHERE, "1.11.2"),
    )

    assert check.status == CheckStatus.UNKNOWN
    assert check.error is not None
    assert "no contiene" in check.error


def test_prerelease_returns_unknown(tmp_path: Path) -> None:
    data = load_release_data("atmosphere")
    data["prerelease"] = True
    service = make_service(tmp_path, FakeTransport(json_response_data(data)))

    check = service.check_component(
        ComponentId.ATMOSPHERE,
        installed(ComponentId.ATMOSPHERE, "1.11.2"),
    )

    assert check.status == CheckStatus.UNKNOWN
    assert check.error is not None
    assert "preliminar" in check.error
