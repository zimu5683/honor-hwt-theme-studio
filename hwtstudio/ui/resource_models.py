from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from ..models import ResourceChange, ResourceSlot, ThemeCatalog, ThemeProject


HEADERS = ["状态", "分类", "模块", "类型", "中文说明", "资源名", "路径", "当前设置"]


class ResourceTableModel(QAbstractTableModel):
    def __init__(self, catalog: ThemeCatalog, project: ThemeProject):
        super().__init__()
        self.catalog = catalog
        self.project = project
        self.resources: list[ResourceSlot] = []
        self._search_texts: list[str] = []
        self.set_resources(catalog.resources)

    def set_resources(self, resources: list[ResourceSlot]) -> None:
        self.beginResetModel()
        self.resources = list(resources)
        self._search_texts = [
            " ".join((slot.id, slot.module, slot.container, slot.name, slot.path, slot.category, slot.label)).lower()
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
        if change.source_kind == "placeholder":
            return "默认灰白图片"
        if change.source_file:
            return Path(change.source_file).name
        return "已启用"

    def slot(self, row: int) -> ResourceSlot:
        return self.resources[row]

    def search_text(self, row: int) -> str:
        return self._search_texts[row]

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
