"""Flujo de comprobación y planificación compartido por CLI y GUI."""

from __future__ import annotations

import os
from collections.abc import Sequence

from magia_updater.core.cache import CacheStore
from magia_updater.core.check import ReleaseService
from magia_updater.core.download import AssetDownloader, OfflineDownloadTransport, ProgressCallback
from magia_updater.core.github import GitHubClient
from magia_updater.core.models import (
    CheckStatus,
    ComponentCheck,
    ComponentId,
    PayloadPolicy,
    SdSnapshot,
    UpdatePlan,
)
from magia_updater.core.planner import plan_update
from magia_updater.core.prepare import UpdatePreparer


def build_update_plan(
    snapshot: SdSnapshot,
    components: Sequence[ComponentId],
    *,
    cache: CacheStore,
    offline: bool,
    refresh: bool,
    no_payloads: bool,
    allow_reboot_payload: bool,
    allow_root_payload: bool,
    progress: ProgressCallback | None = None,
) -> tuple[tuple[ComponentCheck, ...], UpdatePlan]:
    """Comprueba releases y prepara un plan sin escribir en la SD."""
    token = os.environ.get("MAGIA_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    service = ReleaseService(GitHubClient(token=token), cache)
    checks = service.check_all(
        snapshot,
        components=tuple(components),
        offline=offline,
        force=refresh,
    )
    candidates = tuple(
        check
        for check in checks
        if check.status in {CheckStatus.UPDATE_AVAILABLE, CheckStatus.NOT_INSTALLED}
    )
    if not candidates:
        return checks, UpdatePlan(components=(), warnings=())

    transport = OfflineDownloadTransport() if offline else None
    downloader = AssetDownloader(cache, transport=transport)
    preparer = UpdatePreparer(downloader, cache)
    prepared = preparer.prepare_all(candidates, progress=progress)
    policy = PayloadPolicy(
        install_payloads=not no_payloads,
        update_reboot_payload=allow_reboot_payload,
        update_root_payload=allow_root_payload,
    )
    return checks, plan_update(snapshot, prepared, policy)
