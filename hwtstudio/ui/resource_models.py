from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from ..models import ResourceChange, ResourceSlot, ThemeCatalog, ThemeProject
from ..semantic import TYPE_LABELS, friendly_resource_label
from .design_system import Colors


HEADERS = ["状态", "应用/区域", "模块", "类型", "中文作用", "资源名", "路径", "当前设置"]


class ResourceTableModel(QAbstractTableModel):
    def __init__(self, catalog: ThemeCatalog, project: ThemeProject):
        super().__init__()
        self.catalog = catalog
        self.project = project
        self.resources: list[ResourceSlot] = []
        self.installed_packages: set[str] | None = None
        self._search_texts: list[str] = []
        self.set_resources(catalog.resources)

    def set_resources(self, resources: list[ResourceSlot]) -> None:
        self.beginResetModel()
        self.resources = list(resources)
        self._search_texts = [
            " ".join((slot.id, slot.module, slot.container, slot.name, slot.path, slot.category,
                       slot.label, friendly_resource_label(slot))).lower()
            for slot in self.resources
        ]
        self.endResetModel()

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
            applicability = self._applicability(slot)
            values = [
                "已修改" if change and change.enabled else applicability or "使用系统默认",
                slot.category,
                slot.module,
                TYPE_LABELS.get(slot.resource_type, slot.resource_type),
                friendly_resource_label(slot),
                slot.name,
                slot.path or "—",
                self._change_text(change),
            ]
            return values[index.column()]
        if role == Qt.ToolTipRole:
            applicability = self._applicability(slot)
            prefix = f"本机状态：{applicability}\n" if applicability else ""
            return f"{prefix}支持状态：{slot.status}\n风险：{slot.risk}\nID：{slot.id}"
        if role == Qt.ForegroundRole:
            if slot.status == "当前版本不支持":
                return QColor(Colors.ERROR)
            if change and change.enabled:
                return QColor(Colors.SUCCESS)
        if role == Qt.UserRole:
            return slot.id
        return None

    @staticmethod
    def _change_text(change: ResourceChange | None) -> str:
        if not change or not change.enabled:
            return "—"
        if change.value is not None:
            return change.value
        if change.source_kind == "placeholder":
            return "默认灰白图片"
        if change.source_file:
            return Path(change.source_file).name
        return "已启用"

    def slot(self, row: int) -> ResourceSlot:
        return self.resources[row]

    def search_text(self, row: int) -> str:
        return self._search_texts[row]

    def _applicability(self, slot: ResourceSlot) -> str:
        if self.installed_packages is None or not slot.module.startswith("com."):
            return ""
        if slot.module in self.installed_packages:
            return "本机适用"
        if slot.module.startswith("com.huawei."):
            return "兼容资源"
        return "本机未安装"

    def set_installed_packages(self, packages: set[str] | None) -> None:
        self.installed_packages = packages
        self.refresh()

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
        return not self.query or self.query.lower() in model.search_text(source_row)

    def set_filters(self, query: str, category: str, resource_type: str, modified_only: bool):
        modern_change = hasattr(self, "beginFilterChange") and hasattr(self, "endFilterChange")
        if modern_change:
            self.beginFilterChange()
        self.query = query
        self.category = category
        self.resource_type = resource_type
        self.modified_only = modified_only
        if modern_change:
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:
            self.invalidateFilter()
