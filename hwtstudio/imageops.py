from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from .models import ResourceChange, ResourceSlot
from .pngmeta import inject_android_chunks


PLACEHOLDER_SIZE = (1080, 1920)
PLACEHOLDER_RGBA = (242, 242, 242, 255)
MAX_IMAGE_FILE_BYTES = 256 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16384
MAX_IMAGE_PIXELS = 64_000_000
# 真机预览只在截图区域里显示,最高 2048 像素足够清晰,避免在界面线程
# 解码整张高分辨率壁纸导致卡顿。
PREVIEW_MAX_EDGE = 2048


def _validate_image_size(width: int, height: int) -> None:
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width < 1
        or height < 1
    ):
        raise ValueError("图片尺寸无效")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ValueError(f"图片单边不能超过 {MAX_IMAGE_DIMENSION} 像素")
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError(f"图片像素总数不能超过 {MAX_IMAGE_PIXELS}")


def load_image(source: Path) -> Image.Image:
    source = Path(source)
    try:
        file_size = source.stat().st_size
    except OSError as exc:
        raise ValueError(f"图片文件不可用：{source}") from exc
    if file_size > MAX_IMAGE_FILE_BYTES:
        raise ValueError(f"图片文件不能超过 {MAX_IMAGE_FILE_BYTES} 字节")
    with Image.open(source) as opened:
        _validate_image_size(opened.width, opened.height)
        return opened.convert("RGBA")


def load_image_preview(source: Path, max_edge: int = PREVIEW_MAX_EDGE) -> Image.Image:
    """Decode a downscaled RGBA preview without materializing the full bitmap.

    JPEG uses PIL's draft mode first (fast power-of-two decode), and any
    image larger than ``max_edge`` is resized before returning. Export quality
    is untouched: this loader is only used for on-screen previews.
    """
    source = Path(source)
    try:
        file_size = source.stat().st_size
    except OSError as exc:
        raise ValueError(f"图片文件不可用：{source}") from exc
    if file_size > MAX_IMAGE_FILE_BYTES:
        raise ValueError(f"图片文件不能超过 {MAX_IMAGE_FILE_BYTES} 字节")
    max_edge = max(1, int(max_edge))
    with Image.open(source) as opened:
        _validate_image_size(opened.width, opened.height)
        if opened.format == "JPEG" and max(opened.size) > max_edge:
            try:
                opened.draft("RGB", (max_edge, max_edge))
            except (OSError, ValueError):
                pass
        width, height = opened.width, opened.height
        image = opened.convert("RGBA")
        scale = max_edge / max(width, height)
        if scale < 1.0:
            image = image.resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.Resampling.LANCZOS,
            )
        return image


def render_image(source: Path, slot: ResourceSlot, change: ResourceChange) -> bytes:
    image = load_image(source)

    target_width = slot.width or image.width
    target_height = slot.height or image.height
    if target_width and target_height:
        _validate_image_size(target_width, target_height)
        image = fit_image(
            image,
            (target_width, target_height),
            change.fit,
            change.focus_x,
            change.focus_y,
        )
    image = enhance_image(image, change.enhance, change.enhance_strength)

    target_format = _target_format(slot)
    output = BytesIO()
    if target_format == "JPEG":
        image.convert("RGB").save(output, "JPEG", quality=95, optimize=True, subsampling=0)
    elif target_format == "WEBP":
        image.save(output, "WEBP", quality=95, method=6)
    else:
        image.save(output, "PNG", optimize=True, compress_level=9)
    data = output.getvalue()
    if target_format == "PNG" and slot.png_chunks:
        data = inject_android_chunks(data, slot.png_chunks)
    return data


def render_placeholder(slot: ResourceSlot) -> bytes:
    """Render a neutral managed placeholder using the slot's output format."""
    width = slot.width or PLACEHOLDER_SIZE[0]
    height = slot.height or PLACEHOLDER_SIZE[1]
    _validate_image_size(width, height)
    image = Image.new("RGBA", (width, height), PLACEHOLDER_RGBA)
    target_format = _target_format(slot)
    output = BytesIO()
    if target_format == "JPEG":
        image.convert("RGB").save(output, "JPEG", quality=90, optimize=True)
    elif target_format == "WEBP":
        image.save(output, "WEBP", quality=95, method=6)
    else:
        image.save(output, "PNG", optimize=True, compress_level=9)
    data = output.getvalue()
    if target_format == "PNG" and slot.png_chunks:
        data = inject_android_chunks(data, slot.png_chunks)
    return data


def fit_image(
    image: Image.Image,
    size: tuple[int, int],
    mode: str = "cover",
    focus_x: float = 0.5,
    focus_y: float = 0.5,
) -> Image.Image:
    _validate_image_size(*size)
    focus_x = min(1.0, max(0.0, focus_x))
    focus_y = min(1.0, max(0.0, focus_y))
    if mode == "stretch":
        return image.resize(size, Image.Resampling.LANCZOS)
    if mode == "contain":
        contained = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", size, (242, 242, 242, 255))
        canvas.alpha_composite(contained, ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2))
        return canvas
    return ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=(focus_x, focus_y))


def enhance_image(image: Image.Image, mode: str, strength: float) -> Image.Image:
    strength = min(1.0, max(0.0, strength))
    if mode not in {"light", "dark"} or strength <= 0:
        return image
    overlay_color = (255, 255, 255, round(255 * strength)) if mode == "light" else (0, 0, 0, round(255 * strength))
    overlay = Image.new("RGBA", image.size, overlay_color)
    return Image.alpha_composite(image.convert("RGBA"), overlay)


def _format_from_extension(extension: str) -> str:
    lowered = extension.lower()
    if lowered in {".jpg", ".jpeg"}:
        return "JPEG"
    if lowered == ".webp":
        return "WEBP"
    return "PNG"


def _target_format(slot: ResourceSlot) -> str:
    extension = (slot.extension or Path(slot.path).suffix).lower()
    if extension in {".png", ".jpg", ".jpeg", ".webp"}:
        return _format_from_extension(extension)
    actual = (slot.actual_format or "").upper()
    return actual if actual in {"PNG", "JPEG", "WEBP"} else "PNG"
