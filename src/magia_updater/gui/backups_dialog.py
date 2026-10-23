"""Diálogo para consultar y eliminar backups locales."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from magia_updater.core.models import SdSnapshot
from magia_updater.gui.service import BackupInfo, GuiService


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


class BackupsDialog(QDialog):
    def __init__(
        self,
        service: GuiService,
        snapshot: SdSnapshot,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Backups de la SD")
        self.setMinimumSize(820, 420)
        self.service = service
        self.snapshot = snapshot
        self._backups: tuple[BackupInfo, ...] = ()

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Fecha", "Archivos", "Tamaño", "Carpeta"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        open_button = QPushButton("Abrir carpeta")
        open_button.clicked.connect(self._open_selected)
        delete_button = QPushButton("Eliminar backup")
        delete_button.clicked.connect(self._delete_selected)
        refresh_button = QPushButton("Actualizar")
        refresh_button.clicked.connect(self._refresh)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Cerrar")
        buttons.rejected.connect(self.reject)

        actions = QHBoxLayout()
        actions.addWidget(open_button)
        actions.addWidget(delete_button)
        actions.addWidget(refresh_button)
        actions.addStretch()
        actions.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(actions)
        self._refresh()

    def _refresh(self) -> None:
        try:
            self._backups = self.service.list_backups(self.snapshot)
        except Exception as exc:  # noqa: BLE001 - se muestra en la GUI
            QMessageBox.critical(self, "Backups", str(exc))
            return
        self.table.setRowCount(len(self._backups))
        for row, info in enumerate(self._backups):
            values = (
                info.session.created_at,
                str(len(info.session.entries)),
                _format_size(info.size),
                str(info.session.backup_dir),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

    def _selected(self) -> BackupInfo | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._backups):
            return None
        return self._backups[row]

    def _open_selected(self) -> None:
        info = self._selected()
        if info is None:
            QMessageBox.information(self, "Backups", "Selecciona un backup.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(info.session.backup_dir)))

    def _delete_selected(self) -> None:
        info = self._selected()
        if info is None:
            QMessageBox.information(self, "Backups", "Selecciona un backup.")
            return
        answer = QMessageBox.question(
            self,
            "Eliminar backup",
            f"¿Eliminar definitivamente el backup {info.session.backup_dir.name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_backup(info)
        except Exception as exc:  # noqa: BLE001 - se muestra en la GUI
            QMessageBox.critical(self, "Backups", str(exc))
            return
        self._refresh()
