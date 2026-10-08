"""从图片字节里读出宽高和四角是否接近纯白。平台规格不在这里。"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from app.core.errors import AppError, ErrorCode

WHITE_LEVEL = 250
_CONTENT_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "MPO": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


@dataclass(frozen=True, slots=True)
class InspectedImage:
    width_px: int
    height_px: int
    content_type: str
    white_background: bool


def inspect_image(body: bytes) -> InspectedImage:
    if not body:
        raise AppError("无法读取图片", code=ErrorCode.IMAGE_FILE_INVALID)
    try:
        with Image.open(BytesIO(body)) as image:
            image.load()
            fmt = (image.format or "").upper()
            content_type = _CONTENT_TYPES.get(fmt)
            if content_type is None:
                raise AppError("无法读取图片", code=ErrorCode.IMAGE_FILE_INVALID)
            rgb = _to_rgb(image)
            width, height = rgb.size
            if width < 1 or height < 1:
                raise AppError("无法读取图片", code=ErrorCode.IMAGE_FILE_INVALID)
            return InspectedImage(
                width_px=width,
                height_px=height,
                content_type=content_type,
                white_background=_corners_white(rgb),
            )
    except AppError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AppError("无法读取图片", code=ErrorCode.IMAGE_FILE_INVALID) from exc


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"}:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _corners_white(image: Image.Image) -> bool:
    width, height = image.size
    points = ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
    for point in points:
        pixel = image.getpixel(point)
        if not isinstance(pixel, tuple) or len(pixel) < 3:
            return False
        red, green, blue = int(pixel[0]), int(pixel[1]), int(pixel[2])
        if red < WHITE_LEVEL or green < WHITE_LEVEL or blue < WHITE_LEVEL:
            return False
    return True


__all__ = ["WHITE_LEVEL", "InspectedImage", "inspect_image"]
