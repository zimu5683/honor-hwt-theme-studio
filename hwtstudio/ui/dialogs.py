from __future__ import annotations

import uuid
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
)

from ..models import ResourceSlot, ThemeProject
from ..projectio import missing_project_assets
from ..validation import validate_custom_slot
from .design_system import set_role


RESOURCE_TYPE_LABELS = {
    "color": "颜色",
    "bool": "开关",
    "integer": "整数",
    "dimen": "尺寸",
    "string": "文字",
    "image": "图片",
}


class CustomResourceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加高级覆盖资源")
        layout = QFormLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(16)
        self.module = QLineEdit("com.android.settings")
        self.kind = QComboBox()
        for raw, label in RESOURCE_TYPE_LABELS.items():
            self.kind.addItem(label, raw)
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
        set_role(buttons.button(QDialogButtonBox.Ok), "primary")
        set_role(buttons.button(QDialogButtonBox.Cancel), "ghost")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def create_slot(self) -> ResourceSlot:
        module = self.module.text().strip()
        kind = self.kind.currentData() or "color"
        name = self.name.text().strip()
        path = self.path.text().strip().replace("\\", "/")
        try:
            width = int(self.width.text()) if self.width.text().strip() else None
            height = int(self.height.text()) if self.height.text().strip() else None
        except ValueError as exc:
            raise ValueError("宽度和高度必须是整数") from exc
        suffix = Path(path).suffix.lower()
        actual = "JPEG" if suffix in {".jpg", ".jpeg"} else "WEBP" if suffix == ".webp" else "PNG" if kind == "image" else None
        slot = ResourceSlot(
            id=f"__custom__::{uuid.uuid4().hex}", module=module, container=path if kind != "image" else "",
            resource_type=kind, name=name, path=path, category="高级自定义", label=f"自定义：{name}",
            status="可能支持", risk="高", width=width, height=height, actual_format=actual,
            extension=suffix if kind == "image" else None,
        )
        validate_custom_slot(slot)
        return slot


def find_named_files(root: Path, filename: str) -> list[Path]:
    wanted = filename.casefold()
    matches = []
    for directory, _subdirs, files in os.walk(root, onerror=lambda _error: None):
        for name in files:
            if name.casefold() == wanted:
                matches.append(Path(directory) / name)
    return sorted(matches, key=str)


def _choose_match(parent, matches: list[Path]) -> Path | None:
    if len(matches) == 1:
        return matches[0]
    choices = [str(path) for path in matches]
    selected, ok = QInputDialog.getItem(parent, "选择匹配图片", "找到多个同名文件：", choices, 0, False)
    return Path(selected) if ok else None


def resolve_missing_assets(parent, project: ThemeProject, slot_map: dict[str, ResourceSlot]) -> bool:
    while True:
        missing = missing_project_assets(project)
        if not missing:
            return True
        slot_id, missing_path = missing[0]
        slot = slot_map.get(slot_id)
        label = slot.label if slot else slot_id
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("工程图片缺失")
        box.setText(f"{label} 使用的图片不存在")
        box.setInformativeText(str(missing_path))
        replace_button = box.addButton("更换新图片", QMessageBox.AcceptRole)
        placeholder_button = box.addButton("使用灰白图片", QMessageBox.ActionRole)
        search_button = box.addButton("搜索文件夹", QMessageBox.ActionRole)
        cancel_button = box.addButton("取消打开", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == cancel_button:
            return False
        change = project.changes[slot_id]
        if clicked == placeholder_button:
            change.source_kind = "placeholder"
            change.source_file = None
            project.dirty = True
        elif clicked == replace_button:
            filename, _ = QFileDialog.getOpenFileName(parent, "更换缺失图片", "", "图片 (*.png *.jpg *.jpeg *.webp)")
            if filename:
                change.source_kind = "file"
                change.source_file = filename
                project.dirty = True
        elif clicked == search_button:
            directory = QFileDialog.getExistingDirectory(parent, "选择要搜索的文件夹")
            if not directory:
                continue
            root = Path(directory)
            for candidate_id, candidate_path in missing_project_assets(project):
                matches = find_named_files(root, candidate_path.name)
                selected = _choose_match(parent, matches) if matches else None
                if selected:
                    candidate = project.changes[candidate_id]
                    candidate.source_kind = "file"
                    candidate.source_file = str(selected.resolve())
                    project.dirty = True
