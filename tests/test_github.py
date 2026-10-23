from __future__ import annotations

import pytest

from helpers import FakeTransport, json_response, load_release, load_release_data
from magia_updater.core.github import (
    GitHubClient,
    GitHubError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    HttpResponse,
    ReleaseParseError,
    parse_release,
)
from magia_updater.core.models import ComponentId


def test_parse_real_atmosphere_release() -> None:
    release = load_release("atmosphere", ComponentId.ATMOSPHERE)

    assert release.tag == "1.12.1"
    assert str(release.version) == "1.12.1"
    assert release.assets[0].name.startswith("atmosphere-1.12.1")
    assert release.assets[0].sha256 == (
        "f42161b3bce11442ea487eb84811013cf0e5ba571a7964323e206990b341a297"
    )
    assert release.asset("fusee.bin") is not None
    assert release.asset("no-existe.bin") is None


def test_parse_real_hekate_and_sys_patch_releases() -> None:
    hekate = load_release("hekate", ComponentId.HEKATE)
    sys_patch = load_release("sys-patch", ComponentId.SYS_PATCH)

    assert str(hekate.version) == "6.5.4"
    assert hekate.asset("hekate_ctcaer_6.5.4.bin") is not None
    assert str(sys_patch.version) == "1.6.3.0"
    assert sys_patch.asset("sys-patch-v1.6.3.0.zip") is not None


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.pop("tag_name"), "tag_name"),
        (lambda data: data.update(tag_name="not-a-version"), "Tag"),
        (lambda data: data.pop("assets"), "assets"),
        (lambda data: data["assets"][0].update(size="grande"), "tamaño"),
        (lambda data: data["assets"][0].pop("browser_download_url"), "URL"),
        (lambda data: data["assets"][0].update(digest=123), "digest"),
    ],
)
def test_parse_release_rejects_invalid_payloads(mutation, message: str) -> None:
    data = load_release_data("atmosphere")
    mutation(data)
    with pytest.raises(ReleaseParseError, match=message):
        parse_release(ComponentId.ATMOSPHERE, data)


def test_client_uses_etag_and_parses_response() -> None:
    transport = FakeTransport(json_response("atmosphere", etag='"abc"'))
    client = GitHubClient(transport=transport, token="secret")

    response = client.get_latest_release("Atmosphere-NX/Atmosphere", etag='"old"')

    assert response.status == 200
    assert response.etag == '"abc"'
    assert response.data is not None
    assert transport.calls
    url, headers = transport.calls[0]
    assert url.endswith("/repos/Atmosphere-NX/Atmosphere/releases/latest")
    assert headers["If-None-Match"] == '"old"'
    assert headers["Authorization"] == "Bearer secret"
    assert headers["Accept"] == "application/vnd.github+json"


def test_client_not_modified() -> None:
    transport = FakeTransport(HttpResponse(status=304, headers={}, body=b""))
    client = GitHubClient(transport=transport)

    response = client.get_latest_release("CTCaer/hekate", etag='"etag"')

    assert response.not_modified is True
    assert response.etag == '"etag"'
    assert transport.calls[0][1]["If-None-Match"] == '"etag"'


def test_client_rate_limit() -> None:
    transport = FakeTransport(
        HttpResponse(
            status=403,
            headers={"X-RateLimit-Reset": "1700000000"},
            body=b'{"message":"API rate limit exceeded"}',
        )
    )
    client = GitHubClient(transport=transport)

    with pytest.raises(GitHubRateLimitError) as exc:
        client.get_latest_release("Atmosphere-NX/Atmosphere")

    assert exc.value.reset_at is not None
    assert "límite" in str(exc.value)


def test_client_not_found() -> None:
    client = GitHubClient(transport=FakeTransport(HttpResponse(status=404, headers={}, body=b"{}")))
    with pytest.raises(GitHubNotFoundError):
        client.get_latest_release("no/existe")


def test_client_server_error() -> None:
    client = GitHubClient(
        transport=FakeTransport(HttpResponse(status=500, headers={}, body=b"boom"))
    )
    with pytest.raises(GitHubError, match="HTTP 500"):
        client.get_latest_release("Atmosphere-NX/Atmosphere")


def test_client_invalid_json() -> None:
    client = GitHubClient(
        transport=FakeTransport(HttpResponse(status=200, headers={}, body=b"no-json"))
    )
    with pytest.raises(GitHubError, match="JSON"):
        client.get_latest_release("Atmosphere-NX/Atmosphere")


def test_client_propagates_network_error() -> None:
    client = GitHubClient(transport=FakeTransport(error=GitHubNetworkError("sin conexión")))
    with pytest.raises(GitHubNetworkError, match="sin conexión"):
        client.get_latest_release("Atmosphere-NX/Atmosphere")
