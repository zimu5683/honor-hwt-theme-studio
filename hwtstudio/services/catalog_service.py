from __future__ import annotations

from ..catalog import load_catalog
from ..models import ThemeCatalog
from ..paths import bundled_catalog, data_dir, default_source_theme
from ..catalog import save_catalog, scan_theme


def load_preferred_catalog() -> tuple[ThemeCatalog, str]:
    """Load a valid user scan first, falling back to bundled/source data."""
    warning = ""
    cached = data_dir() / "catalog_daxue.json"
    if cached.is_file():
        try:
            catalog = load_catalog(cached)
            if not catalog.resources:
                raise ValueError("资源目录为空")
            return catalog, warning
        except Exception as exc:
            warning = f"用户扫描目录损坏，已回退到内置目录：{exc}"
    bundled = bundled_catalog()
    if bundled.is_file():
        return load_catalog(bundled), warning
    source = default_source_theme()
    if not source.is_file():
        raise FileNotFoundError("找不到资源目录，也找不到默认大雪主题。")
    catalog = scan_theme(source)
    save_catalog(catalog, cached)
    return catalog, warning


def save_user_catalog(catalog: ThemeCatalog) -> None:
    save_catalog(catalog, data_dir() / "catalog_daxue.json")
