from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout

from ..imageops import enhance_image, fit_image, load_image, load_image_preview
from ..models import ResourceChange
from ..paths import bundle_root
from ..semantic import SURFACE_LAYER_VALUES, SURFACE_TREATMENT_VALUES, PreviewSpec, surface_treatment_label
from .design_system import set_role

_MAX_COMPOSITE_CACHE = 24

# 分层面板处理在真机截图上的示意区域：全图铺壁纸，半透明只覆盖这些区域。
_SURFACE_PREVIEW_FROSTED = {
    "settings_detail": ("frosted_search", "frosted_profile", "frosted_cards"),
    "messages": ("frosted_search", "frosted_list", "frosted_bottom"),
    "contacts_list": ("frosted_search", "frosted_cards", "frosted_bottom"),
    "dialer": ("frosted_history", "frosted_keypad", "frosted_bottom"),
}


@dataclass(frozen=True, slots=True)
class PreviewScene:
    name: str
    path: Path
    width: int
    height: int
    targets: dict[str, tuple[float, float, float, float]]


class PreviewRepository:
    def __init__(self, root: Path | None = None):
        self.root = root or (bundle_root() / "assets" / "previews")
        self.device: dict[str, str | int] = {}
        self.note = "基于真机截图的位置与效果预览；系统或应用版本变化可能影响最终形状。"
        self.scenes: dict[str, PreviewScene] = {}
        self._images: dict[str, Image.Image] = {}
        self._composites: OrderedDict[tuple, Image.Image] = OrderedDict()
        self._load()

    @property
    def available(self) -> bool:
        return bool(self.scenes)

    def _load(self) -> None:
        manifest = self.root / "manifest.json"
        if not manifest.is_file():
            return
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            self.device = dict(raw.get("device", {}))
            self.note = str(raw.get("note", self.note))
            for name, value in raw.get("scenes", {}).items():
                image_path = self.root / str(value["file"])
                if image_path.is_file():
                    targets = {
                        key: tuple(float(part) for part in rect)
                        for key, rect in value.get("targets", {}).items()
                    }
                    self.scenes[name] = PreviewScene(
                        name=name,
                        path=image_path,
                        width=int(value.get("width", 0)),
                        height=int(value.get("height", 0)),
                        targets=targets,
                    )
        except (OSError, ValueError, TypeError, KeyError):
            self.scenes.clear()

    def scene(self, spec: PreviewSpec | None) -> PreviewScene | None:
        if spec is None:
            return None
        return self.scenes.get(spec.scene)

    def _image(self, scene: PreviewScene) -> Image.Image | None:
        cached = self._images.get(scene.name)
        if cached is None:
            try:
                cached = load_image(scene.path)
            except (OSError, ValueError, Image.DecompressionBombError):
                return None
            self._images[scene.name] = cached
        return cached.copy()

    @staticmethod
    def _rect_for_target(scene: PreviewScene, target: str) -> tuple[int, int, int, int] | None:
        normalized = scene.targets.get(target)
        if normalized is None:
            return None
        left, top, right, bottom = normalized
        return (
            round(scene.width * left), round(scene.height * top),
            round(scene.width * right), round(scene.height * bottom),
        )

    @staticmethod
    def _rect(scene: PreviewScene, spec: PreviewSpec) -> tuple[int, int, int, int] | None:
        return PreviewRepository._rect_for_target(scene, spec.target)

    def _rects_for_targets(
        self,
        scene: PreviewScene,
        targets: tuple[str, ...],
    ) -> list[tuple[int, int, int, int]]:
        rects: list[tuple[int, int, int, int]] = []
        for target in targets:
            rect = self._rect_for_target(scene, target)
            if rect is not None:
                rects.append(rect)
        return rects

    def base_image(self, spec: PreviewSpec) -> Image.Image | None:
        scene = self.scene(spec)
        return self._image(scene) if scene else None

    def highlighted_image(self, spec: PreviewSpec) -> Image.Image | None:
        scene = self.scene(spec)
        if scene is None:
            return None
        image = self._image(scene)
        if image is None:
            return None
        rect = self._rect(scene, spec)
        if rect:
            self._draw_highlight(image, rect)
        return image

    @staticmethod
    def _draw_highlight(image: Image.Image, rect: tuple[int, int, int, int]) -> None:
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rectangle(rect, outline=(86, 69, 212, 245), width=max(3, image.width // 240))
        draw.rectangle(rect, outline=(255, 255, 255, 220), width=max(1, image.width // 520))

    @staticmethod
    def _change_key(change: ResourceChange | None) -> tuple:
        """A cache key for everything that shapes the composited preview."""
        if change is None or not change.enabled:
            return ()
        stat_key: tuple = ()
        if change.source_file:
            source = Path(change.source_file)
            try:
                info = source.stat()
                stat_key = (info.st_mtime_ns, info.st_size)
            except OSError:
                pass
        return (
            change.value,
            change.source_file,
            change.source_kind,
            stat_key,
            change.fit,
            round(change.focus_x, 4),
            round(change.focus_y, 4),
            change.enhance,
            round(change.enhance_strength, 4),
        )

    @classmethod
    def _compose_change(
        cls,
        image: Image.Image,
        change: ResourceChange,
        rect: tuple[int, int, int, int],
    ) -> None:
        if change.value:
            cls._blend_color(image, rect, change.value)
        elif change.source_kind == "placeholder":
            cls._blend_placeholder(image, rect)
        elif change.source_file:
            source = Path(change.source_file)
            if source.is_file():
                try:
                    replacement = load_image_preview(source)
                    width = max(1, rect[2] - rect[0])
                    height = max(1, rect[3] - rect[1])
                    replacement = fit_image(
                        replacement,
                        (width, height),
                        change.fit,
                        change.focus_x,
                        change.focus_y,
                    )
                    replacement = enhance_image(replacement, change.enhance, change.enhance_strength)
                    image.alpha_composite(replacement, rect[:2])
                except (OSError, ValueError, Image.DecompressionBombError):
                    cls._blend_missing(image, rect)
            else:
                cls._blend_missing(image, rect)

    @classmethod
    def _restore_ink(
        cls,
        original: Image.Image,
        image: Image.Image,
        rects: list[tuple[int, int, int, int]],
    ) -> None:
        """把截图里的深色文字/图标盖回合成图，避免被壁纸和半透明层完全抹掉。"""
        for rect in rects:
            region = original.crop(rect).convert("RGBA")
            gray = region.convert("L")
            mask = gray.point(lambda value: max(0, min(255, (182 - value) * 5)))
            if not mask.getbbox():
                continue
            layer = Image.new("RGBA", region.size, (0, 0, 0, 0))
            layer.paste(region, (0, 0))
            layer.putalpha(mask)
            image.alpha_composite(layer, rect[:2])

    def _treatment_image(
        self,
        scene: PreviewScene,
        spec: PreviewSpec,
        change: ResourceChange,
        treatment: str,
    ) -> Image.Image | None:
        original = self._image(scene)
        if original is None:
            return None
        canvas_rect = self._rect_for_target(scene, "canvas") or self._rect(scene, spec)
        if canvas_rect is None:
            return original
        image = original.copy()
        self._compose_change(image, change, canvas_rect)
        if treatment == "transparent":
            frosted_rects: list[tuple[int, int, int, int]] = []
        elif treatment == "frosted":
            frosted_rects = [canvas_rect]
        else:
            frosted_rects = self._rects_for_targets(scene, _SURFACE_PREVIEW_FROSTED.get(scene.name, ()))
        if frosted_rects:
            overlay = SURFACE_LAYER_VALUES.get("frosted") if treatment == "layered" else SURFACE_TREATMENT_VALUES.get("frosted")
            for rect in frosted_rects:
                if overlay:
                    self._blend_color(image, rect, overlay)
        self._restore_ink(original, image, [canvas_rect])
        return image

    def current_image(
        self,
        spec: PreviewSpec,
        change: ResourceChange | None,
        surfaces: str | None = None,
    ) -> Image.Image | None:
        scene = self.scene(spec)
        if scene is None:
            return None
        key = (scene.name, spec.target, self._change_key(change), surfaces)
        cached = self._composites.get(key)
        if cached is not None:
            self._composites.move_to_end(key)
            return cached
        image = self._image(scene)
        if image is None:
            return None
        rect = self._rect(scene, spec)
        if rect is None:
            return image
        if change is not None and change.enabled:
            if surfaces in {"layered", "frosted", "transparent"}:
                image = self._treatment_image(scene, spec, change, surfaces)
            else:
                self._compose_change(image, change, rect)
        if image is None:
            return None
        self._draw_highlight(image, rect)
        self._composites[key] = image
        while len(self._composites) > _MAX_COMPOSITE_CACHE:
            self._composites.popitem(last=False)
        return image

    @staticmethod
    def _blend_color(image: Image.Image, rect: tuple[int, int, int, int], value: str) -> None:
        normalized = value.strip().lstrip("#")
        if len(normalized) == 6:
            normalized = "FF" + normalized
        if len(normalized) != 8:
            return
        try:
            alpha, red, green, blue = bytes.fromhex(normalized)
        except ValueError:
            return
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        layer.paste((red, green, blue, alpha), rect)
        image.alpha_composite(layer)

    @staticmethod
    def _blend_placeholder(image: Image.Image, rect: tuple[int, int, int, int]) -> None:
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        layer.paste((242, 242, 242, 225), rect)
        image.alpha_composite(layer)

    @staticmethod
    def _blend_missing(image: Image.Image, rect: tuple[int, int, int, int]) -> None:
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        layer.paste((210, 64, 64, 220), rect)
        image.alpha_composite(layer)

    @staticmethod
    def to_pixmap(image: Image.Image, size: tuple[int, int] | None = None) -> QPixmap:
        raw = image.tobytes("raw", "RGBA")
        qimage = QImage(raw, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(qimage, Qt.ImageConversionFlag.NoFormatConversion)
        if size:
            pixmap = pixmap.scaled(*size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return pixmap


class ClickablePreview(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class PreviewDialog(QDialog):
    def __init__(
        self,
        repository: PreviewRepository,
        spec: PreviewSpec,
        change: ResourceChange | None,
        parent=None,
        *,
        mixed: bool = False,
        surfaces: str | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"真机预览：{spec.caption}")
        self.resize(1120, 720)
        root = QVBoxLayout(self)
        surface_note = f" · 面板处理：{surface_treatment_label(surfaces)}" if surfaces else ""
        info = QLabel(
            f"{spec.caption}{surface_note} · 参考设备：{repository.device.get('model', '未知')} · "
            f"{repository.device.get('magic_os', 'MagicOS 未知')} / Android {repository.device.get('android_release', '未知')}"
        )
        info.setWordWrap(True)
        root.addWidget(info)
        columns = QHBoxLayout()
        for title, image in (
            ("原始参考", repository.highlighted_image(spec)),
            ("当前设置预览", repository.current_image(spec, change, surfaces)),
        ):
            column = QVBoxLayout()
            label = QLabel(title)
            label.setAlignment(Qt.AlignCenter)
            column.addWidget(label)
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setMinimumSize(420, 520)
            if image is not None:
                image_label.setPixmap(repository.to_pixmap(image, (520, 560)))
            else:
                image_label.setText("暂无真机参考图")
            column.addWidget(image_label, 1)
            columns.addLayout(column, 1)
        root.addLayout(columns, 1)
        if mixed:
            mixed_note = QLabel("部分兼容资源单独调整，当前预览不合并任意底层值。")
            mixed_note.setObjectName("previewMixedNotice")
            mixed_note.setWordWrap(True)
            root.addWidget(mixed_note)
        note = QLabel(repository.note)
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        set_role(buttons.button(QDialogButtonBox.Close), "ghost")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
