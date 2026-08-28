from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..models import ResourceChange, ResourceSlot, ThemeCatalog, ThemeProject
from ..semantic import (
    SIMPLE_SETTINGS,
    SURFACE_TREATMENT_LABELS,
    SimpleSetting,
    build_surface_targets,
    setting_visible,
)
from .design_system import Colors, apply_type, set_role
from .simple_preview import ClickablePreview, PreviewDialog, PreviewRepository

_TINT_BY_SECTION = {
    "主题": "lavender",
    "全局外观": "cream",
    "系统界面": "mint",
    "桌面": "sky",
    "常用应用": "rose",
}

_DEFAULT_SURFACES = "layered"


def _signature(change: ResourceChange) -> tuple:
    return (
        change.enabled,
        change.value,
        change.source_file,
        change.source_kind,
        change.fit,
        round(change.focus_x, 4),
        round(change.focus_y, 4),
        change.enhance,
        round(change.enhance_strength, 4),
        change.surfaces,
    )


class SimpleSettingCard(QFrame):
    def __init__(
        self,
        setting: SimpleSetting,
        apply_callback: Callable[[SimpleSetting, ResourceChange], None],
        reset_callback: Callable[[SimpleSetting], None],
        preview_repository: PreviewRepository | None = None,
        surfaces_callback: Callable[[SimpleSetting, str], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setting = setting
        self.apply_callback = apply_callback
        self.reset_callback = reset_callback
        self.surfaces_callback = surfaces_callback
        self.slots: list[ResourceSlot] = []
        self.project: ThemeProject | None = None
        self.catalog: ThemeCatalog | None = None
        self.preview_repository = preview_repository
        self.setObjectName("simpleCard")
        self.setProperty("tintRole", _TINT_BY_SECTION.get(setting.section, "lavender"))
        self.setMinimumHeight(236 if setting.kind == "image" else 190)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        self.title = QLabel(setting.title)
        self.title.setObjectName("simpleCardTitle")
        header.addWidget(self.title)
        header.addStretch(1)
        self.count_label = QLabel()
        self.count_label.setObjectName("targetCount")
        header.addWidget(self.count_label)
        layout.addLayout(header)

        description = QLabel(setting.description)
        description.setWordWrap(True)
        description.setObjectName("simpleDescription")
        layout.addWidget(description)
        self.state = QLabel("使用系统默认")
        self.state.setObjectName("simpleState")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)

        self.surfaces_combo: QComboBox | None = None
        if setting.supports_surfaces:
            surfaces_row = QHBoxLayout()
            surfaces_label = QLabel("面板处理")
            surfaces_label.setObjectName("simpleDescription")
            surfaces_row.addWidget(surfaces_label)
            self.surfaces_combo = QComboBox()
            for value, label in SURFACE_TREATMENT_LABELS:
                self.surfaces_combo.addItem(label, value)
            self.surfaces_combo.setToolTip(
                "设置背景图片时自动同步处理标题栏、搜索框、卡片、列表、按键和分隔线等表面，避免白色话框挡住图片。"
            )
            self.surfaces_combo.currentIndexChanged.connect(self._surfaces_changed)
            surfaces_row.addWidget(self.surfaces_combo)
            surfaces_row.addStretch(1)
            layout.addLayout(surfaces_row)

        self.preview = ClickablePreview(self)
        self.preview.setFixedHeight(132)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setObjectName("simplePreview")
        self.preview.setText("尚未选择图片" if setting.kind == "image" else "使用默认颜色")
        self.preview.setToolTip("点击查看原始参考与当前设置预览")
        self.preview.clicked.connect(self._show_preview)
        layout.addWidget(self.preview)

        self.options = QWidget()
        option_layout = QGridLayout(self.options)
        option_layout.setContentsMargins(0, 4, 0, 4)
        self.fit = QComboBox()
        self.fit.addItem("裁剪填满", "cover")
        self.fit.addItem("完整放入", "contain")
        self.fit.addItem("拉伸", "stretch")
        self.enhance = QComboBox()
        self.enhance.addItem("不增强", "none")
        self.enhance.addItem("加亮", "light")
        self.enhance.addItem("变暗", "dark")
        self.strength = QSlider(Qt.Horizontal)
        self.strength.setRange(0, 80)
        self.focus_x = QSlider(Qt.Horizontal)
        self.focus_x.setRange(0, 100)
        self.focus_x.setValue(50)
        self.focus_y = QSlider(Qt.Horizontal)
        self.focus_y.setRange(0, 100)
        self.focus_y.setValue(50)
        option_layout.addWidget(QLabel("适配"), 0, 0)
        option_layout.addWidget(self.fit, 0, 1)
        option_layout.addWidget(QLabel("明暗"), 0, 2)
        option_layout.addWidget(self.enhance, 0, 3)
        option_layout.addWidget(QLabel("强度"), 1, 0)
        option_layout.addWidget(self.strength, 1, 1, 1, 3)
        option_layout.addWidget(QLabel("水平取景"), 2, 0)
        option_layout.addWidget(self.focus_x, 2, 1, 1, 3)
        option_layout.addWidget(QLabel("垂直取景"), 3, 0)
        option_layout.addWidget(self.focus_y, 3, 1, 1, 3)
        self.options.setVisible(False)
        layout.addWidget(self.options)

        buttons = QHBoxLayout()
        self.more_button = QPushButton("更多图片选项", self)
        set_role(self.more_button, "ghost")
        self.more_button.setCheckable(True)
        self.more_button.toggled.connect(self.options.setVisible)
        self.more_button.setVisible(setting.kind == "image")
        buttons.addWidget(self.more_button)
        buttons.addStretch(1)
        self.reset_button = QPushButton("恢复默认")
        set_role(self.reset_button, "ghost")
        self.reset_button.clicked.connect(lambda: self.reset_callback(self.setting))
        buttons.addWidget(self.reset_button)
        self.apply_button = QPushButton("选择图片" if setting.kind == "image" else "设置颜色")
        set_role(self.apply_button, "primary")
        self.apply_button.clicked.connect(self._apply)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def _image_target_count(self) -> int:
        if not self.slots:
            return 0
        total = sum(len(slot.targets) for slot in self.slots if slot.targets)
        return total or len(self.slots)

    def _surface_targets(self, treatment: str) -> list[dict]:
        if self.catalog is None or not self.slots or not self.setting.supports_surfaces:
            return []
        modules = {target["module"] for slot in self.slots for target in slot.targets}
        return build_surface_targets(self.setting.id, self.catalog, modules, treatment)

    def _refresh_count_label(self):
        if self.surfaces_combo is None or not self.setting.supports_surfaces:
            self.count_label.setText(f"影响 {len(self.slots)} 个兼容资源")
            return
        treatment = self.surfaces_combo.currentData() or _DEFAULT_SURFACES
        surface_count = len(self._surface_targets(treatment))
        total = self._image_target_count() + surface_count
        suffix = "（含面板透明化）" if surface_count else ""
        self.count_label.setText(f"影响 {total} 个兼容资源{suffix}")

    def bind(self, slots: list[ResourceSlot], project: ThemeProject, catalog: ThemeCatalog | None = None) -> None:
        self.slots = slots
        self.project = project
        if catalog is not None:
            self.catalog = catalog
        self.setEnabled(bool(slots))
        changes = [project.changes.get(slot.id) for slot in slots]
        enabled = [change for change in changes if change and change.enabled]
        self.setProperty("changed", bool(enabled))
        self.reset_button.setEnabled(bool(enabled))
        if self.surfaces_combo is not None:
            self.surfaces_combo.blockSignals(True)
            treatment = _DEFAULT_SURFACES
            if enabled and len({change.surfaces for change in enabled}) == 1:
                treatment = enabled[0].surfaces
            index = self.surfaces_combo.findData(treatment)
            self.surfaces_combo.setCurrentIndex(max(index, 0))
            self.surfaces_combo.blockSignals(False)
        self._refresh_count_label()
        if not enabled:
            self.state.setText("使用系统默认")
            self.state.setProperty("mixed", False)
            self.preview.setText("尚未选择图片" if self.setting.kind == "image" else "使用默认颜色")
            self._set_scene_preview()
            self._refresh_style()
            return
        signatures = {_signature(change) for change in enabled}
        mixed = len(enabled) != len(slots) or len(signatures) != 1
        self.state.setProperty("mixed", mixed)
        if mixed:
            self.state.setText(f"部分兼容资源单独调整 · 已修改 {len(enabled)}/{len(slots)} 个")
            self.preview.setText("存在单独调整")
            self._set_scene_preview()
        else:
            change = enabled[0]
            if change.value:
                self.state.setText(f"当前颜色：{change.value}")
                self._set_scene_preview(change)
            else:
                if change.source_kind == "placeholder":
                    self.state.setText("使用灰白占位图片")
                elif change.source_file and not Path(change.source_file).is_file():
                    self.state.setText("图片文件缺失，将显示缺失状态预览")
                else:
                    self.state.setText("已选择自定义图片")
                self._set_scene_preview(change)
                self._load_image_options(change)
        self._refresh_style()

    def _refresh_style(self):
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)
        self.style().unpolish(self)
        self.style().polish(self)

    def _set_image_preview(self, change: ResourceChange):
        self.preview.setStyleSheet("")
        if change.source_file and Path(change.source_file).is_file():
            pixmap = QPixmap(change.source_file)
            self.preview.setText("")
            self.preview.setPixmap(pixmap.scaled(420, 112, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        elif change.source_kind == "placeholder":
            self.preview.setText("灰白占位图片")
        else:
            self.preview.setText("尚未选择图片")

    def _set_scene_preview(self, change: ResourceChange | None = None):
        repository = self.preview_repository
        if repository is None or not repository.available or self.setting.preview is None:
            if change is None:
                self.preview.setPixmap(QPixmap())
            if self.setting.kind == "color" and change and change.value:
                self.preview.setText("")
                self.preview.setStyleSheet(
                    f"background: {change.value}; border: 1px solid {Colors.HAIRLINE}; border-radius: 8px;"
                )
            return
        image = (
            repository.current_image(
                self.setting.preview,
                change,
                surfaces=change.surfaces if change is not None and self.setting.supports_surfaces else None,
            )
            if change is not None
            else repository.highlighted_image(self.setting.preview)
        )
        if image is None:
            self.preview.setText("暂无真机参考图")
            return
        self.preview.setStyleSheet("")
        self.preview.setText("")
        self.preview.setPixmap(repository.to_pixmap(image, (420, 124)))

    def _show_preview(self):
        if self.preview_repository is None or not self.preview_repository.available or self.setting.preview is None:
            return
        change = None
        mixed = False
        surfaces = None
        if self.project:
            changes = [self.project.changes.get(slot.id) for slot in self.slots]
            enabled = [item for item in changes if item and item.enabled]
            signatures = {_signature(item) for item in enabled}
            mixed = bool(enabled) and (len(enabled) != len(self.slots) or len(signatures) != 1)
            if enabled and not mixed:
                change = enabled[0]
                if self.setting.supports_surfaces:
                    surfaces = change.surfaces
        dialog = PreviewDialog(
            self.preview_repository,
            self.setting.preview,
            change,
            self,
            mixed=mixed,
            surfaces=surfaces,
        )
        dialog.exec()

    def _load_image_options(self, change: ResourceChange):
        for combo, value in ((self.fit, change.fit), (self.enhance, change.enhance)):
            index = combo.findData(value)
            combo.setCurrentIndex(max(index, 0))
        self.strength.setValue(round(change.enhance_strength * 100))
        self.focus_x.setValue(round(change.focus_x * 100))
        self.focus_y.setValue(round(change.focus_y * 100))

    def _apply(self):
        if not self.slots:
            return
        if self.setting.kind == "color":
            initial = QColor("#FF808080")
            if self.project:
                existing = next((self.project.changes.get(slot.id) for slot in self.slots if self.project.changes.get(slot.id)), None)
                if existing and existing.value:
                    initial = QColor(existing.value)
            color = QColorDialog.getColor(initial, self, self.setting.title, QColorDialog.ShowAlphaChannel)
            if not color.isValid():
                return
            change = ResourceChange(slot_id="", value=color.name(QColor.HexArgb).upper())
        else:
            filename, _ = QFileDialog.getOpenFileName(self, self.setting.title, "", "图片 (*.png *.jpg *.jpeg *.webp)")
            if not filename:
                return
            change = ResourceChange(
                slot_id="",
                source_file=filename,
                source_kind="file",
                fit=self.fit.currentData(),
                enhance=self.enhance.currentData(),
                enhance_strength=self.strength.value() / 100,
                focus_x=self.focus_x.value() / 100,
                focus_y=self.focus_y.value() / 100,
                surfaces=self.surfaces_combo.currentData() if self.surfaces_combo is not None else _DEFAULT_SURFACES,
            )
        self.apply_callback(self.setting, change)

    def _surfaces_changed(self):
        if self.surfaces_combo is None:
            return
        self._refresh_count_label()
        if not self.project or not self.slots:
            return
        changed = [
            self.project.changes.get(slot.id)
            for slot in self.slots
            if self.project.changes.get(slot.id) and self.project.changes[slot.id].enabled
        ]
        if changed and self.surfaces_callback is not None:
            self.surfaces_callback(self.setting, self.surfaces_combo.currentData() or _DEFAULT_SURFACES)


class SimpleEditor(QWidget):
    def __init__(
        self,
        apply_callback,
        reset_callback,
        preview_repository: PreviewRepository | None = None,
        surfaces_callback: Callable[[SimpleSetting, str], None] | None = None,
        catalog: ThemeCatalog | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.cards: dict[str, SimpleSettingCard] = {}
        self._section_grids: list[tuple[QGridLayout, list[SimpleSettingCard]]] = []
        self._column_count = 0
        self._available_width = 0
        self.catalog = catalog
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(32)
        for section in dict.fromkeys(item.section for item in SIMPLE_SETTINGS):
            title = QLabel(section)
            title.setObjectName("simpleSectionTitle")
            apply_type(title, 24)
            root.addWidget(title)
            container = QWidget()
            grid = QGridLayout(container)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(16)
            items = [item for item in SIMPLE_SETTINGS if item.section == section]
            section_cards: list[SimpleSettingCard] = []
            for setting in items:
                card = SimpleSettingCard(
                    setting,
                    apply_callback,
                    reset_callback,
                    preview_repository,
                    surfaces_callback=surfaces_callback,
                )
                self.cards[setting.id] = card
                section_cards.append(card)
            root.addWidget(container)
            self._section_grids.append((grid, section_cards))
        self._relayout_cards()
        root.addStretch(1)

    @staticmethod
    def _columns_for_width(width: int) -> int:
        if width >= 1200:
            return 3
        if width >= 720:
            return 2
        return 1

    def _relayout_cards(self):
        columns = self._columns_for_width(self._available_width or self.width())
        if columns == self._column_count:
            return
        self._column_count = columns
        for grid, cards in self._section_grids:
            while grid.count():
                grid.takeAt(0)
            for column in range(columns):
                grid.setColumnStretch(column, 1)
            for index, card in enumerate(cards):
                grid.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_cards()

    def set_available_width(self, width: int):
        self._available_width = max(0, width)
        self._relayout_cards()

    def bind(
        self,
        resolved: dict[str, list[ResourceSlot]],
        project: ThemeProject,
        installed_packages: set[str] | None,
        catalog: ThemeCatalog | None = None,
    ) -> None:
        if catalog is not None:
            self.catalog = catalog
        for setting in SIMPLE_SETTINGS:
            card = self.cards[setting.id]
            visible = setting_visible(setting, installed_packages) and bool(resolved.get(setting.id))
            card.setVisible(visible)
            if visible:
                card.bind(resolved[setting.id], project, self.catalog)
