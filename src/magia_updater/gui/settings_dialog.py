"""Diálogo de ajustes de la GUI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from magia_updater.core.components import UPDATABLE_COMPONENTS, get_component
from magia_updater.core.models import ComponentId
from magia_updater.core.settings import AppSettings


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ajustes de Magia Updater")
        self.setMinimumWidth(620)
        self._result = settings
        self._settings = settings

        self.cache_edit = QLineEdit(str(settings.cache_dir))
        self.backup_edit = QLineEdit(str(settings.backup_dir))
        self.state_edit = QLineEdit(str(settings.state_dir))
        self.keep_spin = QSpinBox()
        self.keep_spin.setRange(1, 99)
        self.keep_spin.setValue(settings.keep_backups)
        self.mirror_check = QCheckBox("Copiar también el backup a /.magia/backups en la SD")
        self.mirror_check.setChecked(settings.mirror_backup)
        self.offline_check = QCheckBox("Modo offline (usar solo la caché local)")
        self.offline_check.setChecked(settings.offline)
        self.install_payloads_check = QCheckBox(
            "Instalar payloads conocidos en /bootloader/payloads"
        )
        self.install_payloads_check.setChecked(settings.install_payloads)
        self.reboot_payload_check = QCheckBox("Permitir actualizar /atmosphere/reboot_payload.bin")
        self.reboot_payload_check.setChecked(settings.allow_reboot_payload)
        self.root_payload_check = QCheckBox("Permitir crear o actualizar /payload.bin")
        self.root_payload_check.setChecked(settings.allow_root_payload)

        self.component_boxes: dict[ComponentId, QCheckBox] = {}
        components_box = QGroupBox("Componentes a actualizar")
        components_layout = QHBoxLayout(components_box)
        for component in UPDATABLE_COMPONENTS:
            box = QCheckBox(get_component(component).name)
            box.setChecked(component in settings.components)
            self.component_boxes[component] = box
            components_layout.addWidget(box)
        components_layout.addStretch()

        paths_box = QGroupBox("Rutas")
        paths_layout = QFormLayout(paths_box)
        paths_layout.addRow("Caché:", self._path_row(self.cache_edit))
        paths_layout.addRow("Backups:", self._path_row(self.backup_edit))
        paths_layout.addRow("Estado:", self._path_row(self.state_edit))
        paths_layout.addRow("Backups a conservar:", self.keep_spin)

        payloads_box = QGroupBox("Payloads y comportamiento")
        payloads_layout = QVBoxLayout(payloads_box)
        payloads_layout.addWidget(self.install_payloads_check)
        payloads_layout.addWidget(self.reboot_payload_check)
        payloads_layout.addWidget(self.root_payload_check)
        payloads_layout.addWidget(self.mirror_check)
        payloads_layout.addWidget(self.offline_check)

        note = QLabel(
            "Los payloads protegidos solo se modifican si activas su casilla. "
            "La configuración de la SD nunca se sobrescribe."
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(components_box)
        layout.addWidget(paths_box)
        layout.addWidget(payloads_box)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _path_row(self, edit: QLineEdit) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        button = QPushButton("Examinar...")
        button.clicked.connect(lambda: self._choose_directory(edit))
        layout.addWidget(button)
        return widget

    def _choose_directory(self, edit: QLineEdit) -> None:
        start = edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta", start)
        if selected:
            edit.setText(selected)

    def _accept(self) -> None:
        components = tuple(
            component for component, box in self.component_boxes.items() if box.isChecked()
        )
        if not components:
            QMessageBox.warning(self, "Ajustes", "Selecciona al menos un componente.")
            return
        paths = {
            "caché": self.cache_edit.text().strip(),
            "backups": self.backup_edit.text().strip(),
            "estado": self.state_edit.text().strip(),
        }
        for label, value in paths.items():
            if not value:
                QMessageBox.warning(self, "Ajustes", f"La ruta de {label} no puede estar vacía.")
                return
        self._result = AppSettings(
            cache_dir=Path(paths["caché"]).expanduser(),
            backup_dir=Path(paths["backups"]).expanduser(),
            state_dir=Path(paths["estado"]).expanduser(),
            keep_backups=self.keep_spin.value(),
            mirror_backup=self.mirror_check.isChecked(),
            offline=self.offline_check.isChecked(),
            install_payloads=self.install_payloads_check.isChecked(),
            allow_reboot_payload=self.reboot_payload_check.isChecked(),
            allow_root_payload=self.root_payload_check.isChecked(),
            components=components,
        )
        self.accept()

    def settings(self) -> AppSettings:
        return self._result
