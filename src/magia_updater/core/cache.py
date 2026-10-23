"""Caché local de metadatos de releases y assets."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from magia_updater.core.models import (
    ComponentId,
    ReleaseInfo,
    release_from_dict,
    release_to_dict,
)

CACHE_SCHEMA = 1
DEFAULT_MAX_AGE = timedelta(hours=6)


def default_cache_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "MagiaUpdater" / "cache"
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "magia-updater"
    return Path.home() / ".cache" / "magia-updater"


@dataclass(frozen=True)
class CachedRelease:
    release: ReleaseInfo
    etag: str | None
    fetched_at: datetime


class CacheStore:
    """Guarda una release por componente en JSON, con escritura atómica."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_cache_root()

    def release_path(self, component: ComponentId) -> Path:
        return self.root / "releases" / f"{component.value}.json"

    def load_release(self, component: ComponentId) -> CachedRelease | None:
        path = self.release_path(component)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, Mapping) or raw.get("schema") != CACHE_SCHEMA:
            return None

        release_data = raw.get("release")
        if not isinstance(release_data, Mapping):
            return None
        try:
            release = release_from_dict(component, release_data)
        except (ValueError, TypeError):
            return None

        fetched_raw = raw.get("fetched_at")
        try:
            fetched_at = datetime.fromisoformat(str(fetched_raw))
        except (TypeError, ValueError):
            return None
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)

        etag_raw = raw.get("etag")
        etag = etag_raw if isinstance(etag_raw, str) and etag_raw else None
        return CachedRelease(release=release, etag=etag, fetched_at=fetched_at)

    def save_release(
        self,
        component: ComponentId,
        release: ReleaseInfo,
        *,
        etag: str | None,
        fetched_at: datetime | None = None,
    ) -> CachedRelease:
        fetched = fetched_at if fetched_at is not None else datetime.now(UTC)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)

        data: dict[str, Any] = {
            "schema": CACHE_SCHEMA,
            "component": component.value,
            "etag": etag,
            "fetched_at": fetched.isoformat(),
            "release": release_to_dict(release),
        }
        path = self.release_path(component)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

        return CachedRelease(release=release, etag=etag, fetched_at=fetched)

    def is_fresh(
        self,
        cached: CachedRelease,
        *,
        max_age: timedelta = DEFAULT_MAX_AGE,
        now: datetime | None = None,
    ) -> bool:
        current = now if now is not None else datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        age = current - cached.fetched_at
        return age <= max_age

    def asset_path(self, sha256: str, filename: str) -> Path:
        normalized = _normalize_sha256(sha256)
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError(f"Nombre de asset inválido: {filename!r}")
        return self.root / "assets" / normalized / safe_name

    def extraction_dir(self, sha256: str) -> Path:
        return self.root / "extracted" / _normalize_sha256(sha256)

    def extraction_path(self, sha256: str) -> Path:
        return self.extraction_dir(sha256) / "tree"

    def extraction_marker(self, sha256: str) -> Path:
        return self.extraction_dir(sha256) / "complete"

    def clear(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)


def _normalize_sha256(sha256: str) -> str:
    normalized = sha256.strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError(f"SHA-256 inválido: {sha256!r}")
    return normalized
