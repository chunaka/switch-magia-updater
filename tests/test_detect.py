from __future__ import annotations

from pathlib import Path

import pytest

from conftest import (
    make_package3_header,
    write_hbmenu,
    write_package3,
    write_sys_patch,
)
from magia_updater.core.detect import (
    PACKAGE3_EXPECTED_SIZE,
    detect_hbmenu,
    detect_hekate,
    detect_sd,
    detect_sys_patch,
    parse_package3_header,
    parse_sys_patch_log,
)
from magia_updater.core.errors import SdNotFoundError
from magia_updater.core.models import ComponentId, snapshot_to_dict

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_real_package3_header_fixture() -> None:
    header = (FIXTURES / "package3_1.12.1.header.bin").read_bytes()
    info = parse_package3_header(header, size=PACKAGE3_EXPECTED_SIZE)

    assert info is not None
    assert str(info.ams_version) == "1.12.1"
    assert str(info.supported_firmware) == "23.0.1"
    assert info.size == PACKAGE3_EXPECTED_SIZE


def test_parse_package3_rejects_bad_headers() -> None:
    good = make_package3_header()
    assert parse_package3_header(b"", size=0) is None
    assert parse_package3_header(good[:0x20], size=0x20) is None
    assert parse_package3_header(make_package3_header(magic=b"XXXX"), size=64) is None
    assert parse_package3_header(make_package3_header(ams=(0, 12, 1, 0)), size=64) is None
    assert parse_package3_header(make_package3_header(hos=(0, 0, 0)), size=64) is None


def test_detect_sd_full(fake_sd: Path) -> None:
    snapshot = detect_sd(fake_sd)

    assert snapshot.is_switch_sd is True
    assert snapshot.has_cfw is True
    assert snapshot.emummc_present is True
    assert snapshot.atmosphere_config_present is True
    assert snapshot.hekate_config_present is True

    atmosphere = snapshot.component(ComponentId.ATMOSPHERE)
    assert atmosphere.present is True
    assert str(atmosphere.version) == "1.12.1"
    assert str(atmosphere.supported_firmware) == "23.0.1"
    assert atmosphere.evidence == "package3"

    hekate = snapshot.component(ComponentId.HEKATE)
    assert hekate.present is True
    assert str(hekate.version) == "6.5.4"

    sys_patch = snapshot.component(ComponentId.SYS_PATCH)
    assert sys_patch.present is True
    assert str(sys_patch.version) == "1.6.3.0"
    assert sys_patch.evidence == "exefs.nsp"

    hbmenu = snapshot.component(ComponentId.HBMENU)
    assert hbmenu.present is True
    assert str(hbmenu.version) == "3.6.1"

    assert str(snapshot.firmware.version) == "23.0.1"
    assert str(snapshot.firmware.ams_version) == "1.12.1"
    assert snapshot.firmware.source == "config/sys-patch/log.ini"

    # El único aviso esperado en esta SD completa es el de EmuMMC.
    assert any("EmuMMC" in warning for warning in snapshot.warnings)
    assert not any("No se detectó Atmosphère" in warning for warning in snapshot.warnings)
    assert not any("No se detectó Hekate" in warning for warning in snapshot.warnings)
    assert not any("No se detectó sys-patch" in warning for warning in snapshot.warnings)


def test_detect_sd_not_switch(tmp_path: Path) -> None:
    snapshot = detect_sd(tmp_path)

    assert snapshot.is_switch_sd is False
    assert snapshot.has_cfw is False
    assert any("no parece una SD" in warning for warning in snapshot.warnings)
    assert any("No se detectó Atmosphère" in warning for warning in snapshot.warnings)


def test_detect_sd_missing_path(tmp_path: Path) -> None:
    with pytest.raises(SdNotFoundError):
        detect_sd(tmp_path / "no-existe")


def test_hekate_fallback_to_payload_filename(tmp_path: Path) -> None:
    (tmp_path / "hekate_ctcaer_6.5.4.bin").write_bytes(b"payload")
    installed = detect_hekate(tmp_path)

    assert installed.present is True
    assert str(installed.version) == "6.5.4"
    assert installed.evidence == "nombre de payload"


def test_hekate_ignores_ram8gb_only(tmp_path: Path) -> None:
    (tmp_path / "hekate_ctcaer_6.5.4__ram8GB.bin").write_bytes(b"payload")
    installed = detect_hekate(tmp_path)

    assert installed.present is False


def test_hbmenu_requires_v_prefix(tmp_path: Path) -> None:
    write_hbmenu(tmp_path, b"1.0.0\x00v3.6.1")
    installed = detect_hbmenu(tmp_path)

    assert installed.present is True
    assert str(installed.version) == "3.6.1"


def test_sys_patch_log_fallback(tmp_path: Path) -> None:
    log_path = tmp_path / "config/sys-patch/log.ini"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "[stats]\nfw_version = 23.0.1\nams_version = 1.12.1\nversion = 1.6.3.0-b962f75\n",
        encoding="utf-8",
    )

    log = parse_sys_patch_log(log_path)
    assert log is not None
    assert str(log.firmware) == "23.0.1"
    assert str(log.ams_version) == "1.12.1"
    assert str(log.sys_patch_version) == "1.6.3.0"

    installed = detect_sys_patch(tmp_path, log)
    assert installed.present is True
    assert str(installed.version) == "1.6.3.0"
    assert installed.evidence == "log.ini"


def test_sys_patch_exefs_takes_priority(tmp_path: Path) -> None:
    write_sys_patch(tmp_path, b"1.6.2.3-aaaaaaa")
    log_path = tmp_path / "config/sys-patch/log.ini"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("[stats]\nversion=1.6.3.0-b962f75\n", encoding="utf-8")

    installed = detect_sys_patch(tmp_path, parse_sys_patch_log(log_path))
    assert str(installed.version) == "1.6.2.3"
    assert installed.evidence == "exefs.nsp"


def test_payload_detection(fake_sd: Path) -> None:
    (fake_sd / "payload.bin").write_bytes(b"root payload")
    (fake_sd / "atmosphere/reboot_payload.bin").write_bytes(b"reboot payload")
    (fake_sd / "bootloader/payloads/fusee.bin").write_bytes(b"fusee")
    (fake_sd / "bootloader/payloads/hekate_ctcaer_6.5.4.bin").write_bytes(b"hekate")

    snapshot = detect_sd(fake_sd)
    assert snapshot.root_payload is not None
    assert snapshot.reboot_payload is not None
    assert len(snapshot.payloads) == 2
    assert any("/payload.bin" in warning for warning in snapshot.warnings)


def test_package3_size_warning(tmp_path: Path) -> None:
    write_package3(tmp_path, size=0x40)
    snapshot = detect_sd(tmp_path)
    assert any("tamaño esperado" in warning for warning in snapshot.warnings)


def test_snapshot_serialization(fake_sd: Path) -> None:
    snapshot = detect_sd(fake_sd)
    data = snapshot_to_dict(snapshot)

    assert data["root"] == str(fake_sd.resolve())
    assert data["components"]["atmosphere"]["version"] == "1.12.1"
    assert data["components"]["hekate"]["version"] == "6.5.4"
    assert data["components"]["sys-patch"]["version"] == "1.6.3.0"
    assert data["components"]["hbmenu"]["version"] == "3.6.1"
    assert data["firmware"]["version"] == "23.0.1"
    assert data["root_payload"] is None
    assert data["reboot_payload"] is None
