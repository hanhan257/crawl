"""Crawl moveekbuddyshop.com — title, price, link, images, cate."""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopify import ShopifyCrawler  # noqa: E402

SHOP = "https://moveekbuddyshop.com"
OUT = ROOT / "output" / "moveekbuddyshop_products.csv"
WORKERS = 32


def main() -> None:
    print(f"[start] {SHOP} workers={WORKERS} (direct, no proxy)", flush=True)
    t0 = time.time()

    crawler = ShopifyCrawler(
        shop_url=SHOP,
        use_public_api=True,
        proxy_url=None,
    )
    products = crawler.crawl(max_workers=WORKERS)
    print(f"[crawl] got {len(products)} products", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["title", "price", "link", "images", "cate"])
        w.writeheader()
        for p in products:
            cate = ""
            if p.categories:
                cate = p.categories[0]
            elif p.product_type:
                cate = p.product_type
            price = ""
            if p.price is not None:
                price = f"{float(p.price):.2f}"
            w.writerow(
                {
                    "title": p.title or "",
                    "price": price,
                    "link": p.url or "",
                    "images": " | ".join(p.image_urls or []),
                    "cate": cate,
                }
            )

    elapsed = time.time() - t0
    print(f"[done] rows={len(products)} elapsed={elapsed:.1f}s file={OUT}", flush=True)


if __name__ == "__main__":
    main()
