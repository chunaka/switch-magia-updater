from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from magia_updater.core.devices import SdCandidate  # noqa: E402
from magia_updater.core.settings import AppSettings  # noqa: E402
from magia_updater.gui.backups_dialog import BackupsDialog  # noqa: E402
from magia_updater.gui.export_dialog import ExportDialog  # noqa: E402
from magia_updater.gui.main_window import MainWindow  # noqa: E402
from magia_updater.gui.service import GuiService  # noqa: E402
from magia_updater.gui.settings_dialog import SettingsDialog  # noqa: E402


def test_main_window_constructs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])

    window = MainWindow()

    assert app is not None
    assert window.windowTitle() == "Magia Updater"
    assert not window.check_button.isEnabled()
    assert not window.apply_button.isEnabled()
    assert not window.export_button.isEnabled()
    window.close()


def test_main_window_accepts_detected_snapshot(
    fake_sd: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    window._on_detected(window.service.detect(fake_sd))

    assert app is not None
    assert window.check_button.isEnabled()
    assert window.component_table.rowCount() == 4
    window.close()


def test_main_window_autoselects_single_detected_sd(
    fake_sd: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])
    candidate = SdCandidate(
        root=fake_sd.resolve(),
        drive_type="extraíble",
        markers=("atmosphere/package3",),
    )
    window = MainWindow(detect_devices=lambda: (candidate,), auto_detect=False)
    calls: list[bool] = []
    monkeypatch.setattr(window, "_detect", lambda: calls.append(True))

    window._poll_devices(initial=True)

    assert window.sd_combo.currentText() == str(candidate.root)
    assert window._pending_auto_check is True
    assert calls == [True]
    window.close()


def test_main_window_autoselects_replacement_sd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])
    first = SdCandidate(
        root=(tmp_path / "first-sd").resolve(),
        drive_type="extraíble",
        markers=("atmosphere/package3",),
    )
    second = SdCandidate(
        root=(tmp_path / "second-sd").resolve(),
        drive_type="extraíble",
        markers=("atmosphere/package3",),
    )
    candidates: tuple[SdCandidate, ...] = (second,)
    window = MainWindow(detect_devices=lambda: candidates, auto_detect=False)
    window._last_candidates = (first,)
    window.sd_combo.setEditText(str(first.root))
    calls: list[bool] = []
    monkeypatch.setattr(window, "_detect", lambda: calls.append(True))

    window._poll_devices()

    assert window.sd_combo.currentText() == str(second.root)
    assert calls == [True]
    window.close()


def test_main_window_keeps_manual_path_when_sd_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])
    detected = SdCandidate(
        root=(tmp_path / "detected-sd").resolve(),
        drive_type="extraíble",
        markers=("atmosphere/package3",),
    )
    manual = tmp_path / "manual-folder"
    window = MainWindow(
        detect_devices=lambda: (detected,),
        auto_detect=False,
    )
    window.sd_combo.setEditText(str(manual))
    calls: list[bool] = []
    monkeypatch.setattr(window, "_detect", lambda: calls.append(True))

    window._poll_devices()

    assert window.sd_combo.currentText() == str(manual)
    assert calls == []
    window.close()


def test_main_window_does_not_guess_with_several_sds(
    fake_sd: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])
    second = tmp_path / "second-sd"
    second.mkdir()
    candidates = (
        SdCandidate(root=fake_sd.resolve(), drive_type="extraíble", markers=()),
        SdCandidate(root=second.resolve(), drive_type="extraíble", markers=()),
    )
    window = MainWindow(detect_devices=lambda: candidates, auto_detect=False)
    calls: list[bool] = []
    monkeypatch.setattr(window, "_detect", lambda: calls.append(True))

    window._poll_devices(initial=True)

    assert calls == []
    assert "varias SD" in window.auto_label.text()
    window.close()


def test_main_window_clears_snapshot_when_sd_disconnects(
    fake_sd: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])
    candidate = SdCandidate(
        root=fake_sd.resolve(),
        drive_type="extraíble",
        markers=("atmosphere/package3",),
    )
    candidates: tuple[SdCandidate, ...] = (candidate,)
    window = MainWindow(detect_devices=lambda: candidates, auto_detect=False)
    window._on_detected(window.service.detect(fake_sd))
    window._auto_root = candidate.root
    window._last_candidates = (candidate,)

    candidates = ()
    window._poll_devices()

    assert window.snapshot is None
    assert window.apply_button.isEnabled() is False
    window.close()


def test_primary_action_checks_when_no_plan_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])
    window = MainWindow(detect_devices=lambda: (), auto_detect=False)
    calls: list[str] = []
    monkeypatch.setattr(window, "_check", lambda: calls.append("check"))

    window._primary_action()

    assert calls == ["check"]
    window.close()


def test_settings_dialog_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])
    settings = AppSettings(
        cache_dir=tmp_path / "cache",
        backup_dir=tmp_path / "backups",
        state_dir=tmp_path / "state",
        keep_backups=4,
    )

    dialog = SettingsDialog(settings)

    assert dialog.settings() == settings
    assert dialog.keep_spin.value() == 4
    dialog.close()


def test_export_dialog_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])

    dialog = ExportDialog()

    assert dialog.kind().value == "delta"
    assert dialog.format().value == "zip"
    assert dialog.output().name.endswith(".zip")
    dialog.close()


def test_backups_dialog_constructs(
    fake_sd: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])
    settings = AppSettings(
        cache_dir=tmp_path / "cache",
        backup_dir=tmp_path / "backups",
        state_dir=tmp_path / "state",
    )
    service = GuiService(settings)
    snapshot = service.detect(fake_sd)

    dialog = BackupsDialog(service, snapshot)

    assert dialog.table.rowCount() == 0
    dialog.close()
