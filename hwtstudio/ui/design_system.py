from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

from ..paths import bundle_root


class Colors:
    PRIMARY = "#0F62FE"
    BLUE_60 = "#0043CE"
    BLUE_80 = "#002D9C"
    BLUE_HOVER = "#0050E6"
    INK = "#161616"
    INK_MUTED = "#525252"
    INK_SUBTLE = "#8C8C8C"
    CANVAS = "#FFFFFF"
    SURFACE_1 = "#F4F4F4"
    SURFACE_2 = "#E0E0E0"
    HAIRLINE = "#E0E0E0"
    HAIRLINE_STRONG = "#161616"
    SUCCESS = "#24A148"
    WARNING = "#F1C21B"
    ERROR = "#DA1E28"
    ERROR_HOVER = "#B81924"
    INFO = PRIMARY


FONT_FAMILY = "IBM Plex Sans SC"
FONT_FALLBACK = '"IBM Plex Sans SC", "Microsoft YaHei UI", "Segoe UI"'
FONT_FILES = (
    "ibm_plex_sans_sc_light.ttf",
    "ibm_plex_sans_sc_regular.ttf",
    "ibm_plex_sans_sc_semibold.ttf",
)


STYLE_SHEET = f"""
* {{
    color: {Colors.INK};
    outline: none;
}}
QWidget {{
    background-color: {Colors.CANVAS};
    color: {Colors.INK};
    font-family: {FONT_FALLBACK};
    font-size: 16px;
}}
QMainWindow, QDialog {{ background-color: {Colors.CANVAS}; }}
QMenuBar {{
    min-height: 48px;
    padding: 0 16px;
    background-color: {Colors.CANVAS};
    border-bottom: 1px solid {Colors.HAIRLINE};
}}
QMenuBar::item {{ padding: 12px 16px; border-radius: 0; }}
QMenuBar::item:selected {{ background-color: {Colors.SURFACE_1}; color: {Colors.PRIMARY}; }}
QMenu {{
    padding: 4px 0;
    background-color: {Colors.CANVAS};
    border: 1px solid {Colors.HAIRLINE};
}}
QMenu::item {{ padding: 12px 24px; min-height: 24px; }}
QMenu::item:selected {{ background-color: {Colors.SURFACE_1}; color: {Colors.PRIMARY}; }}
QToolBar {{
    min-height: 48px;
    max-height: 48px;
    spacing: 0;
    padding: 0 16px;
    background-color: {Colors.CANVAS};
    border: none;
    border-bottom: 1px solid {Colors.HAIRLINE};
}}
QToolButton {{
    min-height: 24px;
    padding: 12px 16px;
    border: 0;
    border-radius: 0;
    background-color: transparent;
    color: {Colors.INK};
    font-size: 14px;
}}
QToolButton:hover {{ background-color: {Colors.SURFACE_1}; color: {Colors.PRIMARY}; }}
QToolButton:pressed {{ background-color: {Colors.SURFACE_2}; color: {Colors.BLUE_80}; }}
QToolBar::separator {{ width: 1px; margin: 8px 12px; background: {Colors.HAIRLINE}; }}
QPushButton {{
    min-height: 24px;
    padding: 12px 16px;
    border: 1px solid {Colors.PRIMARY};
    border-radius: 0;
    background-color: {Colors.CANVAS};
    color: {Colors.PRIMARY};
    font-size: 14px;
    font-weight: 400;
}}
QPushButton:hover {{ background-color: {Colors.SURFACE_1}; color: {Colors.BLUE_60}; }}
QPushButton:pressed {{ background-color: {Colors.SURFACE_2}; color: {Colors.BLUE_80}; }}
QPushButton[uiRole="primary"] {{ background-color: {Colors.PRIMARY}; color: {Colors.CANVAS}; border-color: {Colors.PRIMARY}; }}
QPushButton[uiRole="primary"]:hover {{ background-color: {Colors.BLUE_HOVER}; color: {Colors.CANVAS}; }}
QPushButton[uiRole="primary"]:pressed {{ background-color: {Colors.BLUE_80}; color: {Colors.CANVAS}; }}
QPushButton[uiRole="secondary"] {{ background-color: {Colors.INK}; color: {Colors.CANVAS}; border-color: {Colors.INK}; }}
QPushButton[uiRole="secondary"]:hover {{ background-color: {Colors.INK_MUTED}; color: {Colors.CANVAS}; }}
QPushButton[uiRole="ghost"] {{ background-color: transparent; color: {Colors.PRIMARY}; border-color: transparent; }}
QPushButton[uiRole="ghost"]:hover {{ background-color: {Colors.SURFACE_1}; color: {Colors.BLUE_60}; }}
QPushButton[uiRole="danger"] {{ background-color: {Colors.ERROR}; color: {Colors.CANVAS}; border-color: {Colors.ERROR}; }}
QPushButton[uiRole="danger"]:hover {{ background-color: {Colors.ERROR_HOVER}; color: {Colors.CANVAS}; }}
QPushButton:disabled, QToolButton:disabled {{ background-color: {Colors.SURFACE_2}; color: {Colors.INK_SUBTLE}; border-color: {Colors.SURFACE_2}; }}
QLineEdit, QComboBox, QPlainTextEdit {{
    min-height: 24px;
    padding: 11px 16px;
    border: none;
    border-bottom: 1px solid {Colors.INK_SUBTLE};
    border-radius: 0;
    background-color: {Colors.SURFACE_1};
    color: {Colors.INK};
    selection-background-color: {Colors.PRIMARY};
    selection-color: {Colors.CANVAS};
}}
QPlainTextEdit {{ padding: 16px; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-bottom: 2px solid {Colors.PRIMARY}; }}
QLineEdit[validationState="error"], QComboBox[validationState="error"] {{ border-bottom: 2px solid {Colors.ERROR}; }}
QLineEdit:read-only {{ background-color: {Colors.SURFACE_2}; color: {Colors.INK_MUTED}; }}
QComboBox::drop-down {{ width: 40px; border: none; }}
QComboBox QAbstractItemView {{
    background-color: {Colors.CANVAS};
    border: 1px solid {Colors.HAIRLINE};
    selection-background-color: {Colors.SURFACE_1};
    selection-color: {Colors.INK};
}}
QCheckBox {{ min-height: 48px; spacing: 8px; color: {Colors.INK_MUTED}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {Colors.INK_MUTED}; border-radius: 0; background: {Colors.CANVAS}; }}
QCheckBox::indicator:checked {{ background: {Colors.PRIMARY}; border-color: {Colors.PRIMARY}; }}
QGroupBox {{
    margin-top: 24px;
    padding: 24px;
    border: 1px solid {Colors.HAIRLINE};
    border-radius: 0;
    background-color: {Colors.CANVAS};
    font-size: 20px;
    font-weight: 400;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 16px; padding: 0 8px; background: {Colors.CANVAS}; }}
QTabWidget::pane {{ border: none; background-color: {Colors.CANVAS}; }}
QTabBar {{ background-color: {Colors.CANVAS}; }}
QTabBar::tab {{
    min-height: 48px;
    padding: 16px 20px;
    border: none;
    border-bottom: 1px solid {Colors.HAIRLINE};
    border-radius: 0;
    background-color: {Colors.CANVAS};
    color: {Colors.INK_MUTED};
    font-size: 14px;
}}
QTabBar::tab:hover {{ color: {Colors.PRIMARY}; background-color: {Colors.SURFACE_1}; }}
QTabBar::tab:selected {{ color: {Colors.INK}; font-weight: 600; border-bottom: 2px solid {Colors.PRIMARY}; }}
QFrame#simpleCard, QFrame#detailPanel {{
    background-color: {Colors.CANVAS};
    border: 1px solid {Colors.HAIRLINE};
    border-radius: 0;
}}
QFrame#simpleCard[changed="true"] {{ background-color: {Colors.SURFACE_1}; border-left: 2px solid {Colors.PRIMARY}; }}
QLabel#pageTitle {{ font-size: 32px; font-weight: 400; color: {Colors.INK}; }}
QLabel#sectionTitle {{ font-size: 24px; font-weight: 400; color: {Colors.INK}; }}
QLabel#simpleCardTitle {{ font-size: 20px; font-weight: 400; color: {Colors.INK}; }}
QLabel#simpleDescription {{ font-size: 14px; color: {Colors.INK_MUTED}; }}
QLabel#targetCount {{ font-size: 12px; color: {Colors.INK_MUTED}; background: transparent; padding: 4px 0; }}
QLabel#simpleState {{ font-size: 14px; color: {Colors.INK_MUTED}; padding: 4px 0; }}
QLabel#simpleState[state="success"] {{ color: {Colors.SUCCESS}; }}
QLabel#simpleState[state="warning"] {{ color: {Colors.WARNING}; }}
QLabel#simpleState[state="error"] {{ color: {Colors.ERROR}; }}
QLabel#phoneStatus {{ font-size: 14px; padding: 12px 16px; color: {Colors.INK_MUTED}; background: {Colors.SURFACE_1}; border-left: 2px solid {Colors.INFO}; }}
QLabel#phoneStatus[state="success"] {{ color: {Colors.SUCCESS}; border-left-color: {Colors.SUCCESS}; }}
QLabel#phoneStatus[state="error"] {{ color: {Colors.ERROR}; border-left-color: {Colors.ERROR}; }}
QLabel#phoneStatus[state="warning"] {{ color: {Colors.INK_MUTED}; border-left-color: {Colors.WARNING}; }}
QLabel#simplePreview {{ background: {Colors.SURFACE_1}; border: 1px solid {Colors.HAIRLINE}; border-radius: 0; }}
QLabel#infoCallout {{ padding: 16px; color: {Colors.INK_MUTED}; background: {Colors.SURFACE_1}; border-left: 2px solid {Colors.INFO}; }}
QLabel#infoCallout[state="success"] {{ color: {Colors.SUCCESS}; border-left-color: {Colors.SUCCESS}; }}
QLabel#infoCallout[state="warning"] {{ color: {Colors.INK_MUTED}; border-left-color: {Colors.WARNING}; }}
QLabel#infoCallout[state="error"] {{ color: {Colors.ERROR}; border-left-color: {Colors.ERROR}; }}
QLabel#warningCallout {{ padding: 16px; color: {Colors.INK}; background: {Colors.SURFACE_1}; border-left: 2px solid {Colors.WARNING}; }}
QLabel#errorCallout {{ padding: 16px; color: {Colors.ERROR}; background: {Colors.SURFACE_1}; border-left: 2px solid {Colors.ERROR}; }}
QLabel#detailTitle {{ font-size: 24px; font-weight: 400; }}
QLabel#detailInfo {{ font-size: 14px; color: {Colors.INK_MUTED}; }}
QLabel#previewPanel {{ background: {Colors.SURFACE_1}; border: 1px solid {Colors.HAIRLINE}; border-radius: 0; }}
QTableView {{
    border: 1px solid {Colors.HAIRLINE};
    border-radius: 0;
    background-color: {Colors.CANVAS};
    alternate-background-color: {Colors.SURFACE_1};
    gridline-color: {Colors.HAIRLINE};
    selection-background-color: {Colors.SURFACE_2};
    selection-color: {Colors.INK};
}}
QHeaderView::section {{ padding: 12px 16px; border: none; border-bottom: 1px solid {Colors.HAIRLINE_STRONG}; background: {Colors.SURFACE_1}; color: {Colors.INK}; font-size: 14px; font-weight: 600; }}
QSplitter::handle {{ background: {Colors.SURFACE_2}; }}
QScrollArea {{ background: transparent; border: none; }}
QSlider::groove:horizontal {{ height: 2px; background: {Colors.SURFACE_2}; }}
QSlider::sub-page:horizontal {{ background: {Colors.PRIMARY}; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -5px 0; background: {Colors.PRIMARY}; border: none; border-radius: 0; }}
QProgressBar {{ min-height: 24px; border: 1px solid {Colors.HAIRLINE}; border-radius: 0; background: {Colors.SURFACE_1}; text-align: center; }}
QProgressBar::chunk {{ background: {Colors.PRIMARY}; border-radius: 0; }}
QStatusBar {{ min-height: 32px; padding: 0 16px; background: {Colors.SURFACE_1}; border-top: 1px solid {Colors.HAIRLINE}; color: {Colors.INK_MUTED}; font-size: 12px; }}
QDialogButtonBox QPushButton {{ min-width: 96px; }}
QScrollBar:vertical {{ width: 12px; background: {Colors.SURFACE_1}; margin: 0; }}
QScrollBar::handle:vertical {{ min-height: 48px; background: {Colors.INK_SUBTLE}; border-radius: 0; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


_FONT_CACHE: str | None = None


def load_design_fonts() -> str:
    global _FONT_CACHE
    if _FONT_CACHE:
        return _FONT_CACHE
    font_dir = bundle_root() / "assets" / "design-res" / "font"
    for filename in FONT_FILES:
        path = font_dir / filename
        if path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    _FONT_CACHE = families[0]
    return _FONT_CACHE or FONT_FAMILY


def apply_design_system(app: QApplication) -> None:
    family = load_design_fonts()
    app.setStyle("Fusion")
    font = QFont(family, 14)
    font.setWeight(QFont.Weight.Normal)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.16)
    app.setFont(font)
    app.setStyleSheet(STYLE_SHEET)


def set_role(widget: QWidget, role: str) -> None:
    widget.setProperty("uiRole", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_state(widget: QWidget, state: str) -> None:
    widget.setProperty("state", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def apply_type(widget: QWidget, size: int, weight: QFont.Weight = QFont.Weight.Normal, spacing: float = 0.16) -> None:
    font = QFont(load_design_fonts())
    font.setPixelSize(size)
    font.setWeight(weight)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    widget.setFont(font)
