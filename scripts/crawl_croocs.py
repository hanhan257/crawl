"""Crawl toan bo croocs.it.com (WooCommerce Store API) -> CSV."""
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
from utils.http_session import create_http_session, parse_proxy_list  # noqa: E402

SHOP = "https://croocs.it.com"
OUT = ROOT / "output" / "croocs_all.csv"
WORKERS = 500


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    proxy_text = ""
    if isinstance(cfg.get("proxy"), dict):
        proxy_text = str(cfg["proxy"].get("url") or "")
    else:
        proxy_text = str(cfg.get("proxy") or "")
    proxies = parse_proxy_list(proxy_text)

    crawler = WooCommerceCrawler(site_url=SHOP, use_public_api=True)
    if proxies:
        crawler.session = create_http_session(proxies[0])
        crawler.session.headers["User-Agent"] = crawler.session.headers.get(
            "User-Agent", ""
        )

    print(f"[start] {SHOP} workers={WORKERS} proxies={len(proxies)}", flush=True)
    t0 = time.time()

    def progress(current: int, total: int, matched: int) -> None:
        print(f"[crawl] page {current}/{total} matched={matched}", flush=True)

    products = crawler.crawl(
        limit=None,
        max_pages=None,
        max_workers=min(WORKERS, 20),  # chi ~15 trang API
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
