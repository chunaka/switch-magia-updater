from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from magia_updater.core.components import (
    ATMOSPHERE,
    HBMENU,
    HEKATE,
    SYS_PATCH,
    UPDATABLE_COMPONENTS,
    get_component,
    recommended_components,
)
from magia_updater.core.detect import detect_sd
from magia_updater.core.models import ComponentId, InstalledComponent


def test_atmosphere_asset_patterns() -> None:
    assert ATMOSPHERE.asset_matches("atmosphere-1.12.1-master-6b0c077b4+hbl-2.4.5+hbmenu-3.6.1.zip")
    assert ATMOSPHERE.asset_matches("fusee.bin")
    assert not ATMOSPHERE.asset_matches("atmosphere-notes.txt")


def test_hekate_asset_patterns_exclude_ram8gb() -> None:
    assert HEKATE.asset_matches("hekate_ctcaer_6.5.4_Nyx_1.9.4.zip")
    assert HEKATE.asset_matches("hekate_ctcaer_6.5.4.bin")
    assert not HEKATE.asset_matches("hekate_ctcaer_6.5.4__ram8GB.bin")
    assert not HEKATE.asset_matches("joiner_scripts_for_windows_linux_macos.zip")


def test_sys_patch_asset_pattern() -> None:
    assert SYS_PATCH.asset_matches("sys-patch-v1.6.3.0.zip")
    assert not SYS_PATCH.asset_matches("sys-patch-overlay.ovl")


def test_component_registry() -> None:
    assert get_component(ComponentId.ATMOSPHERE) is ATMOSPHERE
    assert get_component(ComponentId.HEKATE) is HEKATE
    assert get_component(ComponentId.SYS_PATCH) is SYS_PATCH
    assert get_component(ComponentId.HBMENU) is HBMENU
    assert UPDATABLE_COMPONENTS == (
        ComponentId.ATMOSPHERE,
        ComponentId.HEKATE,
        ComponentId.SYS_PATCH,
    )
    assert HBMENU.informational is True
    assert HEKATE.optional is True


def test_recommended_components_includes_only_present(fake_sd: Path) -> None:
    snapshot = detect_sd(fake_sd)

    assert recommended_components(snapshot) == (
        ComponentId.ATMOSPHERE,
        ComponentId.HEKATE,
        ComponentId.SYS_PATCH,
    )


def test_recommended_components_skips_missing(fake_sd: Path) -> None:
    snapshot = replace(
        detect_sd(fake_sd),
        components={
            ComponentId.ATMOSPHERE: InstalledComponent(ComponentId.ATMOSPHERE, present=True),
            ComponentId.HEKATE: InstalledComponent(ComponentId.HEKATE),
            ComponentId.SYS_PATCH: InstalledComponent(ComponentId.SYS_PATCH),
            ComponentId.HBMENU: InstalledComponent(ComponentId.HBMENU),
        },
    )

    assert recommended_components(snapshot) == (ComponentId.ATMOSPHERE,)
