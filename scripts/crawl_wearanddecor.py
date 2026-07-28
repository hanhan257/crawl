"""Crawl nhanh wearanddecor.com (ShopBase catalog API, khong enrich mo ta)."""
from __future__ import annotations

import csv
import html
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopbase import ShopBaseCrawler  # noqa: E402

SHOP = "https://wearanddecor.com"
OUT = ROOT / "output" / "wearanddecor_all.csv"
WORKERS = 64


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    proxy = ""
    if isinstance(cfg.get("proxy"), dict):
        proxy = str(cfg["proxy"].get("url") or "")
    else:
        proxy = str(cfg.get("proxy") or "")

    crawler = ShopBaseCrawler(site_url=SHOP, proxy_url=proxy or None)
    print(f"[start] {SHOP} catalog-only workers={WORKERS}", flush=True)
    t0 = time.time()

    def progress(current: int, total: int, matched: int) -> None:
        if current % 20 == 0 or current == total:
            print(f"[list] page {current}/{total} items={matched}", flush=True)

    items = crawler._list_catalog_items(
        max_pages=None,
        limit=None,
        max_workers=WORKERS,
        progress_callback=progress,
    )
    print(f"[list] raw_items={len(items)} in {time.time()-t0:.1f}s", flush=True)

    # Dedup by handle (catalog count 10k co the lap)
    by_handle: dict[str, object] = {}
    for item in items:
        product = crawler._map_catalog_item(item, skip_page_fallback=True)
        if not product or not product.url:
            continue
        handle = product.url.rstrip("/").rsplit("/", 1)[-1]
        by_handle[handle] = product

    products = list(by_handle.values())
    print(f"[map] unique={len(products)}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "cate", "link", "images"]
        )
        writer.writeheader()
        for p in products:
            cats = [html.unescape(c) for c in (p.categories or []) if c]
            if not cats and p.product_type:
                cats = [html.unescape(str(p.product_type))]
            writer.writerow(
                {
                    "title": html.unescape(p.title or ""),
                    "price": f"{p.price:.2f}" if p.price is not None else "",
                    "cate": ", ".join(cats),
                    "link": p.url or "",
                    "images": ",".join(p.image_urls or []),
                }
            )
    print(f"[done] {len(products):,} rows -> {OUT} ({time.time()-t0:.1f}s)", flush=True)


if __name__ == "__main__":
    main()
