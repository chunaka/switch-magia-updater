"""Punto de entrada de la interfaz gráfica."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # pragma: no cover - depende del extra opcional
        raise SystemExit('PySide6 no está instalado. Ejecuta: pip install -e ".[gui]"') from exc

    from magia_updater.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Magia Updater")
    app.setOrganizationName("Magia Updater")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
