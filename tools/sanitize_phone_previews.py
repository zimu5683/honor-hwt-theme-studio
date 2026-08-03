"""Create small preview assets from the raw ADB captures.

Raw captures stay outside the repository. This script only writes derived PNGs
and a manifest under assets/previews. By default, dynamic content is mosaicked;
use --no-mask only for an intentionally empty private-space capture.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


SCENES = {
    "launcher_home": {
        "masks": [(0.0, 0.04, 1.0, 0.28), (0.0, 0.62, 1.0, 0.76)],
        "targets": {"wallpaper": (0.0, 0.0, 1.0, 1.0), "labels": (0.08, 0.50, 0.96, 0.80), "widget": (0.05, 0.08, 0.95, 0.28)},
    },
    "launcher_folder": {
        "masks": [(0.05, 0.24, 0.95, 0.72)],
        "targets": {"folder": (0.08, 0.18, 0.92, 0.78)},
    },
    "lock_screen": {
        # Covers notification text and the user/account label near the
        # bottom while leaving the clock and lock affordances visible.
        "masks": [(0.02, 0.28, 0.98, 0.91)],
        "targets": {"wallpaper": (0.0, 0.0, 1.0, 1.0)},
    },
    "theme_gallery": {
        "masks": [],
        "targets": {"cover": (0.03, 0.17, 0.97, 0.43), "accent": (0.03, 0.52, 0.23, 0.58)},
    },
    "settings_detail": {
        "masks": [(0.04, 0.27, 0.96, 0.47), (0.04, 0.48, 0.96, 0.59)],
        "targets": {"page": (0.0, 0.19, 1.0, 1.0), "surface": (0.03, 0.28, 0.97, 0.96), "topbar": (0.0, 0.04, 1.0, 0.22), "primary": (0.18, 0.27, 0.88, 0.92), "controls": (0.04, 0.39, 0.96, 0.53), "divider": (0.17, 0.44, 0.95, 0.46), "content": (0.04, 0.26, 0.96, 0.96)},
    },
    "contacts_list": {
        # The contact list is entirely dynamic; keep only the surrounding
        # application chrome and make the list non-recoverable.
        "masks": [(0.02, 0.08, 0.98, 0.95)],
        "targets": {"content": (0.02, 0.08, 0.98, 0.93), "text": (0.18, 0.12, 0.92, 0.68)},
    },
    "notification_shade": {
        "masks": [(0.02, 0.16, 0.98, 0.86)],
        "targets": {"panel": (0.03, 0.10, 0.97, 0.98), "icon": (0.78, 0.10, 0.92, 0.18)},
    },
    "quick_settings": {
        # Wi-Fi/network names and the status summary are device-specific.
        # The connected SSID, hotspot label, and paired-device name are also
        # covered separately because they sit inside the quick-toggle cards.
        "masks": [
            (0.02, 0.03, 0.98, 0.23),
            (0.25, 0.24, 0.50, 0.37),
            (0.64, 0.24, 0.95, 0.37),
            (0.13, 0.79, 0.66, 0.91),
        ],
        "targets": {"accent": (0.10, 0.44, 0.30, 0.56), "brightness": (0.52, 0.26, 0.69, 0.43)},
    },
    "volume_overlay": {
        "masks": [],
        "targets": {"panel": (0.06, 0.20, 0.94, 0.55), "slider": (0.11, 0.26, 0.89, 0.50)},
    },
    "recent_tasks": {
        "masks": [(0.04, 0.12, 0.96, 0.82)],
        "targets": {"cards": (0.15, 0.14, 0.85, 0.83)},
    },
    "messages": {
        # Sender numbers, dates, and message bodies are all sensitive.
        "masks": [(0.02, 0.08, 0.98, 1.0)],
        "targets": {"content": (0.02, 0.08, 0.98, 0.94), "secondary": (0.16, 0.22, 0.55, 0.26), "bottom": (0.0, 0.82, 1.0, 0.98)},
    },
    "dialer": {
        # Covers the recent-contact card and the complete contact list.
        "masks": [(0.05, 0.10, 0.95, 0.95)],
        "targets": {"content": (0.03, 0.09, 0.97, 0.93)},
    },
    "wechat_settings": {
        "masks": [(0.02, 0.08, 0.98, 0.43)],
        "targets": {"content": (0.02, 0.10, 0.98, 0.93), "text": (0.18, 0.12, 0.70, 0.31), "secondary": (0.18, 0.14, 0.96, 0.33), "brand": (0.05, 0.92, 0.19, 1.0), "bottom": (0.0, 0.92, 1.0, 1.0)},
    },
}


SETTING_TARGETS = {
    "desktop_wallpaper": ("launcher_home", "wallpaper", "桌面整张壁纸位置"),
    "lock_wallpaper": ("lock_screen", "wallpaper", "锁屏整张壁纸位置"),
    "theme_cover": ("theme_gallery", "cover", "主题列表封面区域"),
    "page_background": ("settings_detail", "page", "设置内容背景区域"),
    "surface_background": ("settings_detail", "surface", "设置列表表面区域"),
    "top_bar": ("settings_detail", "topbar", "设置顶部栏区域"),
    "bottom_bar": ("wechat_settings", "bottom", "应用底部导航区域"),
    "primary_text": ("settings_detail", "primary", "设置主要文字区域"),
    "secondary_text": ("messages", "secondary", "短信次要文字区域"),
    "accent": ("theme_gallery", "accent", "主题选中与强调区域"),
    "controls": ("settings_detail", "controls", "设置控件区域"),
    "divider": ("settings_detail", "divider", "设置分隔线区域"),
    "notification_background": ("notification_shade", "panel", "通知面板背景区域"),
    "notification_icon": ("notification_shade", "icon", "通知图标位置"),
    "system_accent": ("quick_settings", "accent", "控制中心选中区域"),
    "brightness": ("quick_settings", "brightness", "亮度滑块区域"),
    "volume_background": ("volume_overlay", "panel", "音量面板表面区域"),
    "volume_slider": ("volume_overlay", "slider", "音量滑块区域"),
    "folder_background": ("launcher_folder", "folder", "桌面文件夹展开区域"),
    "launcher_label": ("launcher_home", "labels", "桌面图标名称区域"),
    "widget_text": ("launcher_home", "widget", "桌面小组件文字区域"),
    "recent_tasks": ("recent_tasks", "cards", "最近任务卡片区域"),
    "settings_background": ("settings_detail", "content", "设置应用内容背景"),
    "messages_background": ("messages", "content", "短信应用内容背景"),
    "phone_background": ("dialer", "content", "电话应用内容背景"),
    "contacts_background": ("contacts_list", "content", "联系人应用内容背景"),
    "wechat_background": ("wechat_settings", "content", "微信内容背景"),
    "wechat_primary_text": ("wechat_settings", "text", "微信主要文字"),
    "wechat_secondary_text": ("wechat_settings", "secondary", "微信次要文字"),
    "wechat_brand": ("wechat_settings", "brand", "微信品牌强调区域"),
}


def _box(size: tuple[int, int], normalized: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    width, height = size
    left, top, right, bottom = normalized
    return (round(width * left), round(height * top), round(width * right), round(height * bottom))


def _mosaic(image: Image.Image, box: tuple[int, int, int, int]) -> None:
    crop = image.crop(box)
    width, height = crop.size
    if not width or not height:
        return
    small = crop.resize((max(1, width // 28), max(1, height // 28)), Image.Resampling.BILINEAR)
    mosaic = small.resize((width, height), Image.Resampling.NEAREST).filter(ImageFilter.GaussianBlur(1.2))
    image.paste(mosaic, box)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle(box, fill=(128, 128, 128, 34), outline=(235, 235, 235, 150), width=max(2, width // 450))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=Path(r"C:\Users\Public\phone-preview-raw"))
    parser.add_argument("--output", type=Path, default=Path("assets/previews"))
    parser.add_argument(
        "--no-mask",
        action="store_true",
        help="不应用场景隐私马赛克；仅用于已确认无个人内容的隐私空间截图。",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = next(args.raw.glob("launcher_home.json"), None)
    if metadata is not None:
        captured_device = json.loads(metadata.read_text(encoding="utf-8"))["device"]
    else:
        # Allow a privacy-only re-sanitize pass over an already-derived asset
        # directory after raw screenshots have been removed.
        manifest_path = args.raw / "manifest.json"
        if not manifest_path.is_file():
            raise SystemExit(f"找不到原始采集元数据：{args.raw}")
        captured_device = json.loads(manifest_path.read_text(encoding="utf-8")).get("device", {})
    # Never publish the network ADB serial. Device identity is enough to
    # explain the reference image, while the capture tool discovers serials at
    # runtime on each run.
    device = {
        key: captured_device.get(key)
        for key in ("model", "android_release", "magic_os", "width", "height", "density")
    }
    app_versions = {
        "com.android.settings": "14.0.0.210",
        "com.android.systemui": "16.0.0.2",
        "com.hihonor.android.launcher": "17.0.20.200",
        "com.hihonor.android.thememanager": "20.1.42.303",
        "com.hihonor.mms": "18.1.18.301",
        "com.hihonor.contacts": "18.1.18.301",
        "com.tencent.mm": "8.0.76",
    }
    scenes: dict[str, dict] = {}
    for scene, spec in SCENES.items():
        source = args.raw / f"{scene}.png"
        if not source.is_file():
            raise SystemExit(f"缺少场景截图：{source}")
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        applied_masks = [] if args.no_mask else spec["masks"]
        for normalized in applied_masks:
            _mosaic(image, _box(image.size, normalized))
        # Keep the published assets small enough for the one-file EXE.
        image.thumbnail((612, 1350), Image.Resampling.LANCZOS)
        output_name = f"{scene}.png"
        image.save(args.output / output_name, "PNG", optimize=True, compress_level=9)
        scenes[scene] = {
            "file": output_name,
            "width": image.width,
            "height": image.height,
            "targets": {name: list(rect) for name, rect in spec["targets"].items()},
            "privacy_masks": [list(rect) for rect in applied_masks],
        }
    manifest = {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "device": device,
        "app_versions": app_versions,
        "note": "基于真机截图的位置与效果预览；系统或应用版本变化可能影响最终形状。",
        "scenes": scenes,
        "settings": {
            setting_id: {"scene": scene, "target": target, "caption": caption}
            for setting_id, (scene, target, caption) in SETTING_TARGETS.items()
        },
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    mask_status = "未应用隐私马赛克（--no-mask）" if args.no_mask else "已应用隐私马赛克"
    print(f"{mask_status}；已生成 {len(scenes)} 个场景和 {len(SETTING_TARGETS)} 个项目映射：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
