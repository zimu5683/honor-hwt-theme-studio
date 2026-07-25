from __future__ import annotations

import re


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

