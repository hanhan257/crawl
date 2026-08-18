"""Crawl nebgift.com (ShopBase) → CSV title, des, cate, giá (+ link)."""
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

SHOP = "https://nebgift.com"
OUT = ROOT / "output" / "nebgift_all.csv"
WORKERS = 64


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    proxy = ""
    if isinstance(cfg.get("proxy"), dict):
        proxy = str(cfg["proxy"].get("url") or "")
    else:
        proxy = str(cfg.get("proxy") or "")

    crawler = ShopBaseCrawler(site_url=SHOP, proxy_url=proxy or None)
    print(f"[start] {SHOP} workers={WORKERS}", flush=True)
    t0 = time.time()

    def progress(current: int, total: int, matched: int) -> None:
        if current % 25 == 0 or current == total or matched % 200 == 0:
            print(
                f"[prog] {current}/{total} matched={matched}",
                flush=True,
            )

    products = crawler._crawl_catalog(
        limit=None,
        max_pages=None,
        max_workers=WORKERS,
        progress_callback=progress,
    )
    # Dedup by URL handle
    by_handle: dict[str, object] = {}
    for p in products:
        if not p or not p.url:
            continue
        handle = p.url.rstrip("/").rsplit("/", 1)[-1]
        by_handle[handle] = p
    products = list(by_handle.values())
    print(f"[map] unique={len(products)} in {time.time()-t0:.1f}s", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "des", "cate", "price", "link"]
        )
        writer.writeheader()
        for p in products:
            cats = [html.unescape(c) for c in (p.categories or []) if c]
            if not cats and p.product_type:
                cats = [html.unescape(str(p.product_type))]
            writer.writerow(
                {
                    "title": html.unescape(p.title or ""),
                    "des": html.unescape(p.description or ""),
                    "cate": ", ".join(cats),
                    "price": f"{p.price:.2f}" if p.price is not None else "",
                    "link": p.url or "",
                }
            )
    print(f"[done] {len(products):,} rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
