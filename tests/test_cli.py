from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import load_release
from magia_updater.cli import main
from magia_updater.core.cache import CacheStore
from magia_updater.core.models import ComponentId


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
