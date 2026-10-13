"""Tách màu khỏi tiêu đề NFL — chỉ khi màu ở cuối, cách bằng 2+ spaces."""
from __future__ import annotations

import re

# Dài trước để khớp "Midnight Green" trước "Green"
NFL_COLORS: tuple[str, ...] = (
    "White Gold",
    "Black White",
    "White Gray",
    "Midnight Green",
    "Kelly Green",
    "Legacy Green",
    "Dark Green",
    "Steel Blue",
    "Royal Blue",
    "College Navy",
    "Navy Alternate",
    "Black Alternate",
    "Red Alternate",
    "Green Alternate",
    "Purple Alternate",
    "Blue Alternate",
    "White Alternate",
    "Navy Blue",
    "Light Blue",
    "Anthracite",
    "Camo",
    "Valkyrie",
    "Navy",
    "Black",
    "White",
    "Red",
    "Green",
    "Purple",
    "Blue",
    "Gold",
    "Orange",
    "Brown",
    "Yellow",
    "Grey",
    "Gray",
    "Silver",
    "Pink",
    "Teal",
    "Maroon",
    "Cream",
)


def split_nfl_title_color(title: str) -> tuple[str, str]:
    """
    Chỉ tách màu khi nằm cuối title, cách bằng >= 2 spaces.

    VD có color:
      Mens Houston Texans 2024 Vapor Limited Jersey   Demeco Ryans Jersey   Navy
      -> title giữ phần trước, color = Navy

    VD không tách (màu nằm trong title):
      Nike Philadelphia Eagles #7 Michael Vick Dark Green Limited Jersey
      Oregon Ducks ... – All Stitched – Black – Dante Moore Jersey
    """
    raw = (title or "").strip()
    if not raw:
        return "", ""

    for color in NFL_COLORS:
        m = re.search(rf"\s{{2,}}{re.escape(color)}\s*$", raw, re.I)
        if m:
            base = raw[: m.start()].strip()
            return base, color

    return raw, ""
