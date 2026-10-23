from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from magia_updater.core.github import HttpResponse, parse_release
from magia_updater.core.models import ComponentId, ReleaseInfo

FIXTURES = Path(__file__).parent / "fixtures"


def load_release_data(name: str) -> dict[str, Any]:
    path = FIXTURES / "releases" / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def load_release(name: str, component: ComponentId) -> ReleaseInfo:
    return parse_release(component, load_release_data(name))


def json_response_data(
    data: Mapping[str, Any],
    *,
    status: int = 200,
    etag: str | None = '"fixture-etag"',
) -> HttpResponse:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if etag is not None:
        headers["ETag"] = etag
    return HttpResponse(
        status=status,
        headers=headers,
        body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
    )


def json_response(
    name: str,
    *,
    status: int = 200,
    etag: str | None = '"fixture-etag"',
) -> HttpResponse:
    return json_response_data(load_release_data(name), status=status, etag=etag)


class FakeTransport:
    def __init__(
        self,
        response: HttpResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        self.calls.append((url, dict(headers)))
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("FakeTransport sin respuesta configurada")
        return self.response
