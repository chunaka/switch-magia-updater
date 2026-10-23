from __future__ import annotations

from pathlib import Path

import pytest

from magia_updater.core.detect import PACKAGE3_EXPECTED_SIZE


def make_package3_header(
    *,
    ams: tuple[int, int, int, int] = (1, 12, 1, 0),
    hos: tuple[int, int, int] = (23, 0, 1),
    magic: bytes = b"PK31",
) -> bytes:
    header = bytearray(0x40)
    header[0:4] = magic
    header[0x35] = hos[2]
    header[0x36] = hos[1]
    header[0x37] = hos[0]
    header[0x38] = ams[3]
    header[0x39] = ams[2]
    header[0x3A] = ams[1]
    header[0x3B] = ams[0]
    return bytes(header)


def write_package3(
    root: Path,
    *,
    ams: tuple[int, int, int, int] = (1, 12, 1, 0),
    hos: tuple[int, int, int] = (23, 0, 1),
    size: int = PACKAGE3_EXPECTED_SIZE,
) -> Path:
    path = root / "atmosphere/package3"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = make_package3_header(ams=ams, hos=hos)
    with path.open("wb") as stream:
        stream.write(header)
        if size > len(header):
            stream.seek(size - 1)
            stream.write(b"\x00")
    return path


def write_hekate(root: Path, version: bytes = b"v6.5.4") -> Path:
    path = root / "bootloader/update.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 64 + version + b"\x00" * 64)
    return path


def write_hbmenu(root: Path, payload: bytes = b"v3.6.1") -> Path:
    path = root / "hbmenu.nro"
    path.write_bytes(b"\x00" * 64 + payload + b"\x00" * 64)
    return path


def write_sys_patch(root: Path, version: bytes = b"1.6.3.0-b962f75") -> Path:
    path = root / "atmosphere/contents/420000000000000B/exefs.nsp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 64 + version + b"\x00" * 64)
    return path


def write_sys_patch_log(
    root: Path,
    *,
    firmware: str = "23.0.1",
    ams: str = "1.12.1",
    sys_patch: str = "1.6.3.0-b962f75",
) -> Path:
    path = root / "config/sys-patch/log.ini"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[stats]\nfw_version={firmware}\nams_version={ams}\nversion={sys_patch}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def fake_sd(tmp_path: Path) -> Path:
    root = tmp_path / "sd"
    root.mkdir()
    (root / "atmosphere/config").mkdir(parents=True)
    (root / "atmosphere/contents").mkdir(parents=True)
    (root / "bootloader/ini").mkdir(parents=True)
    (root / "bootloader/payloads").mkdir(parents=True)
    (root / "switch").mkdir()
    (root / "emuMMC").mkdir()
    (root / "Nintendo").mkdir()
    (root / "bootloader/hekate_ipl.ini").write_text("[config]\n", encoding="utf-8")

    write_package3(root)
    write_hekate(root)
    write_hbmenu(root)
    write_sys_patch(root)
    write_sys_patch_log(root)
    return root
