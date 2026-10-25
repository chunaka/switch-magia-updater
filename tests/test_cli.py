from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import write_package3
from helpers import load_release
from magia_updater.cli import main
from magia_updater.core.backup import BackupManager
from magia_updater.core.cache import CacheStore
from magia_updater.core.devices import SdCandidate
from magia_updater.core.identity import get_sd_identity
from magia_updater.core.models import (
    AssetInfo,
    ComponentId,
    FileOperation,
    OperationKind,
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


def test_cli_detect_autodetects_single_sd(
    fake_sd: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate = SdCandidate(
        root=fake_sd.resolve(),
        drive_type="ruta",
        markers=("atmosphere/package3",),
    )
    monkeypatch.setattr("magia_updater.cli.find_switch_sds", lambda: (candidate,))

    result = main(["detect", "--json"])
    captured = capsys.readouterr()

    assert result == 0
    assert json.loads(captured.out)["root"] == str(fake_sd.resolve())


def test_cli_autodetect_requires_choice_with_several_sds(
    fake_sd: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    second = tmp_path / "second-sd"
    second.mkdir()
    monkeypatch.setattr(
        "magia_updater.cli.find_switch_sds",
        lambda: (
            SdCandidate(root=fake_sd.resolve(), drive_type="ruta", markers=()),
            SdCandidate(root=second.resolve(), drive_type="ruta", markers=()),
        ),
    )

    result = main(["detect"])
    captured = capsys.readouterr()

    assert result == 2
    assert "varias SD" in captured.err


def test_cli_autodetect_reports_missing_sd(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("magia_updater.cli.find_switch_sds", lambda: ())

    result = main(["detect"])
    captured = capsys.readouterr()

    assert result == 2
    assert "No se detectó" in captured.err


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    assert "magia 1.0.0" in capsys.readouterr().out


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


class _TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def _apply_args(
    fake_sd: Path,
    cache_dir: Path,
    backup_dir: Path,
    state_dir: Path,
    *extra: str,
) -> list[str]:
    return [
        "apply",
        "--sd",
        str(fake_sd),
        "--components",
        "atmosphere",
        "--offline",
        "--cache-dir",
        str(cache_dir),
        "--no-payloads",
        "--backup-dir",
        str(backup_dir),
        "--state-dir",
        str(state_dir),
        *extra,
    ]


def test_cli_export_zip_delta(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)
    output = tmp_path / "update.zip"

    result = main(
        [
            "export",
            "--sd",
            str(fake_sd),
            "--components",
            "atmosphere",
            "--offline",
            "--cache-dir",
            str(cache_dir),
            "--no-payloads",
            "--output",
            str(output),
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "Paquete exportado:" in captured.out
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == [
            "atmosphere/package3",
            "switch/daybreak.nro",
            "manifest.json",
            "LEEME.txt",
        ]
        assert archive.read("atmosphere/package3") == b"nuevo package3"
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["package_type"] == "delta"
    assert not (fake_sd / "switch/daybreak.nro").exists()
    assert (fake_sd / "atmosphere/package3").read_bytes() != b"nuevo package3"


def test_cli_export_folder_full_with_reboot_payload(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)
    output = tmp_path / "package"

    result = main(
        [
            "export",
            "--sd",
            str(fake_sd),
            "--components",
            "atmosphere",
            "--offline",
            "--cache-dir",
            str(cache_dir),
            "--no-payloads",
            "--allow-reboot-payload",
            "--format",
            "folder",
            "--kind",
            "full",
            "--output",
            str(output),
        ]
    )
    capsys.readouterr()

    assert result == 0
    assert (output / "atmosphere/package3").read_bytes() == b"nuevo package3"
    assert (output / "atmosphere/reboot_payload.bin").read_bytes() == b"fusee desde el zip"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["package_type"] == "full"
    assert not (fake_sd / "atmosphere/reboot_payload.bin").exists()


def test_cli_export_rejects_existing_output(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)
    output = tmp_path / "update.zip"
    output.write_bytes(b"existing")

    result = main(
        [
            "export",
            "--sd",
            str(fake_sd),
            "--components",
            "atmosphere",
            "--offline",
            "--cache-dir",
            str(cache_dir),
            "--output",
            str(output),
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert "ya existe" in captured.err


def test_cli_apply_offline_end_to_end(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)
    backup_dir = tmp_path / "backups"
    state_dir = tmp_path / "state"

    result = main(
        _apply_args(
            fake_sd,
            cache_dir,
            backup_dir,
            state_dir,
            "--yes",
            "--mirror-backup",
        )
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "Actualización aplicada." in captured.out
    assert "Backup:" in captured.out
    assert "Estado:" in captured.out
    assert (fake_sd / "atmosphere/package3").read_bytes() == b"nuevo package3"
    assert (fake_sd / "switch/daybreak.nro").read_bytes() == b"daybreak"
    assert not (fake_sd / "atmosphere/reboot_payload.bin").exists()
    assert len(list(backup_dir.rglob("manifest.json"))) == 1
    assert len(list(state_dir.glob("*.json"))) == 1
    assert (fake_sd / ".magia/backups").is_dir()


def test_cli_apply_requires_yes_when_noninteractive(
    fake_sd: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    result = main(_apply_args(fake_sd, cache_dir, backup_dir, tmp_path / "state"))
    captured = capsys.readouterr()

    assert result == 2
    assert "--yes" in captured.err
    assert not backup_dir.exists()
    assert (fake_sd / "atmosphere/package3").read_bytes() != b"nuevo package3"


def test_cli_apply_prompts_and_accepts(
    fake_sd: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(sys, "stdin", _TtyInput("s\n"))

    result = main(_apply_args(fake_sd, cache_dir, backup_dir, tmp_path / "state"))
    captured = capsys.readouterr()

    assert result == 0
    assert "¿Aplicar estos cambios?" in captured.out
    assert (fake_sd / "atmosphere/package3").read_bytes() == b"nuevo package3"


def test_cli_apply_prompts_and_cancels(
    fake_sd: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(sys, "stdin", _TtyInput("n\n"))

    result = main(_apply_args(fake_sd, cache_dir, backup_dir, tmp_path / "state"))
    captured = capsys.readouterr()

    assert result == 1
    assert "Aplicación cancelada." in captured.out
    assert not backup_dir.exists()
    assert (fake_sd / "atmosphere/package3").read_bytes() != b"nuevo package3"


def test_cli_apply_allows_protected_payloads(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)

    result = main(
        _apply_args(
            fake_sd,
            cache_dir,
            tmp_path / "backups",
            tmp_path / "state",
            "--yes",
            "--allow-reboot-payload",
            "--allow-root-payload",
        )
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "Actualización aplicada." in captured.out
    assert (fake_sd / "atmosphere/reboot_payload.bin").read_bytes() == b"fusee desde el zip"
    assert (fake_sd / "payload.bin").read_bytes() == b"fusee"


def test_cli_apply_no_changes(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)
    backup_dir = tmp_path / "backups"

    result = main(_apply_args(fake_sd, cache_dir, backup_dir, tmp_path / "state", "--yes"))
    captured = capsys.readouterr()

    assert result == 0
    assert "No hay cambios que aplicar." in captured.out
    assert not backup_dir.exists()


def test_cli_apply_recovers_incomplete_transaction(
    fake_sd: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_package3(fake_sd, ams=(1, 11, 2, 0))
    cache_dir = tmp_path / "cache"
    _populate_plan_cache(cache_dir)
    backup_dir = tmp_path / "backups"
    state_dir = tmp_path / "state"
    manager = BackupManager(backup_dir)
    sd_id = get_sd_identity(fake_sd).key
    session = manager.create_session(
        fake_sd,
        sd_id,
        [
            FileOperation(
                relative_path="atmosphere/package3",
                kind=OperationKind.REPLACE,
            )
        ],
    )
    (fake_sd / "atmosphere/package3").write_bytes(b"parcial")
    manager.write_journal(
        session,
        status="applying",
        pending="atmosphere/package3",
    )

    result = main(_apply_args(fake_sd, cache_dir, backup_dir, state_dir, "--yes"))
    captured = capsys.readouterr()

    assert result == 0
    assert "Transacciones incompletas recuperadas:" in captured.out
    assert (fake_sd / "atmosphere/package3").read_bytes() == b"nuevo package3"
    assert manager.read_journal(session)["status"] == "rolled_back"
