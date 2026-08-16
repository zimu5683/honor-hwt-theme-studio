from __future__ import annotations

from dataclasses import dataclass, replace
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
class PreviewSpec:
    scene: str
    target: str
    caption: str


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
    preview: PreviewSpec | None = None
    supports_surfaces: bool = False


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
    supports_surfaces: bool = False,
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
        supports_surfaces=supports_surfaces,
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

    _setting("settings_background", "常用应用", "设置页面背景", "为系统“设置”页面铺设自定义图片背景；默认标题与页面全透明，搜索框、卡片和分隔线半透明，图片不被白色面板挡住。", "image",
             slot_ids=("__synthetic__::background::设置背景",), required_packages=("com.android.settings",),
             supports_surfaces=True),
    _setting("messages_background", "常用应用", "短信页面背景", "为信息列表和会话页面铺设自定义图片背景；默认标题与页面全透明，列表、输入栏和分隔线半透明，图片不被白色面板挡住。", "image",
             slot_ids=("__synthetic__::background::信息/短信背景",),
             required_packages=("com.hihonor.mms", "com.android.mms", "com.huawei.mms"),
             supports_surfaces=True),
    _setting("phone_background", "常用应用", "电话页面背景", "为拨号和通话相关页面铺设自定义图片背景；默认标题与页面全透明，工具栏、数字按键和通话记录半透明，图片不被白色面板挡住。", "image",
             slot_ids=("__synthetic__::background::电话背景",),
             required_packages=("com.hihonor.phone", "com.hihonor.phoneservice", "com.android.server.telecom",
                                "com.huawei.phone", "com.huawei.phoneservice"),
             supports_surfaces=True),
    _setting("contacts_background", "常用应用", "联系人页面背景", "为联系人列表和详情页面铺设自定义图片背景；默认标题与页面全透明，搜索框、联系人卡片和分隔线半透明，不改动拨号键盘。", "image",
             slot_ids=("__synthetic__::background::联系人背景",),
             required_packages=("com.hihonor.contacts", "com.android.contacts", "com.huawei.contacts"),
             supports_surfaces=True),
    _setting("wechat_background", "常用应用", "微信页面背景色", "微信列表、聊天页面的基础背景颜色；不替换聊天图片。", "color",
             names=("BW_100",), modules=("com.tencent.mm",), required_packages=("com.tencent.mm",)),
    _setting("wechat_primary_text", "常用应用", "微信主要文字", "微信标题、联系人名称和消息正文的颜色。", "color",
             names=("FG_0",), modules=("com.tencent.mm",), required_packages=("com.tencent.mm",)),
    _setting("wechat_secondary_text", "常用应用", "微信次要文字", "微信时间、摘要和说明文字的颜色。", "color",
             names=("FG_1", "FG_2"), modules=("com.tencent.mm",), required_packages=("com.tencent.mm",)),
    _setting("wechat_brand", "常用应用", "微信品牌强调色", "微信选中项、链接和主要操作按钮的颜色。", "color",
             names=("Brand_100", "Brand_100_CARE"), modules=("com.tencent.mm",), required_packages=("com.tencent.mm",)),
)


_PREVIEW_SPECS = {
    "desktop_wallpaper": PreviewSpec("launcher_home", "wallpaper", "桌面整张壁纸位置"),
    "lock_wallpaper": PreviewSpec("lock_screen", "wallpaper", "锁屏整张壁纸位置"),
    "theme_cover": PreviewSpec("theme_gallery", "cover", "主题列表封面区域"),
    "page_background": PreviewSpec("settings_detail", "page", "设置内容背景区域"),
    "surface_background": PreviewSpec("settings_detail", "surface", "设置列表表面区域"),
    "top_bar": PreviewSpec("settings_detail", "topbar", "设置顶部栏区域"),
    "bottom_bar": PreviewSpec("wechat_settings", "bottom", "应用底部导航区域"),
    "primary_text": PreviewSpec("settings_detail", "primary", "设置主要文字区域"),
    "secondary_text": PreviewSpec("messages", "secondary", "短信次要文字区域"),
    "accent": PreviewSpec("theme_gallery", "accent", "主题选中与强调区域"),
    "controls": PreviewSpec("settings_detail", "controls", "设置控件区域"),
    "divider": PreviewSpec("settings_detail", "divider", "设置分隔线区域"),
    "notification_background": PreviewSpec("notification_shade", "panel", "通知面板背景区域"),
    "notification_icon": PreviewSpec("notification_shade", "icon", "通知图标位置"),
    "system_accent": PreviewSpec("quick_settings", "accent", "控制中心选中区域"),
    "brightness": PreviewSpec("quick_settings", "brightness", "亮度滑块区域"),
    "volume_background": PreviewSpec("volume_overlay", "panel", "音量面板表面区域"),
    "volume_slider": PreviewSpec("volume_overlay", "slider", "音量滑块区域"),
    "folder_background": PreviewSpec("launcher_folder", "folder", "桌面文件夹展开区域"),
    "launcher_label": PreviewSpec("launcher_home", "labels", "桌面图标名称区域"),
    "widget_text": PreviewSpec("launcher_home", "widget", "桌面小组件文字区域"),
    "recent_tasks": PreviewSpec("recent_tasks", "cards", "最近任务卡片区域"),
    "settings_background": PreviewSpec("settings_detail", "content", "设置应用内容背景"),
    "messages_background": PreviewSpec("messages", "content", "短信应用内容背景"),
    "phone_background": PreviewSpec("dialer", "content", "电话应用内容背景"),
    "contacts_background": PreviewSpec("contacts_list", "content", "联系人应用内容背景"),
    "wechat_background": PreviewSpec("wechat_settings", "content", "微信内容背景"),
    "wechat_primary_text": PreviewSpec("wechat_settings", "text", "微信主要文字"),
    "wechat_secondary_text": PreviewSpec("wechat_settings", "secondary", "微信次要文字"),
    "wechat_brand": PreviewSpec("wechat_settings", "brand", "微信品牌强调区域"),
}

SIMPLE_SETTINGS = tuple(
    replace(setting, preview=_PREVIEW_SPECS[setting.id])
    for setting in SIMPLE_SETTINGS
)


SIMPLE_BY_ID = {item.id: item for item in SIMPLE_SETTINGS}


# ---------------------------------------------------------------------------
# 常用应用页面背景的"面板透明化"同步:
# 图片背景写入后,白色面板/话框(标题栏、搜索框、卡片、列表、分隔线等)仍会
# 挡住图片。这里按大雪源主题(30039574_大雪.hwt)实测的透明资源清单,把同一
# 模块里控制这些表面的颜色一并写出;文字颜色不在此范围内,保持深色可读。
# 名称来自源主题各模块 theme.xml / framework-res-hnext|hwext/theme.xml。
# ---------------------------------------------------------------------------

SURFACE_TREATMENT_LABELS = (
    ("system", "跟随系统（不改面板）"),
    ("layered", "标题与页面全透明 · 列表半透明（默认）"),
    ("frosted", "全部半透明磨砂"),
    ("transparent", "全部全透明"),
)

SURFACE_TREATMENT_MODES = frozenset(value for value, _ in SURFACE_TREATMENT_LABELS)
# 统一处理模式：所有同步表面使用同一个值。
SURFACE_TREATMENT_VALUES = {
    "frosted": "#4DFFFFFF",
    "transparent": "#00000000",
}
# 分层处理模式：页面/标题使用 transparent，卡片/列表/按键使用 frosted。
SURFACE_LAYER_VALUES = {
    "transparent": "#00000000",
    "frosted": "#66FFFFFF",
}


def _merge_surface_maps(*maps: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    merged: dict[str, tuple[str, ...]] = {}
    for mapping in maps:
        for container, names in mapping.items():
            merged[container] = tuple(dict.fromkeys((*merged.get(container, ()), *names)))
    return merged


# 每个应用模块通用的表面颜色族(按容器分组)。
# 分层模式里 appbar/toolbar/white_bg 都视为“页面/标题/底”的全透明层，
# 卡片、搜索框、底栏和按键等再单独用半透明资源覆盖。
_SURFACE_COMMON_TRANSPARENT = {
    "framework-res-hnext/theme.xml": (
        "magic_appbar_bg", "magic_appbar_bg_blur",
        "magic_toolbar_bg", "magic_toolbar_bg_blur",
        "magic_color_bg", "magic_white_bg",
    ),
    "framework-res-hwext/theme.xml": (
        "emui_appbar_bg", "emui_appbar_bg_blur",
        "emui_toolbar_bg", "emui_toolbar_bg_blur",
        "emui_color_bg", "emui_white_bg",
    ),
}
_SURFACE_COMMON_FROSTED = {
    "framework-res-hnext/theme.xml": (
        "magic_navigationbar_bg", "magic_navigationbar_bg_blur",
        "navigationbar_magic_light",
        "magic_subtab_bg", "magic_subtab_bg_blur",
        "magic_color_tips_bg",
    ),
    "framework-res-hwext/theme.xml": (
        "emui_navigationbar_bg", "emui_navigationbar_bg_blur",
        "navigationbar_emui_light",
        "emui_subtab_bg", "emui_subtab_bg_blur",
        "emui_color_tips_bg",
    ),
}
_SURFACE_FAMILY = _merge_surface_maps(_SURFACE_COMMON_TRANSPARENT, _SURFACE_COMMON_FROSTED)

# 各项目在应用自身 theme.xml 里的专属表面颜色(搜索框、名片卡片、分隔线等)。
# 统一处理模式(frosted/transparent)沿用这份清单，保持旧工程行为一致。
SURFACE_SYNC_NAMES: dict[str, dict[str, tuple[str, ...]]] = {
    "contacts_background": {
        **_SURFACE_FAMILY,
        "theme.xml": (
            "magic_color_bg", "emui_color_bg",
            "people_background", "contacts_header_background",
            "searchview_background_white", "bottom_tab_bg",
            "hwsubtab_magic_color_bg",
            "divider_color", "tips_and_divider_color",
            "magic_color_subheader_divider", "magic_color_divider_horizontal",
            "familyname_overlay_list_divider",
        ),
    },
    "settings_background": {
        **_SURFACE_FAMILY,
        "theme.xml": (
            "searchview_background_color", "magic_color_bg_cardview",
            "card_background_color_selector", "magic_card_panel_bg",
            "emui_card_panel_bg", "emui_inputbox_bg",
        ),
    },
    "messages_background": {
        **_SURFACE_FAMILY,
        "theme.xml": (
            "color_gray_one", "magic_gray_5", "magic_black_color_alpha_5",
            "conversation_background", "conversation_item_divider_color",
            "attach_panel_item_color", "message_editor_background",
            "duoqu_border_color", "duoqu_menu_splite_bgcolor",
        ),
    },
    "phone_background": {
        **_SURFACE_FAMILY,
        "theme.xml": (),
    },
}

# 分层处理模式(layered)按角色拆分。title/page 全透明，卡片/搜索/列表/按键/
# 底部导航等保持半透明，形成用户想要的两个层次。
SURFACE_LAYER_SYNC_NAMES: dict[str, dict[str, dict[str, tuple[str, ...]]]] = {
    "contacts_background": {
        "transparent": _merge_surface_maps(
            _SURFACE_COMMON_TRANSPARENT,
            {
                "theme.xml": (
                    "magic_color_bg", "emui_color_bg",
                    "people_background", "contacts_header_background",
                ),
            },
        ),
        "frosted": _merge_surface_maps(
            _SURFACE_COMMON_FROSTED,
            {
                "theme.xml": (
                    "searchview_background_white", "bottom_tab_bg",
                    "hwsubtab_magic_color_bg", "default_nav_bar_color",
                    "divider_color", "tips_and_divider_color",
                    "magic_color_subheader_divider", "magic_color_divider_horizontal",
                    "familyname_overlay_list_divider",
                ),
            },
        ),
    },
    "settings_background": {
        "transparent": _SURFACE_COMMON_TRANSPARENT,
        "frosted": _merge_surface_maps(
            _SURFACE_COMMON_FROSTED,
            {
                "theme.xml": (
                    "searchview_background_color", "magic_color_bg_cardview",
                    "card_background_color_selector", "magic_card_panel_bg",
                    "emui_card_panel_bg", "emui_inputbox_bg",
                    "magic_listcard_bg", "preference_divider_grey",
                ),
            },
        ),
    },
    "messages_background": {
        "transparent": _merge_surface_maps(
            _SURFACE_COMMON_TRANSPARENT,
            {"theme.xml": ("conversation_background",)},
        ),
        "frosted": _merge_surface_maps(
            _SURFACE_COMMON_FROSTED,
            {
                "theme.xml": (
                    "color_gray_one", "magic_gray_5", "magic_black_color_alpha_5",
                    "conversation_item_divider_color",
                    "attach_panel_item_color", "message_editor_background",
                    "message_editor_background_trans", "duoqu_border_color",
                    "duoqu_menu_background_color", "duoqu_menu_splite_bgcolor",
                ),
            },
        ),
    },
    "phone_background": {
        "transparent": _merge_surface_maps(
            _SURFACE_COMMON_TRANSPARENT,
            {
                "framework-res-hnext/theme.xml": ("hwtoolbar_background",),
                "framework-res-hwext/theme.xml": ("hwtoolbar_background",),
            },
        ),
        "frosted": _SURFACE_COMMON_FROSTED,
    },
}

# 通话/拨号界面实际位于联系人应用。用户要求“电话背景”额外同步这些拨号盘
# 表面，而“联系人背景”不得改动拨号键盘。
_DIALER_SURFACE_MODULES = ("com.hihonor.contacts", "com.android.contacts", "com.huawei.contacts")
_DIALER_SURFACE_NAMES = {
    "theme.xml": (
        "dialpad_background_color",
        "recent_task_jhh_background_color",
    ),
}

# 仅改颜色仍会留下不透明的图片底：设置卡片、信息搜索框、联系人标题和拨号盘
# 底图需要在导出时生成半透明/透明 PNG 替换。图片目标带有角色：frosted 走
# 半透明，transparent 走全透明。
_SURFACE_IMAGE_TARGETS: dict[str, dict[str, tuple[tuple[str, str], ...]]] = {
    "settings_background": {
        "com.android.settings": (("res/drawable/card_background.9.png", "transparent"),),
    },
    "messages_background": {
        "com.hihonor.mms": (
            ("res/drawable-xxhdpi/message_search_view_edit_bg.png", "transparent"),
            ("res/drawable-xxhdpi/message_search_view_edit_bg_onappbar.png", "transparent"),
        ),
        "com.android.mms": (
            ("res/drawable-xxhdpi/message_search_view_edit_bg.png", "transparent"),
            ("res/drawable-xxhdpi/message_search_view_edit_bg_onappbar.png", "transparent"),
        ),
        "com.huawei.mms": (
            ("res/drawable-xxhdpi/message_search_view_edit_bg.png", "transparent"),
            ("res/drawable-xxhdpi/message_search_view_edit_bg_onappbar.png", "transparent"),
        ),
    },
    "phone_background": {
        "com.hihonor.contacts": (("res/drawable-xxhdpi/dialpad_background_drawable.9.png", "frosted"),),
        "com.android.contacts": (("res/drawable-xxhdpi/dialpad_background_drawable.9.png", "frosted"),),
        "com.huawei.contacts": (("res/drawable-xxhdpi/dialpad_background_drawable.9.png", "frosted"),),
    },
    "contacts_background": {
        "com.hihonor.contacts": (("res/drawable-xxhdpi/header_background4.9.png", "transparent"),),
        "com.android.contacts": (("res/drawable-xxhdpi/header_background4.9.png", "transparent"),),
        "com.huawei.contacts": (("res/drawable-xxhdpi/header_background4.9.png", "transparent"),),
    },
}


def surface_treatment_label(treatment: str) -> str:
    return dict(SURFACE_TREATMENT_LABELS).get(treatment, "跟随系统")


def surface_value_for_treatment(treatment: str, role: str = "frosted") -> str | None:
    """返回指定处理模式/角色应写入的颜色值。"""
    if treatment == "layered":
        return SURFACE_LAYER_VALUES.get(role)
    return SURFACE_TREATMENT_VALUES.get(treatment)


def _uniform_surface_names(setting_id: str, module: str) -> dict[str, tuple[str, ...]]:
    if setting_id == "phone_background" and module in _DIALER_SURFACE_MODULES:
        return _DIALER_SURFACE_NAMES
    return SURFACE_SYNC_NAMES.get(setting_id, {})


def _layered_surface_names(
    setting_id: str,
    module: str,
) -> dict[str, dict[str, tuple[str, ...]]]:
    if setting_id == "phone_background" and module in _DIALER_SURFACE_MODULES:
        return {"frosted": _DIALER_SURFACE_NAMES}
    return SURFACE_LAYER_SYNC_NAMES.get(setting_id, {})


def build_surface_targets(
    setting_id: str,
    catalog: ThemeCatalog,
    modules: Iterable[str],
    treatment: str,
) -> list[dict]:
    """为一个背景项目构造表面颜色同步目标(只包含目录里实际存在的资源)。"""
    if treatment not in SURFACE_TREATMENT_MODES or treatment == "system":
        return []
    module_set = set(modules)
    if setting_id == "phone_background":
        module_set.update(_DIALER_SURFACE_MODULES)
    available = {
        (slot.module, slot.container, slot.name)
        for slot in catalog.resources
        if slot.resource_type == "color" and slot.status != "当前版本不支持"
    }
    targets: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for module in sorted(module_set):
        if treatment == "layered":
            role_maps = _layered_surface_names(setting_id, module)
            for role, names_by_container in role_maps.items():
                value = SURFACE_LAYER_VALUES.get(role)
                if value is None:
                    continue
                for container, names in names_by_container.items():
                    for name in names:
                        key = (module, container, "color", name)
                        if (module, container, name) not in available or key in seen:
                            continue
                        seen.add(key)
                        targets.append(
                            {
                                "module": module,
                                "container": container,
                                "resource_type": "color",
                                "name": name,
                                "value": value,
                            }
                        )
        else:
            value = SURFACE_TREATMENT_VALUES.get(treatment)
            if value is None:
                continue
            for container, names in _uniform_surface_names(setting_id, module).items():
                for name in names:
                    key = (module, container, "color", name)
                    if (module, container, name) not in available or key in seen:
                        continue
                    seen.add(key)
                    targets.append(
                        {
                            "module": module,
                            "container": container,
                            "resource_type": "color",
                            "name": name,
                            "value": value,
                        }
                    )

    image_slots = {
        (slot.module, slot.path): slot
        for slot in catalog.resources
        if slot.resource_type == "image" and slot.status != "当前版本不支持"
    }
    for module, paths in _SURFACE_IMAGE_TARGETS.get(setting_id, {}).items():
        if module not in module_set:
            continue
        for path, role in paths:
            slot = image_slots.get((module, path))
            if slot is None:
                continue
            targets.append(
                {
                    "module": module,
                    "resource_type": "image",
                    "path": path,
                    "slot_id": slot.id,
                    "role": role,
                    "value": surface_value_for_treatment(treatment, role),
                }
            )
    return targets


def background_setting_for_slot(slot: ResourceSlot) -> SimpleSetting | None:
    """返回合成背景槽位对应的简洁项目(仅限支持面板透明化的项目)。"""
    for setting in SIMPLE_SETTINGS:
        if setting.supports_surfaces and slot.id in setting.slot_ids:
            return setting
    return None


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
