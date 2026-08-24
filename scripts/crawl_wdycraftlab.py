"""Crawl us.wdycraftlab.com (WooCommerce Store API) -> CSV."""
from __future__ import annotations

import csv
import html
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.woocommerce import WooCommerceCrawler  # noqa: E402

SHOP = "https://us.wdycraftlab.com"
OUT = ROOT / "output" / "wdycraftlab_products.csv"
WORKERS = 20


def main() -> None:
    print(f"[start] {SHOP} workers={WORKERS} (direct, curl_cffi fallback)", flush=True)
    t0 = time.time()

    crawler = WooCommerceCrawler(site_url=SHOP, use_public_api=True, proxy_url=None)

    def progress(current: int, total: int, matched: int) -> None:
        if current == 1 or current == total or current % 10 == 0:
            print(f"[crawl] page {current}/{total} matched={matched}", flush=True)

    products = crawler.crawl(
        limit=None,
        max_pages=None,
        per_page=100,
        max_workers=WORKERS,
        progress_callback=progress,
    )
    print(f"[map] products={len(products)} in {time.time() - t0:.1f}s", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "link", "images", "cate"]
        )
        writer.writeheader()
        for p in products:
            cats = [html.unescape(c) for c in (p.categories or []) if c]
            writer.writerow(
                {
                    "title": html.unescape(p.title or ""),
                    "price": f"{p.price:.2f}" if p.price is not None else "",
                    "link": p.url or "",
                    "images": " | ".join(p.image_urls or []),
                    "cate": ", ".join(cats),
                }
            )

    elapsed = time.time() - t0
    print(f"[done] rows={len(products):,} elapsed={elapsed:.1f}s file={OUT}", flush=True)


if __name__ == "__main__":
    main()
