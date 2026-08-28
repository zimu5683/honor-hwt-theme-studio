from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

from .paths import ensure_no_symlink_parents, unique_temp_path

PLACEHOLDER_COLOR = (242, 242, 242)
IMAGE_LAYOUT = {
    "wallpaper/home_wallpaper_0.jpg": (2160, 2160),
    "wallpaper/unlock_wallpaper_0.jpg": (1080, 2160),
    "preview/cover.jpg": (1080, 1920),
    "preview/icon_small.jpg": (510, 340),
    "preview/icon_small_1.jpg": (640, 640),
    "preview/preview_icons_0.jpg": (1080, 2160),
    "preview/preview_unlock_0.jpg": (1080, 2160),
    "preview/preview_widget_0.jpg": (1080, 2160),
    "preview/preview_widget_1.jpg": (1080, 2160),
}

# Honor Theme Manager 20.x does not infer ZIP directories from child paths.
# For a third-party local theme it explicitly requires a ``preview/`` entry
# and an ``icons`` module before the package is admitted to the local-theme
# list.  The icons module is deliberately empty, so unconfigured icons still
# fall back to the system defaults.
DIRECTORY_ENTRIES = ("unlock/", "wallpaper/", "preview/")


def empty_icons_module() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=9):
        pass
    return output.getvalue()


def description_xml(title: str, author: str, designer: str, version: str, screen: str) -> bytes:
    from xml.sax.saxutils import escape

    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<HwTheme>
  <title>{escape(title)}</title>
  <title-cn>{escape(title)}</title-cn>
  <author>{escape(author)}</author>
  <designer>{escape(designer)}</designer>
  <screen>{escape(screen)}</screen>
  <version>{escape(version)}</version>
  <font>Default</font>
  <font-cn>默认</font-cn>
  <icon-show>false</icon-show>
  <wallpaper>HWThemeEngine</wallpaper>
  <theme-banner-show>true</theme-banner-show>
  <briefinfo>由大雪主题编辑器生成的空白主题</briefinfo>
  <wallpaper-dark>false</wallpaper-dark>
</HwTheme>
'''
    return xml.encode("utf-8")


def unlock_xml() -> bytes:
    return b'<?xml version="1.0" encoding="utf-8"?>\n<HWTheme>\n  <item style="slide"/>\n</HWTheme>\n'


def placeholder_jpeg(size: tuple[int, int]) -> bytes:
    image = Image.new("RGB", size, PLACEHOLDER_COLOR)
    output = BytesIO()
    image.save(output, "JPEG", quality=90, optimize=True)
    return output.getvalue()


def blank_entries(title="空白主题", author="子木", designer="子木", version="1.0.0", screen="FHD") -> dict[str, bytes]:
    entries = {name: b"" for name in DIRECTORY_ENTRIES}
    entries.update({
        "description.xml": description_xml(title, author, designer, version, screen),
        "unlock/theme.xml": unlock_xml(),
        "icons": empty_icons_module(),
    })
    entries.update({path: placeholder_jpeg(size) for path, size in IMAGE_LAYOUT.items()})
    return entries


def create_blank_theme(
    output: Path,
    title: str = "空白主题",
    author: str = "子木",
    designer: str = "子木",
    version: str = "1.0.0",
    screen: str = "FHD",
) -> Path:
    output = Path(output)
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise ValueError("空白主题输出目标不是普通文件")
    ensure_no_symlink_parents(output, "空白主题输出目录不能包含符号链接")
    output.parent.mkdir(parents=True, exist_ok=True)
    ensure_no_symlink_parents(output, "空白主题输出目录不能包含符号链接")
    temp = unique_temp_path(output)
    temp.unlink(missing_ok=True)
    try:
        with ZipFile(temp, "w", ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in blank_entries(title, author, designer, version, screen).items():
                archive.writestr(name, data)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return output
