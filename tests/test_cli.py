from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from conftest import write_package3
from helpers import load_release
from magia_updater.cli import main
from magia_updater.core.cache import CacheStore
from magia_updater.core.models import (
    AssetInfo,
    ComponentId,
    ReleaseInfo,
    Version,
)


def test_cli_detect_json(fake_sd: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = main(["detect", "--sd", str(fake_sd), "--json"])
    captured = capsys.readouterr()

    assert result == 0
    data = json.loads(captured.out)
    assert data["is_switch_sd"] is True
    assert data["components"]["atmosphere"]["version"] == "1.12.1"
    assert data["components"]["hekate"]["version"] == "6.5.4"
    assert data["components"]["sys-patch"]["version"] == "1.6.3.0"
    assert data["firmware"]["version"] == "23.0.1"
    assert captured.err == ""


def test_cli_detect_human(fake_sd: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = main(["detect", "--sd", str(fake_sd)])
    captured = capsys.readouterr()

    assert result == 0
    assert "SD de Switch: sí" in captured.out
    assert "Atmosphère: 1.12.1 (package3)" in captured.out
    assert "Hekate + Nyx: 6.5.4 (bootloader/update.bin)" in captured.out
    assert "sys-patch: 1.6.3.0 (exefs.nsp)" in captured.out
    assert "hbmenu: 3.6.1 (hbmenu.nro)" in captured.out
    assert "Firmware: 23.0.1 (config/sys-patch/log.ini)" in captured.out


def test_cli_detect_missing_sd(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = main(["detect", "--sd", str(tmp_path / "missing")])
    captured = capsys.readouterr()

    assert result == 2
    assert "Error:" in captured.err


def test_cli_detect_not_switch_sd(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = main(["detect", "--sd", str(tmp_path)])
    captured = capsys.readouterr()

    assert result == 1
    assert "SD de Switch: no" in captured.out


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    assert "magia 0.1.0" in capsys.readouterr().out


def _populate_cache(cache_dir: Path) -> None:
    cache = CacheStore(cache_dir)
    for component, name in (
        (ComponentId.ATMOSPHERE, "atmosphere"),
        (ComponentId.HEKATE, "hekate"),
        (ComponentId.SYS_PATCH, "sys-patch"),
    ):
        cache.save_release(component, load_release(name, component), etag=None)


def _populate_plan_cache(cache_dir: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("atmosphere/package3", b"nuevo package3")
        archive.writestr("atmosphere/reboot_payload.bin", b"fusee desde el zip")
        archive.writestr("switch/daybreak.nro", b"daybreak")
    archive_data = buffer.getvalue()
    payload_data = b"fusee"
    archive_asset = AssetInfo(
        name="atmosphere-1.12.1.zip",
        size=len(archive_data),
        download_url="https://example.test/atmosphere-1.12.1.zip",
        digest="sha256:" + hashlib.sha256(archive_data).hexdigest(),
    )
    payload_asset = AssetInfo(
        name="fusee.bin",
        size=len(payload_data),
        download_url="https://example.test/fusee.bin",
        digest="sha256:" + hashlib.sha256(payload_data).hexdigest(),
    )
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
        assets=(archive_asset, payload_asset),
    )
    cache = CacheStore(cache_dir)
    cache.save_release(ComponentId.ATMOSPHERE, release, etag=None)
    for asset, data in ((archive_asset, archive_data), (payload_asset, payload_data)):
        path = cache.asset_path(asset.sha256 or "", asset.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def test_cli_plan_offline_json(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)

    result = main(
        [
            "plan",
            "--sd",
            str(fake_sd),
            "--components",
            "atmosphere",
            "--offline",
            "--cache-dir",
            str(cache_dir),
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    data = json.loads(captured.out)
    assert data["plan"]["requires_confirmation"] is True
    component = data["plan"]["components"][0]
    assert component["component"] == "atmosphere"
    operations = {operation["path"]: operation for operation in component["operations"]}
    assert operations["atmosphere/package3"]["kind"] == "replace"
    assert operations["switch/daybreak.nro"]["kind"] == "create"
    assert operations["bootloader/payloads/fusee.bin"]["kind"] == "create"
    assert operations["atmosphere/reboot_payload.bin"]["kind"] == "skip"
    assert operations["atmosphere/reboot_payload.bin"]["requires_confirmation"] is True


def test_cli_plan_offline_requires_cached_assets(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)
    cache = CacheStore(cache_dir)
    release = cache.load_release(ComponentId.ATMOSPHERE)
    assert release is not None
    asset = release.release.assets[0]
    cache.asset_path(asset.sha256 or "", asset.name).unlink()

    result = main(
        [
            "plan",
            "--sd",
            str(fake_sd),
            "--components",
            "atmosphere",
            "--offline",
            "--cache-dir",
            str(cache_dir),
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert "offline" in captured.err.lower()


def test_cli_check_offline_json(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir = tmp_path / "cache"
    _populate_cache(cache_dir)

    result = main(
        [
            "check",
            "--sd",
            str(fake_sd),
            "--offline",
            "--cache-dir",
            str(cache_dir),
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    data = json.loads(captured.out)
    assert data["checks"]["atmosphere"]["status"] == "up_to_date"
    assert data["checks"]["hekate"]["status"] == "up_to_date"
    assert data["checks"]["sys-patch"]["status"] == "up_to_date"
    assert data["checks"]["atmosphere"]["from_cache"] is True


def test_cli_check_offline_human(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir = tmp_path / "cache"
    _populate_cache(cache_dir)

    result = main(["check", "--sd", str(fake_sd), "--offline", "--cache-dir", str(cache_dir)])
    captured = capsys.readouterr()

    assert result == 0
    assert "Atmosphère: 1.12.1 -> 1.12.1 (al día) [caché]" in captured.out
    assert "Hekate + Nyx: 6.5.4 -> 6.5.4 (al día) [caché]" in captured.out


def test_cli_check_without_cache(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "check",
            "--sd",
            str(fake_sd),
            "--offline",
            "--cache-dir",
            str(tmp_path / "empty-cache"),
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert "(sin datos)" in captured.out


def test_cli_check_invalid_component(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "check",
            "--sd",
            str(fake_sd),
            "--components",
            "atmosphere,inexistente",
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert "Componente inválido" in captured.err


def test_cli_check_not_switch_sd(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "check",
            "--sd",
            str(tmp_path),
            "--offline",
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert "no parece una SD" in captured.err
