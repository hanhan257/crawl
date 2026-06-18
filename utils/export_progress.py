from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.export_fields import ProgressCallback


class ExportProgress:
    """Chia tiến trình xuất file thành các bước có trọng số."""

    STEPS = (
        ("prepare", 0.10, "Chuẩn bị dữ liệu..."),
        ("products", 0.65, "Xử lý sản phẩm"),
        ("write", 0.15, "Ghi file CSV..."),
        ("images", 0.10, "Tải ảnh backup (nếu có)..."),
    )

    def __init__(self, callback: ProgressCallback | None = None, product_count: int = 1):
        self.callback = callback
        self.product_count = max(product_count, 1)
        self._offsets = self._build_offsets()

    def _build_offsets(self) -> dict[str, float]:
        offsets: dict[str, float] = {}
        running = 0.0
        for key, weight, _ in self.STEPS:
            offsets[key] = running
            running += weight
        return offsets

    def _emit(self, value: float, message: str) -> None:
        if self.callback:
            self.callback(min(max(value, 0.0), 1.0), message)

    def step(self, key: str, sub: float = 0.0, message: str | None = None) -> None:
        weight = next(w for k, w, _ in self.STEPS if k == key)
        base = self._offsets[key]
        label = message or next(m for k, _, m in self.STEPS if k == key)
        self._emit(base + weight * sub, label)

    def product(self, index: int, total: int | None = None) -> None:
        total = total or self.product_count
        sub = (index + 1) / max(total, 1)
        self.step(
            "products",
            sub,
            f"Xử lý sản phẩm {index + 1}/{total}...",
        )
