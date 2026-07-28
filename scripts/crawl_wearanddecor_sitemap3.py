"""Crawl 1 file sitemap_products ShopBase -> CSV."""
from __future__ import annotations

import csv
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopbase import ShopBaseCrawler  # noqa: E402
from utils.http_session import DEFAULT_USER_AGENT, create_http_session, parse_proxy_list  # noqa: E402

SITEMAP_URL = (
    "https://wearanddecor.com/sitemap_products_3.xml"
    "?from=1000000671335016&to=1000000671241086"
)
OUT = ROOT / "output" / "wearanddecor_sitemap3.csv"
WORKERS = 64
PRODUCT_RE = re.compile(r"/products/([^<?#\s\"']+)", re.I)
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
IMG_RE = re.compile(
    r"<loc>\s*(https?://[^<\s]+/products/[^<\s]+)\s*</loc>.*?"
    r"<image:loc>\s*([^<\s]+)\s*</image:loc>.*?"
    r"<image:title>\s*([^<]*?)\s*</image:title>",
    re.I | re.S,
)


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    proxy = ""
    if isinstance(cfg.get("proxy"), dict):
        proxy = str(cfg["proxy"].get("url") or "")
    else:
        proxy = str(cfg.get("proxy") or "")
    proxies = parse_proxy_list(proxy)
    sess = create_http_session(proxies[0] if proxies else None)
    sess.headers["User-Agent"] = DEFAULT_USER_AGENT

    print(f"[start] {SITEMAP_URL}", flush=True)
    t0 = time.time()
    response = sess.get(SITEMAP_URL, timeout=60)
    response.raise_for_status()
    xml = response.text

    # Prefer structured image blocks; fallback to loc-only.
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in IMG_RE.finditer(xml):
        loc, img, title = match.group(1), match.group(2), match.group(3)
        handle_m = PRODUCT_RE.search(loc)
        if not handle_m:
            continue
        handle = unquote(handle_m.group(1)).strip()
        if not handle or handle in seen:
            continue
        seen.add(handle)
        entries.append(
            {
                "handle": handle,
                "link": loc.strip(),
                "title": html.unescape(title.strip()),
                "images": img.strip(),
            }
        )
    if not entries:
        for loc in LOC_RE.findall(xml):
            handle_m = PRODUCT_RE.search(loc)
            if not handle_m:
                continue
            handle = unquote(handle_m.group(1)).strip()
            if not handle or handle in seen:
                continue
            seen.add(handle)
            entries.append(
                {
                    "handle": handle,
                    "link": loc.strip(),
                    "title": "",
                    "images": "",
                }
            )

    print(f"[sitemap] handles={len(entries)} in {time.time()-t0:.1f}s", flush=True)
    crawler = ShopBaseCrawler("https://wearanddecor.com", proxy_url=proxy or None)

    rows: list[dict[str, str]] = []
    lock_count = {"ok": 0, "fail": 0}

    def fetch_one(entry: dict[str, str]) -> dict[str, str]:
        product = crawler.crawl_product(entry["handle"])
        if product:
            cats = [html.unescape(c) for c in (product.categories or []) if c]
            if not cats and product.product_type:
                cats = [html.unescape(str(product.product_type))]
            images = list(product.image_urls or [])
            if not images and entry.get("images"):
                images = [entry["images"]]
            return {
                "title": html.unescape(product.title or entry.get("title") or ""),
                "price": f"{product.price:.2f}" if product.price is not None else "",
                "cate": ", ".join(cats),
                "link": product.url or entry["link"],
                "images": ",".join(images),
            }
        return {
            "title": entry.get("title") or "",
            "price": "",
            "cate": "",
            "link": entry["link"],
            "images": entry.get("images") or "",
        }

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(fetch_one, entry): entry for entry in entries}
        done = 0
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            done += 1
            if row.get("price"):
                lock_count["ok"] += 1
            else:
                lock_count["fail"] += 1
            if done % 50 == 0 or done == len(entries):
                print(
                    f"[crawl] {done}/{len(entries)} ok={lock_count['ok']} miss={lock_count['fail']}",
                    flush=True,
                )

    # Keep sitemap order
    by_link = {row["link"].rstrip("/"): row for row in rows}
    ordered = []
    for entry in entries:
        key = entry["link"].rstrip("/")
        ordered.append(by_link.get(key) or {
            "title": entry.get("title") or "",
            "price": "",
            "cate": "",
            "link": entry["link"],
            "images": entry.get("images") or "",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "cate", "link", "images"]
        )
        writer.writeheader()
        writer.writerows(ordered)

    print(
        f"[done] {len(ordered):,} rows ok={lock_count['ok']} -> {OUT} ({time.time()-t0:.1f}s)",
        flush=True,
    )


if __name__ == "__main__":
    main()
