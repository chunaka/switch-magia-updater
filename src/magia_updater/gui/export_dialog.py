"""Diálogo para elegir el formato y destino de una exportación."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from magia_updater.core.export import ExportFormat, ExportKind


def _default_output() -> Path:
    desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    base = Path(desktop) if desktop else Path.home()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return base / f"magia-update-{stamp}.zip"


class ExportDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Exportar paquete de actualización")
        self.setMinimumWidth(620)

        self.kind_combo = QComboBox()
        self.kind_combo.addItem("Delta (solo cambios)", ExportKind.DELTA.value)
        self.kind_combo.addItem("Full (incluye archivos idénticos)", ExportKind.FULL.value)
        self.format_combo = QComboBox()
        self.format_combo.addItem("ZIP", ExportFormat.ZIP.value)
        self.format_combo.addItem("Carpeta", ExportFormat.FOLDER.value)
        self.output_edit = QLineEdit(str(_default_output()))

        form = QFormLayout()
        form.addRow("Contenido:", self.kind_combo)
        form.addRow("Formato:", self.format_combo)
        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_edit)
        browse = QPushButton("Examinar...")
        browse.clicked.connect(self._choose_output)
        output_layout.addWidget(browse)
        form.addRow("Destino:", output_row)

        note = QLabel(
            "La exportación no modifica la SD. El paquete incluye manifest.json y LEEME.txt "
            "con las rutas, tamaños y hashes de los archivos."
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Exportar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _choose_output(self) -> None:
        current = self.output_edit.text().strip()
        if self.format_combo.currentData() == ExportFormat.ZIP.value:
            selected, _ = QFileDialog.getSaveFileName(
                self,
                "Guardar paquete ZIP",
                current or str(_default_output()),
                "Paquete ZIP (*.zip)",
            )
            if selected:
                if not selected.lower().endswith(".zip"):
                    selected += ".zip"
                self.output_edit.setText(selected)
            return

        start = str(Path(current).parent) if current else str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta destino", start)
        if selected:
            self.output_edit.setText(str(Path(selected) / "magia-update"))

    def _accept(self) -> None:
        output = self.output_edit.text().strip()
        if not output:
            QMessageBox.warning(self, "Exportar", "Indica una ruta de destino.")
            return
        if Path(output).exists():
            QMessageBox.warning(
                self,
                "Exportar",
                "El destino ya existe. Elige una ruta nueva para no sobrescribir nada.",
            )
            return
        self.accept()

    def kind(self) -> ExportKind:
        return ExportKind(str(self.kind_combo.currentData()))

    def format(self) -> ExportFormat:
        return ExportFormat(str(self.format_combo.currentData()))

    def output(self) -> Path:
        return Path(self.output_edit.text().strip()).expanduser()
