from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import ResourceSlot, ThemeCatalog


TYPE_LABELS = {
    "color": "颜色",
    "bool": "开关",
    "integer": "数字",
    "dimen": "尺寸",
    "string": "文字",
    "image": "图片",
    "icon": "图标",
    "wallpaper": "壁纸",
    "preview": "预览图",
}

SPECIAL_MODULES = {
    "com.android.systemui",
    "com.hihonor.android.launcher",
    "com.huawei.android.launcher",
    "com.tencent.mm",
}


@dataclass(frozen=True, slots=True)
class SimpleSetting:
    id: str
    section: str
    title: str
    description: str
    kind: str
    names: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    slot_ids: tuple[str, ...] = ()
    required_packages: tuple[str, ...] = ()
    scope: str = "exact"


def _setting(
    id: str,
    section: str,
    title: str,
    description: str,
    kind: str,
    *,
    names: Iterable[str] = (),
    modules: Iterable[str] = (),
    slot_ids: Iterable[str] = (),
    required_packages: Iterable[str] = (),
    scope: str = "exact",
) -> SimpleSetting:
    return SimpleSetting(
        id=id,
        section=section,
        title=title,
        description=description,
        kind=kind,
        names=tuple(names),
        modules=tuple(modules),
        slot_ids=tuple(slot_ids),
        required_packages=tuple(required_packages),
        scope=scope,
    )


SIMPLE_SETTINGS = (
    _setting("desktop_wallpaper", "主题", "桌面壁纸", "手机桌面后方显示的整张图片。", "image",
             slot_ids=("__root__::image::wallpaper/home_wallpaper_0.jpg",)),
    _setting("lock_wallpaper", "主题", "锁屏壁纸", "点亮屏幕后、解锁前显示的整张图片。", "image",
             slot_ids=("__root__::image::wallpaper/unlock_wallpaper_0.jpg",)),
    _setting("theme_cover", "主题", "主题封面与缩略图", "主题列表中的封面和小尺寸预览。", "image",
             slot_ids=("__root__::image::preview/cover.jpg", "__root__::image::preview/icon_small.jpg")),

    _setting("page_background", "全局外观", "页面背景", "应用内容区域最底层的背景颜色。", "color",
             names=("magic_color_bg", "emui_color_bg"), scope="global"),
    _setting("surface_background", "全局外观", "卡片与弹窗背景", "列表卡片、面板和弹窗表面的颜色。", "color",
             names=("magic_white_bg", "emui_white_bg", "magic_card_panel_bg", "emui_card_panel_bg",
                    "magic_listcard_bg", "magic_color_bg_cardview"), scope="global"),
    _setting("top_bar", "全局外观", "顶部栏背景", "页面标题栏和工具栏的背景颜色。", "color",
             names=("magic_appbar_bg", "magic_appbar_bg_blur", "magic_toolbar_bg", "magic_toolbar_bg_blur",
                    "emui_appbar_bg", "emui_appbar_bg_blur", "emui_toolbar_bg", "emui_toolbar_bg_blur",
                    "hwtoolbar_background", "hwtoolbar_background_color_normal"), scope="global"),
    _setting("bottom_bar", "全局外观", "底部导航背景", "底部导航栏和页面标签栏的背景颜色。", "color",
             names=("magic_navigationbar_bg", "magic_navigationbar_bg_blur", "magic_subtab_bg", "magic_subtab_bg_blur",
                    "emui_navigationbar_bg", "emui_navigationbar_bg_blur", "emui_subtab_bg", "emui_subtab_bg_blur"),
             scope="global"),
    _setting("primary_text", "全局外观", "主要文字", "标题、列表名称等最重要文字的颜色。", "color",
             names=("magic_text_primary", "magic_appbar_title", "emui_text_primary", "emui_appbar_title"), scope="global"),
    _setting("secondary_text", "全局外观", "次要与提示文字", "说明、提示、未选中标签等辅助文字的颜色。", "color",
             names=("magic_text_hint", "magic_secondary", "magic_appbar_subtitle", "magic_subtab_text_off",
                    "emui_text_hint", "emui_secondary", "emui_appbar_subtitle", "emui_subtab_text_off"), scope="global"),
    _setting("accent", "全局外观", "强调与选中颜色", "选中项、链接和需要突出显示内容的颜色。", "color",
             names=("magic_accent", "magic_primary", "emui_accent", "emui_primary"), scope="global"),
    _setting("controls", "全局外观", "按钮、进度与开关", "按钮、进度条、开关开启和勾选状态的颜色。", "color",
             names=("magic_functional_blue", "magic_progress", "magic_switch_bg_on", "magic_control_hightlight",
                    "magic_checkbox_boxedge", "emui_functional_blue", "emui_progress", "emui_switch_bg_on",
                    "emui_control_hightlight", "emui_checkbox_boxedge"), scope="global"),
    _setting("divider", "全局外观", "分隔线", "列表行、卡片和不同内容区域之间的线条颜色。", "color",
             names=("divider_color", "list_divider_color", "section_divider_color", "preference_divider_grey",
                    "tips_and_divider_color", "popup_list_divider_color"), scope="global"),

    _setting("notification_background", "系统界面", "通知面板背景", "下拉通知列表和通知卡片的背景颜色。", "color",
             names=("notification_background_color_without_alpha", "notification_material_background_color"),
             modules=("com.android.systemui",)),
    _setting("notification_icon", "系统界面", "通知图标", "通知面板中小图标的统一颜色。", "color",
             names=("hnnotification_icon_color",), modules=("com.android.systemui",)),
    _setting("system_accent", "系统界面", "通知与控制中心强调色", "快捷开关选中、通知按钮和高亮状态的颜色。", "color",
             names=("hnnotification_accent", "qs_tile_tint_on", "magic_activated"), modules=("com.android.systemui",)),
    _setting("brightness", "系统界面", "亮度控件", "控制中心亮度图标和亮度滑块的颜色。", "color",
             names=("hnnotification_brightness_icon_color", "hnnotification_brightness_slider_color"),
             modules=("com.android.systemui",)),
    _setting("volume_background", "系统界面", "音量面板", "按音量键后弹出面板的背景颜色。", "color",
             names=("volume_background_color", "volume_background_lower_color"), modules=("com.android.systemui",)),
    _setting("volume_slider", "系统界面", "音量滑块", "音量图标、已调节部分和轨道的颜色。", "color",
             names=("volume_image_color", "icon_volume_dialog_color", "volume_slider_background_color", "volume_line_color"),
             modules=("com.android.systemui",)),

    _setting("folder_background", "桌面", "文件夹背景", "桌面文件夹展开后的背景颜色。", "color",
             names=("folder_background_simple",),
             modules=("com.hihonor.android.launcher", "com.huawei.android.launcher"),
             required_packages=("com.hihonor.android.launcher", "com.huawei.android.launcher")),
    _setting("launcher_label", "桌面", "桌面应用名称", "桌面图标下方应用名称的文字颜色。", "color",
             names=("workspace_app_text_color", "folder_app_text_color"),
             modules=("com.hihonor.android.launcher", "com.huawei.android.launcher"),
             required_packages=("com.hihonor.android.launcher", "com.huawei.android.launcher")),
    _setting("widget_text", "桌面", "小组件文字", "桌面小组件内主要文字的颜色。", "color",
             names=("widget_text_color",), modules=("com.hihonor.android.launcher", "com.huawei.android.launcher"),
             required_packages=("com.hihonor.android.launcher", "com.huawei.android.launcher")),
    _setting("recent_tasks", "桌面", "最近任务卡片", "多任务界面中应用卡片周围的背景颜色。", "color",
             names=("recent_task_jhh_background_color", "app_card_bg_color_blur"),
             modules=("com.hihonor.android.launcher", "com.huawei.android.launcher"),
             required_packages=("com.hihonor.android.launcher", "com.huawei.android.launcher")),

    _setting("settings_background", "常用应用", "设置页面背景", "为系统“设置”页面铺设自定义图片背景。", "image",
             slot_ids=("__synthetic__::background::设置背景",), required_packages=("com.android.settings",)),
    _setting("messages_background", "常用应用", "短信页面背景", "为信息列表和会话页面铺设自定义图片背景。", "image",
             slot_ids=("__synthetic__::background::信息/短信背景",),
             required_packages=("com.hihonor.mms", "com.android.mms", "com.huawei.mms")),
    _setting("phone_background", "常用应用", "电话页面背景", "为拨号和通话相关页面铺设自定义图片背景。", "image",
             slot_ids=("__synthetic__::background::电话背景",),
             required_packages=("com.hihonor.phone", "com.hihonor.phoneservice", "com.android.server.telecom",
                                "com.huawei.phone", "com.huawei.phoneservice")),
    _setting("contacts_background", "常用应用", "联系人页面背景", "为联系人列表和详情页面铺设自定义图片背景。", "image",
             slot_ids=("__synthetic__::background::联系人背景",),
             required_packages=("com.hihonor.contacts", "com.android.contacts", "com.huawei.contacts")),
    _setting("wechat_background", "常用应用", "微信页面背景色", "微信列表、聊天页面的基础背景颜色；不替换聊天图片。", "color",
             names=("BW_100",), modules=("com.tencent.mm",), required_packages=("com.tencent.mm",)),
    _setting("wechat_primary_text", "常用应用", "微信主要文字", "微信标题、联系人名称和消息正文的颜色。", "color",
             names=("FG_0",), modules=("com.tencent.mm",), required_packages=("com.tencent.mm",)),
    _setting("wechat_secondary_text", "常用应用", "微信次要文字", "微信时间、摘要和说明文字的颜色。", "color",
             names=("FG_1", "FG_2"), modules=("com.tencent.mm",), required_packages=("com.tencent.mm",)),
    _setting("wechat_brand", "常用应用", "微信品牌强调色", "微信选中项、链接和主要操作按钮的颜色。", "color",
             names=("Brand_100", "Brand_100_CARE"), modules=("com.tencent.mm",), required_packages=("com.tencent.mm",)),
)


SIMPLE_BY_ID = {item.id: item for item in SIMPLE_SETTINGS}


def setting_visible(setting: SimpleSetting, installed_packages: set[str] | None) -> bool:
    if installed_packages is None or not setting.required_packages:
        return True
    return bool(set(setting.required_packages) & installed_packages)


def resolve_setting(setting: SimpleSetting, catalog: ThemeCatalog) -> list[ResourceSlot]:
    ids = set(setting.slot_ids)
    names = set(setting.names)
    modules = set(setting.modules)
    result: list[ResourceSlot] = []
    for slot in catalog.resources:
        if slot.status == "当前版本不支持" or slot.resource_type not in ({"image", "wallpaper", "preview"} if setting.kind == "image" else {setting.kind}):
            continue
        if ids and slot.id in ids:
            result.append(slot)
            continue
        if not names or slot.name not in names:
            continue
        if setting.scope == "global":
            if slot.module in SPECIAL_MODULES or slot.module in {"icons", "__root__"}:
                continue
        elif modules and slot.module not in modules:
            continue
        result.append(slot)
    return result


def resolve_all(catalog: ThemeCatalog) -> dict[str, list[ResourceSlot]]:
    resolved = {setting.id: resolve_setting(setting, catalog) for setting in SIMPLE_SETTINGS}
    seen: dict[str, str] = {}
    for setting_id, slots in resolved.items():
        for slot in slots:
            previous = seen.get(slot.id)
            if previous is not None:
                raise ValueError(f"简洁项目资源重叠：{previous} / {setting_id} / {slot.id}")
            seen[slot.id] = setting_id
    return resolved


def simple_setting_for_slot(slot_id: str, resolved: dict[str, list[ResourceSlot]]) -> str | None:
    for setting_id, slots in resolved.items():
        if any(slot.id == slot_id for slot in slots):
            return setting_id
    return None


def friendly_resource_label(slot: ResourceSlot) -> str:
    if slot.id.startswith("__custom__"):
        return slot.label
    direct = {
        "home_wallpaper_0": "桌面壁纸",
        "unlock_wallpaper_0": "锁屏壁纸",
        "cover": "主题封面",
        "icon_small": "主题缩略图",
    }
    stem = slot.name.rsplit(".", 1)[0]
    if stem.lower() in direct:
        return direct[stem.lower()]

    key = stem.lower()
    for prefix in ("magic_", "emui_", "hw", "hn"):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    components = (
        ("navigationbar", "底部导航栏"), ("bottombar", "底部标签栏"),
        ("appbar", "顶部标题栏"), ("toolbar", "顶部工具栏"), ("subtab", "页内标签栏"),
        ("notification", "通知"), ("brightness", "亮度"), ("volume", "音量"),
        ("checkbox", "勾选框"), ("switch", "开关"), ("progress", "进度条"),
        ("divider", "分隔线"), ("wallpaper", "壁纸"), ("background", "背景"),
    )
    roles = (
        ("text_primary", "主要文字"), ("text_secondary", "次要文字"), ("text_hint", "提示文字"),
        ("subtitle", "副标题"), ("title", "标题"), ("icon", "图标"),
        ("accent", "强调色"), ("primary", "主要颜色"), ("secondary", "次要颜色"),
        ("bg", "背景"), ("color", "颜色"),
    )
    states = (
        ("pressed", "按下状态"), ("disabled", "禁用状态"), ("disable", "禁用状态"),
        ("selected", "选中状态"), ("highlight", "高亮状态"), ("hightlight", "高亮状态"),
        ("_on", "开启状态"), ("_off", "关闭状态"), ("blur", "模糊效果"),
    )
    parts: list[str] = []
    for token, label in (*components, *roles, *states):
        if token in key and label not in parts:
            parts.append(label)
    if parts:
        return " · ".join(parts)
    if slot.resource_type in {"image", "icon", "wallpaper", "preview"}:
        return "高级图片资源（暂无可靠中文说明）"
    return "高级资源（暂无可靠中文说明）"
