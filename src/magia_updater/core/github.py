"""Cliente mínimo de la API de GitHub con transporte inyectable para tests."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from magia_updater import __version__
from magia_updater.core.errors import MagiaError
from magia_updater.core.models import AssetInfo, ComponentId, ReleaseInfo, Version

API_VERSION = "2022-11-28"
DEFAULT_BASE_URL = "https://api.github.com"
USER_AGENT = f"magia-updater/{__version__}"


class GitHubError(MagiaError):
    """Error al consultar GitHub."""


class GitHubNetworkError(GitHubError):
    """No se pudo conectar con GitHub."""


class GitHubNotFoundError(GitHubError):
    """El repositorio o la release no existe."""


class GitHubRateLimitError(GitHubError):
    """GitHub rechazó la consulta por límite de peticiones."""

    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class ReleaseParseError(GitHubError):
    """La release no tiene el formato esperado."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse: ...


class UrllibTransport:
    """Transporte real basado en la biblioteca estándar."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return HttpResponse(
                    status=int(response.status),
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            response_headers = dict(exc.headers.items()) if exc.headers is not None else {}
            return HttpResponse(status=int(exc.code), headers=response_headers, body=exc.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubNetworkError(f"No se pudo conectar con GitHub: {exc}") from exc


@dataclass(frozen=True)
class ReleaseResponse:
    status: int
    etag: str | None
    data: Mapping[str, Any] | None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


def _rate_limit_reset(headers: Mapping[str, str]) -> datetime | None:
    raw = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


class GitHubClient:
    """Consulta la última release estable de un repositorio."""

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
    ) -> None:
        self._transport = transport if transport is not None else UrllibTransport(timeout)
        self._token = token
        self._base_url = base_url.rstrip("/")

    def get_latest_release(self, repo: str, *, etag: str | None = None) -> ReleaseResponse:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if etag:
            headers["If-None-Match"] = etag

        url = f"{self._base_url}/repos/{repo}/releases/latest"
        response = self._transport.get(url, headers)

        if response.status == 304:
            return ReleaseResponse(status=304, etag=etag, data=None)

        if response.status in (403, 429):
            reset_at = _rate_limit_reset(response.headers)
            detail = response.body.decode("utf-8", errors="replace")[:500]
            message = "GitHub rechazó la consulta (límite de peticiones o permisos)."
            if reset_at is not None:
                message += f" Se puede reintentar después de {reset_at.isoformat()}."
            if detail:
                message += f" Detalle: {detail}"
            raise GitHubRateLimitError(message, reset_at=reset_at)

        if response.status == 404:
            raise GitHubNotFoundError(f"No se encontró el repositorio o la release: {repo}")

        if response.status >= 400:
            detail = response.body.decode("utf-8", errors="replace")[:500]
            raise GitHubError(f"GitHub respondió HTTP {response.status}: {detail}")

        try:
            data = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubError("GitHub devolvió una respuesta JSON inválida.") from exc
        if not isinstance(data, Mapping):
            raise GitHubError("GitHub devolvió un objeto JSON inesperado.")

        return ReleaseResponse(
            status=200,
            etag=_header(response.headers, "ETag"),
            data=data,
        )


def parse_release(component: ComponentId, data: Mapping[str, Any]) -> ReleaseInfo:
    """Normaliza el JSON de GitHub y valida los campos que usa el actualizador."""
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise ReleaseParseError("La release no tiene tag_name.")
    try:
        version = Version.parse(tag)
    except ValueError as exc:
        raise ReleaseParseError(f"Tag de release inválido: {tag!r}") from exc

    raw_assets = data.get("assets")
    if not isinstance(raw_assets, list):
        raise ReleaseParseError(f"La release {tag} no tiene una lista de assets.")

    assets: list[AssetInfo] = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, Mapping):
            raise ReleaseParseError(f"Asset inválido en la release {tag}.")
        name = raw_asset.get("name")
        size = raw_asset.get("size")
        url = raw_asset.get("browser_download_url")
        digest = raw_asset.get("digest")
        if not isinstance(name, str) or not name:
            raise ReleaseParseError(f"Asset sin nombre en la release {tag}.")
        if not isinstance(size, int) or size < 0:
            raise ReleaseParseError(f"Asset con tamaño inválido: {name}")
        if not isinstance(url, str) or not url:
            raise ReleaseParseError(f"Asset sin URL de descarga: {name}")
        if digest is not None and not isinstance(digest, str):
            raise ReleaseParseError(f"Asset con digest inválido: {name}")
        assets.append(AssetInfo(name=name, size=size, download_url=url, digest=digest))

    published_at = data.get("published_at")
    return ReleaseInfo(
        component=component,
        tag=tag,
        version=version,
        name=str(data.get("name") or tag),
        html_url=str(data.get("html_url") or ""),
        published_at=str(published_at) if published_at else None,
        body=str(data.get("body") or ""),
        prerelease=bool(data.get("prerelease", False)),
        draft=bool(data.get("draft", False)),
        assets=tuple(assets),
    )
