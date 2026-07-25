from __future__ import annotations

import copy
import json
import shutil
import sys
import traceback
import uuid
from io import BytesIO
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSortFilterProxyModel, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPixmap, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QSlider,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .blank import create_blank_theme
from . import __version__
from .catalog import load_catalog, save_catalog, scan_theme
from .exporter import default_export_name, export_theme
from .imageops import render_image
from .models import ResourceChange, ResourceSlot, ThemeCatalog, ThemeProject
from .paths import APP_NAME, bundled_blank_theme, bundled_catalog, data_dir, default_source_theme
from .projectio import load_project, save_project
from .ssh_transfer import transfer_to_phone
from .validation import normalize_color


HEADERS = ["状态", "分类", "模块", "类型", "中文说明", "资源名", "路径", "当前设置"]


class ResourceTableModel(QAbstractTableModel):
    def __init__(self, catalog: ThemeCatalog, project: ThemeProject):
        super().__init__()
        self.catalog = catalog
        self.project = project
        self.resources = catalog.resources

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.resources)

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        slot = self.resources[index.row()]
        change = self.project.changes.get(slot.id)
        if role == Qt.DisplayRole:
            values = [
                "已启用" if change and change.enabled else "未启用（系统默认）",
                slot.category,
                slot.module,
                slot.resource_type,
                slot.label,
                slot.name,
                slot.path or "—",
                self._change_text(change),
            ]
            return values[index.column()]
        if role == Qt.ToolTipRole:
            return f"状态：{slot.status}\n风险：{slot.risk}\nID：{slot.id}"
        if role == Qt.ForegroundRole:
            if slot.status == "当前版本不支持":
                return QColor("#B00020")
            if change and change.enabled:
                return QColor("#006C4C")
        if role == Qt.UserRole:
            return slot.id
        return None

    @staticmethod
    def _change_text(change: ResourceChange | None) -> str:
        if not change or not change.enabled:
            return "—"
        if change.value is not None:
            return change.value
        if change.source_file:
            return Path(change.source_file).name
        return "已启用"

    def slot(self, row: int) -> ResourceSlot:
        return self.resources[row]

    def refresh(self):
        self.layoutChanged.emit()


class ResourceFilterModel(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.query = ""
        self.category = "全部"
        self.resource_type = "全部"
        self.modified_only = False

    def filterAcceptsRow(self, source_row, source_parent):
        model: ResourceTableModel = self.sourceModel()
        slot = model.slot(source_row)
        if self.category != "全部" and slot.category != self.category:
            return False
        if self.resource_type != "全部" and slot.resource_type != self.resource_type:
            return False
        if self.modified_only and slot.id not in model.project.changes:
            return False
        if self.query:
            haystack = " ".join((slot.id, slot.module, slot.container, slot.name, slot.path, slot.category, slot.label)).lower()
            if self.query.lower() not in haystack:
                return False
        return True

    def set_filters(self, query: str, category: str, resource_type: str, modified_only: bool):
        self.query = query
        self.category = category
        self.resource_type = resource_type
        self.modified_only = modified_only
        self.invalidateFilter()


class ChangeCommand(QUndoCommand):
    def __init__(self, window: "MainWindow", slot_id: str, new_change: ResourceChange | None, text: str):
        super().__init__(text)
        self.window = window
        self.slot_id = slot_id
        self.new_change = copy.deepcopy(new_change)
        self.old_change = copy.deepcopy(window.project.changes.get(slot_id))

    def _set(self, change):
        if change is None:
            self.window.project.changes.pop(self.slot_id, None)
        else:
            self.window.project.changes[self.slot_id] = copy.deepcopy(change)
        self.window.project.dirty = True
        self.window.refresh_views()

    def redo(self):
        self._set(self.new_change)

    def undo(self):
        self._set(self.old_change)


class BulkChangeCommand(QUndoCommand):
    def __init__(self, window: "MainWindow", new_changes: dict[str, ResourceChange], text: str):
        super().__init__(text)
        self.window = window
        self.new_changes = copy.deepcopy(new_changes)
        self.old_changes = {key: copy.deepcopy(window.project.changes.get(key)) for key in new_changes}

    def _apply(self, values):
        for key, change in values.items():
            if change is None:
                self.window.project.changes.pop(key, None)
            else:
                self.window.project.changes[key] = copy.deepcopy(change)
        self.window.project.dirty = True
        self.window.refresh_views()

    def redo(self):
        self._apply(self.new_changes)

    def undo(self):
        self._apply(self.old_changes)


class CustomResourceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加高级覆盖资源")
        layout = QFormLayout(self)
        self.module = QLineEdit("com.android.settings")
        self.kind = QComboBox()
        self.kind.addItems(["color", "bool", "string", "image"])
        self.name = QLineEdit()
        self.path = QLineEdit("theme.xml")
        self.width = QLineEdit()
        self.height = QLineEdit()
        layout.addRow("目标包名/模块", self.module)
        layout.addRow("资源类型", self.kind)
        layout.addRow("资源名", self.name)
        layout.addRow("theme.xml 或图片路径", self.path)
        layout.addRow("目标宽度（图片可选）", self.width)
        layout.addRow("目标高度（图片可选）", self.height)
        hint = QLabel("仅在已经确认准确包名和 Android 资源路径时使用。图片路径示例：res/drawable-xxhdpi/example.png")
        hint.setWordWrap(True)
        layout.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def create_slot(self) -> ResourceSlot:
        module = self.module.text().strip()
        kind = self.kind.currentText()
        name = self.name.text().strip()
        path = self.path.text().strip().replace("\\", "/")
        if not module or not name or not path:
            raise ValueError("模块、资源名和路径不能为空")
        if kind == "image" and Path(path).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("图片路径必须以 .png、.jpg、.jpeg 或 .webp 结尾")
        try:
            width = int(self.width.text()) if self.width.text().strip() else None
            height = int(self.height.text()) if self.height.text().strip() else None
        except ValueError:
            raise ValueError("宽度和高度必须是整数")
        actual = None
        if kind == "image":
            suffix = Path(path).suffix.lower()
            actual = "JPEG" if suffix in {".jpg", ".jpeg"} else "WEBP" if suffix == ".webp" else "PNG"
        return ResourceSlot(
            id=f"__custom__::{uuid.uuid4().hex}",
            module=module,
            container=path if kind != "image" else "",
            resource_type=kind,
            name=name,
            path=path,
            category="高级自定义",
            label=f"自定义：{name}",
            status="可能支持",
            risk="高",
            width=width,
            height=height,
            actual_format=actual,
            extension=Path(path).suffix.lower() if kind == "image" else None,
            synthetic=False,
        )


class TransferWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            self.finished.emit(transfer_to_phone(self.path))
        except Exception:
            self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1500, 920)
        self.catalog = self._load_initial_catalog()
        self.project = ThemeProject()
        self.undo_stack = QUndoStack(self)
        self.last_export: Path | None = None
        self.transfer_thread: QThread | None = None
        self._build_ui()
        self._bind_project()
        self.log("已加载大雪资源目录。原始主题仅用于只读分析，不会被修改。")

    def _load_initial_catalog(self) -> ThemeCatalog:
        catalog_path = bundled_catalog()
        if catalog_path.is_file():
            return load_catalog(catalog_path)
        cached = data_dir() / "catalog_daxue.json"
        if cached.is_file():
            return load_catalog(cached)
        source = default_source_theme()
        if not source.is_file():
            raise FileNotFoundError("找不到资源目录，也找不到默认大雪主题。")
        catalog = scan_theme(source)
        save_catalog(catalog, cached)
        return catalog

    def _build_ui(self):
        self.setStatusBar(QStatusBar())
        self._build_toolbar()
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_overview_tab(), "概览")
        self.tabs.addTab(self._build_common_tab(), "常用编辑")
        self.tabs.addTab(self._build_resources_tab(), "全部资源")
        self.tabs.addTab(self._build_changes_tab(), "修改清单")
        self.tabs.addTab(self._build_log_tab(), "日志")

    def _build_toolbar(self):
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        actions = [
            ("新建工程", self.new_project),
            ("打开工程", self.open_project),
            ("保存工程", self.save_project),
            ("导出 HWT", self.export_hwt),
            ("发送到手机", self.send_phone),
            ("重新扫描大雪", self.rescan_source),
        ]
        for label, callback in actions:
            action = QAction(label, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        toolbar.addSeparator()
        undo_action = self.undo_stack.createUndoAction(self, "撤销")
        redo_action = self.undo_stack.createRedoAction(self, "重做")
        undo_action.setShortcut("Ctrl+Z")
        redo_action.setShortcut("Ctrl+Y")
        toolbar.addAction(undo_action)
        toolbar.addAction(redo_action)

    def _build_overview_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("空白主题工程")
        title.setFont(QFont("Microsoft YaHei UI", 20, QFont.Bold))
        layout.addWidget(title)
        description = QLabel(
            "大雪主题只提供资源名称和目标路径。未启用的项目不会写入新主题，手机将继续使用系统默认资源。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        stats = self.catalog.stats
        stats_text = (
            f"应用/框架 ZIP：{stats.get('modules', 0)}　"
            f"颜色槽位：{stats.get('color_slots', 0)}　"
            f"图片槽位：{stats.get('image_slots', 0)}　"
            f"图标槽位：{stats.get('icon_slots', 0)}　"
            f"资源总槽位：{stats.get('resource_slots', len(self.catalog.resources))}"
        )
        stats_label = QLabel(stats_text)
        stats_label.setStyleSheet("padding: 12px; background: #EEF3F8; border-radius: 6px;")
        layout.addWidget(stats_label)

        identity = QGroupBox("主题身份")
        form = QFormLayout(identity)
        self.name_edit = QLineEdit()
        self.title_edit = QLineEdit()
        self.author_edit = QLineEdit()
        self.designer_edit = QLineEdit()
        self.version_edit = QLineEdit()
        self.screen_edit = QLineEdit()
        for label, widget in [
            ("方案名称", self.name_edit),
            ("主题标题", self.title_edit),
            ("作者", self.author_edit),
            ("设计者", self.designer_edit),
            ("版本", self.version_edit),
            ("屏幕类型", self.screen_edit),
        ]:
            form.addRow(label, widget)
            widget.textEdited.connect(self._identity_edited)
        layout.addWidget(identity)

        warning = QLabel(
            f"源主题包含 {len(self.catalog.warnings)} 条兼容性记录；它们不会复制到空白主题。"
            "微信 8.0.76 主界面图片背景已标记为当前 HWT 方案不支持。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("padding: 10px; color: #7A3E00; background: #FFF4E5;")
        layout.addWidget(warning)
        layout.addStretch(1)
        return page

    def _build_common_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel("选择常用区域后会跳转到对应资源。只有点击“应用修改”的资源才会进入导出的 HWT。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        grid = QGridLayout()
        categories = [
            ("桌面和锁屏壁纸", "主题基础"),
            ("微信", "微信"),
            ("设置", "设置"),
            ("信息/短信", "信息与短信"),
            ("电话", "电话与通话"),
            ("联系人", "联系人"),
            ("桌面", "桌面"),
            ("控制中心/通知栏", "控制中心与通知栏"),
            ("系统通用框架", "系统通用框架"),
            ("桌面图标", "桌面图标"),
        ]
        for index, (label, category) in enumerate(categories):
            button = QPushButton(label)
            button.setMinimumHeight(56)
            button.clicked.connect(lambda checked=False, c=category: self.show_category(c))
            grid.addWidget(button, index // 3, index % 3)
        layout.addLayout(grid)

        backgrounds = QGroupBox("独立应用背景快捷入口")
        bg_layout = QHBoxLayout(backgrounds)
        for slot in self.catalog.resources:
            if slot.synthetic and slot.id.startswith("__synthetic__::background"):
                button = QPushButton(slot.label)
                button.clicked.connect(lambda checked=False, sid=slot.id: self.select_slot(sid))
                bg_layout.addWidget(button)
        layout.addWidget(backgrounds)
        layout.addStretch(1)
        return page

    def _build_resources_tab(self):
        page = QWidget()
        root = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索包名、资源名、路径或中文说明……")
        self.category_combo = QComboBox()
        self.category_combo.addItems(["全部"] + sorted({x.category for x in self.catalog.resources}))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["全部"] + sorted({x.resource_type for x in self.catalog.resources}))
        self.modified_check = QCheckBox("仅显示已修改")
        bulk_button = QPushButton("批量设置筛选颜色")
        bulk_button.clicked.connect(self.bulk_set_filtered_colors)
        custom_button = QPushButton("添加高级资源")
        custom_button.clicked.connect(self.add_custom_resource)
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.category_combo)
        filters.addWidget(self.type_combo)
        filters.addWidget(self.modified_check)
        filters.addWidget(bulk_button)
        filters.addWidget(custom_button)
        root.addLayout(filters)

        self.resource_model = ResourceTableModel(self.catalog, self.project)
        self.proxy_model = ResourceFilterModel()
        self.proxy_model.setSourceModel(self.resource_model)
        self.table = QTableView()
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)

        self.detail = self._build_detail_panel()
        splitter = QSplitter()
        splitter.addWidget(self.table)
        splitter.addWidget(self.detail)
        splitter.setSizes([1050, 380])
        root.addWidget(splitter, 1)

        self.search_edit.textChanged.connect(self._filters_changed)
        self.category_combo.currentTextChanged.connect(self._filters_changed)
        self.type_combo.currentTextChanged.connect(self._filters_changed)
        self.modified_check.toggled.connect(self._filters_changed)
        return page

    def _build_detail_panel(self):
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(panel)
        self.detail_title = QLabel("请选择一个资源")
        self.detail_title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        self.detail_title.setWordWrap(True)
        layout.addWidget(self.detail_title)
        self.detail_info = QLabel()
        self.detail_info.setWordWrap(True)
        self.detail_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.detail_info)

        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("颜色、布尔值或文字")
        layout.addWidget(self.value_edit)
        self.color_button = QPushButton("选择颜色")
        self.color_button.clicked.connect(self.pick_color)
        layout.addWidget(self.color_button)

        image_row = QHBoxLayout()
        self.image_edit = QLineEdit()
        self.image_edit.setReadOnly(True)
        choose = QPushButton("选择图片")
        choose.clicked.connect(self.choose_image)
        image_row.addWidget(self.image_edit, 1)
        image_row.addWidget(choose)
        layout.addLayout(image_row)
        self.preview_label = QLabel("图片预览")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(220)
        self.preview_label.setStyleSheet("background: #F2F2F2; border: 1px solid #C8C8C8;")
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
        preview_button.clicked.connect(self.preview_processed_image)
        layout.addWidget(preview_button)
        buttons = QHBoxLayout()
        apply_button = QPushButton("应用修改")
        apply_button.clicked.connect(self.apply_detail)
        reset_button = QPushButton("恢复系统默认")
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

    def _bind_project(self):
        self.resource_model.project = self.project
        self.resource_model.beginResetModel()
        self.resource_model.resources = [*self.catalog.resources, *self.project.custom_resources]
        self.resource_model.endResetModel()
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
        self.proxy_model.set_filters(
            self.search_edit.text(), self.category_combo.currentText(), self.type_combo.currentText(), self.modified_check.isChecked()
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
        self.detail_title.setText(slot.label)
        self.detail_info.setText(
            f"分类：{slot.category}\n模块：{slot.module}\n类型：{slot.resource_type}\n"
            f"资源：{slot.name}\n路径：{slot.path or '按目标注入'}\n支持状态：{slot.status}　风险：{slot.risk}\n"
            f"尺寸：{slot.width or '—'} × {slot.height or '—'}　格式：{slot.actual_format or '—'}\n"
            f"重复声明：{slot.occurrences}"
        )
        self.value_edit.setText(change.value if change and change.value is not None else "")
        self.image_source = change.source_file if change else None
        self.image_edit.setText(self.image_source or "")
        self._set_combo_data(self.fit_combo, change.fit if change else "cover")
        self._set_combo_data(self.enhance_combo, change.enhance if change else "none")
        self.enhance_slider.setValue(round((change.enhance_strength if change else 0) * 100))
        self.focus_x.setValue(round((change.focus_x if change else 0.5) * 100))
        self.focus_y.setValue(round((change.focus_y if change else 0.5) * 100))
        self._show_raw_preview()
        is_image = slot.resource_type in {"image", "icon", "wallpaper", "preview"}
        self.value_edit.setVisible(not is_image)
        self.color_button.setVisible(slot.resource_type == "color")
        for widget in [self.image_edit, self.fit_combo, self.enhance_combo, self.enhance_slider, self.focus_x, self.focus_y, self.preview_label]:
            widget.setVisible(is_image)
        unsupported = slot.status == "当前版本不支持"
        for widget in self.detail_widgets:
            widget.setEnabled(not unsupported)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str):
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def pick_color(self):
        initial = QColor(self.value_edit.text()) if self.value_edit.text() else QColor("#808080")
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
        if not self.image_source or not Path(self.image_source).is_file():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("图片预览")
            return
        pixmap = QPixmap(self.image_source)
        self.preview_label.setText("")
        self.preview_label.setPixmap(pixmap.scaled(320, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def preview_processed_image(self):
        if not self.selected_slot or not self.image_source:
            return
        try:
            change = self._detail_change()
            data = render_image(Path(self.image_source), self.selected_slot, change)
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            self.preview_label.setText("")
            self.preview_label.setPixmap(pixmap.scaled(320, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception as exc:
            QMessageBox.critical(self, "预览失败", str(exc))

    def _detail_change(self) -> ResourceChange:
        slot = self.selected_slot
        if slot is None:
            raise ValueError("没有选择资源")
        if slot.resource_type in {"image", "icon", "wallpaper", "preview"}:
            if not self.image_source:
                raise ValueError("请先选择图片")
            return ResourceChange(
                slot_id=slot.id,
                source_file=self.image_source,
                fit=self.fit_combo.currentData(),
                focus_x=self.focus_x.value() / 100,
                focus_y=self.focus_y.value() / 100,
                enhance=self.enhance_combo.currentData(),
                enhance_strength=self.enhance_slider.value() / 100,
            )
        value = self.value_edit.text().strip()
        if not value:
            raise ValueError("请输入资源值")
        if slot.resource_type == "color":
            value = normalize_color(value)
        if slot.resource_type == "bool" and value.lower() not in {"true", "false"}:
            raise ValueError("布尔值只能是 true 或 false")
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
                self.undo_stack.push(BulkChangeCommand(self, matches, f"同步修改 {self.selected_slot.label}"))
            else:
                self.undo_stack.push(ChangeCommand(self, self.selected_slot.id, change, f"修改 {self.selected_slot.label}"))
            self.statusBar().showMessage(f"已启用：{self.selected_slot.label}", 4000)
        except Exception as exc:
            QMessageBox.warning(self, "无法应用", str(exc))

    def reset_detail(self):
        if self.selected_slot and self.selected_slot.id in self.project.changes:
            self.undo_stack.push(ChangeCommand(self, self.selected_slot.id, None, f"恢复 {self.selected_slot.label}"))

    def refresh_views(self):
        if hasattr(self, "resource_model"):
            self.resource_model.project = self.project
            expected = len(self.catalog.resources) + len(self.project.custom_resources)
            if len(self.resource_model.resources) != expected:
                self.resource_model.beginResetModel()
                self.resource_model.resources = [*self.catalog.resources, *self.project.custom_resources]
                self.resource_model.endResetModel()
            self.resource_model.refresh()
            self.proxy_model.invalidateFilter()
        if hasattr(self, "changes_text"):
            slot_map = {slot.id: slot for slot in [*self.catalog.resources, *self.project.custom_resources]}
            lines = []
            for slot_id, change in self.project.changes.items():
                slot = slot_map.get(slot_id)
                label = slot.label if slot else slot_id
                value = change.value or change.source_file or "已启用"
                lines.append(f"{label}\n  {slot_id}\n  → {value}")
            self.changes_text.setPlainText("\n\n".join(lines) if lines else "当前没有启用任何覆盖资源。")
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
        color = QColorDialog.getColor(QColor("#808080"), self, "批量设置当前筛选结果中的颜色", QColorDialog.ShowAlphaChannel)
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
            QMessageBox.warning(self, "无法添加", str(exc))

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
            self.project = load_project(Path(filename))
            self.undo_stack.clear()
            self._bind_project()
            self.log(f"已打开工程：{filename}")
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", str(exc))

    def save_project(self):
        path = self.project.project_file
        if path is None:
            filename, _ = QFileDialog.getSaveFileName(self, "保存主题工程", f"{self.project.name}.hwtproj.json", "主题工程 (*.hwtproj.json)")
            if not filename:
                return
            path = Path(filename)
        try:
            save_project(self.project, path)
            self.refresh_views()
            self.log(f"工程已保存：{path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def export_hwt(self):
        self._identity_edited()
        start = Path(r"D:\HONOR Share\Honor Share")
        if not start.exists():
            start = Path.home()
        filename, _ = QFileDialog.getSaveFileName(self, "导出 HWT", str(start / default_export_name(self.project)), "荣耀主题 (*.hwt)")
        if not filename:
            return
        try:
            path, report = export_theme(self.project, self.catalog, Path(filename))
            self.last_export = path
            self.refresh_views()
            self.log(f"导出成功：{path}\nSHA-256：{report['sha256']}\n已写入 {report['applied_count']} 个覆盖目标。")
            QMessageBox.information(self, "导出成功", f"主题已生成：\n{path}\n\nSHA-256：\n{report['sha256']}")
        except Exception as exc:
            self.log(traceback.format_exc())
            QMessageBox.critical(self, "导出失败", str(exc))

    def send_phone(self):
        path = self.last_export
        if not path or not path.is_file():
            filename, _ = QFileDialog.getOpenFileName(self, "选择要发送的主题", r"D:\HONOR Share\Honor Share", "荣耀主题 (*.hwt)")
            if not filename:
                return
            path = Path(filename)
        self.progress = QProgressDialog("正在通过 phone-termux 上传并校验……", None, 0, 0, self)
        self.progress.setWindowTitle("发送到手机")
        self.progress.setCancelButton(None)
        self.progress.show()
        self.transfer_thread = QThread(self)
        worker = TransferWorker(path)
        worker.moveToThread(self.transfer_thread)
        self.transfer_thread.started.connect(worker.run)
        worker.finished.connect(self._transfer_finished)
        worker.failed.connect(self._transfer_failed)
        worker.finished.connect(self.transfer_thread.quit)
        worker.failed.connect(self.transfer_thread.quit)
        self.transfer_thread.finished.connect(worker.deleteLater)
        self.transfer_thread.start()
        self._transfer_worker = worker

    def _transfer_finished(self, result: dict):
        self.progress.close()
        self.log(f"发送成功：{result['remote']}\nSHA-256：{result['sha256']}")
        QMessageBox.information(self, "发送成功", f"手机路径：\n{result['remote']}\n\nSHA-256 校验一致。")

    def _transfer_failed(self, detail: str):
        self.progress.close()
        self.log(detail)
        QMessageBox.critical(self, "发送失败", detail.splitlines()[-1] if detail else "未知错误")

    def rescan_source(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择大雪源主题", str(default_source_theme()), "荣耀主题 (*.hwt)")
        if not filename:
            return
        try:
            catalog = scan_theme(Path(filename))
            cached = data_dir() / "catalog_daxue.json"
            save_catalog(catalog, cached)
            self.catalog = catalog
            self.resource_model.beginResetModel()
            self.resource_model.catalog = catalog
            self.resource_model.resources = catalog.resources
            self.resource_model.endResetModel()
            self.category_combo.clear()
            self.category_combo.addItems(["全部"] + sorted({x.category for x in catalog.resources}))
            self.log(f"扫描完成：{filename}，资源槽位 {len(catalog.resources)}。")
        except Exception as exc:
            QMessageBox.critical(self, "扫描失败", str(exc))

    def _confirm_discard(self) -> bool:
        if not self.project.dirty:
            return True
        answer = QMessageBox.question(self, "未保存修改", "当前工程尚未保存，是否放弃修改？")
        return answer == QMessageBox.Yes

    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

    def log(self, message: str):
        if hasattr(self, "log_text"):
            self.log_text.appendPlainText(message.rstrip() + "\n")


def apply_style(app: QApplication):
    app.setStyle("Fusion")
    app.setStyleSheet(
        """
        QWidget { font-family: 'Microsoft YaHei UI'; font-size: 10pt; }
        QPushButton { padding: 7px 12px; }
        QLineEdit, QComboBox { padding: 5px; }
        QGroupBox { font-weight: bold; margin-top: 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
        QTableView { gridline-color: #E0E0E0; alternate-background-color: #F7F9FB; }
        """
    )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("子木")
    apply_style(app)
    try:
        window = MainWindow()
    except Exception:
        QMessageBox.critical(None, "启动失败", traceback.format_exc())
        return 1
    window.show()
    return app.exec()
