"""Crawl Teemill collection thewildlifetrustsshop.com -> CSV."""
from __future__ import annotations

import csv
import html
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.teemill import TeemillCrawler  # noqa: E402

SHOP = "https://thewildlifetrustsshop.com"
COLLECTION = "womens-hoodies"
OUT = ROOT / "output" / "thewildlifetrustsshop_womens-hoodies.csv"


def main() -> None:
    print(f"[start] {SHOP}/collection/{COLLECTION}/", flush=True)
    t0 = time.time()

    crawler = TeemillCrawler(SHOP)
    products = crawler.crawl_collection(COLLECTION)

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
