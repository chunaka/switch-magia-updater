from __future__ import annotations

from dataclasses import replace

import pytest

from helpers import load_release
from magia_updater.core.assets import AssetSelectionError, select_assets
from magia_updater.core.components import ATMOSPHERE, HBMENU, HEKATE, SYS_PATCH
from magia_updater.core.models import ComponentId


def test_select_atmosphere_assets() -> None:
    release = load_release("atmosphere", ComponentId.ATMOSPHERE)
    selection = select_assets(ATMOSPHERE, release)

    assert selection.complete is True
    assert selection.archive is not None
    assert selection.archive.name.startswith("atmosphere-1.12.1")
    assert [asset.name for asset in selection.payloads] == ["fusee.bin"]


def test_select_hekate_assets_excludes_ram8gb() -> None:
    release = load_release("hekate", ComponentId.HEKATE)
    selection = select_assets(HEKATE, release)

    assert selection.complete is True
    assert selection.archive is not None
    assert selection.archive.name == "hekate_ctcaer_6.5.4_Nyx_1.9.4.zip"
    assert [asset.name for asset in selection.payloads] == ["hekate_ctcaer_6.5.4.bin"]


def test_select_sys_patch_assets() -> None:
    release = load_release("sys-patch", ComponentId.SYS_PATCH)
    selection = select_assets(SYS_PATCH, release)

    assert selection.complete is True
    assert selection.archive is not None
    assert selection.archive.name == "sys-patch-v1.6.3.0.zip"
    assert selection.payloads == ()


def test_informational_component_has_no_archive() -> None:
    release = replace(
        load_release("atmosphere", ComponentId.ATMOSPHERE),
        component=ComponentId.HBMENU,
    )
    selection = select_assets(HBMENU, release)

    assert selection.archive is None
    assert selection.complete is False


def test_missing_archive_is_rejected() -> None:
    release = load_release("atmosphere", ComponentId.ATMOSPHERE)
    without_archive = replace(
        release,
        assets=tuple(asset for asset in release.assets if not asset.name.endswith(".zip")),
    )

    with pytest.raises(AssetSelectionError, match="no contiene"):
        select_assets(ATMOSPHERE, without_archive)


def test_missing_payload_is_rejected() -> None:
    release = load_release("atmosphere", ComponentId.ATMOSPHERE)
    without_fusee = replace(
        release,
        assets=tuple(asset for asset in release.assets if asset.name != "fusee.bin"),
    )

    with pytest.raises(AssetSelectionError, match="fusee"):
        select_assets(ATMOSPHERE, without_fusee)


def test_ambiguous_asset_is_rejected() -> None:
    release = load_release("atmosphere", ComponentId.ATMOSPHERE)
    duplicated = replace(release, assets=(*release.assets, release.assets[0]))

    with pytest.raises(AssetSelectionError, match="más de un asset"):
        select_assets(ATMOSPHERE, duplicated)


def test_wrong_component_is_rejected() -> None:
    release = load_release("atmosphere", ComponentId.ATMOSPHERE)
    wrong = replace(release, component=ComponentId.HEKATE)

    with pytest.raises(AssetSelectionError, match="pertenece"):
        select_assets(ATMOSPHERE, wrong)
