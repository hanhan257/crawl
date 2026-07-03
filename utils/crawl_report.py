from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from models.product import Product
from utils.detector import DetectedTarget


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", text.strip().lower())
    return slug.strip("_") or "products"


@dataclass
class CrawlReport:
    source_url: str
    platform: str
    crawl_type: str
    query: str | None = None
    category: str | None = None
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    workers: int = 1
    limit: int | None = None
    max_pages: int | None = None
    expected_total: int | None = None
    products_crawled: int = 0
    pages_fetched: int = 0
    errors: list[str] = field(default_factory=list)
    json_name: str = ""
    urls_csv_name: str = ""
    json_bytes: bytes = b""
    urls_csv_bytes: bytes = b""

    def summary(self) -> str:
        parts = [f"**{self.products_crawled:,}** SP".replace(",", ".")]
        if self.expected_total is not None:
            parts.append(f"dự kiến shop ~{self.expected_total:,}".replace(",", "."))
        parts.append(f"{self.duration_seconds:.1f}s")
        if self.workers > 1:
            parts.append(f"{self.workers} luồng")
        return " · ".join(parts)


def save_crawl_report(
    products: list[Product],
    target: DetectedTarget,
    source_url: str,
    *,
    workers: int = 1,
    limit: int | None = None,
    max_pages: int | None = None,
    expected_total: int | None = None,
    started_at: datetime | None = None,
    pages_fetched: int = 0,
    errors: list[str] | None = None,
) -> CrawlReport:
    started = started_at or datetime.now(timezone.utc)
    finished = datetime.now(timezone.utc)
    duration = max(0.0, (finished - started).total_seconds())

    export_name = target.query or target.category_slug or target.handle or "products"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = _slugify(str(export_name))
    json_name = f"crawl_report_{base}_{ts}.json"
    csv_name = f"crawl_urls_{base}_{ts}.csv"

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "title", "url", "price", "sku", "source"])
    for product in products:
        writer.writerow(
            [
                product.id,
                product.title,
                product.url or "",
                product.price if product.price is not None else "",
                product.sku or "",
                product.source,
            ]
        )
    urls_csv_bytes = buffer.getvalue().encode("utf-8-sig")

    report = CrawlReport(
        source_url=source_url,
        platform=target.platform,
        crawl_type=target.crawl_type,
        query=target.query,
        category=target.category_slug or target.handle,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_seconds=round(duration, 2),
        workers=max(1, workers),
        limit=limit,
        max_pages=max_pages,
        expected_total=expected_total,
        products_crawled=len(products),
        pages_fetched=pages_fetched,
        errors=list(errors or [])[:50],
        json_name=json_name,
        urls_csv_name=csv_name,
    )

    payload = asdict(report)
    payload.pop("json_bytes", None)
    payload.pop("urls_csv_bytes", None)
    payload["message"] = (
        f"Crawl {report.products_crawled} sản phẩm từ {report.platform} "
        f"({report.crawl_type}) trong {report.duration_seconds}s"
    )
    report.json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    report.urls_csv_bytes = urls_csv_bytes
    return report
