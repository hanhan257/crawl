"""Crawl toan bo joyoce.com (WooCommerce Store API) -> CSV giong croocs."""
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

from crawlers.woocommerce import WooCommerceCrawler  # noqa: E402

SHOP = "https://joyoce.com"
OUT = ROOT / "output" / "joyoce_all.csv"
WORKERS = 500


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    proxy = ""
    if isinstance(cfg.get("proxy"), dict):
        proxy = str(cfg["proxy"].get("url") or "")
    else:
        proxy = str(cfg.get("proxy") or "")

    crawler = WooCommerceCrawler(
        site_url=SHOP,
        use_public_api=True,
        proxy_url=proxy or None,
    )
    print(f"[start] {SHOP} workers={WORKERS}", flush=True)
    t0 = time.time()

    def progress(current: int, total: int, matched: int) -> None:
        if current % 25 == 0 or current == total:
            print(f"[crawl] page {current}/{total} matched={matched}", flush=True)

    products = crawler.crawl(
        limit=None,
        max_pages=None,
        max_workers=WORKERS,
        progress_callback=progress,
    )
    print(f"[map] products={len(products)} in {time.time()-t0:.1f}s", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "cate", "link", "images"]
        )
        writer.writeheader()
        for p in products:
            cats = [html.unescape(c) for c in (p.categories or []) if c]
            writer.writerow(
                {
                    "title": html.unescape(p.title or ""),
                    "price": f"{p.price:.2f}" if p.price is not None else "",
                    "cate": ", ".join(cats),
                    "link": p.url or "",
                    "images": ",".join(p.image_urls or []),
                }
            )
    print(f"[done] {len(products):,} rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
