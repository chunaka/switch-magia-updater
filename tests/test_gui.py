from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from magia_updater.core.settings import AppSettings  # noqa: E402
from magia_updater.gui.export_dialog import ExportDialog  # noqa: E402
from magia_updater.gui.main_window import MainWindow  # noqa: E402
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
