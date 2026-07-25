from __future__ import annotations

import copy
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QPixmap, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
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

from . import __version__
from .catalog import scan_theme
from .exporter import default_export_name, export_theme, preflight_export
from .imageops import render_image, render_placeholder
from .models import ResourceChange, ResourceSlot, ThemeCatalog, ThemeProject
from .paths import APP_NAME, default_source_theme
from .projectio import load_project, save_project
from .validation import validate_change_value
from .services.catalog_service import load_preferred_catalog, save_user_catalog
from .ui.commands import BulkChangeCommand, ChangeCommand
from .ui.dialogs import CustomResourceDialog, resolve_missing_assets
from .ui.phone_dialog import PhoneTransferDialog
from .ui.resource_models import ResourceFilterModel, ResourceTableModel
from .ui.workers import TransferWorker


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
        if self._catalog_load_warning:
            self.log(self._catalog_load_warning)

    def _load_initial_catalog(self) -> ThemeCatalog:
        catalog, self._catalog_load_warning = load_preferred_catalog()
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
        advanced = self.menuBar().addMenu("高级")
        ssh_action = QAction("通过 Termux/SSH 发送到手机", self)
        ssh_action.triggered.connect(self.send_phone_ssh)
        advanced.addAction(ssh_action)
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
        self.stats_label = stats_label
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
        self.catalog_warning_label = warning
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
        self.backgrounds_group = backgrounds
        self.backgrounds_layout = QHBoxLayout(backgrounds)
        self._refresh_background_shortcuts()
        layout.addWidget(backgrounds)
        layout.addStretch(1)
        return page

    def _refresh_background_shortcuts(self):
        while self.backgrounds_layout.count():
            item = self.backgrounds_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for slot in self.catalog.resources:
            if slot.synthetic and slot.id.startswith("__synthetic__::background"):
                button = QPushButton(slot.label)
                button.clicked.connect(lambda checked=False, sid=slot.id: self.select_slot(sid))
                self.backgrounds_layout.addWidget(button)

    def _refresh_catalog_summary(self):
        stats = self.catalog.stats
        self.stats_label.setText(
            f"应用/框架 ZIP：{stats.get('modules', 0)}　"
            f"颜色槽位：{stats.get('color_slots', 0)}　"
            f"图片槽位：{stats.get('image_slots', 0)}　"
            f"图标槽位：{stats.get('icon_slots', 0)}　"
            f"资源总槽位：{stats.get('resource_slots', len(self.catalog.resources))}"
        )
        self.catalog_warning_label.setText(
            f"源主题包含 {len(self.catalog.warnings)} 条兼容性记录；它们不会复制到空白主题。"
            "微信 8.0.76 主界面图片背景已标记为当前 HWT 方案不支持。"
        )

    def _replace_combo_items(self, combo: QComboBox, values: list[str]):
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        index = combo.findText(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def bind_catalog(self, catalog: ThemeCatalog):
        self.catalog = catalog
        self.resource_model.catalog = catalog
        self.resource_model.project = self.project
        resources = [*catalog.resources, *self.project.custom_resources]
        self.resource_model.set_resources(resources)
        self._replace_combo_items(self.category_combo, ["全部"] + sorted({x.category for x in resources}))
        self._replace_combo_items(self.type_combo, ["全部"] + sorted({x.resource_type for x in resources}))
        self._refresh_catalog_summary()
        self._refresh_background_shortcuts()
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
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((150, 130, 230, 90, 250, 220, 380)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)

        self.detail = self._build_detail_panel()
        splitter = QSplitter()
        splitter.addWidget(self.table)
        splitter.addWidget(self.detail)
        splitter.setSizes([1050, 380])
        root.addWidget(splitter, 1)

        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(200)
        self.filter_timer.timeout.connect(self._apply_filters)
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
        self.choose_image_button = choose
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
        self.processed_preview_button = preview_button
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
        resources = [*self.catalog.resources, *self.project.custom_resources]
        self.resource_model.set_resources(resources)
        self._replace_combo_items(self.category_combo, ["全部"] + sorted({x.category for x in resources}))
        self._replace_combo_items(self.type_combo, ["全部"] + sorted({x.resource_type for x in resources}))
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
            QMessageBox.critical(self, "预览失败", str(exc))

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
            loaded = load_project(Path(filename))
            slot_map = {slot.id: slot for slot in [*self.catalog.resources, *loaded.custom_resources]}
            if not resolve_missing_assets(self, loaded, slot_map):
                return
            self.project = loaded
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
            slot_map = {slot.id: slot for slot in [*self.catalog.resources, *self.project.custom_resources]}
            if not resolve_missing_assets(self, self.project, slot_map):
                return
            save_project(self.project, path)
            self.refresh_views()
            self.log(f"工程已保存：{path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

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
                self.tabs.setCurrentIndex(3)
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
            self.log(traceback.format_exc())
            QMessageBox.critical(self, "导出失败", str(exc))

    def send_phone(self):
        path = self.last_export
        if not path or not path.is_file():
            filename, _ = QFileDialog.getOpenFileName(self, "选择要发送的主题", r"D:\HONOR Share\Honor Share", "荣耀主题 (*.hwt)")
            if not filename:
                return
            path = Path(filename)
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
                self, "选择要发送的主题", r"D:\HONOR Share\Honor Share", "荣耀主题 (*.hwt)"
            )
            if not filename:
                return
            path = Path(filename)
        self._start_phone_transfer(path, use_ssh=True)

    def _start_phone_transfer(self, path: Path, *, device=None, pair_code: str = "", use_ssh: bool = False):
        if self.transfer_thread and self.transfer_thread.isRunning():
            QMessageBox.warning(self, "正在发送", "已有一个发送任务正在运行。")
            return
        initial = "正在通过 Termux/SSH 上传并校验……" if use_ssh else "正在准备发送到手机……"
        self.progress = QProgressDialog(initial, "取消", 0, 1000, self)
        self.progress.setWindowTitle("发送到手机")
        self.progress.setMinimumDuration(0)
        self.progress.setValue(0)
        self.progress.show()
        self.transfer_thread = QThread(self)
        worker = TransferWorker(path, device=device, pair_code=pair_code, use_ssh=use_ssh)
        worker.moveToThread(self.transfer_thread)
        self.transfer_thread.started.connect(worker.run)
        worker.finished.connect(self._transfer_finished)
        worker.failed.connect(self._transfer_failed)
        worker.progress.connect(self._transfer_progress)
        self.progress.canceled.connect(worker.cancel, Qt.DirectConnection)
        worker.finished.connect(self.transfer_thread.quit)
        worker.failed.connect(self.transfer_thread.quit)
        self.transfer_thread.finished.connect(worker.deleteLater)
        self.transfer_thread.finished.connect(self._transfer_thread_finished)
        self.transfer_thread.start()
        self._transfer_worker = worker

    def _transfer_progress(self, sent: int, total: int, stage: str):
        if not getattr(self, "progress", None):
            return
        self.progress.setLabelText(stage)
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(sent * 1000 / total)))
        else:
            self.progress.setRange(0, 0)

    def _transfer_thread_finished(self):
        self.transfer_thread = None
        self._transfer_worker = None

    def _transfer_finished(self, result: dict):
        self.progress.close()
        self.log(f"发送成功：{result['remote']}\nSHA-256：{result['sha256']}")
        if result.get("transport") == "apk":
            opened = "手机端会显示打开荣耀‘主题’的通知。"
            warning_text = ""
        else:
            opened = "已为你打开荣耀‘主题’应用。" if result.get("theme_app_opened") else "请手动打开荣耀‘主题’应用。"
            warnings = result.get("preflight", {}).get("warnings", [])
            warning_text = "\n" + "\n".join(warnings) if warnings else ""
        QMessageBox.information(
            self,
            "发送成功",
            f"手机路径：\n{result['remote']}\n\nSHA-256 校验一致。\n{opened}"
            f"{warning_text}\n请进入‘我的→下载→主题’查找；如页面已经打开，请返回后重新进入一次。",
        )

    def _transfer_failed(self, detail: str, code: str = "unexpected"):
        self.progress.close()
        self.log(detail)
        message = detail.splitlines()[-1] if detail else "未知错误"
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
