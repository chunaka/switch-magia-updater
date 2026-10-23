from __future__ import annotations

from pathlib import Path

import pytest

from magia_updater.core.models import (
    ComponentId,
    ComponentPlan,
    ReleaseInfo,
    UpdatePlan,
    Version,
)
from magia_updater.core.state import (
    AppliedComponent,
    StateError,
    StateStore,
    plan_digest,
    state_from_plan,
)


def make_plan() -> UpdatePlan:
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
        assets=(),
    )
    component = ComponentPlan(
        component=ComponentId.ATMOSPHERE,
        release=release,
        operations=(),
    )
    return UpdatePlan(components=(component,))


def test_state_roundtrip(tmp_path: Path) -> None:
    plan = make_plan()
    state = state_from_plan(
        plan,
        sd_id="sd-1",
        backup_dir=tmp_path / "backup",
        applied_at="2026-01-01T12:00:00+00:00",
    )
    store = StateStore(tmp_path / "state")

    path = store.save(state)
    loaded = store.load("sd-1")

    assert path == tmp_path / "state/sd-1.json"
    assert loaded == state
    assert loaded is not None
    assert loaded.components == (
        AppliedComponent(
            component=ComponentId.ATMOSPHERE,
            version="1.12.1",
            release_tag="1.12.1",
        ),
    )


def test_state_plan_digest_is_stable(tmp_path: Path) -> None:
    plan = make_plan()
    state = state_from_plan(
        plan,
        sd_id="sd-1",
        backup_dir=tmp_path / "backup",
        applied_at="2026-01-01T12:00:00+00:00",
    )

    assert state.plan_sha256 == plan_digest(plan)
    assert len(state.plan_sha256) == 64


def test_state_load_missing_returns_none(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")

    assert store.load("sd-1") is None


def test_state_clear(tmp_path: Path) -> None:
    plan = make_plan()
    store = StateStore(tmp_path / "state")
    state = state_from_plan(
        plan,
        sd_id="sd-1",
        backup_dir=tmp_path / "backup",
        applied_at="2026-01-01T12:00:00+00:00",
    )
    store.save(state)

    assert store.clear("sd-1") is True
    assert store.load("sd-1") is None
    assert store.clear("sd-1") is False


@pytest.mark.parametrize("sd_id", ["", "../evil", "a/b", ".hidden", "a..b"])
def test_state_rejects_invalid_ids(tmp_path: Path, sd_id: str) -> None:
    store = StateStore(tmp_path / "state")

    with pytest.raises(StateError):
        store.path(sd_id)


def test_state_rejects_invalid_json(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    path = store.root / "sd-1.json"
    path.parent.mkdir(parents=True)
    path.write_text("no es json", encoding="utf-8")

    with pytest.raises(StateError):
        store.load("sd-1")
