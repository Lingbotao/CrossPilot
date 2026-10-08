"""各平台主图尺寸与白底要求。

只负责给出提示。不合规不阻止保存，刊登前的合规拦截留在 M4。
"""

from __future__ import annotations

from dataclasses import dataclass

ISSUE_TOO_SMALL = "TOO_SMALL"
ISSUE_TOO_LARGE = "TOO_LARGE"
ISSUE_NOT_WHITE = "NOT_WHITE"
ISSUE_BAD_TYPE = "BAD_TYPE"
ISSUE_TOO_HEAVY = "TOO_HEAVY"


@dataclass(frozen=True, slots=True)
class ImageSpec:
    min_edge: int
    max_edge: int
    max_bytes: int
    require_white_main: bool
    allowed_types: frozenset[str]


IMAGE_SPECS: dict[str, ImageSpec] = {
    "amazon": ImageSpec(1000, 10000, 10_485_760, True, frozenset({"image/jpeg", "image/png"})),
    "shopee": ImageSpec(500, 2048, 2_097_152, True, frozenset({"image/jpeg", "image/png"})),
    "lazada": ImageSpec(330, 5000, 2_097_152, True, frozenset({"image/jpeg", "image/png"})),
    "tiktok": ImageSpec(600, 5000, 5_242_880, True, frozenset({"image/jpeg", "image/png"})),
}


def max_upload_bytes() -> int:
    return max(spec.max_bytes for spec in IMAGE_SPECS.values())


def assess_image(
    *,
    image_type: str,
    width_px: int,
    height_px: int,
    byte_size: int,
    content_type: str,
    white_background: bool,
) -> list[dict[str, object]]:
    """按平台规格列出问题。返回顺序与规格表一致。"""

    checks: list[dict[str, object]] = []
    short_edge = min(width_px, height_px)
    long_edge = max(width_px, height_px)
    for platform, spec in IMAGE_SPECS.items():
        issues: list[str] = []
        if content_type not in spec.allowed_types:
            issues.append(ISSUE_BAD_TYPE)
        if short_edge < spec.min_edge:
            issues.append(ISSUE_TOO_SMALL)
        if long_edge > spec.max_edge:
            issues.append(ISSUE_TOO_LARGE)
        if byte_size > spec.max_bytes:
            issues.append(ISSUE_TOO_HEAVY)
        if image_type == "MAIN" and spec.require_white_main and not white_background:
            issues.append(ISSUE_NOT_WHITE)
        checks.append({"platform_code": platform, "ok": not issues, "issues": issues})
    return checks


__all__ = [
    "IMAGE_SPECS",
    "ISSUE_BAD_TYPE",
    "ISSUE_NOT_WHITE",
    "ISSUE_TOO_HEAVY",
    "ISSUE_TOO_LARGE",
    "ISSUE_TOO_SMALL",
    "ImageSpec",
    "assess_image",
    "max_upload_bytes",
]
