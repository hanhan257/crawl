"""Crawl teesbox.co (Shopify sitemap + GraphQL batch — >25k SP) -> CSV."""
from __future__ import annotations

import csv
import html
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopify import ShopifyCrawler  # noqa: E402

SHOP = "https://www.teesbox.co"
OUT = ROOT / "output" / "teesbox_products.csv"
WORKERS = 32


def main() -> None:
    print(f"[start] {SHOP} sitemap+GraphQL workers={WORKERS}", flush=True)
    t0 = time.time()

    crawler = ShopifyCrawler(
        shop_url=SHOP,
        use_public_api=True,
        proxy_url=None,
        request_delay=0.05,
    )

    def progress(current: int, total: int, matched: int) -> None:
        if current == 1 or current == total or current % 500 == 0:
            print(f"[crawl] {current}/{total} products={matched}", flush=True)

    # GraphQL catalog query giới hạn 25k — dùng sitemap lấy ~27k handle.
    products = crawler._crawl_via_sitemap(
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
        for product in products:
            cats = [html.unescape(c) for c in (product.categories or []) if c]
            if not cats and product.product_type:
                cats = [html.unescape(str(product.product_type))]
            writer.writerow(
                {
                    "title": html.unescape(product.title or ""),
                    "price": f"{product.price:.2f}" if product.price is not None else "",
                    "link": product.url or "",
                    "images": " | ".join(product.image_urls or []),
                    "cate": ", ".join(cats),
                }
            )

    elapsed = time.time() - t0
    print(f"[done] rows={len(products):,} elapsed={elapsed:.1f}s file={OUT}", flush=True)


if __name__ == "__main__":
    main()
