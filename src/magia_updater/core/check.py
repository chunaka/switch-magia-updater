"""Comparación de lo instalado contra las últimas releases de GitHub."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from magia_updater.core.assets import AssetSelectionError, select_assets
from magia_updater.core.cache import DEFAULT_MAX_AGE, CacheStore
from magia_updater.core.components import UPDATABLE_COMPONENTS, get_component
from magia_updater.core.github import GitHubClient, GitHubError, parse_release
from magia_updater.core.models import (
    CheckStatus,
    ComponentCheck,
    ComponentDefinition,
    ComponentId,
    InstalledComponent,
    ReleaseInfo,
    SdSnapshot,
    Version,
)


def _asset_signature(
    definition: ComponentDefinition, release: ReleaseInfo
) -> dict[str, tuple[int, str | None]] | None:
    try:
        selection = select_assets(definition, release)
    except AssetSelectionError:
        return None
    assets = []
    if selection.archive is not None:
        assets.append(selection.archive)
    assets.extend(selection.payloads)
    return {asset.name: (asset.size, asset.sha256) for asset in assets}


def _was_republished(
    definition: ComponentDefinition,
    previous: ReleaseInfo | None,
    current: ReleaseInfo,
) -> bool:
    if previous is None or previous.tag != current.tag:
        return False
    old_signature = _asset_signature(definition, previous)
    new_signature = _asset_signature(definition, current)
    if old_signature is None or new_signature is None:
        return False
    if set(old_signature) != set(new_signature):
        return True
    for name, (old_size, old_digest) in old_signature.items():
        new_size, new_digest = new_signature[name]
        if old_size != new_size:
            return True
        if old_digest is not None and new_digest is not None and old_digest != new_digest:
            return True
    return False


class ReleaseService:
    """Consulta releases con caché y ETag; cae a caché si no hay red."""

    def __init__(
        self,
        client: GitHubClient,
        cache: CacheStore,
        *,
        max_age: timedelta = DEFAULT_MAX_AGE,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.cache = cache
        self.max_age = max_age
        self._now = now if now is not None else lambda: datetime.now(UTC)

    def check_component(
        self,
        component: ComponentId,
        installed: InstalledComponent,
        *,
        offline: bool = False,
        force: bool = False,
    ) -> ComponentCheck:
        definition = get_component(component)
        now = self._now()
        cached = self.cache.load_release(component)
        previous_release = cached.release if cached is not None else None
        warnings: list[str] = []
        from_cache = False
        release: ReleaseInfo | None = None

        if offline:
            if cached is None:
                return self._unknown(
                    component,
                    installed,
                    now,
                    error="No hay datos en caché para este componente.",
                    warnings=("Modo offline sin caché disponible.",),
                )
            release = cached.release
            from_cache = True
            warnings.append(
                "Modo offline: se usan datos de caché"
                f" ({cached.fetched_at.isoformat(timespec='seconds')})."
            )
        elif (
            cached is not None
            and not force
            and self.cache.is_fresh(cached, max_age=self.max_age, now=now)
        ):
            release = cached.release
            from_cache = True
        else:
            try:
                response = self.client.get_latest_release(
                    definition.repo, etag=cached.etag if cached is not None else None
                )
                if response.not_modified:
                    if cached is None:
                        raise GitHubError("GitHub respondió 304 sin una caché previa.")
                    release = cached.release
                    cached = self.cache.save_release(
                        component, release, etag=cached.etag, fetched_at=now
                    )
                    from_cache = True
                    warnings.append("GitHub confirmó que la caché sigue vigente.")
                else:
                    if response.data is None:
                        raise GitHubError("GitHub devolvió una respuesta sin datos.")
                    release = parse_release(component, response.data)
                    self.cache.save_release(component, release, etag=response.etag, fetched_at=now)
            except GitHubError as exc:
                if cached is None:
                    return self._unknown(
                        component,
                        installed,
                        now,
                        error=str(exc),
                        warnings=("No se pudo consultar GitHub y no hay caché disponible.",),
                    )
                release = cached.release
                from_cache = True
                warnings.append(f"No se pudo consultar GitHub ({exc}); se usa la caché.")

        if release is None:
            return self._unknown(
                component,
                installed,
                now,
                error="No se pudo obtener una release.",
            )

        if release.prerelease or release.draft:
            return self._unknown(
                component,
                installed,
                now,
                latest=release.version,
                release=release,
                error="GitHub devolvió una release preliminar o un borrador.",
                warnings=tuple(warnings),
            )

        try:
            selection = select_assets(definition, release)
        except AssetSelectionError as exc:
            return self._unknown(
                component,
                installed,
                now,
                latest=release.version,
                release=release,
                error=str(exc),
                warnings=tuple(warnings),
            )

        republished = _was_republished(definition, previous_release, release)
        installed_version = installed.version

        if not installed.present:
            status = CheckStatus.NOT_INSTALLED
        elif installed_version is None:
            return self._unknown(
                component,
                installed,
                now,
                latest=release.version,
                release=release,
                error="El componente está presente, pero su versión no se pudo detectar.",
                warnings=tuple(warnings),
            )
        elif installed_version < release.version:
            status = CheckStatus.UPDATE_AVAILABLE
        elif installed_version == release.version:
            status = CheckStatus.UPDATE_AVAILABLE if republished else CheckStatus.UP_TO_DATE
        else:
            status = CheckStatus.INSTALLED_NEWER

        if republished:
            warnings.append(
                "El asset cambió para el mismo tag; la release fue re-subida. "
                "Se recomienda reinstalar para asegurar los archivos nuevos."
            )
        if status == CheckStatus.INSTALLED_NEWER:
            warnings.append(
                "La versión instalada es más nueva que la última release estable; "
                "no se bajará de versión automáticamente."
            )

        return ComponentCheck(
            component=component,
            installed=installed_version,
            latest=release.version,
            status=status,
            release=release,
            selection=selection,
            from_cache=from_cache,
            checked_at=now,
            warnings=tuple(warnings),
            republished=republished,
        )

    def check_all(
        self,
        snapshot: SdSnapshot,
        *,
        components: Iterable[ComponentId] = UPDATABLE_COMPONENTS,
        offline: bool = False,
        force: bool = False,
    ) -> tuple[ComponentCheck, ...]:
        return tuple(
            self.check_component(
                component,
                snapshot.component(component),
                offline=offline,
                force=force,
            )
            for component in components
        )

    def _unknown(
        self,
        component: ComponentId,
        installed: InstalledComponent,
        now: datetime,
        *,
        error: str,
        latest: Version | None = None,
        release: ReleaseInfo | None = None,
        warnings: tuple[str, ...] = (),
    ) -> ComponentCheck:
        return ComponentCheck(
            component=component,
            installed=installed.version,
            latest=latest,
            status=CheckStatus.UNKNOWN,
            release=release,
            from_cache=False,
            checked_at=now,
            warnings=warnings,
            error=error,
        )
