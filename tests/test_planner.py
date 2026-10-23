from __future__ import annotations

from pathlib import Path

import pytest

from magia_updater.core.models import (
    CheckStatus,
    ComponentCheck,
    ComponentId,
    FirmwareInfo,
    OperationKind,
    PayloadPolicy,
    ReleaseInfo,
    SdSnapshot,
    Version,
)
from magia_updater.core.planner import (
    is_preserved,
    plan_component,
    plan_update,
)
from magia_updater.core.prepare import PreparedComponent


def write_file(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_snapshot(tmp_path: Path) -> SdSnapshot:
    root = tmp_path / "sd"
    root.mkdir()
    return SdSnapshot(
        root=root,
        components={},
        firmware=FirmwareInfo(),
        is_switch_sd=True,
        has_cfw=True,
        emummc_present=False,
        atmosphere_config_present=False,
        hekate_config_present=False,
        root_payload=None,
        reboot_payload=None,
        payloads=(),
    )


def make_check(component: ComponentId) -> ComponentCheck:
    release = ReleaseInfo(
        component=component,
        tag="1.0.0",
        version=Version.parse("1.0.0"),
        name="",
        html_url="",
        published_at=None,
        body="",
        prerelease=False,
        draft=False,
        assets=(),
    )
    return ComponentCheck(
        component=component,
        installed=Version.parse("0.9.0"),
        latest=Version.parse("1.0.0"),
        status=CheckStatus.UPDATE_AVAILABLE,
        release=release,
    )


def make_prepared(
    tmp_path: Path,
    component: ComponentId,
    entries: dict[str, bytes],
    payloads: dict[str, bytes] | None = None,
) -> PreparedComponent:
    archive_root = tmp_path / f"archive-{component.value}"
    archive_root.mkdir()
    for relative, data in entries.items():
        write_file(archive_root, relative, data)

    payload_paths: dict[str, Path] = {}
    for name, data in (payloads or {}).items():
        payload_paths[name] = write_file(tmp_path / f"payloads-{component.value}", name, data)

    return PreparedComponent(
        check=make_check(component),
        archive_root=archive_root,
        payloads=payload_paths,
    )


def op_for(plan, relative: str):
    for operation in plan.operations:
        if operation.relative_path == relative:
            return operation
    raise AssertionError(f"No hay operación para {relative}")


def atmosphere_prepared(tmp_path: Path) -> PreparedComponent:
    return make_prepared(
        tmp_path,
        ComponentId.ATMOSPHERE,
        {
            "atmosphere/package3": b"nuevo package3",
            "atmosphere/config_templates/system_settings.ini": b"template",
            "atmosphere/reboot_payload.bin": b"fusee desde el zip",
            "atmosphere/contents/0100000000000000/exefs.nsp": b"sysmodule de usuario",
            "hbmenu.nro": b"hbmenu",
            "switch/daybreak.nro": b"daybreak",
            "switch/homebrew.nro": b"homebrew de usuario",
            "bootloader/payloads/custom.bin": b"payload de usuario",
        },
        {"fusee.bin": b"fusee nuevo"},
    )


def test_plan_preserves_configuration_and_user_files(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    prepared = atmosphere_prepared(tmp_path)

    plan = plan_component(snapshot, prepared)

    assert op_for(plan, "atmosphere/package3").kind == OperationKind.CREATE
    assert (
        op_for(plan, "atmosphere/config_templates/system_settings.ini").kind == OperationKind.CREATE
    )
    assert op_for(plan, "hbmenu.nro").kind == OperationKind.CREATE
    assert op_for(plan, "switch/daybreak.nro").kind == OperationKind.CREATE
    assert op_for(plan, "bootloader/payloads/fusee.bin").kind == OperationKind.CREATE

    assert op_for(plan, "atmosphere/contents/0100000000000000/exefs.nsp").kind == OperationKind.SKIP
    assert op_for(plan, "switch/homebrew.nro").kind == OperationKind.SKIP
    assert op_for(plan, "bootloader/payloads/custom.bin").kind == OperationKind.SKIP

    reboot = op_for(plan, "atmosphere/reboot_payload.bin")
    assert reboot.kind == OperationKind.SKIP
    assert reboot.requires_confirmation is True
    assert plan.requires_confirmation is True
    assert any("reboot_payload.bin" in warning for warning in plan.warnings)


def test_plan_keeps_identical_files_and_replaces_changed_files(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    prepared = atmosphere_prepared(tmp_path)
    write_file(snapshot.root, "atmosphere/package3", b"nuevo package3")
    write_file(snapshot.root, "hbmenu.nro", b"hbmenu viejo")

    plan = plan_component(snapshot, prepared)

    assert op_for(plan, "atmosphere/package3").kind == OperationKind.KEEP
    assert op_for(plan, "hbmenu.nro").kind == OperationKind.REPLACE


def test_sys_patch_replaces_only_its_title_id(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    prepared = make_prepared(
        tmp_path,
        ComponentId.SYS_PATCH,
        {
            "atmosphere/contents/420000000000000B/exefs.nsp": b"nuevo sys-patch",
            "atmosphere/contents/420000000000000B/flags/boot2.flag": b"",
            "atmosphere/contents/0100000000000000/exefs.nsp": b"otro sysmodule",
            "switch/.overlays/sys-patch-overlay.ovl": b"overlay",
            "switch/.overlays/otro.ovl": b"overlay de usuario",
            "config/sys-patch/config.ini": b"config",
        },
    )
    write_file(snapshot.root, "atmosphere/contents/420000000000000B/exefs.nsp", b"viejo")

    plan = plan_component(snapshot, prepared)

    assert (
        op_for(plan, "atmosphere/contents/420000000000000B/exefs.nsp").kind == OperationKind.REPLACE
    )
    assert (
        op_for(plan, "atmosphere/contents/420000000000000B/flags/boot2.flag").kind
        == OperationKind.CREATE
    )
    assert op_for(plan, "atmosphere/contents/0100000000000000/exefs.nsp").kind == OperationKind.SKIP
    assert op_for(plan, "switch/.overlays/sys-patch-overlay.ovl").kind == OperationKind.CREATE
    assert op_for(plan, "switch/.overlays/otro.ovl").kind == OperationKind.SKIP
    assert op_for(plan, "config/sys-patch/config.ini").kind == OperationKind.SKIP


def test_hekate_preserves_configs_and_places_payload(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    prepared = make_prepared(
        tmp_path,
        ComponentId.HEKATE,
        {
            "bootloader/sys/nyx.bin": b"nyx",
            "bootloader/update.bin": b"update",
            "bootloader/hekate_ipl.ini": b"config de usuario",
            "bootloader/ini/entrada.ini": b"entrada",
            "bootloader/payloads/viejo.bin": b"payload de usuario",
            "hekate_ctcaer_6.5.4.bin": b"hekate root zip",
        },
        {"hekate_ctcaer_6.5.4.bin": b"hekate nuevo"},
    )

    plan = plan_component(snapshot, prepared)

    assert op_for(plan, "bootloader/sys/nyx.bin").kind == OperationKind.CREATE
    assert op_for(plan, "bootloader/update.bin").kind == OperationKind.CREATE
    assert op_for(plan, "bootloader/payloads/hekate_ctcaer_6.5.4.bin").kind == OperationKind.CREATE
    assert op_for(plan, "hekate_ctcaer_6.5.4.bin").kind == OperationKind.SKIP
    assert op_for(plan, "bootloader/hekate_ipl.ini").kind == OperationKind.SKIP
    assert op_for(plan, "bootloader/ini/entrada.ini").kind == OperationKind.SKIP
    assert op_for(plan, "bootloader/payloads/viejo.bin").kind == OperationKind.SKIP


def test_root_payload_requires_confirmation_by_default(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    snapshot = SdSnapshot(
        **{
            **snapshot.__dict__,
            "root_payload": write_file(snapshot.root, "payload.bin", b"payload desconocido"),
        }
    )
    prepared = atmosphere_prepared(tmp_path)

    plan = plan_component(snapshot, prepared)

    operation = op_for(plan, "payload.bin")
    assert operation.kind == OperationKind.KEEP
    assert operation.requires_confirmation is True
    assert any("/payload.bin" in warning for warning in plan.warnings)


def test_root_payload_is_updated_when_explicitly_allowed(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    prepared = atmosphere_prepared(tmp_path)

    plan = plan_component(
        snapshot,
        prepared,
        PayloadPolicy(update_root_payload=True),
    )

    operation = op_for(plan, "payload.bin")
    assert operation.kind == OperationKind.CREATE
    assert operation.requires_confirmation is False
    assert operation.source == prepared.payloads["fusee.bin"]


def test_reboot_payload_is_updated_when_explicitly_allowed(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    prepared = atmosphere_prepared(tmp_path)

    plan = plan_component(
        snapshot,
        prepared,
        PayloadPolicy(update_reboot_payload=True),
    )

    operation = op_for(plan, "atmosphere/reboot_payload.bin")
    assert operation.kind == OperationKind.CREATE
    assert operation.requires_confirmation is False


def test_payload_installation_can_be_disabled(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    prepared = atmosphere_prepared(tmp_path)

    plan = plan_component(
        snapshot,
        prepared,
        PayloadPolicy(install_payloads=False),
    )

    assert all(
        operation.relative_path != "bootloader/payloads/fusee.bin" for operation in plan.operations
    )
    assert any("No se instalarán payloads" in warning for warning in plan.warnings)


def test_conflict_when_destination_is_a_directory(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    (snapshot.root / "hbmenu.nro").mkdir()
    prepared = atmosphere_prepared(tmp_path)

    plan = plan_component(snapshot, prepared)

    operation = op_for(plan, "hbmenu.nro")
    assert operation.kind == OperationKind.CONFLICT
    assert operation.requires_confirmation is True


def test_plan_update_aggregates_components(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    atmosphere = atmosphere_prepared(tmp_path)
    hekate = make_prepared(
        tmp_path,
        ComponentId.HEKATE,
        {"bootloader/update.bin": b"update"},
        {"hekate_ctcaer_6.5.4.bin": b"hekate"},
    )

    plan = plan_update(snapshot, (atmosphere, hekate))

    assert [component.component for component in plan.components] == [
        ComponentId.ATMOSPHERE,
        ComponentId.HEKATE,
    ]
    assert len(plan.operations) > 0
    assert plan.requires_confirmation is True


@pytest.mark.parametrize(
    ("relative", "component", "preserved"),
    [
        ("emuMMC/raw1.bin", ComponentId.ATMOSPHERE, True),
        ("Nintendo/Contents/foo", ComponentId.ATMOSPHERE, True),
        ("atmosphere/config/system_settings.ini", ComponentId.ATMOSPHERE, True),
        ("atmosphere/hosts/default.txt", ComponentId.ATMOSPHERE, True),
        ("atmosphere/contents/0100000000000000/exefs.nsp", ComponentId.ATMOSPHERE, True),
        ("atmosphere/contents/420000000000000B/exefs.nsp", ComponentId.ATMOSPHERE, True),
        ("atmosphere/contents/420000000000000B/exefs.nsp", ComponentId.SYS_PATCH, False),
        ("bootloader/hekate_ipl.ini", ComponentId.HEKATE, True),
        ("bootloader/ini/entrada.ini", ComponentId.HEKATE, True),
        ("bootloader/payloads/custom.bin", ComponentId.HEKATE, True),
        ("switch/homebrew.nro", ComponentId.ATMOSPHERE, True),
        ("switch/daybreak.nro", ComponentId.ATMOSPHERE, False),
        ("switch/.overlays/sys-patch-overlay.ovl", ComponentId.SYS_PATCH, False),
        ("switch/.overlays/otro.ovl", ComponentId.SYS_PATCH, True),
        ("config/sys-patch/config.ini", ComponentId.SYS_PATCH, True),
    ],
)
def test_is_preserved_rules(
    relative: str,
    component: ComponentId,
    preserved: bool,
) -> None:
    assert is_preserved(relative, component) is preserved
