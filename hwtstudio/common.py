from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath


MAX_ARCHIVE_ENTRY_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000
# HWT commonly stores small, uniform JPEG previews; their legitimate ratio can
# exceed safezip's generic 200:1 default. Keep a domain-specific margin while
# still rejecting high-ratio compression bombs before they are read.
MAX_ARCHIVE_COMPRESSION_RATIO = 500.0
MAX_CATALOG_BYTES = 32 * 1024 * 1024
MAX_PROJECT_BYTES = 16 * 1024 * 1024


# Mappings confirmed by the Huawei-to-Honor converter reference project. They
# apply to scanned source resources during export; custom resources keep the
# exact names chosen by the user.
HONOR_MODULE_ALIASES = {
    "com.huawei.android.launcher": "com.hihonor.android.launcher",
    "com.huawei.phone.recorder": "com.hihonor.phone.recorder",
    "com.huawei.aod": "com.hihonor.aod",
    "framework-res-hwext": "framework-res-hnext",
}

# The reference converter applies these aliases to icon and nested resource
# paths, where the package name is part of the filename rather than the outer
# HWT module name. Keep the longest aliases first to avoid a broad replacement
# shadowing a more specific migration.
HONOR_PATH_ALIASES = {
    "com.huawei.android.totemweather": "com.hihonor.android.totemweather",
    "com.huawei.music": "com.google.android.apps.youtube.music",
    "com.hihonor.tipsove": "com.hihonor.tips",
    "com.android.deskclock": "com.hihonor.deskclock",
    "com.huawei.deskclock": "com.hihonor.deskclock",
    "com.huawei": "com.hihonor",
    "com.hicloud": "com.hihonor",
}
_HONOR_PATH_ALIASES_SORTED = tuple(
    sorted(HONOR_PATH_ALIASES.items(), key=lambda item: len(item[0]), reverse=True),
)


def honor_module_name(value: str) -> str:
    return HONOR_MODULE_ALIASES.get(value, value)


def honor_resource_name(value: str) -> str:
    return value.replace("emui", "magic").replace("hw", "hn")


def honor_resource_path(value: str) -> str:
    parts = value.split("/")
    converted: list[str] = []
    for index, part in enumerate(parts):
        if part == "framework-res-hwext":
            part = "framework-res-hnext"
        for old, new in _HONOR_PATH_ALIASES_SORTED:
            part = part.replace(old, new)
        if index == len(parts) - 1 and part.lower().endswith(".png"):
            part = part.replace("emui", "magic")
        converted.append(part)
    return "/".join(converted)


def normalize_archive_path(value: str) -> str:
    """Return the canonical Unicode form used for archive path checks."""
    return unicodedata.normalize("NFC", value)


def is_safe_archive_path(value: str) -> bool:
    value = normalize_archive_path(value)
    if not value or "\\" in value or ":" in value or value.startswith("/") or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


MODULE_CATEGORIES = {
    "__root__": "主题基础",
    "icons": "桌面图标",
    "com.tencent.mm": "微信",
    "com.android.settings": "设置",
    "com.android.systemui": "控制中心与通知栏",
    "com.android.mms": "信息与短信",
    "com.hihonor.mms": "信息与短信",
    "com.huawei.mms": "信息与短信",
    "com.android.server.telecom": "电话与通话",
    "com.hihonor.phone": "电话与通话",
    "com.huawei.phone": "电话与通话",
    "com.hihonor.phoneservice": "电话与通话",
    "com.huawei.phoneservice": "电话与通话",
    "com.android.contacts": "联系人",
    "com.hihonor.contacts": "联系人",
    "com.huawei.contacts": "联系人",
    "com.huawei.meetime": "联系人",
    "com.hihonor.android.launcher": "桌面",
    "com.huawei.android.launcher": "桌面",
    "framework-res": "系统通用框架",
    "framework-res-hnext": "荣耀系统框架",
    "framework-res-hwext": "华为系统框架",
}


ALIASES = {
    "home_wallpaper_0": "桌面壁纸",
    "unlock_wallpaper_0": "锁屏壁纸",
    "cover": "主题封面",
    "icon_small": "主题缩略图",
    "background_magic": "荣耀页面背景",
    "background_emui": "华为页面背景",
    "settings_background": "设置页面背景色",
    "colorwindowsbg": "窗口背景色",
    "conversation_background": "会话背景色",
    "message_editor_background": "短信输入栏背景",
    "message_pop_incoming_bg_color": "接收短信气泡背景",
    "message_pop_send_bg_color": "发送短信气泡背景",
    "fab_new_message_bg": "新建短信按钮背景",
    "hwtoolbar_background_color_normal": "工具栏背景",
    "status_bar": "状态栏",
    "navigation_bar": "导航栏",
    "control_center": "控制中心",
    "brightness": "亮度条",
    "switch": "开关",
    "button": "按钮",
    "divider": "分隔线",
    "primary_text": "主要文字",
    "secondary_text": "次要文字",
}


KEYWORD_LABELS = [
    ("wallpaper", "壁纸"),
    ("background", "背景"),
    ("_bg", "背景"),
    ("button", "按钮"),
    ("switch", "开关"),
    ("icon", "图标"),
    ("text", "文字"),
    ("divider", "分隔线"),
    ("toolbar", "工具栏"),
    ("status", "状态"),
    ("navigation", "导航"),
    ("accent", "强调色"),
    ("primary", "主要颜色"),
    ("secondary", "次要颜色"),
    ("pressed", "按下状态"),
    ("disabled", "禁用状态"),
    ("selected", "选中状态"),
]


def module_category(module: str) -> str:
    if module in MODULE_CATEGORIES:
        return MODULE_CATEGORIES[module]
    if "deskclock" in module:
        return "时钟"
    if "calendar" in module:
        return "日历"
    if "filemanager" in module:
        return "文件管理"
    if "notepad" in module:
        return "备忘录"
    if "calculator" in module:
        return "计算器"
    if "photos" in module:
        return "图库"
    if "systemmanager" in module or "devicemanager" in module:
        return "系统管理"
    if "thememanager" in module:
        return "主题应用"
    return "其他应用"


def friendly_label(name: str, resource_type: str) -> str:
    stem = name.rsplit(".", 1)[0]
    key = stem.lower()
    if key in ALIASES:
        return ALIASES[key]
    parts = []
    for token, label in KEYWORD_LABELS:
        if token in key and label not in parts:
            parts.append(label)
    if parts:
        return " / ".join(parts) + f"（{stem}）"
    if resource_type == "color":
        return f"颜色：{stem}"
    if resource_type == "bool":
        return f"开关：{stem}"
    if resource_type in {"image", "icon", "wallpaper", "preview"}:
        return f"图片：{stem}"
    return re.sub(r"[_-]+", " ", stem)


def risk_for(module: str, name: str, resource_type: str) -> str:
    lowered = name.lower()
    if module.startswith("framework-res") or module == "com.android.systemui":
        return "高"
    if resource_type == "image" and (".9." in lowered or lowered.endswith(".9.png")):
        return "中"
    if any(x in lowered for x in ("status", "navigation", "system", "window")):
        return "中"
    return "低"


COMMON_BACKGROUND_TARGETS = {
    "设置背景": ["com.android.settings"],
    "信息/短信背景": ["com.hihonor.mms", "com.android.mms", "com.huawei.mms"],
    "电话背景": [
        "com.hihonor.phone",
        "com.hihonor.phoneservice",
        "com.android.server.telecom",
        "com.huawei.phone",
        "com.huawei.phoneservice",
    ],
    "联系人背景": ["com.hihonor.contacts", "com.android.contacts", "com.huawei.contacts"],
}
