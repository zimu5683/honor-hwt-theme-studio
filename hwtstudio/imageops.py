from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

from .models import ResourceChange, ResourceSlot
from .pngmeta import inject_android_chunks


def render_image(source: Path, slot: ResourceSlot, change: ResourceChange) -> bytes:
    with Image.open(source) as opened:
        image = opened.convert("RGBA")

    target_width = slot.width or image.width
    target_height = slot.height or image.height
    if target_width and target_height:
        image = fit_image(
            image,
            (target_width, target_height),
            change.fit,
            change.focus_x,
            change.focus_y,
        )
    image = enhance_image(image, change.enhance, change.enhance_strength)

    target_format = (slot.actual_format or _format_from_extension(slot.extension or Path(slot.path).suffix)).upper()
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


def fit_image(
    image: Image.Image,
    size: tuple[int, int],
    mode: str = "cover",
    focus_x: float = 0.5,
    focus_y: float = 0.5,
) -> Image.Image:
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

