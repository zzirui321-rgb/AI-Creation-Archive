from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from PIL import ImageDraw, ImageFont


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False):
    candidates = ["msyhbd.ttc" if bold else "msyh.ttc", "segoeui.ttf", "DejaVuSans.ttf", "PingFang.ttc"]
    if windows := os.environ.get("WINDIR"):
        candidates = [str(Path(windows) / "Fonts" / name) for name in candidates[:2]] + candidates
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def text_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    *,
    size: int = 20,
    fill: str = "#f1f5f9",
    bold: bool = False,
) -> None:
    left, top, right, bottom = box
    face = font(size, bold)
    line_height = size + max(4, size // 4)
    max_lines = max(0, (bottom - top) // line_height)
    if not max_lines:
        return
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        current = ""
        for word in re.findall(r"\S+\s*", paragraph):
            if current and draw.textlength(current + word.rstrip(), font=face) > right - left:
                lines.append(current.rstrip())
                current = ""
            for char in word:
                if current and draw.textlength(current + char, font=face) > right - left:
                    lines.append(current.rstrip())
                    current = ""
                current += char
        lines.append(current.rstrip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "...", font=face) > right - left:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "..."
    for index, line in enumerate(lines):
        draw.text((left, top + index * line_height), line, font=face, fill=fill)
