from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QWidget
import qtawesome as qta

from ..paths import APP_NAME
from .design_system import Colors


class WindowTitleBar(QFrame):
    """Small client-side title bar for the frameless desktop window."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.window = window
        self.setObjectName("windowTitleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._dragging = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 0, 0)
        layout.setSpacing(8)

        logo = QLabel("❄")
        logo.setObjectName("windowLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedWidth(22)
        layout.addWidget(logo)

        self.title = QLabel()
        self.title.setObjectName("windowTitle")
        self.title.setText(f"{APP_NAME}")
        layout.addWidget(self.title, 1)

        self.minimize_button = self._button("fa5s.window-minimize", "最小化", self.window.showMinimized)
        self.maximize_button = self._button("fa5s.window-maximize", "最大化", self.toggle_maximized)
        self.close_button = self._button("fa5s.times", "关闭", self.window.close, role="close")
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)
        self.window.windowTitleChanged.connect(self._set_title)

    def _button(self, icon_name: str, tooltip: str, callback, role: str | None = None) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("windowControl")
        button.setIcon(qta.icon(icon_name, color=Colors.INK_MUTED))
        button.setToolTip(tooltip)
        if role:
            button.setProperty("windowRole", role)
        button.clicked.connect(callback)
        return button

    def _set_title(self, title: str) -> None:
        self.title.setText(title)

    def toggle_maximized(self) -> None:
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()
        self.update_controls()

    def update_controls(self) -> None:
        maximized = self.window.isMaximized()
        icon = "fa5s.window-restore" if maximized else "fa5s.window-maximize"
        self.maximize_button.setIcon(qta.icon(icon, color=Colors.INK_MUTED))
        self.maximize_button.setToolTip("还原" if maximized else "最大化")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            handle = self.window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._dragging = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.update_controls()
