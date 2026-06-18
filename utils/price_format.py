from __future__ import annotations

import math
import re

DEFAULT_EXPORT_PRICE = 22.99


def _coerce_price(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "-"}:
        return None

    cleaned = re.sub(r"\bUSD\b", "", text, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"[^\d.,-]", "", cleaned)
    if not cleaned:
        return None

    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def format_export_price(price: object, *, default: float | None = None) -> str:
    """Giá xuất CSV: 22.99 (chấm thập phân, không kèm USD)."""
    value = _coerce_price(price)
    if value is None or value <= 0:
        if default is None:
            return ""
        value = default
    return f"{value:.2f}"
