from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout

from ..imageops import enhance_image, fit_image
from ..models import ResourceChange
from ..paths import bundle_root
from ..semantic import PreviewSpec
from .design_system import set_role


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

    def _image(self, scene: PreviewScene) -> Image.Image:
        cached = self._images.get(scene.name)
        if cached is None:
            with Image.open(scene.path) as opened:
                cached = opened.convert("RGBA")
            self._images[scene.name] = cached
        return cached.copy()

    @staticmethod
    def _rect(scene: PreviewScene, spec: PreviewSpec) -> tuple[int, int, int, int] | None:
        normalized = scene.targets.get(spec.target)
        if normalized is None:
            return None
        left, top, right, bottom = normalized
        return (
            round(scene.width * left), round(scene.height * top),
            round(scene.width * right), round(scene.height * bottom),
        )

    def base_image(self, spec: PreviewSpec) -> Image.Image | None:
        scene = self.scene(spec)
        return self._image(scene) if scene else None

    def highlighted_image(self, spec: PreviewSpec) -> Image.Image | None:
        scene = self.scene(spec)
        if scene is None:
            return None
        image = self._image(scene)
        rect = self._rect(scene, spec)
        if rect:
            draw = ImageDraw.Draw(image, "RGBA")
            draw.rectangle(rect, outline=(86, 69, 212, 245), width=max(3, image.width // 240))
            draw.rectangle(rect, outline=(255, 255, 255, 220), width=max(1, image.width // 520))
        return image

    def current_image(self, spec: PreviewSpec, change: ResourceChange | None) -> Image.Image | None:
        scene = self.scene(spec)
        if scene is None:
            return None
        image = self._image(scene)
        rect = self._rect(scene, spec)
        if rect is None:
            return image
        if change is not None and change.enabled:
            if change.value:
                self._blend_color(image, rect, change.value)
            elif change.source_kind == "placeholder":
                self._blend_placeholder(image, rect)
            elif change.source_file:
                source = Path(change.source_file)
                if source.is_file():
                    try:
                        with Image.open(source) as opened:
                            replacement = opened.convert("RGBA")
                        width = max(1, rect[2] - rect[0])
                        height = max(1, rect[3] - rect[1])
                        replacement = fit_image(replacement, (width, height), change.fit, change.focus_x, change.focus_y)
                        replacement = enhance_image(replacement, change.enhance, change.enhance_strength)
                        image.alpha_composite(replacement, rect[:2])
                    except (OSError, ValueError):
                        self._blend_missing(image, rect)
                else:
                    self._blend_missing(image, rect)
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rectangle(rect, outline=(86, 69, 212, 245), width=max(3, image.width // 240))
        draw.rectangle(rect, outline=(255, 255, 255, 220), width=max(1, image.width // 520))
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
    ):
        super().__init__(parent)
        self.setWindowTitle(f"真机预览：{spec.caption}")
        self.resize(1120, 720)
        root = QVBoxLayout(self)
        info = QLabel(
            f"{spec.caption} · 参考设备：{repository.device.get('model', '未知')} · "
            f"{repository.device.get('magic_os', 'MagicOS 未知')} / Android {repository.device.get('android_release', '未知')}"
        )
        info.setWordWrap(True)
        root.addWidget(info)
        columns = QHBoxLayout()
        for title, image in (("原始参考", repository.highlighted_image(spec)), ("当前设置预览", repository.current_image(spec, change))):
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
