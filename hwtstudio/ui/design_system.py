from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

from ..paths import bundle_root


class Colors:
    """Studio Soft Light tokens adapted from the local Notion design analysis."""

    PRIMARY = "#5645D4"
    PRIMARY_HOVER = "#6252D9"
    PRIMARY_PRESSED = "#4534B3"
    PRIMARY_DEEP = "#3A2A99"
    LINK = "#0075DE"
    LINK_PRESSED = "#005BAB"
    INK_DEEP = "#000000"
    INK = "#1A1A1A"
    INK_MUTED = "#5D5B54"
    INK_SUBTLE = "#787671"
    INK_STONE = "#A4A097"
    MUTED = "#BBB8B1"
    CANVAS = "#F6F5F4"
    SURFACE_1 = "#FFFFFF"
    SURFACE_2 = "#FAFAF9"
    SURFACE_3 = "#F0EEEC"
    HAIRLINE = "#E5E3DF"
    HAIRLINE_SOFT = "#EDE9E4"
    HAIRLINE_STRONG = "#C8C4BE"
    SUCCESS = "#1AAE39"
    WARNING = "#DD5B00"
    ERROR = "#E03131"
    ERROR_HOVER = "#B92323"
    INFO = PRIMARY
    TINT_LAVENDER = "#E6E0F5"
    TINT_CREAM = "#F8F5E8"
    TINT_MINT = "#D9F3E1"
    TINT_SKY = "#DCEFFA"
    TINT_PEACH = "#FFE8D4"
    TINT_ROSE = "#FDE0EC"

    # Compatibility aliases retained for existing resource-model code.
    BLUE_60 = PRIMARY_PRESSED
    BLUE_80 = PRIMARY_DEEP
    BLUE_HOVER = PRIMARY_HOVER


# IBM Plex Sans SC remains the reliable CJK desktop fallback; Inter is bundled
# for Android/Latin glyphs where the platform can select it automatically.
FONT_FAMILY = "IBM Plex Sans SC"
FONT_FALLBACK = '"IBM Plex Sans SC", "Inter", "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI", sans-serif'
FONT_FILES = ("ibm_plex_sans_sc_regular.ttf", "ibm_plex_sans_sc_semibold.ttf")


STYLE_SHEET = f"""
* {{
    color: {Colors.INK};
    outline: none;
}}
QWidget {{
    color: {Colors.INK};
    font-family: {FONT_FALLBACK};
    font-size: 14px;
}}
QMainWindow, QDialog {{ background-color: {Colors.CANVAS}; }}
QFrame#windowTitleBar {{
    min-height: 44px;
    max-height: 44px;
    background-color: {Colors.CANVAS};
    border-bottom: 1px solid {Colors.HAIRLINE};
}}
QLabel#windowTitle {{ font-size: 13px; font-weight: 500; color: {Colors.INK_MUTED}; }}
QLabel#windowLogo {{ color: {Colors.PRIMARY}; font-size: 18px; font-weight: 600; }}
QToolButton#windowControl {{
    min-width: 44px;
    max-width: 44px;
    min-height: 44px;
    max-height: 44px;
    border: none;
    border-radius: 0;
    background: transparent;
    color: {Colors.INK_MUTED};
}}
QToolButton#windowControl:hover {{ background: {Colors.SURFACE_3}; color: {Colors.INK}; }}
QToolButton#windowControl[windowRole="close"]:hover {{ background: {Colors.ERROR}; color: {Colors.SURFACE_1}; }}
QToolBar {{
    min-height: 56px;
    max-height: 56px;
    spacing: 4px;
    padding: 8px 20px;
    background-color: {Colors.SURFACE_1};
    border: none;
    border-bottom: 1px solid {Colors.HAIRLINE};
}}
QToolButton {{
    min-height: 38px;
    padding: 8px 12px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    color: {Colors.INK_MUTED};
    font-size: 13px;
}}
QToolButton:hover {{ background: {Colors.SURFACE_3}; color: {Colors.INK}; }}
QToolButton:pressed {{ background: {Colors.HAIRLINE}; color: {Colors.INK_DEEP}; }}
QToolButton[uiRole="primary"] {{ background: {Colors.PRIMARY}; color: {Colors.SURFACE_1}; }}
QToolButton[uiRole="secondary"] {{ background: {Colors.SURFACE_1}; border-color: {Colors.HAIRLINE_STRONG}; color: {Colors.INK}; }}
QToolBar::separator {{ width: 1px; margin: 10px 8px; background: {Colors.HAIRLINE}; }}
QMenuBar {{ min-height: 0; max-height: 0; padding: 0; border: none; background: transparent; }}
QMenuBar::item {{ padding: 0; }}
QMenu {{
    padding: 6px;
    background: {Colors.SURFACE_1};
    border: 1px solid {Colors.HAIRLINE};
    border-radius: 8px;
}}
QMenu::item {{ min-height: 32px; padding: 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {Colors.TINT_LAVENDER}; color: {Colors.PRIMARY_DEEP}; }}
QPushButton {{
    min-height: 40px;
    padding: 10px 18px;
    border: 1px solid {Colors.HAIRLINE_STRONG};
    border-radius: 8px;
    background-color: {Colors.SURFACE_1};
    color: {Colors.INK};
    font-size: 14px;
    font-weight: 500;
}}
QPushButton:hover {{ background-color: {Colors.SURFACE_3}; }}
QPushButton:pressed {{ background-color: {Colors.HAIRLINE}; }}
QPushButton[uiRole="primary"] {{ background: {Colors.PRIMARY}; color: {Colors.SURFACE_1}; border-color: {Colors.PRIMARY}; }}
QPushButton[uiRole="primary"]:hover {{ background: {Colors.PRIMARY_HOVER}; border-color: {Colors.PRIMARY_HOVER}; }}
QPushButton[uiRole="primary"]:pressed {{ background: {Colors.PRIMARY_PRESSED}; border-color: {Colors.PRIMARY_PRESSED}; }}
QPushButton[uiRole="secondary"] {{ background: {Colors.INK_DEEP}; color: {Colors.SURFACE_1}; border-color: {Colors.INK_DEEP}; }}
QPushButton[uiRole="secondary"]:hover {{ background: {Colors.INK_MUTED}; border-color: {Colors.INK_MUTED}; }}
QPushButton[uiRole="tertiary"] {{ background: {Colors.SURFACE_1}; color: {Colors.PRIMARY_DEEP}; border-color: {Colors.PRIMARY}; }}
QPushButton[uiRole="tertiary"]:hover {{ background: {Colors.TINT_LAVENDER}; }}
QPushButton[uiRole="ghost"] {{ background: transparent; color: {Colors.PRIMARY_DEEP}; border-color: transparent; }}
QPushButton[uiRole="ghost"]:hover {{ background: {Colors.TINT_LAVENDER}; }}
QPushButton[uiRole="danger"] {{ background: {Colors.ERROR}; color: {Colors.SURFACE_1}; border-color: {Colors.ERROR}; }}
QPushButton[uiRole="danger"]:hover {{ background: {Colors.ERROR_HOVER}; border-color: {Colors.ERROR_HOVER}; }}
QPushButton:disabled, QToolButton:disabled {{ background: {Colors.SURFACE_3}; color: {Colors.MUTED}; border-color: {Colors.SURFACE_3}; }}
QLineEdit, QComboBox, QPlainTextEdit {{
    min-height: 42px;
    padding: 8px 12px;
    border: 1px solid {Colors.HAIRLINE_STRONG};
    border-radius: 8px;
    background-color: {Colors.SURFACE_1};
    color: {Colors.INK};
    selection-background-color: {Colors.PRIMARY};
    selection-color: {Colors.SURFACE_1};
}}
QPlainTextEdit {{ padding: 12px; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 2px solid {Colors.PRIMARY}; padding: 7px 11px; }}
QLineEdit[validationState="error"], QComboBox[validationState="error"] {{ border: 2px solid {Colors.ERROR}; padding: 7px 11px; }}
QLineEdit:read-only {{ background: {Colors.SURFACE_3}; color: {Colors.INK_MUTED}; }}
QComboBox::drop-down {{ width: 34px; border: none; }}
QComboBox QAbstractItemView {{
    padding: 4px;
    background: {Colors.SURFACE_1};
    border: 1px solid {Colors.HAIRLINE};
    selection-background-color: {Colors.TINT_LAVENDER};
    selection-color: {Colors.INK};
}}
QCheckBox {{ min-height: 40px; spacing: 8px; color: {Colors.INK_MUTED}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {Colors.HAIRLINE_STRONG}; border-radius: 4px; background: {Colors.SURFACE_1}; }}
QCheckBox::indicator:checked {{ background: {Colors.PRIMARY}; border-color: {Colors.PRIMARY}; }}
QGroupBox {{
    margin-top: 10px;
    padding: 18px;
    border: 1px solid {Colors.HAIRLINE};
    border-radius: 12px;
    background: {Colors.SURFACE_1};
    font-size: 16px;
    font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 16px; padding: 0 8px; background: {Colors.SURFACE_1}; }}
QFrame#identityPanel {{ background: {Colors.SURFACE_1}; border: 1px solid {Colors.HAIRLINE}; border-radius: 12px; }}
QWidget#simplePage {{ background: {Colors.CANVAS}; }}
QTabWidget::pane {{ border: none; background: {Colors.CANVAS}; }}
QTabBar {{ background: {Colors.CANVAS}; }}
QTabBar::tab {{
    min-height: 34px;
    padding: 8px 14px;
    margin: 10px 3px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: {Colors.INK_MUTED};
    font-size: 13px;
}}
QTabBar::tab:hover {{ background: {Colors.SURFACE_3}; color: {Colors.INK}; }}
QTabBar::tab:selected {{ background: {Colors.INK}; color: {Colors.SURFACE_1}; font-weight: 600; }}
QFrame#simpleCard, QFrame#detailPanel {{
    background: {Colors.SURFACE_1};
    border: 1px solid {Colors.HAIRLINE};
    border-radius: 12px;
}}
QFrame#simpleCard[tintRole="lavender"] {{ border-top: 3px solid {Colors.PRIMARY}; }}
QFrame#simpleCard[tintRole="cream"] {{ border-top: 3px solid {Colors.WARNING}; }}
QFrame#simpleCard[tintRole="mint"] {{ border-top: 3px solid {Colors.SUCCESS}; }}
QFrame#simpleCard[tintRole="sky"] {{ border-top: 3px solid {Colors.LINK}; }}
QFrame#simpleCard[tintRole="peach"] {{ border-top: 3px solid {Colors.ERROR}; }}
QFrame#simpleCard[tintRole="rose"] {{ border-top: 3px solid {Colors.PRIMARY_HOVER}; }}
QFrame#simpleCard[changed="true"] {{ background: {Colors.TINT_LAVENDER}; border-color: {Colors.PRIMARY}; }}
QLabel#pageTitle {{ font-size: 30px; font-weight: 600; color: {Colors.INK}; }}
QLabel#sectionTitle, QLabel#simpleSectionTitle {{ font-size: 22px; font-weight: 600; color: {Colors.INK}; }}
QLabel#simpleCardTitle {{ font-size: 18px; font-weight: 600; color: {Colors.INK}; }}
QLabel#simpleDescription, QLabel#detailInfo {{ font-size: 14px; color: {Colors.INK_MUTED}; }}
QLabel#targetCount {{ font-size: 12px; color: {Colors.INK_SUBTLE}; background: transparent; padding: 4px 0; }}
QLabel#simpleState {{ font-size: 13px; color: {Colors.INK_MUTED}; padding: 4px 0; }}
QLabel#simpleState[state="success"] {{ color: {Colors.SUCCESS}; }}
QLabel#simpleState[state="warning"] {{ color: {Colors.WARNING}; }}
QLabel#simpleState[state="error"] {{ color: {Colors.ERROR}; }}
QLabel#phoneStatus {{ min-height: 32px; padding: 6px 12px; color: {Colors.PRIMARY_DEEP}; background: {Colors.TINT_LAVENDER}; border-radius: 9999px; }}
QLabel#phoneStatus[state="success"] {{ color: {Colors.SUCCESS}; background: {Colors.TINT_MINT}; }}
QLabel#phoneStatus[state="error"] {{ color: {Colors.ERROR}; background: {Colors.TINT_ROSE}; }}
QLabel#phoneStatus[state="warning"] {{ color: {Colors.WARNING}; background: {Colors.TINT_CREAM}; }}
QLabel#simplePreview {{ background: {Colors.SURFACE_2}; border: 1px dashed {Colors.HAIRLINE_STRONG}; border-radius: 8px; color: {Colors.INK_SUBTLE}; }}
QLabel#infoCallout {{ padding: 12px 14px; color: {Colors.INK_MUTED}; background: {Colors.TINT_LAVENDER}; border-radius: 8px; }}
QLabel#infoCallout[state="success"] {{ color: {Colors.SUCCESS}; background: {Colors.TINT_MINT}; }}
QLabel#infoCallout[state="warning"] {{ color: {Colors.WARNING}; background: {Colors.TINT_CREAM}; }}
QLabel#infoCallout[state="error"] {{ color: {Colors.ERROR}; background: {Colors.TINT_ROSE}; }}
QLabel#warningCallout {{ padding: 12px 14px; color: {Colors.WARNING}; background: {Colors.TINT_CREAM}; border-radius: 8px; }}
QLabel#errorCallout {{ padding: 12px 14px; color: {Colors.ERROR}; background: {Colors.TINT_ROSE}; border-radius: 8px; }}
QLabel#detailTitle {{ font-size: 22px; font-weight: 600; }}
QLabel#previewPanel {{ background: {Colors.SURFACE_2}; border: 1px solid {Colors.HAIRLINE}; border-radius: 12px; }}
QTableView {{
    border: 1px solid {Colors.HAIRLINE};
    border-radius: 12px;
    background: {Colors.SURFACE_1};
    alternate-background-color: {Colors.SURFACE_2};
    gridline-color: {Colors.HAIRLINE_SOFT};
    selection-background-color: {Colors.TINT_LAVENDER};
    selection-color: {Colors.INK};
}}
QHeaderView::section {{ padding: 10px 12px; border: none; border-bottom: 1px solid {Colors.HAIRLINE_STRONG}; background: {Colors.SURFACE_2}; color: {Colors.INK_MUTED}; font-size: 12px; font-weight: 600; }}
QSplitter::handle {{ background: {Colors.HAIRLINE_SOFT}; }}
QScrollArea {{ background: transparent; border: none; }}
QSlider::groove:horizontal {{ height: 4px; background: {Colors.SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {Colors.PRIMARY}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; background: {Colors.PRIMARY}; border: 2px solid {Colors.SURFACE_1}; border-radius: 7px; }}
QProgressBar {{ min-height: 20px; border: 1px solid {Colors.HAIRLINE}; border-radius: 8px; background: {Colors.SURFACE_2}; text-align: center; }}
QProgressBar::chunk {{ background: {Colors.PRIMARY}; border-radius: 7px; }}
QStatusBar {{ min-height: 28px; padding: 0 16px; background: {Colors.SURFACE_2}; border-top: 1px solid {Colors.HAIRLINE}; color: {Colors.INK_SUBTLE}; font-size: 12px; }}
QDialogButtonBox QPushButton {{ min-width: 96px; }}
QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 48px; background: {Colors.INK_STONE}; border-radius: 5px; }}
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
                    break
    return _FONT_CACHE or FONT_FAMILY


def apply_design_system(app: QApplication) -> None:
    family = load_design_fonts()
    app.setStyle("Fusion")
    font = QFont(family, 14)
    font.setWeight(QFont.Weight.Normal)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
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


def apply_type(widget: QWidget, size: int, weight: QFont.Weight = QFont.Weight.Normal, spacing: float = 0.0) -> None:
    font = QFont(load_design_fonts())
    font.setPixelSize(size)
    font.setWeight(weight)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    widget.setFont(font)
