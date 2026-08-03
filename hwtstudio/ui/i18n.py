"""Qt standard-dialog translations installed before any application window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication


_TRANSLATORS: list[QTranslator] = []


def install_qt_translations(app: QApplication) -> list[QTranslator]:
    """Load both Qt translation catalogs and keep them alive for app lifetime."""
    if _TRANSLATORS:
        return _TRANSLATORS
    translation_dir = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    for filename in ("qtbase_zh_CN.qm", "qt_zh_CN.qm"):
        translator = QTranslator(app)
        if translator.load(str(translation_dir / filename)):
            app.installTranslator(translator)
            _TRANSLATORS.append(translator)
    return _TRANSLATORS
