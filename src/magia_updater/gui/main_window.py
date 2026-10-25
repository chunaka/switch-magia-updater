"""Ventana principal en español para Magia Updater."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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

from magia_updater.core.components import (
    UPDATABLE_COMPONENTS,
    get_component,
    recommended_components,
)
from magia_updater.core.detect import component_names
from magia_updater.core.devices import SdCandidate, find_switch_sds
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
from magia_updater.gui.workers import FunctionWorker, ProgressBridge

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
    def __init__(
        self,
        *,
        detect_devices: Callable[[], tuple[SdCandidate, ...]] | None = None,
        auto_detect: bool = True,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Magia Updater")
        self.resize(1080, 800)

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
        self._progress_bridge: ProgressBridge | None = None

        self._detect_devices = detect_devices if detect_devices is not None else find_switch_sds
        self._auto_detect = auto_detect
        self._auto_started = False
        self._pending_auto_check = False
        self._auto_root: Path | None = None
        self._last_candidates: tuple[SdCandidate, ...] = ()
        self._components_initialized_for: Path | None = None

        self._build_ui()
        self._set_busy(False)
        self.log("Conecta la SD de tu Switch y espera a que se detecte automáticamente.")
        if settings_error is not None:
            self.log(f"Aviso de ajustes: {settings_error}")
            QMessageBox.warning(self, "Ajustes", settings_error)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        title = QLabel("Magia Updater")
        title_font = title.font()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        subtitle = QLabel(
            "Actualiza Atmosphère, Hekate y sys-patch sin tocar tu configuración."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        sd_box = QGroupBox("SD de Nintendo Switch")
        sd_layout = QVBoxLayout(sd_box)
        sd_row = QHBoxLayout()
        self.sd_combo = QComboBox()
        self.sd_combo.setEditable(True)
        self.sd_combo.setMinimumWidth(360)
        line_edit = self.sd_combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("E:\\")
            line_edit.returnPressed.connect(self._detect)
        self.sd_combo.setToolTip(
            "Elige una unidad detectada o escribe la ruta raíz de la SD."
        )
        self.browse_button = QPushButton("Elegir carpeta...")
        self.refresh_button = QPushButton("Buscar SD")
        self.detect_button = QPushButton("Detectar")
        self.browse_button.clicked.connect(self._browse_sd)
        self.refresh_button.clicked.connect(self._refresh_devices)
        self.detect_button.clicked.connect(self._detect)
        self.sd_combo.currentIndexChanged.connect(self._on_sd_choice_changed)
        sd_row.addWidget(QLabel("Unidad:"))
        sd_row.addWidget(self.sd_combo, 1)
        sd_row.addWidget(self.browse_button)
        sd_row.addWidget(self.refresh_button)
        sd_row.addWidget(self.detect_button)
        sd_layout.addLayout(sd_row)
        self.auto_label = QLabel("Buscando la SD...")
        self.auto_label.setWordWrap(True)
        sd_layout.addWidget(self.auto_label)
        layout.addWidget(sd_box)

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

        self.component_table = self._table(
            ["Componente", "Instalado", "Última release", "Estado"]
        )
        layout.addWidget(self.component_table, 2)

        actions = QHBoxLayout()
        self.apply_button = QPushButton("Conecta la SD")
        self.apply_button.setMinimumHeight(38)
        self.apply_button.setDefault(True)
        self.check_button = QPushButton("Buscar de nuevo")
        self.advanced_button = QPushButton("Avanzado ▾")
        self.advanced_button.setCheckable(True)
        self.apply_button.clicked.connect(self._primary_action)
        self.check_button.clicked.connect(self._check)
        actions.addWidget(self.apply_button)
        actions.addWidget(self.check_button)
        actions.addStretch()
        actions.addWidget(self.advanced_button)
        layout.addLayout(actions)

        self.stage_label = QLabel("")
        self.stage_label.setWordWrap(True)
        layout.addWidget(self.stage_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.advanced_panel = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)

        components_box = QGroupBox("Componentes a actualizar")
        components_layout = QHBoxLayout(components_box)
        self.component_boxes: dict[ComponentId, QCheckBox] = {}
        for component in UPDATABLE_COMPONENTS:
            box = QCheckBox(get_component(component).name)
            box.setChecked(component in self.settings.components)
            self.component_boxes[component] = box
            components_layout.addWidget(box)
        components_layout.addStretch()
        advanced_layout.addWidget(components_box)

        self.tabs = QTabWidget()
        self.plan_table = self._table(["Operación", "Ruta", "Componente", "Motivo"])
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.tabs.addTab(self.plan_table, "Plan")
        self.tabs.addTab(self.log_view, "Registro")
        advanced_layout.addWidget(self.tabs, 1)

        advanced_actions = QHBoxLayout()
        self.export_button = QPushButton("Exportar paquete...")
        self.backups_button = QPushButton("Backups")
        self.settings_button = QPushButton("Ajustes")
        self.export_button.clicked.connect(self._export)
        self.backups_button.clicked.connect(self._backups)
        self.settings_button.clicked.connect(self._settings)
        advanced_actions.addWidget(self.export_button)
        advanced_actions.addWidget(self.backups_button)
        advanced_actions.addStretch()
        advanced_actions.addWidget(self.settings_button)
        advanced_layout.addLayout(advanced_actions)

        self.advanced_panel.setVisible(False)
        self.advanced_button.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_panel, 1)

        self._device_timer = QTimer(self)
        self._device_timer.setInterval(2000)
        self._device_timer.timeout.connect(self._poll_devices)

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

    def _toggle_advanced(self, visible: bool) -> None:
        self.advanced_panel.setVisible(visible)
        self.advanced_button.setText("Avanzado ▴" if visible else "Avanzado ▾")

    # ------------------------------------------------------ SD auto-detect

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._auto_detect or self._auto_started:
            return
        self._auto_started = True
        self._poll_devices(initial=True)
        self._device_timer.start()

    def _refresh_devices(self) -> None:
        self._last_candidates = ()
        self._poll_devices(initial=True)

    def _poll_devices(self, *, initial: bool = False) -> None:
        if self._busy and not initial:
            return
        if QApplication.activeModalWidget() is not None:
            return
        try:
            candidates = self._detect_devices()
        except Exception as exc:  # noqa: BLE001 - se muestra en la GUI
            self.auto_label.setText(f"No se pudo buscar la SD: {exc}")
            return

        roots = tuple(str(candidate.root) for candidate in candidates)
        previous = tuple(str(candidate.root) for candidate in self._last_candidates)
        if not initial and roots == previous:
            return
        current_text = self.sd_combo.currentText().strip()
        self._last_candidates = candidates
        self._populate_sd_combo(candidates)

        if not candidates:
            self.auto_label.setText(
                "No se detectó ninguna SD. Conéctala al PC y pulsa «Buscar SD»."
            )
            if self._auto_root is not None and self.snapshot is not None:
                self._clear_snapshot("La SD se desconectó.")
            return

        if len(candidates) == 1:
            self.auto_label.setText(f"SD detectada: {candidates[0].display_name}")
        else:
            roots_text = ", ".join(str(candidate.root) for candidate in candidates)
            self.auto_label.setText(
                f"Se detectaron varias SD: {roots_text}. Elige una en la lista."
            )

        preferred: SdCandidate | None = None
        if self.settings.last_sd_root is not None:
            preferred = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.root == self.settings.last_sd_root
                ),
                None,
            )
        if preferred is None and current_text:
            preferred = next(
                (
                    candidate
                    for candidate in candidates
                    if str(candidate.root) == current_text
                ),
                None,
            )
        previous_paths = set(previous)
        current_is_manual = (
            bool(current_text)
            and current_text not in previous_paths
            and not any(str(candidate.root) == current_text for candidate in candidates)
        )
        if preferred is None and len(candidates) == 1 and not current_is_manual:
            preferred = candidates[0]
        if preferred is None:
            return
        if self.snapshot is not None and self.snapshot.root == preferred.root:
            return
        if self._busy:
            return

        self.sd_combo.setEditText(str(preferred.root))
        self._auto_root = preferred.root
        self._pending_auto_check = True
        self._detect()

    def _populate_sd_combo(self, candidates: tuple[SdCandidate, ...]) -> None:
        current = self.sd_combo.currentText().strip()
        self.sd_combo.blockSignals(True)
        self.sd_combo.clear()
        for candidate in candidates:
            self.sd_combo.addItem(str(candidate.root))
            index = self.sd_combo.count() - 1
            details = ", ".join(candidate.markers)
            self.sd_combo.setItemData(
                index,
                f"{candidate.drive_type}: {details}",
                Qt.ItemDataRole.ToolTipRole,
            )
        texts = [self.sd_combo.itemText(index) for index in range(self.sd_combo.count())]
        if current and current not in texts:
            self.sd_combo.setEditText(current)
        elif candidates:
            selected = next(
                (
                    index
                    for index, candidate in enumerate(candidates)
                    if str(candidate.root) == current
                ),
                0,
            )
            self.sd_combo.setCurrentIndex(selected)
        self.sd_combo.blockSignals(False)

    def _on_sd_choice_changed(self, index: int) -> None:
        if index < 0 or self._busy:
            return
        text = self.sd_combo.itemText(index).strip()
        if not text:
            return
        if self.snapshot is not None and str(self.snapshot.root) == text:
            return
        self._auto_root = Path(text)
        self._detect()

    def _browse_sd(self) -> None:
        start = self.sd_combo.currentText().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(
            self, "Seleccionar la raíz de la SD", start
        )
        if selected:
            self.sd_combo.setEditText(selected)
            self._auto_root = None
            self._detect()

    def _root(self) -> Path | None:
        text = self.sd_combo.currentText().strip()
        if not text:
            QMessageBox.warning(
                self, "SD", "Conecta la SD de tu Switch o elige su carpeta."
            )
            return None
        root = Path(text).expanduser()
        if not root.is_dir():
            QMessageBox.warning(
                self,
                "SD",
                f"La carpeta no existe o no está disponible:\n{root}",
            )
            return None
        return root

    def _clear_snapshot(self, message: str) -> None:
        removed = self._auto_root
        self.snapshot = None
        self._auto_root = None
        self._components_initialized_for = None
        self._clear_plan()
        self.component_table.setRowCount(0)
        self.sd_status.setText("SD: sin detectar")
        self.cfw_status.setText("CFW: sin detectar")
        self.firmware_status.setText("Firmware: sin detectar")
        self.stage_label.setText(message)
        self.log(message)
        if removed is not None and self.sd_combo.currentText().strip() == str(removed):
            self.sd_combo.setEditText("")

    # -------------------------------------------------------------- actions

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _selected_components(
        self, *, show_warning: bool = True
    ) -> tuple[ComponentId, ...]:
        selected = tuple(
            component for component, box in self.component_boxes.items() if box.isChecked()
        )
        if not selected and show_warning:
            QMessageBox.warning(
                self, "Componentes", "Selecciona al menos un componente."
            )
        return selected

    def _set_component_boxes(self, components: tuple[ComponentId, ...]) -> None:
        selected = set(components)
        for component, box in self.component_boxes.items():
            box.setChecked(component in selected)

    def _remember_sd(self, root: Path) -> None:
        if self.settings.last_sd_root == root:
            return
        settings = replace(self.settings, last_sd_root=root)
        try:
            self.settings_store.save(settings)
        except (SettingsError, OSError) as exc:
            self.log(f"No se pudo recordar la SD: {exc}")
            return
        self.settings = settings
        self.service.set_settings(settings)

    def _detect(self) -> None:
        if self._busy:
            return
        root = self._root()
        if root is None:
            return
        self._pending_auto_check = True
        self._run(
            "Detectando la SD...",
            lambda: self.service.detect(root),
            self._on_detected,
        )

    def _on_detected(self, result: object) -> None:
        assert isinstance(result, SdSnapshot)
        self.snapshot = result
        self._clear_plan()
        if self._components_initialized_for != result.root:
            self._set_component_boxes(recommended_components(result))
            self._components_initialized_for = result.root
        self._populate_components(result)
        self.sd_status.setText(f"SD: {result.root}")
        self.cfw_status.setText(f"CFW: {'sí' if result.has_cfw else 'no'}")
        if result.firmware.version is None:
            self.firmware_status.setText("Firmware: desconocido")
        else:
            self.firmware_status.setText(f"Firmware: {result.firmware.version}")
        if not self._last_candidates:
            self.auto_label.setText(f"SD seleccionada: {result.root}")
        self.log(f"SD detectada: {result.root}")
        for warning in result.warnings:
            self.log(f"Aviso: {warning}")
        for error in result.errors:
            self.log(f"Error: {error}")
        if not result.is_switch_sd:
            QMessageBox.warning(self, "SD", "La carpeta no parece una SD de Switch.")
        else:
            remembered = self._auto_root if self._auto_root is not None else result.root
            self._remember_sd(remembered)
        self._update_actions()

        should_check = self._pending_auto_check
        self._pending_auto_check = False
        if not should_check or not result.is_switch_sd:
            return
        components = self._selected_components(show_warning=False)
        if components:
            self._check()
        else:
            self.stage_label.setText("No hay componentes CFW instalados que actualizar.")
            self.log("No se detectaron componentes CFW que actualizar.")

    def _check(self) -> None:
        if self._busy:
            return
        if self.snapshot is None:
            QMessageBox.information(
                self, "Buscar", "Primero conecta o detecta la SD."
            )
            return
        components = self._selected_components()
        if not components:
            return
        snapshot = self.snapshot
        bridge = ProgressBridge(self)
        bridge.progress.connect(self._on_progress)
        self._progress_bridge = bridge
        self._run(
            "Buscando actualizaciones y descargando lo necesario...",
            lambda: self.service.build_plan(
                snapshot,
                components=components,
                refresh=True,
                progress=bridge.report,
            ),
            self._on_session,
        )

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
            return
        self.progress.setRange(0, total)
        self.progress.setValue(min(done, total))
        self.progress.setFormat(f"Descargando… {done * 100 // total} %")

    def _release_progress_bridge(self) -> None:
        bridge = self._progress_bridge
        self._progress_bridge = None
        if bridge is not None:
            bridge.deleteLater()

    def _on_session(self, result: object) -> None:
        assert isinstance(result, UpdateSession)
        self.session = result
        self._populate_components(result.snapshot, result.checks)
        self._populate_plan(result)
        if result.plan.changes:
            self.plan_status.setText(
                f"Plan: {result.change_count} cambios, "
                f"{len(result.plan.warnings)} avisos"
            )
            self.stage_label.setText(
                "Plan listo. Revisa el resumen y pulsa «Actualizar ahora»."
            )
            self.log(
                f"Plan calculado: {result.change_count} cambios en "
                f"{len(result.plan.components)} componentes."
            )
        else:
            self.plan_status.setText("Plan: todo al día")
            self.stage_label.setText("Todo al día. No hay cambios que aplicar.")
            self.log("No hay actualizaciones disponibles.")
        for warning in result.plan.warnings:
            self.log(f"Aviso del plan: {warning}")
        for check in result.checks:
            if check.error:
                self.log(f"{component_names()[check.component]}: {check.error}")
            for warning in check.warnings:
                self.log(f"{component_names()[check.component]}: {warning}")
        if result.plan.conflicts:
            self.stage_label.setText(
                "Hay conflictos en el plan. Revisa la pestaña Plan."
            )
            self.advanced_button.setChecked(True)
            self.tabs.setCurrentWidget(self.plan_table)
            QMessageBox.warning(
                self, "Plan", "El plan contiene conflictos y no se puede aplicar."
            )
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
        session = self.session
        has_sd = self.snapshot is not None and self.snapshot.is_switch_sd
        has_export = session is not None and bool(session.plan.components)
        busy = self._busy

        self.sd_combo.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.detect_button.setEnabled(not busy)
        self.check_button.setEnabled(not busy and has_sd)
        self.export_button.setEnabled(not busy and has_export)
        self.backups_button.setEnabled(not busy and has_sd)
        self.settings_button.setEnabled(not busy)
        self.advanced_button.setEnabled(True)

        if busy:
            self.apply_button.setEnabled(False)
        elif session is not None and session.plan.changes and not session.plan.conflicts:
            self.apply_button.setEnabled(True)
            self.apply_button.setText(
                f"Actualizar ahora ({len(session.plan.changes)} cambios)"
            )
        elif session is None and has_sd:
            self.apply_button.setEnabled(True)
            self.apply_button.setText("Buscar actualizaciones")
        elif session is not None and session.plan.conflicts:
            self.apply_button.setEnabled(False)
            self.apply_button.setText("Hay conflictos")
        elif session is not None:
            self.apply_button.setEnabled(False)
            self.apply_button.setText("Todo al día")
        else:
            self.apply_button.setEnabled(False)
            self.apply_button.setText("Conecta la SD")

        for box in self.component_boxes.values():
            box.setEnabled(not busy)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
            self.progress.setFormat("")
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
        self.stage_label.setText(message)
        self.log(message)
        worker = FunctionWorker(function)
        self._workers.add(worker)

        def success(result: object) -> None:
            self._workers.discard(worker)
            self._release_progress_bridge()
            self._set_busy(False)
            self.stage_label.setText("Listo.")
            on_success(result)

        def failure(message_text: str) -> None:
            self._workers.discard(worker)
            self._release_progress_bridge()
            self._set_busy(False)
            friendly = self._friendly_error(message_text)
            self.stage_label.setText(f"Error: {friendly}")
            self.log(f"Error: {friendly}")
            QMessageBox.critical(self, "Error", friendly)

        worker.signals.finished.connect(success)
        worker.signals.failed.connect(failure)
        QThreadPool.globalInstance().start(worker)

    def _friendly_error(self, message: str) -> str:
        text = message.strip()
        lowered = text.casefold()
        if "límite de peticiones" in lowered or "rate limit" in lowered:
            return (
                "GitHub limitó temporalmente las consultas. Espera unos minutos "
                "y vuelve a intentarlo."
            )
        if (
            "no se pudo conectar" in lowered
            or "github" in lowered
            or "timed out" in lowered
            or "network" in lowered
        ):
            return (
                "No se pudo conectar con GitHub. Comprueba tu conexión a Internet "
                "y vuelve a intentarlo."
            )
        if "no hay espacio" in lowered:
            return (
                "No hay espacio suficiente en la SD. Libera espacio o usa otra tarjeta."
            )
        if "acceso denegado" in lowered or "permission" in lowered or "denied" in lowered:
            return (
                "Windows no permitió escribir en la SD. Comprueba que el bloqueo "
                "físico esté desactivado y que la tarjeta no esté en solo lectura."
            )
        if "no existe la ruta" in lowered or "no es una carpeta" in lowered:
            return "La SD ya no está disponible. Vuelve a conectarla y pulsa «Buscar SD»."
        if "sha-256" in lowered or "hash" in lowered:
            return (
                "La descarga no coincide con lo publicado. Vuelve a intentarlo; "
                "si persiste, revisa tu conexión."
            )
        return text

    def _primary_action(self) -> None:
        session = self.session
        if (
            session is not None
            and session.plan.changes
            and not session.plan.conflicts
        ):
            self._apply()
        else:
            self._check()

    def _apply(self) -> None:
        if self._busy:
            return
        if self.session is None or not self.session.plan.changes:
            QMessageBox.information(self, "Aplicar", "No hay cambios para aplicar.")
            return
        session = self.session
        if session.plan.conflicts:
            QMessageBox.warning(
                self,
                "Plan",
                "El plan contiene conflictos. Revisa la pestaña Plan en Avanzado.",
            )
            return
        names = component_names()
        details = [f"Se actualizarán {len(session.plan.changes)} archivos."]
        for component_plan in session.plan.components:
            if not component_plan.changes:
                continue
            release = (
                component_plan.release.tag
                if component_plan.release is not None
                else "versión nueva"
            )
            details.append(f"• {names[component_plan.component]} → {release}")
        details.append(f"Backup local: {self.settings.backup_dir}")
        if self.settings.mirror_backup:
            details.append("También se copiará el backup a /.magia/backups en la SD.")
        protected: list[str] = []
        if self.settings.allow_reboot_payload:
            protected.append("/atmosphere/reboot_payload.bin")
        if self.settings.allow_root_payload:
            protected.append("/payload.bin")
        if protected:
            policy_note = (
                "También se actualizarán payloads protegidos autorizados en Ajustes: "
                + ", ".join(protected)
                + "."
            )
        else:
            policy_note = (
                "Tus configuraciones, sysmodules y payloads personalizados no se tocarán."
            )
        for warning in session.plan.warnings:
            details.append(f"Aviso: {warning}")
        answer = QMessageBox.question(
            self,
            "Aplicar actualización",
            "Se creará un backup antes de escribir.\n"
            + policy_note
            + "\n\n"
            + "\n".join(details)
            + "\n\n¿Continuar?",
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
            "Aplicando actualización...",
            lambda: self.service.apply(session),
            self._on_applied,
        )

    def _on_applied(self, result: object) -> None:
        assert isinstance(result, ApplyResult)
        self.log(f"Actualización aplicada. Backup: {result.backup_dir}")
        if result.state_path is not None:
            self.log(f"Estado: {result.state_path}")
        for warning in result.warnings:
            self.log(f"Aviso: {warning}")

        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Information)
        message.setWindowTitle("Actualización completada")
        message.setText("La SD se actualizó correctamente.")
        message.setInformativeText(
            f"Archivos aplicados: {len(result.applied)}\n"
            f"Backup: {result.backup_dir}\n\n"
            "Puedes expulsar la SD de forma segura."
        )
        open_button = message.addButton(
            "Abrir carpeta de backup", QMessageBox.ButtonRole.ActionRole
        )
        message.addButton(QMessageBox.StandardButton.Ok)
        message.exec()
        if message.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.backup_dir)))

        self._clear_plan()
        self._components_initialized_for = None
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
            lambda: self.service.export(
                session, output, kind=kind, format=export_format
            ),
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
        settings = replace(
            dialog.settings(), last_sd_root=self.settings.last_sd_root
        )
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
