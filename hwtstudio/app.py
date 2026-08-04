from __future__ import annotations

import copy
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, QSize, QStandardPaths, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QMouseEvent, QPixmap, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QBoxLayout,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableView,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

from . import __version__
from .catalog import scan_theme, source_compatibility_report
from .exporter import default_export_name, export_theme, preflight_export
from .imageops import render_image, render_placeholder
from .models import ResourceChange, ResourceSlot, ThemeCatalog, ThemeProject
from .paths import APP_NAME, default_source_theme
from .phone_transfer import PhoneRegistry
from .projectio import load_project, save_project
from .validation import validate_change_value
from .services.catalog_service import load_preferred_catalog, save_user_catalog
from .semantic import SIMPLE_SETTINGS, TYPE_LABELS, friendly_resource_label, resolve_all
from .ui.commands import BulkChangeCommand, ChangeCommand
from .ui.design_system import Colors, apply_design_system, apply_type, set_role, set_state
from .ui.dialogs import CustomResourceDialog, resolve_missing_assets
from .ui.i18n import install_qt_translations
from .ui.phone_dialog import PhoneTransferDialog
from .ui.resource_models import ResourceFilterModel, ResourceTableModel
from .ui.simple_editor import SimpleEditor
from .ui.simple_preview import PreviewRepository
from .ui.titlebar import WindowTitleBar
from .ui.workers import ProfileWorker, TransferWorker, UpdateWorker
from .updater import Release, UpdateCheck, VerifiedDownload, launch_update, release_page_url


def _compact_error_detail(detail: str, fallback: str, *, limit: int = 240) -> str:
    first_line = next((line.strip() for line in detail.splitlines() if line.strip()), "")
    if not first_line:
        return fallback
    return first_line if len(first_line) <= limit else first_line[:limit] + "..."


_PREFLIGHT_WARNING_LABELS = {
    "image_format_mismatch": "图片格式不匹配",
    "missing_slot": "资源不存在",
    "unsupported": "资源不支持",
    "duplicate_target_merged": "重复目标已合并",
    "duplicate_target_resolved": "重复目标已按兼容性处理",
}


def _format_preflight_warnings(value: object) -> str:
    if not isinstance(value, list):
        return ""
    lines: list[str] = []
    fields = (("module", "模块"), ("path", "路径"), ("slot_id", "资源"), ("reason", "原因"),
              ("message", "说明"), ("expected", "期望"), ("actual", "实际"))
    for item in value[:8]:
        if isinstance(item, str):
            line = _compact_error_detail(item, "预检警告")
        elif isinstance(item, dict):
            kind = item.get("kind")
            kind_text = kind if isinstance(kind, str) and kind else "unknown"
            label = _PREFLIGHT_WARNING_LABELS.get(kind_text, f"预检警告：{kind_text}")
            details = []
            for field, field_label in fields:
                detail = item.get(field)
                if isinstance(detail, (str, int, float)) and not isinstance(detail, bool):
                    details.append(f"{field_label} {detail}")
            line = label + ("：" + " / ".join(details) if details else "")
            line = _compact_error_detail(line, "预检警告")
        else:
            line = _compact_error_detail(str(item), "预检警告")
        if line:
            lines.append(line)
    return "\n".join(lines)


class MainWindow(QMainWindow):
    def __init__(self):
        app = QApplication.instance()
        if app is not None:
            install_qt_translations(app)
        super().__init__()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1500, 920)
        self.catalog = self._load_initial_catalog()
        self.project = ThemeProject()
        self.undo_stack = QUndoStack(self)
        self.settings = QSettings("子木", APP_NAME)
        self.last_export: Path | None = None
        self.transfer_thread: QThread | None = None
        self._transfer_worker: TransferWorker | None = None
        self._transfer_generation = 0
        self.update_thread: QThread | None = None
        self.update_worker: UpdateWorker | None = None
        self._update_generation = 0
        self.update_info: UpdateCheck | None = None
        self.update_progress: QProgressDialog | None = None
        profile_device_id = self.settings.value("phone/profile_device_id", "", type=str)
        saved_devices = PhoneRegistry().load()
        self.phone_profile = self._cached_phone_profile(saved_devices, profile_device_id)
        self.installed_packages: set[str] | None = (
            set(self.phone_profile.installed_packages) if self.phone_profile else None
        )
        self.profile_thread: QThread | None = None
        self._profile_worker: ProfileWorker | None = None
        self._profile_generation = 0
        self._closing = False
        self._log_lines: list[str] = []
        self._resize_margin = 6
        self.simple_resolved = resolve_all(self.catalog)
        self.preview_repository = PreviewRepository()
        self._build_ui()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._bind_project()
        self._update_phone_ui(cached=bool(self.phone_profile))
        self.log("已加载大雪资源目录。原始主题仅用于只读分析，不会被修改。")
        self._log_source_compatibility_summary(self.catalog)
        if self._catalog_load_warning:
            self.log(self._catalog_load_warning)

    @staticmethod
    def _cached_phone_profile(devices: dict, preferred_device_id: str):
        preferred = devices.get(preferred_device_id) if preferred_device_id else None
        if preferred is not None and preferred.profile is not None:
            return preferred.profile
        if preferred_device_id:
            return None
        profiles = [device.profile for device in devices.values() if device.profile]
        return profiles[0] if len(profiles) == 1 else None

    def _load_initial_catalog(self) -> ThemeCatalog:
        catalog, self._catalog_load_warning = load_preferred_catalog()
        return catalog

    def _build_ui(self):
        self.title_bar = WindowTitleBar(self)
        self.setMenuWidget(self.title_bar)
        self.setStatusBar(QStatusBar())
        self._build_toolbar()
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_simple_tab(), "简洁编辑")
        self.tabs.addTab(self._build_changes_tab(), "修改记录")
        self.tabs.addTab(self._build_resources_tab(), "高级编辑")
        self.setMinimumSize(320, 480)

    def _build_toolbar(self):
        toolbar = QToolBar("主工具栏")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.setMinimumWidth(0)
        self.main_toolbar = toolbar
        self.addToolBar(toolbar)
        actions = [
            ("fa5s.file", "新建工程", self.new_project, Colors.INK_MUTED, "ghost"),
            ("fa5s.folder-open", "打开工程", self.open_project, Colors.INK_MUTED, "ghost"),
            ("fa5s.save", "保存工程", self.save_project, Colors.INK_MUTED, "ghost"),
            ("fa5s.file-export", "导出 HWT", self.export_hwt, Colors.PRIMARY, "secondary"),
            ("fa5s.mobile-alt", "发送到手机", self.send_phone, Colors.PRIMARY, "primary"),
        ]
        for icon_name, label, callback, icon_color, role in actions:
            action = QAction(qta.icon(icon_name, color=icon_color), label, self)
            action.setToolTip(label)
            action.triggered.connect(callback)
            toolbar.addAction(action)
            button = toolbar.widgetForAction(action)
            if button is not None:
                button.setProperty("uiRole", role)

        advanced = QMenu("更多", self)
        custom_action = QAction("添加自定义资源", self)
        custom_action.triggered.connect(self.add_custom_resource)
        advanced.addAction(custom_action)
        rescan_action = QAction("重新扫描资源目录", self)
        rescan_action.triggered.connect(self.rescan_source)
        advanced.addAction(rescan_action)
        report_action = QAction("查看源主题兼容性报告", self)
        report_action.triggered.connect(self.show_source_compatibility_report)
        advanced.addAction(report_action)
        log_action = QAction("查看运行日志", self)
        log_action.triggered.connect(self.show_log_dialog)
        advanced.addAction(log_action)
        advanced.addSeparator()
        update_action = QAction("检查更新", self)
        update_action.triggered.connect(self.check_for_updates)
        self.update_action = update_action
        advanced.addAction(update_action)
        advanced.addSeparator()
        ssh_action = QAction("通过 Termux/SSH 发送到手机", self)
        ssh_action.triggered.connect(self.send_phone_ssh)
        advanced.addAction(ssh_action)
        more_button = QToolButton()
        more_button.setText("更多")
        more_button.setIcon(qta.icon("fa5s.ellipsis-h", color=Colors.INK_MUTED))
        more_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_button.setMenu(advanced)
        more_button.setToolTip("更多操作")
        toolbar.addWidget(more_button)
        toolbar.addSeparator()
        undo_action = self.undo_stack.createUndoAction(self, "撤销")
        redo_action = self.undo_stack.createRedoAction(self, "重做")
        undo_action.setIcon(qta.icon("fa5s.undo", color=Colors.INK_MUTED))
        redo_action.setIcon(qta.icon("fa5s.redo", color=Colors.INK_MUTED))
        undo_action.setShortcut("Ctrl+Z")
        redo_action.setShortcut("Ctrl+Y")
        toolbar.addAction(undo_action)
        toolbar.addAction(redo_action)

    def _build_simple_tab(self):
        page = QWidget()
        page.setObjectName("simplePage")
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)
        self.simple_page = page
        header = QHBoxLayout()
        header.setSpacing(16)
        self.simple_header = header
        title_box = QVBoxLayout()
        title_box.setSpacing(8)
        title = QLabel("用看得懂的项目制作主题")
        title.setObjectName("pageTitle")
        apply_type(title, 30)
        title_box.addWidget(title)
        subtitle = QLabel("一次设置会自动同步相关兼容资源；需要逐项调整时再进入“高级编辑”。")
        subtitle.setObjectName("simpleDescription")
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)
        self.phone_status = QLabel("通用模式 · 尚未识别手机")
        self.phone_status.setObjectName("phoneStatus")
        header.addWidget(self.phone_status)
        self.connect_phone_button = QPushButton("识别手机")
        set_role(self.connect_phone_button, "tertiary")
        self.connect_phone_button.clicked.connect(self.connect_phone_profile)
        header.addWidget(self.connect_phone_button)
        root.addLayout(header)

        identity = QFrame()
        identity.setObjectName("identityPanel")
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(18, 14, 18, 16)
        identity_layout.setSpacing(12)
        identity_header = QHBoxLayout()
        identity_title = QLabel("主题信息")
        identity_title.setObjectName("sectionTitle")
        identity_header.addWidget(identity_title)
        identity_header.addStretch(1)
        identity_toggle = QPushButton("展开")
        identity_toggle.setCheckable(True)
        set_role(identity_toggle, "ghost")
        self.identity_toggle = identity_toggle
        identity_header.addWidget(identity_toggle)
        identity_layout.addLayout(identity_header)
        self.name_edit = QLineEdit()
        self.title_edit = QLineEdit()
        self.author_edit = QLineEdit()
        self.designer_edit = QLineEdit()
        self.version_edit = QLineEdit()
        self.screen_edit = QLineEdit()
        identity_fields = QWidget()
        identity_form = QFormLayout(identity_fields)
        identity_form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        self.identity_form = identity_form
        for label, widget in [
            ("方案名称", self.name_edit),
            ("主题标题", self.title_edit),
            ("作者", self.author_edit),
            ("设计者", self.designer_edit),
            ("版本", self.version_edit),
            ("屏幕类型", self.screen_edit),
        ]:
            identity_form.addRow(label, widget)
            widget.textEdited.connect(self._identity_edited)
        identity_layout.addWidget(identity_fields)
        identity_fields.setVisible(False)
        identity_toggle.toggled.connect(identity_fields.setVisible)
        identity_toggle.toggled.connect(lambda checked: identity_toggle.setText("收起" if checked else "展开"))
        root.addWidget(identity)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.simple_scroll = scroll
        host = QWidget()
        host.setObjectName("simpleHost")
        host.setMaximumWidth(1440)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        self.simple_editor = SimpleEditor(self.apply_simple_setting, self.reset_simple_setting, self.preview_repository)
        host_layout.addWidget(self.simple_editor)
        scroll.setWidget(host)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        root.addWidget(scroll, 1)
        self.simple_editor.set_available_width(scroll.viewport().width())
        return page

    def _replace_combo_items(self, combo: QComboBox, values: list[str]):
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        index = combo.findText(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _replace_type_items(self, resources: list[ResourceSlot]):
        current = self.type_combo.currentData() or "全部"
        self.type_combo.blockSignals(True)
        self.type_combo.clear()
        self.type_combo.addItem("全部类型", "全部")
        for resource_type in sorted({item.resource_type for item in resources}):
            self.type_combo.addItem(TYPE_LABELS.get(resource_type, resource_type), resource_type)
        index = self.type_combo.findData(current)
        self.type_combo.setCurrentIndex(index if index >= 0 else 0)
        self.type_combo.blockSignals(False)

    def bind_catalog(self, catalog: ThemeCatalog):
        self.catalog = catalog
        self.simple_resolved = resolve_all(catalog)
        self.resource_model.catalog = catalog
        self.resource_model.project = self.project
        resources = [*catalog.resources, *self.project.custom_resources]
        self.resource_model.set_resources(resources)
        self._replace_combo_items(self.category_combo, ["全部"] + sorted({x.category for x in resources}))
        self._replace_type_items(resources)
        self.selected_slot = None
        self.table.clearSelection()
        self.detail_title.setText("请选择一个资源")
        self.detail_info.clear()
        self.image_source = None
        self._apply_filters()
        self.refresh_views()

    def _build_resources_tab(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)
        filter_bar = QWidget()
        filter_layout = QGridLayout(filter_bar)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setHorizontalSpacing(16)
        filter_layout.setVerticalSpacing(8)
        self.filter_bar = filter_bar
        self.filter_layout = filter_layout
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索应用、中文作用、资源名或路径……")
        self.category_combo = QComboBox()
        self.category_combo.addItems(["全部"] + sorted({x.category for x in self.catalog.resources}))
        self.type_combo = QComboBox()
        self.type_combo.addItem("全部类型", "全部")
        for resource_type in sorted({x.resource_type for x in self.catalog.resources}):
            self.type_combo.addItem(TYPE_LABELS.get(resource_type, resource_type), resource_type)
        self.modified_check = QCheckBox("仅显示已修改")
        bulk_button = QPushButton("批量设置筛选颜色")
        set_role(bulk_button, "tertiary")
        bulk_button.clicked.connect(self.bulk_set_filtered_colors)
        self.technical_columns_check = QCheckBox("显示技术信息")
        self.technical_columns_check.toggled.connect(self._toggle_technical_columns)
        self.filter_widgets = [
            self.search_edit,
            self.category_combo,
            self.type_combo,
            self.modified_check,
            bulk_button,
            self.technical_columns_check,
        ]
        root.addWidget(filter_bar)

        self.resource_model = ResourceTableModel(self.catalog, self.project)
        self.proxy_model = ResourceFilterModel()
        self.proxy_model.setSourceModel(self.resource_model)
        self.table = QTableView()
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((150, 130, 230, 90, 250, 220, 380)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self._toggle_technical_columns(False)

        self.detail = self._build_detail_panel()
        self.resource_splitter = QSplitter(Qt.Horizontal)
        self.resource_splitter.addWidget(self.table)
        self.resource_splitter.addWidget(self.detail)
        self.resource_splitter.setSizes([1050, 380])
        root.addWidget(self.resource_splitter, 1)
        self._layout_filter_bar(self.width())

        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(200)
        self.filter_timer.timeout.connect(self._apply_filters)
        self.search_edit.textChanged.connect(self._filters_changed)
        self.category_combo.currentTextChanged.connect(self._filters_changed)
        self.type_combo.currentTextChanged.connect(self._filters_changed)
        self.modified_check.toggled.connect(self._filters_changed)
        return page

    def _layout_filter_bar(self, width: int):
        if not hasattr(self, "filter_layout"):
            return
        while self.filter_layout.count():
            self.filter_layout.takeAt(0)
        for column in range(6):
            self.filter_layout.setColumnStretch(column, 0)
        search, category, resource_type, modified, bulk, technical = self.filter_widgets
        if width >= 1056:
            self.filter_layout.addWidget(search, 0, 0, 1, 2)
            self.filter_layout.addWidget(category, 0, 2)
            self.filter_layout.addWidget(resource_type, 0, 3)
            self.filter_layout.addWidget(modified, 0, 4)
            self.filter_layout.addWidget(bulk, 0, 5)
            self.filter_layout.addWidget(technical, 0, 6)
            self.filter_layout.setColumnStretch(0, 1)
            self.filter_layout.setColumnStretch(1, 1)
        elif width >= 672:
            self.filter_layout.addWidget(search, 0, 0, 1, 4)
            self.filter_layout.addWidget(category, 1, 0)
            self.filter_layout.addWidget(resource_type, 1, 1)
            self.filter_layout.addWidget(modified, 1, 2)
            self.filter_layout.addWidget(bulk, 1, 3)
            self.filter_layout.addWidget(technical, 1, 4)
            self.filter_layout.setColumnStretch(0, 1)
        else:
            for row, widget in enumerate(self.filter_widgets):
                self.filter_layout.addWidget(widget, row, 0)
            self.filter_layout.setColumnStretch(0, 1)

    def _toggle_technical_columns(self, visible: bool):
        if not hasattr(self, "table"):
            return
        for column in (2, 5, 6):
            self.table.setColumnHidden(column, not visible)

    def _build_detail_panel(self):
        panel = QFrame()
        panel.setObjectName("detailPanel")
        panel.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(panel)
        self.detail_title = QLabel("请选择一个资源")
        self.detail_title.setObjectName("detailTitle")
        apply_type(self.detail_title, 24)
        self.detail_title.setWordWrap(True)
        layout.addWidget(self.detail_title)
        self.detail_info = QLabel()
        self.detail_info.setObjectName("detailInfo")
        self.detail_info.setWordWrap(True)
        self.detail_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.detail_info)

        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("颜色、布尔值或文字")
        layout.addWidget(self.value_edit)
        self.color_button = QPushButton("选择颜色")
        set_role(self.color_button, "tertiary")
        self.color_button.clicked.connect(self.pick_color)
        layout.addWidget(self.color_button)

        image_row = QHBoxLayout()
        self.image_edit = QLineEdit()
        self.image_edit.setReadOnly(True)
        choose = QPushButton("选择图片")
        set_role(choose, "tertiary")
        self.choose_image_button = choose
        choose.clicked.connect(self.choose_image)
        image_row.addWidget(self.image_edit, 1)
        image_row.addWidget(choose)
        layout.addLayout(image_row)
        self.preview_label = QLabel("图片预览")
        self.preview_label.setObjectName("previewPanel")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(220)
        layout.addWidget(self.preview_label)

        form = QFormLayout()
        self.fit_combo = QComboBox()
        self.fit_combo.addItem("裁剪填满", "cover")
        self.fit_combo.addItem("完整放入", "contain")
        self.fit_combo.addItem("拉伸", "stretch")
        self.enhance_combo = QComboBox()
        self.enhance_combo.addItem("不增强", "none")
        self.enhance_combo.addItem("加亮", "light")
        self.enhance_combo.addItem("变暗", "dark")
        self.enhance_slider = QSlider(Qt.Horizontal)
        self.enhance_slider.setRange(0, 80)
        self.focus_x = QSlider(Qt.Horizontal)
        self.focus_x.setRange(0, 100)
        self.focus_x.setValue(50)
        self.focus_y = QSlider(Qt.Horizontal)
        self.focus_y.setRange(0, 100)
        self.focus_y.setValue(50)
        self.sync_compatible = QCheckBox("同步同分类、同名的荣耀/华为/Android兼容资源")
        form.addRow("适配方式", self.fit_combo)
        form.addRow("自动增强", self.enhance_combo)
        form.addRow("增强强度", self.enhance_slider)
        form.addRow("水平取景", self.focus_x)
        form.addRow("垂直取景", self.focus_y)
        form.addRow(self.sync_compatible)
        layout.addLayout(form)

        preview_button = QPushButton("刷新处理后预览")
        set_role(preview_button, "tertiary")
        self.processed_preview_button = preview_button
        preview_button.clicked.connect(self.preview_processed_image)
        layout.addWidget(preview_button)
        buttons = QHBoxLayout()
        apply_button = QPushButton("应用修改")
        set_role(apply_button, "primary")
        apply_button.clicked.connect(self.apply_detail)
        reset_button = QPushButton("恢复系统默认")
        set_role(reset_button, "ghost")
        reset_button.clicked.connect(self.reset_detail)
        buttons.addWidget(apply_button)
        buttons.addWidget(reset_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.detail_widgets = [
            self.value_edit,
            self.color_button,
            self.image_edit,
            choose,
            self.fit_combo,
            self.enhance_combo,
            self.enhance_slider,
            self.focus_x,
            self.focus_y,
            self.sync_compatible,
            preview_button,
            apply_button,
            reset_button,
        ]
        self.selected_slot: ResourceSlot | None = None
        self.image_source: str | None = None
        return panel

    def _build_changes_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.changes_text = QPlainTextEdit()
        self.changes_text.setReadOnly(True)
        layout.addWidget(self.changes_text)
        return page

    def _build_log_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        return page

    def show_log_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("运行日志")
        dialog.resize(850, 560)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n".join(self._log_lines))
        layout.addWidget(text)
        close_button = QPushButton("关闭")
        set_role(close_button, "ghost")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def check_for_updates(self, silent: bool = False):
        if self._closing:
            return
        if self.update_thread and self.update_thread.isRunning():
            if not silent:
                self.statusBar().showMessage("正在检查更新……")
            return
        self.statusBar().showMessage("正在检查 GitHub Release 更新……")
        self._update_generation += 1
        generation = self._update_generation
        self.update_thread = QThread(self)
        worker = UpdateWorker(silent=silent, task_id=generation)
        worker.moveToThread(self.update_thread)
        self.update_thread.started.connect(worker.run_check)
        worker.checked.connect(self._update_checked, Qt.ConnectionType.QueuedConnection)
        worker.check_failed.connect(self._update_check_failed, Qt.ConnectionType.QueuedConnection)
        worker.checked.connect(self.update_thread.quit)
        worker.check_failed.connect(self.update_thread.quit)
        self.update_thread.finished.connect(worker.deleteLater)
        self.update_thread.setProperty("hwt_generation", generation)
        self.update_thread.finished.connect(self._update_thread_finished)
        self.update_thread.finished.connect(self.update_thread.deleteLater)
        self.update_worker = worker
        self.update_thread.start()

    def _update_checked(self, info: UpdateCheck, silent: bool, generation: int | None = None):
        if generation is not None and generation != self._update_generation:
            return
        if self._closing:
            return
        self.update_info = info
        latest = info.latest_version or "未知"
        if hasattr(self, "update_action"):
            self.update_action.setText(f"检查更新（{latest}）" if info.update_available else "检查更新")
        if not info.update_available or info.release is None:
            self.statusBar().showMessage(f"当前已是最新版本（{info.current_version}）。", 5000)
            if not silent:
                QMessageBox.information(self, "检查更新", f"当前已是最新版本：{info.current_version}")
            return

        release = info.release
        asset_text = release.asset.name if release.asset else "没有可用的 Windows 更新包"
        self.statusBar().showMessage(f"发现新版本 {release.version}：{asset_text}", 10000)
        self.log(f"发现 GitHub Release 更新：{info.current_version} → {release.version}。")
        self._show_update_prompt(release)

    def _update_check_failed(self, detail: str, silent: bool, generation: int | None = None):
        if generation is not None and generation != self._update_generation:
            return
        if self._closing:
            return
        message = "暂时无法检查更新，请确认网络连接后稍后重试。"
        self.log(f"检查更新失败：{detail}")
        self.statusBar().showMessage(message, 8000)
        if not silent:
            QMessageBox.warning(self, "检查更新失败", message)

    def _show_update_prompt(self, release: Release):
        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        summary = release.body or "该版本包含功能改进和问题修复。"
        asset_text = release.asset.name if release.asset else "当前没有适用于 Windows 的自动更新包"
        box.setText(f"发现大雪主题编辑器新版本：{release.version}")
        box.setInformativeText(f"当前版本：{__version__}\n更新包：{asset_text}\n\n{summary}")
        update_button = box.addButton("下载并更新", QMessageBox.AcceptRole)
        release_button = box.addButton("打开发布页", QMessageBox.ActionRole)
        box.addButton("稍后", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is update_button:
            if release.asset is None:
                QDesktopServices.openUrl(QUrl(release_page_url(release)))
            else:
                self._start_update_download(release)
        elif clicked is release_button:
            QDesktopServices.openUrl(QUrl(release_page_url(release)))

    def _start_update_download(self, release: Release):
        if self._closing:
            return
        if self.update_thread and self.update_thread.isRunning():
            QMessageBox.information(self, "请稍候", "更新检查或下载任务仍在进行中。")
            return
        self._update_generation += 1
        generation = self._update_generation
        self.update_progress = QProgressDialog("正在准备下载更新包……", "取消", 0, 1000, self)
        self.update_progress.setWindowTitle("更新大雪主题编辑器")
        self.update_progress.setMinimumDuration(0)
        self.update_progress.setValue(0)
        self.update_progress.show()
        self.update_thread = QThread(self)
        worker = UpdateWorker(release=release, task_id=generation)
        worker.moveToThread(self.update_thread)
        self.update_thread.started.connect(worker.run_download)
        worker.progress.connect(self._update_download_progress, Qt.ConnectionType.QueuedConnection)
        worker.downloaded.connect(self._update_downloaded, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._update_download_failed, Qt.ConnectionType.QueuedConnection)
        worker.downloaded.connect(self.update_thread.quit)
        worker.failed.connect(self.update_thread.quit)
        self.update_thread.finished.connect(worker.deleteLater)
        self.update_thread.setProperty("hwt_generation", generation)
        self.update_thread.finished.connect(self._update_thread_finished)
        self.update_thread.finished.connect(self.update_thread.deleteLater)
        self.update_progress.canceled.connect(worker.cancel, Qt.DirectConnection)
        self.update_worker = worker
        self.update_thread.start()

    def _update_download_progress(self, received: int, total: int, stage: str, generation: int | None = None):
        if generation is not None and generation != self._update_generation:
            return
        if self._closing or self.update_progress is None:
            return
        self.update_progress.setLabelText(stage)
        if total > 0:
            self.update_progress.setRange(0, 1000)
            self.update_progress.setValue(min(1000, int(received * 1000 / total)))
        else:
            self.update_progress.setRange(0, 0)

    def _update_downloaded(self, download: VerifiedDownload, generation: int | None = None):
        if generation is not None and generation != self._update_generation:
            return
        if self._closing:
            return
        progress = self.update_progress
        self.update_progress = None
        if progress is not None:
            progress.close()
        path = download.path
        self.log(f"更新包已下载并通过 SHA-256 校验：{path}")
        try:
            should_exit = launch_update(download)
        except Exception:
            self.log(traceback.format_exc())
            QMessageBox.critical(self, "启动更新失败", f"更新包已保存到：\n{path}\n\n无法自动启动新版本，请手动打开该文件。")
            return
        if should_exit:
            self.statusBar().showMessage("更新程序已启动，编辑器即将退出……")
            QTimer.singleShot(0, QApplication.instance().quit)
        else:
            QMessageBox.information(self, "更新包已准备好", f"更新包已下载：\n{path}\n\n已打开新版本程序。")

    def _update_download_failed(self, detail: str, generation: int | None = None):
        if generation is not None and generation != self._update_generation:
            return
        if self._closing:
            return
        progress = self.update_progress
        self.update_progress = None
        if progress is not None:
            progress.close()
        message = "取消" if "取消" in detail else "更新包下载或校验失败，请稍后重试。"
        self.log(f"下载更新失败：{detail}")
        if "取消" in message:
            QMessageBox.information(self, "已取消更新", message)
        else:
            QMessageBox.critical(self, "下载更新失败", message)

    def _update_thread_finished(self, generation: int | None = None):
        if generation is None:
            sender = self.sender()
            generation = sender.property("hwt_generation") if sender is not None else None
        if generation is not None and generation != self._update_generation:
            return
        self.update_thread = None
        self.update_worker = None
        self._maybe_close_after_threads()

    def connect_phone_profile(self):
        if self.profile_thread and self.profile_thread.isRunning():
            return
        dialog = PhoneTransferDialog(self, purpose="profile")
        if dialog.exec() != QDialog.Accepted or dialog.device is None:
            return
        self.connect_phone_button.setEnabled(False)
        self.phone_status.setText("正在读取手机适配信息……")
        self._profile_generation += 1
        generation = self._profile_generation
        thread = QThread(self)
        worker = ProfileWorker(dialog.device, dialog.pair_code, task_id=generation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._profile_finished)
        worker.failed.connect(self._profile_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.setProperty("hwt_generation", generation)
        thread.finished.connect(self._profile_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self.profile_thread = thread
        self._profile_worker = worker
        thread.start()

    def _profile_finished(self, device, profile, generation: int | None = None):
        if generation is not None and generation != self._profile_generation:
            return
        if self._closing:
            return
        self.settings.setValue("phone/profile_device_id", device.device_id)
        self.phone_profile = profile
        self.installed_packages = set(profile.installed_packages)
        self.resource_model.set_installed_packages(self.installed_packages)
        self._update_phone_ui(cached=False)
        self.refresh_views()
        self.log(f"已识别手机：{profile.manufacturer} {profile.model}，适用应用 {len(profile.installed_packages)} 个。")

    def _profile_failed(self, detail: str, code: str, generation: int | None = None):
        if generation is not None and generation != self._profile_generation:
            return
        if self._closing:
            return
        messages = {
            "profile_unsupported": "手机助手版本过低，无法读取适配信息，请先更新手机助手。",
            "no_device": "没有找到可用手机，请确认手机助手已打开并已配对。",
            "unexpected": "读取手机信息失败，请确认手机和电脑处于同一网络。",
        }
        message = messages.get(code) or _compact_error_detail(detail, "无法读取手机信息")
        self.log(f"手机识别原始异常：{detail}")
        self._update_phone_ui(cached=bool(self.phone_profile))
        self.phone_status.setText(message)
        set_state(self.phone_status, "error")
        if code == "profile_unsupported":
            QMessageBox.information(self, "需要更新手机助手", message)
        else:
            QMessageBox.warning(self, "识别失败", message)

    def _profile_thread_finished(self, generation: int | None = None):
        if generation is None:
            sender = self.sender()
            generation = sender.property("hwt_generation") if sender is not None else None
        if generation is not None and generation != self._profile_generation:
            return
        self.profile_thread = None
        self._profile_worker = None
        self.connect_phone_button.setEnabled(True)
        self._maybe_close_after_threads()

    def _update_phone_ui(self, *, cached: bool):
        if not hasattr(self, "phone_status"):
            return
        profile = self.phone_profile
        if profile is None:
            self.phone_status.setText("通用模式 · 尚未识别手机")
            set_state(self.phone_status, "warning")
            return
        os_text = profile.os_name or (f"Android {profile.android_release}" if profile.android_release else "系统版本未知")
        prefix = "上次识别" if cached else "已连接"
        self.phone_status.setText(f"{prefix} · {profile.model} · {os_text}")
        set_state(self.phone_status, "success")
        if hasattr(self, "resource_model"):
            self.resource_model.set_installed_packages(self.installed_packages)

    def _bind_project(self):
        self.resource_model.project = self.project
        resources = [*self.catalog.resources, *self.project.custom_resources]
        if [slot.id for slot in self.resource_model.resources] != [slot.id for slot in resources]:
            self.resource_model.set_resources(resources)
        self._replace_combo_items(self.category_combo, ["全部"] + sorted({x.category for x in resources}))
        self._replace_type_items(resources)
        self.name_edit.setText(self.project.name)
        self.title_edit.setText(self.project.title)
        self.author_edit.setText(self.project.author)
        self.designer_edit.setText(self.project.designer)
        self.version_edit.setText(self.project.version)
        self.screen_edit.setText(self.project.screen)
        self.refresh_views()

    def _identity_edited(self):
        self.project.name = self.name_edit.text().strip() or "我的主题"
        self.project.title = self.title_edit.text().strip() or "空白主题"
        self.project.author = self.author_edit.text().strip() or "子木"
        self.project.designer = self.designer_edit.text().strip() or self.project.author
        self.project.version = self.version_edit.text().strip() or "1.0.0"
        self.project.screen = self.screen_edit.text().strip() or "FHD"
        self.project.dirty = True

    def _filters_changed(self):
        self.filter_timer.start()

    def _apply_filters(self):
        self.proxy_model.set_filters(
            self.search_edit.text(), self.category_combo.currentText(), self.type_combo.currentData() or "全部",
            self.modified_check.isChecked()
        )

    def _selection_changed(self):
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return
        source = self.proxy_model.mapToSource(indexes[0])
        self.load_slot(self.resource_model.slot(source.row()))

    def load_slot(self, slot: ResourceSlot):
        self.selected_slot = slot
        change = self.project.changes.get(slot.id)
        display_label = friendly_resource_label(slot)
        self.detail_title.setText(display_label)
        self.detail_info.setText(
            f"应用/区域：{slot.category}\n模块：{slot.module}\n类型：{TYPE_LABELS.get(slot.resource_type, slot.resource_type)}\n"
            f"资源：{slot.name}\n路径：{slot.path or '按目标注入'}\n支持状态：{slot.status}　风险：{slot.risk}\n"
            f"尺寸：{slot.width or '—'} × {slot.height or '—'}　格式：{slot.actual_format or '—'}\n"
            f"重复声明：{slot.occurrences}"
        )
        self.value_edit.setText(change.value if change and change.value is not None else "")
        self.image_source = change.source_file if change and change.source_kind == "file" else None
        self.image_edit.setText("默认灰白图片" if change and change.source_kind == "placeholder" else self.image_source or "")
        self._set_combo_data(self.fit_combo, change.fit if change else "cover")
        self._set_combo_data(self.enhance_combo, change.enhance if change else "none")
        self.enhance_slider.setValue(round((change.enhance_strength if change else 0) * 100))
        self.focus_x.setValue(round((change.focus_x if change else 0.5) * 100))
        self.focus_y.setValue(round((change.focus_y if change else 0.5) * 100))
        self._show_raw_preview()
        is_image = slot.resource_type in {"image", "icon", "wallpaper", "preview"}
        self.value_edit.setVisible(not is_image)
        self.color_button.setVisible(slot.resource_type == "color")
        for widget in [
            self.image_edit, self.choose_image_button, self.fit_combo, self.enhance_combo,
            self.enhance_slider, self.focus_x, self.focus_y, self.preview_label, self.processed_preview_button,
        ]:
            widget.setVisible(is_image)
        unsupported = slot.status == "当前版本不支持"
        for widget in self.detail_widgets:
            widget.setEnabled(not unsupported)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str):
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def pick_color(self):
        initial = QColor(self.value_edit.text()) if self.value_edit.text() else QColor(Colors.INK_MUTED)
        color = QColorDialog.getColor(initial, self, "选择颜色", QColorDialog.ShowAlphaChannel)
        if color.isValid():
            self.value_edit.setText(color.name(QColor.HexArgb).upper())

    def choose_image(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if filename:
            self.image_source = filename
            self.image_edit.setText(filename)
            self._show_raw_preview()

    def _show_raw_preview(self):
        if self.selected_slot:
            change = self.project.changes.get(self.selected_slot.id)
            if change and change.source_kind == "placeholder" and not self.image_source:
                pixmap = QPixmap()
                pixmap.loadFromData(render_placeholder(self.selected_slot))
                self.preview_label.setText("")
                self.preview_label.setPixmap(pixmap.scaled(320, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        if not self.image_source or not Path(self.image_source).is_file():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("图片预览")
            return
        pixmap = QPixmap(self.image_source)
        self.preview_label.setText("")
        self.preview_label.setPixmap(pixmap.scaled(320, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def preview_processed_image(self):
        if not self.selected_slot:
            return
        try:
            existing = self.project.changes.get(self.selected_slot.id)
            if not self.image_source and existing and existing.source_kind == "placeholder":
                data = render_placeholder(self.selected_slot)
            elif self.image_source:
                change = self._detail_change()
                data = render_image(Path(self.image_source), self.selected_slot, change)
            else:
                return
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            self.preview_label.setText("")
            self.preview_label.setPixmap(pixmap.scaled(320, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception as exc:
            self._show_operation_error(
                "预览失败",
                "无法生成图片预览。",
                "请确认图片文件完整，或重新选择图片后再试。",
                exc,
            )

    def _detail_change(self) -> ResourceChange:
        slot = self.selected_slot
        if slot is None:
            raise ValueError("没有选择资源")
        if slot.resource_type in {"image", "icon", "wallpaper", "preview"}:
            if not self.image_source:
                existing = self.project.changes.get(slot.id)
                if existing and existing.source_kind == "placeholder":
                    updated = copy.deepcopy(existing)
                    updated.fit = self.fit_combo.currentData()
                    updated.focus_x = self.focus_x.value() / 100
                    updated.focus_y = self.focus_y.value() / 100
                    updated.enhance = self.enhance_combo.currentData()
                    updated.enhance_strength = self.enhance_slider.value() / 100
                    return updated
                raise ValueError("请先选择图片")
            return ResourceChange(
                slot_id=slot.id,
                source_file=self.image_source,
                source_kind="file",
                fit=self.fit_combo.currentData(),
                focus_x=self.focus_x.value() / 100,
                focus_y=self.focus_y.value() / 100,
                enhance=self.enhance_combo.currentData(),
                enhance_strength=self.enhance_slider.value() / 100,
            )
        value = self.value_edit.text().strip()
        if not value:
            raise ValueError("请输入资源值")
        value = validate_change_value(slot.resource_type, value)
        return ResourceChange(slot_id=slot.id, value=value)

    def apply_detail(self):
        if not self.selected_slot:
            return
        try:
            change = self._detail_change()
            if self.sync_compatible.isChecked() and not self.selected_slot.id.startswith("__custom__"):
                matches = {}
                for candidate in self.resource_model.resources:
                    if (
                        candidate.resource_type == self.selected_slot.resource_type
                        and candidate.name == self.selected_slot.name
                        and candidate.category == self.selected_slot.category
                        and candidate.status != "当前版本不支持"
                    ):
                        cloned = copy.deepcopy(change)
                        cloned.slot_id = candidate.id
                        matches[candidate.id] = cloned
                self.undo_stack.push(BulkChangeCommand(self, matches, f"同步修改 {friendly_resource_label(self.selected_slot)}"))
            else:
                self.undo_stack.push(ChangeCommand(self, self.selected_slot.id, change, f"修改 {friendly_resource_label(self.selected_slot)}"))
            self.statusBar().showMessage(f"已修改：{friendly_resource_label(self.selected_slot)}", 4000)
        except Exception as exc:
            self._show_operation_error(
                "无法应用",
                "无法应用本次修改。",
                "请检查资源类型和填写的值后重试。",
                exc,
                warning=True,
            )

    def reset_detail(self):
        if self.selected_slot and self.selected_slot.id in self.project.changes:
            self.undo_stack.push(ChangeCommand(self, self.selected_slot.id, None, f"恢复 {friendly_resource_label(self.selected_slot)}"))

    def apply_simple_setting(self, setting, template: ResourceChange):
        slots = self.simple_resolved.get(setting.id, [])
        changes: dict[str, ResourceChange] = {}
        for slot in slots:
            change = copy.deepcopy(template)
            change.slot_id = slot.id
            changes[slot.id] = change
        if not changes:
            QMessageBox.information(self, "没有适用资源", "当前资源目录中没有找到这个项目的可用资源。")
            return
        self.undo_stack.push(BulkChangeCommand(self, changes, f"设置{setting.title}"))
        self.statusBar().showMessage(f"已设置“{setting.title}”，同步 {len(changes)} 个兼容资源", 5000)

    def reset_simple_setting(self, setting):
        slots = self.simple_resolved.get(setting.id, [])
        resets = {slot.id: None for slot in slots if slot.id in self.project.changes}
        if resets:
            self.undo_stack.push(BulkChangeCommand(self, resets, f"恢复{setting.title}"))

    def refresh_views(self):
        if hasattr(self, "resource_model"):
            self.resource_model.project = self.project
            expected_resources = [*self.catalog.resources, *self.project.custom_resources]
            if [slot.id for slot in self.resource_model.resources] != [slot.id for slot in expected_resources]:
                self.resource_model.set_resources(expected_resources)
            self.resource_model.refresh()
            self.proxy_model.set_filters(
                self.proxy_model.query,
                self.proxy_model.category,
                self.proxy_model.resource_type,
                self.proxy_model.modified_only,
            )
        if hasattr(self, "simple_editor"):
            self.simple_editor.bind(self.simple_resolved, self.project, self.installed_packages)
        if hasattr(self, "changes_text"):
            lines = []
            grouped_ids: set[str] = set()
            changed_groups = 0
            affected = 0
            for setting in SIMPLE_SETTINGS:
                slots = self.simple_resolved.get(setting.id, [])
                modified = [slot for slot in slots if slot.id in self.project.changes]
                if not modified:
                    continue
                changed_groups += 1
                affected += len(modified)
                grouped_ids.update(slot.id for slot in modified)
                values = {self.project.changes[slot.id].value for slot in modified if self.project.changes[slot.id].value}
                value_text = next(iter(values)) if len(values) == 1 else "自定义图片" if setting.kind == "image" else "含单独调整"
                if len(modified) != len(slots):
                    value_text = "含单独调整"
                lines.append(f"● {setting.title}\n  {value_text} · 已修改 {len(modified)}/{len(slots)} 个兼容资源")
            advanced = [slot_id for slot_id in self.project.changes if slot_id not in grouped_ids]
            header = f"已修改 {changed_groups + len(advanced)} 项，涉及 {affected + len(advanced)} 个资源。"
            if advanced:
                slot_map = {slot.id: slot for slot in [*self.catalog.resources, *self.project.custom_resources]}
                details = []
                for slot_id in advanced:
                    slot = slot_map.get(slot_id)
                    details.append(f"  • {friendly_resource_label(slot) if slot else '未知高级资源'}")
                lines.append(f"高级修改（{len(advanced)} 项）\n" + "\n".join(details))
            self.changes_text.setPlainText(header + ("\n\n" + "\n\n".join(lines) if lines else "\n\n当前使用系统默认资源。"))
        self.setWindowTitle(f"{APP_NAME} {__version__} - {self.project.name}{' *' if self.project.dirty else ''}")

    def show_category(self, category: str):
        index = self.category_combo.findText(category)
        self.category_combo.setCurrentIndex(index if index >= 0 else 0)
        self.tabs.setCurrentIndex(2)

    def select_slot(self, slot_id: str):
        self.category_combo.setCurrentIndex(0)
        self.search_edit.setText(slot_id)
        self.tabs.setCurrentIndex(2)
        if self.proxy_model.rowCount() > 0:
            self.table.selectRow(0)

    def bulk_set_filtered_colors(self):
        color = QColorDialog.getColor(QColor(Colors.INK_MUTED), self, "批量设置当前筛选结果中的颜色", QColorDialog.ShowAlphaChannel)
        if not color.isValid():
            return
        value = color.name(QColor.HexArgb).upper()
        changes: dict[str, ResourceChange] = {}
        for row in range(self.proxy_model.rowCount()):
            source = self.proxy_model.mapToSource(self.proxy_model.index(row, 0))
            slot = self.resource_model.slot(source.row())
            if slot.resource_type == "color" and slot.status != "当前版本不支持":
                changes[slot.id] = ResourceChange(slot_id=slot.id, value=value)
        if not changes:
            QMessageBox.information(self, "没有颜色资源", "当前筛选结果中没有可编辑的颜色资源。")
            return
        answer = QMessageBox.question(self, "确认批量修改", f"将 {len(changes)} 个颜色资源设置为 {value}，是否继续？")
        if answer == QMessageBox.Yes:
            self.undo_stack.push(BulkChangeCommand(self, changes, f"批量设置 {len(changes)} 个颜色"))

    def add_custom_resource(self):
        dialog = CustomResourceDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            slot = dialog.create_slot()
            self.project.custom_resources.append(slot)
            self.project.dirty = True
            if self.category_combo.findText("高级自定义") < 0:
                self.category_combo.addItem("高级自定义")
            self.refresh_views()
            self.select_slot(slot.id)
            self.log(f"已添加高级资源槽位：{slot.module} / {slot.path} / {slot.name}")
        except Exception as exc:
            self._show_operation_error(
                "无法添加",
                "无法添加高级资源。",
                "请检查资源路径、类型和尺寸后重试。",
                exc,
                warning=True,
            )

    def new_project(self):
        if not self._confirm_discard():
            return
        self.project = ThemeProject()
        self.undo_stack.clear()
        self._bind_project()
        self.log("已新建空白主题工程。")

    def open_project(self):
        if not self._confirm_discard():
            return
        filename, _ = QFileDialog.getOpenFileName(self, "打开主题工程", "", "主题工程 (*.hwtproj.json *.json)")
        if not filename:
            return
        try:
            loaded = load_project(Path(filename))
            slot_map = {slot.id: slot for slot in [*self.catalog.resources, *loaded.custom_resources]}
            if not resolve_missing_assets(self, loaded, slot_map):
                return
            self.project = loaded
            self.undo_stack.clear()
            self._bind_project()
            self.log(f"已打开工程：{filename}")
        except Exception as exc:
            self._show_operation_error(
                "打开失败",
                "无法打开主题工程。",
                "请确认文件未损坏、格式正确，或重新选择工程文件。",
                exc,
            )

    def save_project(self):
        path = self.project.project_file
        if path is None:
            filename, _ = QFileDialog.getSaveFileName(self, "保存主题工程", f"{self.project.name}.hwtproj.json", "主题工程 (*.hwtproj.json)")
            if not filename:
                return
            path = Path(filename)
        try:
            slot_map = {slot.id: slot for slot in [*self.catalog.resources, *self.project.custom_resources]}
            if not resolve_missing_assets(self, self.project, slot_map):
                return
            save_project(self.project, path)
            self.refresh_views()
            self.log(f"工程已保存：{path}")
        except Exception as exc:
            self._show_operation_error(
                "保存失败",
                "无法保存主题工程。",
                "请确认目标文件夹可写，或更换保存位置后重试。",
                exc,
            )

    def export_hwt(self):
        slot_map = {slot.id: slot for slot in [*self.catalog.resources, *self.project.custom_resources]}
        if not resolve_missing_assets(self, self.project, slot_map):
            return
        preflight = preflight_export(self.project, self.catalog)
        if not preflight["valid"]:
            detail = "\n".join(
                f"• {item.get('slot_id', item.get('kind', '错误'))}：{item.get('message', item.get('kind', '无法导出'))}"
                for item in preflight["errors"][:10]
            )
            first_slot = preflight["errors"][0].get("slot_id") if preflight["errors"] else None
            if first_slot:
                self.select_slot(first_slot)
            else:
                self.tabs.setCurrentIndex(1)
            QMessageBox.warning(self, "导出预检失败", f"请先处理以下问题：\n\n{detail}")
            return
        summary = (
            f"已启用修改：{preflight['enabled_changes']}\n"
            f"值资源目标：{preflight['value_targets']}\n"
            f"图片目标：{preflight['image_targets']}\n"
            f"预计跳过：{preflight['skipped']}\n\n是否继续选择导出位置？"
        )
        if QMessageBox.question(self, "导出预检通过", summary) != QMessageBox.Yes:
            return
        start = self._theme_file_dialog_directory()
        filename, _ = QFileDialog.getSaveFileName(self, "导出 HWT", str(start / default_export_name(self.project)), "荣耀主题 (*.hwt)")
        if not filename:
            return
        self._remember_theme_file_directory(Path(filename).parent)
        try:
            path, report = export_theme(self.project, self.catalog, Path(filename))
            self.last_export = path
            self.refresh_views()
            warning = f"\n\n{report['report_warning']}" if report.get("report_warning") else ""
            detail = (
                f"主题已生成：\n{path}\n\n模块：{report['module_count']}\n"
                f"颜色/文字目标：{report['preflight']['value_targets']}\n"
                f"图片目标：{report['preflight']['image_targets']}\n"
                f"跳过：{len(report['skipped'])}\n文件大小：{report['file_size'] / 1024 / 1024:.2f} MB\n"
                f"验证：通过\n\nSHA-256：\n{report['sha256']}{warning}"
            )
            self.log(f"导出成功：{path}\nSHA-256：{report['sha256']}\n已写入 {report['applied_count']} 个覆盖目标。{warning}")
            QMessageBox.information(self, "导出成功", detail)
        except Exception as exc:
            self._show_operation_error(
                "导出失败",
                "无法导出 HWT。",
                "请处理导出预检问题，并确认目标位置可写后重试。",
                exc,
            )

    def send_phone(self):
        path = self.last_export
        if not path or not path.is_file():
            filename, _ = QFileDialog.getOpenFileName(
                self, "选择要发送的主题", str(self._theme_file_dialog_directory()), "荣耀主题 (*.hwt)"
            )
            if not filename:
                return
            path = Path(filename)
            self._remember_theme_file_directory(path.parent)
        dialog = PhoneTransferDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._start_phone_transfer(
            path,
            device=dialog.device,
            pair_code=dialog.pair_code,
            use_ssh=dialog.use_ssh,
        )

    def send_phone_ssh(self):
        path = self.last_export
        if not path or not path.is_file():
            filename, _ = QFileDialog.getOpenFileName(
                self, "选择要发送的主题", str(self._theme_file_dialog_directory()), "荣耀主题 (*.hwt)"
            )
            if not filename:
                return
            path = Path(filename)
            self._remember_theme_file_directory(path.parent)
        self._start_phone_transfer(path, use_ssh=True)

    def _theme_file_dialog_directory(self) -> Path:
        remembered = self.settings.value("paths/theme_directory", "", type=str)
        documents = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        )
        downloads = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        )
        candidates = []
        if self.last_export is not None:
            candidates.append(self.last_export.parent)
        if remembered:
            candidates.append(Path(remembered))
        candidates.extend(
            (
                documents / "HONOR Share" / "Honor Share",
                downloads,
                documents,
                Path.home(),
            )
        )
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return Path.home()

    def _remember_theme_file_directory(self, directory: Path):
        directory = Path(directory).expanduser()
        if directory.is_dir():
            self.settings.setValue("paths/theme_directory", str(directory))

    def _start_phone_transfer(self, path: Path, *, device=None, pair_code: str = "", use_ssh: bool = False):
        if self.transfer_thread and self.transfer_thread.isRunning():
            QMessageBox.warning(self, "正在发送", "已有一个发送任务正在运行。")
            return
        self._transfer_generation += 1
        generation = self._transfer_generation
        initial = "正在通过 Termux/SSH 上传并校验……" if use_ssh else "正在准备发送到手机……"
        self.progress = QProgressDialog(initial, "取消", 0, 1000, self)
        self.progress.setWindowTitle("发送到手机")
        self.progress.setMinimumDuration(0)
        self.progress.setValue(0)
        self.progress.show()
        self.transfer_thread = QThread(self)
        worker = TransferWorker(
            path, device=device, pair_code=pair_code, use_ssh=use_ssh, task_id=generation
        )
        worker.moveToThread(self.transfer_thread)
        self.transfer_thread.started.connect(worker.run)
        worker.finished.connect(self._transfer_finished)
        worker.failed.connect(self._transfer_failed)
        worker.progress.connect(self._transfer_progress)
        self.progress.canceled.connect(worker.cancel, Qt.DirectConnection)
        worker.finished.connect(self.transfer_thread.quit)
        worker.failed.connect(self.transfer_thread.quit)
        self.transfer_thread.finished.connect(worker.deleteLater)
        self.transfer_thread.setProperty("hwt_generation", generation)
        self.transfer_thread.finished.connect(self._transfer_thread_finished)
        self.transfer_thread.finished.connect(self.transfer_thread.deleteLater)
        self.transfer_thread.start()
        self._transfer_worker = worker

    def _transfer_progress(self, sent: int, total: int, stage: str, generation: int | None = None):
        if generation is not None and generation != self._transfer_generation:
            return
        if self._closing or not getattr(self, "progress", None):
            return
        self.progress.setLabelText(stage)
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(sent * 1000 / total)))
        else:
            self.progress.setRange(0, 0)

    def _transfer_thread_finished(self, generation: int | None = None):
        if generation is None:
            sender = self.sender()
            generation = sender.property("hwt_generation") if sender is not None else None
        if generation is not None and generation != self._transfer_generation:
            return
        self.transfer_thread = None
        self._transfer_worker = None
        self._maybe_close_after_threads()

    def _transfer_finished(self, result: dict, generation: int | None = None):
        if generation is not None and generation != self._transfer_generation:
            return
        if self._closing:
            return
        progress = getattr(self, "progress", None)
        self.progress = None
        if progress is not None:
            progress.close()
        self.log(f"发送成功：{result['remote']}\nSHA-256：{result['sha256']}")
        if result.get("transport") == "apk":
            opened = "手机端会显示打开荣耀‘主题’的通知。"
            warning_text = ""
        else:
            opened = "已为你打开荣耀‘主题’应用。" if result.get("theme_app_opened") else "请手动打开荣耀‘主题’应用。"
            preflight = result.get("preflight")
            warnings = preflight.get("warnings", []) if isinstance(preflight, dict) else []
            formatted_warnings = _format_preflight_warnings(warnings)
            warning_text = "\n" + formatted_warnings if formatted_warnings else ""
        QMessageBox.information(
            self,
            "发送成功",
            f"手机路径：\n{result['remote']}\n\nSHA-256 校验一致。\n{opened}"
            f"{warning_text}\n请进入‘我的→下载→主题’查找；如页面已经打开，请返回后重新进入一次。",
        )

    def _transfer_failed(self, detail: str, code: str = "unexpected", generation: int | None = None):
        if generation is not None and generation != self._transfer_generation:
            return
        if self._closing:
            return
        progress = getattr(self, "progress", None)
        self.progress = None
        if progress is not None:
            progress.close()
        self.log(detail)
        messages = {
            "cancelled": "发送已取消。",
            "no_device": "没有选择手机，请先连接并识别手机。",
            "file_changed": "主题文件在发送过程中发生变化，请重新选择或重新导出后再试。",
            "unexpected": "发送失败，请检查手机助手、网络连接和主题文件。",
        }
        message = messages.get(code, "发送失败，请检查手机助手、网络连接和主题文件。")
        if code == "cancelled":
            QMessageBox.information(self, "已取消", message)
        else:
            QMessageBox.critical(self, "发送失败", message)

    def rescan_source(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择大雪源主题", str(default_source_theme()), "荣耀主题 (*.hwt)")
        if not filename:
            return
        try:
            catalog = scan_theme(Path(filename))
            save_user_catalog(catalog)
            self.bind_catalog(catalog)
            self.log(f"扫描完成：{filename}，资源槽位 {len(catalog.resources)}。")
            self._log_source_compatibility_summary(catalog)
        except Exception as exc:
            self._show_operation_error(
                "扫描失败",
                "无法扫描主题。",
                "请确认选中的 HWT 文件完整且未被占用后重试。",
                exc,
            )

    def _log_source_compatibility_summary(self, catalog: ThemeCatalog) -> None:
        summary = source_compatibility_report(catalog)["summary"]
        self.log(
            "源主题兼容性报告："
            f"兼容性警告 {summary['compatibility_warnings']} 条，"
            f"扫描完整性警告 {summary['scan_integrity_warnings']} 条；"
            "这些警告不会直接替代导出文件的严格验证。"
        )

    def show_source_compatibility_report(self):
        report = source_compatibility_report(self.catalog)
        summary = report["summary"]
        lines = [
            f"源主题：{report['source_path'] or '未记录'}",
            f"模块：{summary['modules']}    资源槽位：{summary['resource_slots']}",
            f"兼容性警告：{summary['compatibility_warnings']}",
            f"扫描完整性警告：{summary['scan_integrity_warnings']}",
            "",
            "警告分类：",
        ]
        lines.extend(
            f"{kind}：{count}"
            for kind, count in list(summary["by_kind"].items())[:12]
        )
        lines.extend(("", "严格导出验证：未在此报告中执行；导出时会单独验证生成的 HWT。"))
        QMessageBox.information(self, "源主题兼容性报告", "\n".join(lines))

    def _confirm_discard(self) -> bool:
        if not self.project.dirty:
            return True
        answer = QMessageBox.question(self, "未保存修改", "当前工程尚未保存，是否放弃修改？")
        return answer == QMessageBox.Yes

    def _background_threads(self) -> list[QThread]:
        return [
            thread
            for thread in (self.update_thread, self.profile_thread, self.transfer_thread)
            if thread is not None
        ]

    def _has_running_background_threads(self) -> bool:
        return any(thread.isRunning() for thread in self._background_threads())

    def _cancel_background_tasks(self):
        for worker in (self.update_worker, self._profile_worker, self._transfer_worker):
            if worker is not None:
                worker.cancel()
        for thread in self._background_threads():
            thread.requestInterruption()
            thread.quit()
        for dialog_name in ("update_progress", "progress"):
            dialog = getattr(self, dialog_name, None)
            if dialog is not None:
                dialog.close()
                setattr(self, dialog_name, None)

    def _maybe_close_after_threads(self):
        if self._closing and not self._has_running_background_threads():
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        if self._closing:
            if self._has_running_background_threads():
                event.ignore()
            else:
                event.accept()
            return
        if not self._confirm_discard():
            event.ignore()
            return
        self._closing = True
        self._cancel_background_tasks()
        if self._has_running_background_threads():
            self.statusBar().showMessage("正在停止后台任务……")
            event.ignore()
        else:
            event.accept()

    def _resize_edges_at(self, global_pos):
        if self.isMaximized() or self.isFullScreen():
            return Qt.Edges()
        rect = self.frameGeometry()
        margin = self._resize_margin
        edges = Qt.Edges()
        if abs(global_pos.x() - rect.left()) <= margin:
            edges |= Qt.Edge.LeftEdge
        if abs(global_pos.x() - rect.right()) <= margin:
            edges |= Qt.Edge.RightEdge
        if abs(global_pos.y() - rect.top()) <= margin:
            edges |= Qt.Edge.TopEdge
        if abs(global_pos.y() - rect.bottom()) <= margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def eventFilter(self, watched, event):
        if isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched)):
            if isinstance(event, QMouseEvent):
                global_pos = event.globalPosition().toPoint()
                edges = self._resize_edges_at(global_pos)
                if event.type() == QEvent.Type.MouseMove and not (event.buttons() & Qt.MouseButton.LeftButton):
                    if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
                        if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
                            self.setCursor(Qt.CursorShape.SizeFDiagCursor if edges & Qt.Edge.LeftEdge else Qt.CursorShape.SizeBDiagCursor)
                        else:
                            self.setCursor(Qt.CursorShape.SizeHorCursor)
                    elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
                        self.setCursor(Qt.CursorShape.SizeVerCursor)
                    else:
                        self.unsetCursor()
                elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton and edges:
                    handle = self.windowHandle()
                    if handle is not None and handle.startSystemResize(edges):
                        event.accept()
                        return True
                elif event.type() == QEvent.Type.MouseButtonRelease:
                    self.unsetCursor()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.width()
        if hasattr(self, "simple_header"):
            self.main_toolbar.setToolButtonStyle(
                Qt.ToolButtonIconOnly if width < 720 else Qt.ToolButtonTextBesideIcon
            )
            self.simple_header.setDirection(
                QBoxLayout.TopToBottom if width < 720 else QBoxLayout.LeftToRight
            )
        if hasattr(self, "simple_scroll"):
            self.simple_editor.set_available_width(self.simple_scroll.viewport().width())
        if hasattr(self, "identity_form"):
            self.identity_form.setRowWrapPolicy(
                QFormLayout.WrapAllRows if width < 720 else QFormLayout.DontWrapRows
            )
        if hasattr(self, "filter_bar"):
            self._layout_filter_bar(width)
        if hasattr(self, "resource_splitter"):
            self.resource_splitter.setOrientation(Qt.Vertical if width < 1056 else Qt.Horizontal)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "simple_scroll"):
            self.simple_editor.set_available_width(self.simple_scroll.viewport().width())

    def minimumSizeHint(self):
        return QSize(320, 480)

    def log(self, message: str):
        self._log_lines.append(message.rstrip())
        if hasattr(self, "log_text"):
            self.log_text.appendPlainText(message.rstrip() + "\n")

    def _show_operation_error(
        self,
        title: str,
        reason: str,
        suggestion: str,
        exc: Exception,
        *,
        warning: bool = False,
    ) -> None:
        self.log(f"{title}（原始错误）：{exc}")
        trace = traceback.format_exc().strip()
        if trace and trace != "NoneType: None":
            self.log(trace)
        message = f"{reason}\n\n处理建议：{suggestion}"
        if warning:
            QMessageBox.warning(self, title, message)
        else:
            QMessageBox.critical(self, title, message)


def apply_style(app: QApplication):
    install_qt_translations(app)
    apply_design_system(app)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("子木")
    apply_style(app)
    try:
        window = MainWindow()
    except Exception:
        traceback.print_exc()
        QMessageBox.critical(None, "启动失败", "程序启动失败，请查看运行日志并确认安装目录完整。")
        return 1
    window.show()
    if os.environ.get("HWT_DISABLE_UPDATE_CHECK") != "1":
        QTimer.singleShot(1800, lambda: window.check_for_updates(silent=True))
    return app.exec()
