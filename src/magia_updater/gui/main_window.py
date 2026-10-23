"""Ventana principal en español para Magia Updater."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from magia_updater.core.components import UPDATABLE_COMPONENTS, get_component
from magia_updater.core.detect import component_names
from magia_updater.core.export import ExportResult
from magia_updater.core.installer import ApplyResult
from magia_updater.core.models import (
    CheckStatus,
    ComponentCheck,
    ComponentId,
    InstalledComponent,
    OperationKind,
    SdSnapshot,
)
from magia_updater.core.settings import AppSettings, SettingsError, SettingsStore
from magia_updater.gui.backups_dialog import BackupsDialog
from magia_updater.gui.export_dialog import ExportDialog
from magia_updater.gui.service import GuiService, UpdateSession
from magia_updater.gui.settings_dialog import SettingsDialog
from magia_updater.gui.workers import FunctionWorker

STATUS_LABELS = {
    CheckStatus.UPDATE_AVAILABLE: "actualización disponible",
    CheckStatus.UP_TO_DATE: "al día",
    CheckStatus.NOT_INSTALLED: "no instalado",
    CheckStatus.INSTALLED_NEWER: "instalado más nuevo",
    CheckStatus.UNKNOWN: "sin datos",
}

OPERATION_LABELS = {
    OperationKind.CREATE: "crear",
    OperationKind.REPLACE: "reemplazar",
    OperationKind.KEEP: "mantener",
    OperationKind.SKIP: "omitir",
    OperationKind.CONFLICT: "conflicto",
}


def _format_installed(installed: InstalledComponent) -> str:
    if not installed.present:
        return "no detectado"
    if installed.version is None:
        return "presente (versión desconocida)"
    text = str(installed.version)
    if installed.evidence:
        text = f"{text} ({installed.evidence})"
    return text


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Magia Updater")
        self.resize(1080, 760)

        self.settings_store = SettingsStore()
        settings_error: str | None = None
        try:
            self.settings = self.settings_store.load()
        except SettingsError as exc:
            self.settings = AppSettings()
            settings_error = str(exc)
        self.service = GuiService(self.settings)

        self.snapshot: SdSnapshot | None = None
        self.session: UpdateSession | None = None
        self._busy = False
        self._workers: set[FunctionWorker] = set()

        self._build_ui()
        self._set_busy(False)
        self.log("Selecciona la carpeta raíz de la SD y pulsa Detectar.")
        if settings_error is not None:
            self.log(f"Aviso de ajustes: {settings_error}")
            QMessageBox.warning(self, "Ajustes", settings_error)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        sd_box = QGroupBox("SD de Nintendo Switch")
        sd_layout = QHBoxLayout(sd_box)
        self.sd_edit = QLineEdit()
        self.sd_edit.setPlaceholderText("Ruta raíz, por ejemplo E:\\")
        self.browse_button = QPushButton("Examinar...")
        self.detect_button = QPushButton("Detectar")
        self.browse_button.clicked.connect(self._browse_sd)
        self.detect_button.clicked.connect(self._detect)
        sd_layout.addWidget(QLabel("Ruta:"))
        sd_layout.addWidget(self.sd_edit, 1)
        sd_layout.addWidget(self.browse_button)
        sd_layout.addWidget(self.detect_button)
        layout.addWidget(sd_box)

        components_box = QGroupBox("Componentes")
        components_layout = QHBoxLayout(components_box)
        self.component_boxes: dict[ComponentId, QCheckBox] = {}
        for component in UPDATABLE_COMPONENTS:
            box = QCheckBox(get_component(component).name)
            box.setChecked(component in self.settings.components)
            self.component_boxes[component] = box
            components_layout.addWidget(box)
        components_layout.addStretch()
        layout.addWidget(components_box)

        status_box = QGroupBox("Estado")
        status_layout = QHBoxLayout(status_box)
        self.sd_status = QLabel("SD: sin detectar")
        self.cfw_status = QLabel("CFW: sin detectar")
        self.firmware_status = QLabel("Firmware: sin detectar")
        self.plan_status = QLabel("Plan: sin calcular")
        status_layout.addWidget(self.sd_status)
        status_layout.addWidget(self.cfw_status)
        status_layout.addWidget(self.firmware_status)
        status_layout.addWidget(self.plan_status)
        status_layout.addStretch()
        layout.addWidget(status_box)

        self.tabs = QTabWidget()
        self.component_table = self._table(["Componente", "Instalado", "Última release", "Estado"])
        self.plan_table = self._table(["Operación", "Ruta", "Componente", "Motivo"])
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.tabs.addTab(self.component_table, "Componentes")
        self.tabs.addTab(self.plan_table, "Plan")
        self.tabs.addTab(self.log_view, "Registro")
        layout.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        self.check_button = QPushButton("Buscar actualizaciones")
        self.apply_button = QPushButton("Aplicar cambios")
        self.export_button = QPushButton("Exportar paquete...")
        self.backups_button = QPushButton("Backups")
        self.settings_button = QPushButton("Ajustes")
        self.check_button.clicked.connect(self._check)
        self.apply_button.clicked.connect(self._apply)
        self.export_button.clicked.connect(self._export)
        self.backups_button.clicked.connect(self._backups)
        self.settings_button.clicked.connect(self._settings)
        actions.addWidget(self.check_button)
        actions.addWidget(self.apply_button)
        actions.addWidget(self.export_button)
        actions.addWidget(self.backups_button)
        actions.addStretch()
        actions.addWidget(self.settings_button)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        for column in range(len(headers) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)
        return table

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _root(self) -> Path | None:
        text = self.sd_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "SD", "Indica la ruta raíz de la SD.")
            return None
        return Path(text).expanduser()

    def _selected_components(self) -> tuple[ComponentId, ...]:
        selected = tuple(
            component for component, box in self.component_boxes.items() if box.isChecked()
        )
        if not selected:
            QMessageBox.warning(self, "Componentes", "Selecciona al menos un componente.")
        return selected

    def _browse_sd(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Seleccionar la raíz de la SD", self.sd_edit.text() or str(Path.home())
        )
        if selected:
            self.sd_edit.setText(selected)

    def _detect(self) -> None:
        root = self._root()
        if root is None:
            return
        self._run("Detectando SD...", lambda: self.service.detect(root), self._on_detected)

    def _on_detected(self, result: object) -> None:
        assert isinstance(result, SdSnapshot)
        self.snapshot = result
        self._clear_plan()
        self._populate_components(result)
        self.sd_status.setText(f"SD: {result.root}")
        self.cfw_status.setText(f"CFW: {'sí' if result.has_cfw else 'no'}")
        if result.firmware.version is None:
            self.firmware_status.setText("Firmware: desconocido")
        else:
            self.firmware_status.setText(f"Firmware: {result.firmware.version}")
        self.log(f"SD detectada: {result.root}")
        for warning in result.warnings:
            self.log(f"Aviso: {warning}")
        for error in result.errors:
            self.log(f"Error: {error}")
        if not result.is_switch_sd:
            QMessageBox.warning(self, "SD", "La carpeta no parece una SD de Switch.")
        self._update_actions()

    def _check(self) -> None:
        if self.snapshot is None:
            QMessageBox.information(self, "Buscar", "Primero detecta la SD.")
            return
        components = self._selected_components()
        if not components:
            return
        snapshot = self.snapshot
        self._run(
            "Buscando actualizaciones...",
            lambda: self.service.build_plan(snapshot, components=components, refresh=True),
            self._on_session,
        )

    def _on_session(self, result: object) -> None:
        assert isinstance(result, UpdateSession)
        self.session = result
        self._populate_components(result.snapshot, result.checks)
        self._populate_plan(result)
        self.plan_status.setText(
            f"Plan: {result.change_count} cambios, {len(result.plan.warnings)} avisos"
        )
        self.log(
            f"Plan calculado: {result.change_count} cambios en {len(result.plan.components)} componentes."
        )
        for warning in result.plan.warnings:
            self.log(f"Aviso del plan: {warning}")
        if result.plan.conflicts:
            QMessageBox.warning(self, "Plan", "El plan contiene conflictos y no se puede aplicar.")
        self.tabs.setCurrentWidget(self.plan_table)
        self._update_actions()

    def _populate_components(
        self,
        snapshot: SdSnapshot,
        checks: tuple[ComponentCheck, ...] = (),
    ) -> None:
        names = component_names()
        checks_by_component = {check.component: check for check in checks}
        rows: list[tuple[str, str, str, str]] = []
        for component in (
            ComponentId.ATMOSPHERE,
            ComponentId.HEKATE,
            ComponentId.SYS_PATCH,
            ComponentId.HBMENU,
        ):
            check = checks_by_component.get(component)
            latest = str(check.latest) if check is not None and check.latest else "—"
            status = STATUS_LABELS[check.status] if check is not None else "—"
            rows.append(
                (
                    names[component],
                    _format_installed(snapshot.components[component]),
                    latest,
                    status,
                )
            )
        self._set_rows(self.component_table, rows)

    def _populate_plan(self, session: UpdateSession) -> None:
        names = component_names()
        rows: list[tuple[str, str, str, str]] = []
        for component_plan in session.plan.components:
            for operation in component_plan.operations:
                path = operation.relative_path
                if operation.requires_confirmation:
                    path = f"{path} [confirmar]"
                rows.append(
                    (
                        OPERATION_LABELS[operation.kind],
                        path,
                        names[component_plan.component],
                        operation.reason,
                    )
                )
        self._set_rows(self.plan_table, rows)

    def _set_rows(self, table: QTableWidget, rows: Sequence[Sequence[str]]) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row_index, column, QTableWidgetItem(value))

    def _clear_plan(self) -> None:
        self.session = None
        self.plan_table.setRowCount(0)
        self.plan_status.setText("Plan: sin calcular")
        self._update_actions()

    def _update_actions(self) -> None:
        has_sd = self.snapshot is not None and self.snapshot.is_switch_sd
        has_changes = (
            self.session is not None
            and bool(self.session.plan.changes)
            and not self.session.plan.conflicts
        )
        has_export = self.session is not None and bool(self.session.plan.components)
        self.browse_button.setEnabled(not self._busy)
        self.detect_button.setEnabled(not self._busy)
        self.check_button.setEnabled(not self._busy and has_sd)
        self.apply_button.setEnabled(not self._busy and has_changes)
        self.export_button.setEnabled(not self._busy and has_export)
        self.backups_button.setEnabled(not self._busy and has_sd)
        self.settings_button.setEnabled(not self._busy)
        for box in self.component_boxes.values():
            box.setEnabled(not self._busy)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self._update_actions()

    def _run(
        self,
        message: str,
        function: Callable[[], Any],
        on_success: Callable[[object], None],
    ) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self.log(message)
        worker = FunctionWorker(function)
        self._workers.add(worker)

        def success(result: object) -> None:
            self._workers.discard(worker)
            self._set_busy(False)
            on_success(result)

        def failure(message_text: str) -> None:
            self._workers.discard(worker)
            self._set_busy(False)
            self.log(f"Error: {message_text}")
            QMessageBox.critical(self, "Error", message_text)

        worker.signals.finished.connect(success)
        worker.signals.failed.connect(failure)
        QThreadPool.globalInstance().start(worker)

    def _apply(self) -> None:
        if self.session is None or not self.session.plan.changes:
            QMessageBox.information(self, "Aplicar", "No hay cambios para aplicar.")
            return
        session = self.session
        details = [
            f"Componentes: {len(session.plan.components)}",
            f"Archivos a escribir: {len(session.plan.changes)}",
            f"Backup local: {self.settings.backup_dir}",
        ]
        if self.settings.mirror_backup:
            details.append("También se copiará el backup a /.magia/backups en la SD.")
        for warning in session.plan.warnings:
            details.append(f"Aviso: {warning}")
        answer = QMessageBox.question(
            self,
            "Aplicar actualización",
            "Se creará un backup antes de escribir.\n\n" + "\n".join(details) + "\n\n¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        snapshot = session.snapshot
        self._run(
            "Recuperando transacciones incompletas...",
            lambda: self.service.recover(snapshot),
            self._after_recover,
        )

    def _after_recover(self, result: object) -> None:
        assert isinstance(result, tuple)
        if result:
            self.log(f"Transacciones recuperadas: {len(result)}")
            for path in result:
                self.log(f"  - {path}")
            QMessageBox.information(
                self,
                "Recuperación",
                "Se recuperaron transacciones incompletas. Vuelve a buscar actualizaciones.",
            )
            self._detect()
            return
        if self.session is None:
            return
        session = self.session
        self._run(
            "Aplicando actualización...", lambda: self.service.apply(session), self._on_applied
        )

    def _on_applied(self, result: object) -> None:
        assert isinstance(result, ApplyResult)
        self.log(f"Actualización aplicada. Backup: {result.backup_dir}")
        if result.state_path is not None:
            self.log(f"Estado: {result.state_path}")
        for warning in result.warnings:
            self.log(f"Aviso: {warning}")
        QMessageBox.information(
            self,
            "Actualización",
            f"Se aplicaron {len(result.applied)} archivos.\nBackup: {result.backup_dir}",
        )
        self._clear_plan()
        self._detect()

    def _export(self) -> None:
        if self.session is None:
            QMessageBox.information(self, "Exportar", "Primero calcula un plan.")
            return
        dialog = ExportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        session = self.session
        kind = dialog.kind()
        export_format = dialog.format()
        output = dialog.output()
        self._run(
            "Exportando paquete...",
            lambda: self.service.export(session, output, kind=kind, format=export_format),
            self._on_exported,
        )

    def _on_exported(self, result: object) -> None:
        assert isinstance(result, ExportResult)
        self.log(f"Paquete exportado: {result.path}")
        self.log(
            f"Tipo: {result.kind.value}; archivos: {result.file_count}; "
            f"tamaño: {_format_size(result.total_size)}"
        )
        QMessageBox.information(
            self,
            "Exportar",
            f"Paquete creado:\n{result.path}\n\nArchivos: {result.file_count}",
        )

    def _backups(self) -> None:
        if self.snapshot is None:
            QMessageBox.information(self, "Backups", "Primero detecta la SD.")
            return
        BackupsDialog(self.service, self.snapshot, self).exec()

    def _settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.settings()
        try:
            self.settings_store.save(settings)
        except (SettingsError, OSError) as exc:
            QMessageBox.critical(self, "Ajustes", f"No se pudieron guardar: {exc}")
            return
        self.settings = settings
        self.service.set_settings(settings)
        for component, box in self.component_boxes.items():
            box.setChecked(component in settings.components)
        self._clear_plan()
        self.log("Ajustes guardados.")
        self._update_actions()
