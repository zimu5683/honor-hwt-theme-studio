from __future__ import annotations

import copy

from PySide6.QtGui import QUndoCommand

from ..models import ResourceChange


class ChangeCommand(QUndoCommand):
    def __init__(self, window, slot_id: str, new_change: ResourceChange | None, text: str):
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
    def __init__(self, window, new_changes: dict[str, ResourceChange], text: str):
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
