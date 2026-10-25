"""Punto de entrada de la interfaz gráfica."""

from __future__ import annotations

import sys
from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


def _load_icon() -> QIcon:
    from PySide6.QtGui import QIcon

    try:
        path = files("magia_updater").joinpath("assets/magia.ico")
        return QIcon(str(path))
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return QIcon()


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # pragma: no cover - depende del extra opcional
        raise SystemExit('PySide6 no está instalado. Ejecuta: pip install -e ".[gui]"') from exc

    from magia_updater.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Magia Updater")
    app.setApplicationDisplayName("Magia Updater")
    app.setOrganizationName("Magia Updater")
    icon = _load_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
