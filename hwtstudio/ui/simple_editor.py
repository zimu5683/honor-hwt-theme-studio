from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
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
    QColorDialog,
)

from ..models import ResourceChange, ResourceSlot, ThemeProject
from ..semantic import SIMPLE_SETTINGS, SimpleSetting, setting_visible


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
    )


class SimpleSettingCard(QFrame):
    def __init__(
        self,
        setting: SimpleSetting,
        apply_callback: Callable[[SimpleSetting, ResourceChange], None],
        reset_callback: Callable[[SimpleSetting], None],
        parent=None,
    ):
        super().__init__(parent)
        self.setting = setting
        self.apply_callback = apply_callback
        self.reset_callback = reset_callback
        self.slots: list[ResourceSlot] = []
        self.project: ThemeProject | None = None
        self.setObjectName("simpleCard")
        self.setMinimumHeight(168 if setting.kind == "image" else 142)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)

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

        self.preview = QLabel()
        self.preview.setFixedHeight(52 if setting.kind == "image" else 24)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setObjectName("simplePreview")
        self.preview.setVisible(True)
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
        self.more_button = QPushButton("更多图片选项")
        self.more_button.setCheckable(True)
        self.more_button.toggled.connect(self.options.setVisible)
        self.more_button.setVisible(setting.kind == "image")
        buttons.addWidget(self.more_button)
        buttons.addStretch(1)
        self.reset_button = QPushButton("恢复默认")
        self.reset_button.clicked.connect(lambda: self.reset_callback(self.setting))
        buttons.addWidget(self.reset_button)
        self.apply_button = QPushButton("选择图片" if setting.kind == "image" else "设置颜色")
        self.apply_button.setObjectName("primaryAction")
        self.apply_button.clicked.connect(self._apply)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def bind(self, slots: list[ResourceSlot], project: ThemeProject) -> None:
        self.slots = slots
        self.project = project
        self.count_label.setText(f"影响 {len(slots)} 个兼容资源")
        self.setEnabled(bool(slots))
        changes = [project.changes.get(slot.id) for slot in slots]
        enabled = [change for change in changes if change and change.enabled]
        self.reset_button.setEnabled(bool(enabled))
        if not enabled:
            self.state.setText("使用系统默认")
            self.state.setProperty("mixed", False)
            self.preview.clear()
            self.preview.setStyleSheet("")
            self._refresh_style()
            return
        signatures = {_signature(change) for change in enabled}
        mixed = len(enabled) != len(slots) or len(signatures) != 1
        self.state.setProperty("mixed", mixed)
        if mixed:
            self.state.setText(f"含单独调整 · 已修改 {len(enabled)}/{len(slots)} 个")
            self.preview.clear()
            self.preview.setStyleSheet("")
        else:
            change = enabled[0]
            if change.value:
                self.state.setText(f"当前颜色：{change.value}")
                self.preview.setStyleSheet(f"background: {change.value}; border-radius: 6px;")
            else:
                self.state.setText("已选择自定义图片")
                self._set_image_preview(change)
                self._load_image_options(change)
        self._refresh_style()

    def _refresh_style(self):
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)

    def _set_image_preview(self, change: ResourceChange):
        self.preview.setStyleSheet("")
        if change.source_file and Path(change.source_file).is_file():
            pixmap = QPixmap(change.source_file)
            self.preview.setPixmap(pixmap.scaled(250, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        elif change.source_kind == "placeholder":
            self.preview.setText("灰白占位图片")
        else:
            self.preview.clear()

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
            )
        self.apply_callback(self.setting, change)


class SimpleEditor(QWidget):
    def __init__(self, apply_callback, reset_callback, parent=None):
        super().__init__(parent)
        self.cards: dict[str, SimpleSettingCard] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        for section in dict.fromkeys(item.section for item in SIMPLE_SETTINGS):
            title = QLabel(section)
            title.setObjectName("simpleSectionTitle")
            root.addWidget(title)
            container = QWidget()
            grid = QGridLayout(container)
            grid.setContentsMargins(0, 0, 0, 8)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(12)
            items = [item for item in SIMPLE_SETTINGS if item.section == section]
            for index, setting in enumerate(items):
                card = SimpleSettingCard(setting, apply_callback, reset_callback)
                self.cards[setting.id] = card
                grid.addWidget(card, index // 2, index % 2)
            root.addWidget(container)
        root.addStretch(1)

    def bind(
        self,
        resolved: dict[str, list[ResourceSlot]],
        project: ThemeProject,
        installed_packages: set[str] | None,
    ) -> None:
        for setting in SIMPLE_SETTINGS:
            card = self.cards[setting.id]
            visible = setting_visible(setting, installed_packages) and bool(resolved.get(setting.id))
            card.setVisible(visible)
            if visible:
                card.bind(resolved[setting.id], project)
