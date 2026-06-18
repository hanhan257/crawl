from __future__ import annotations

import random
from typing import TypeVar

T = TypeVar("T")

EXPORT_ORDER_OPTIONS = ["original", "reverse", "shuffle"]

EXPORT_ORDER_LABELS = {
    "original": "Trên → dưới (giữ thứ tự gốc)",
    "reverse": "Dưới → trên (đảo ngược)",
    "shuffle": "Trộn ngẫu nhiên",
}


def apply_export_selection(
    items: list[T],
    quantity: int = 0,
    order: str = "original",
) -> list[T]:
    """Lọc số lượng và sắp xếp danh sách."""
    if not items:
        return []

    selected = list(items)

    if order == "reverse":
        selected = selected[::-1]
    elif order == "shuffle":
        selected = selected.copy()
        random.shuffle(selected)

    if quantity > 0:
        selected = selected[:quantity]

    return selected


def apply_export_selection_indexed(
    items: list[tuple[int, T]],
    quantity: int = 0,
    order: str = "original",
) -> list[tuple[int, T]]:
    """Lọc/sắp xếp nhưng giữ row_id gốc (index trong products_raw)."""
    if not items:
        return []

    selected = list(items)

    if order == "reverse":
        selected = selected[::-1]
    elif order == "shuffle":
        selected = selected.copy()
        random.shuffle(selected)

    if quantity > 0:
        selected = selected[:quantity]

    return selected


def describe_export_selection(total: int, quantity: int, order: str) -> str:
    export_count = total if quantity <= 0 else min(quantity, total)
    order_label = EXPORT_ORDER_LABELS.get(order, order)
    if export_count == total:
        return f"Xuất **{export_count}** SP · {order_label}"
    return f"Xuất **{export_count}** / {total} SP · {order_label}"


def describe_edit_selection(total: int, quantity: int, order: str, shown: int) -> str:
    order_label = EXPORT_ORDER_LABELS.get(order, order)
    if quantity <= 0 and shown == total:
        return f"Đang chỉnh sửa **{shown}** / {total} SP · {order_label}"
    return f"Đang chỉnh sửa **{shown}** / {total} SP (lọc {quantity or shown}) · {order_label}"
