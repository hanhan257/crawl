"""Crawl petfiestas.com (Shopline + Cloudflare) via sitemap + HTML -> CSV."""
from __future__ import annotations

import csv
import gzip
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.curl_http import curl_get  # noqa: E402

SHOP = "https://www.petfiestas.com"
SITEMAP = f"{SHOP}/sitemap_products_1.xml.gz"
OUT = ROOT / "output" / "petfiestas_products.csv"
WORKERS = 12
MAX_RETRIES = 3
RETRY_DELAY = 0.35

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
LD_JSON_PATTERN = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>',
    re.S,
)
STATICDJ_PATTERN = re.compile(
    r"https?://img\.staticdj\.com/[^\s\"'<>?]+(?:\?[^\s\"'<>]*)?",
    re.I,
)


def fetch_sitemap_urls() -> list[str]:
    from curl_cffi import requests as curl_requests

    resp = curl_requests.get(SITEMAP, impersonate="chrome120", timeout=60)
    resp.raise_for_status()
    data = gzip.decompress(resp.content)
    root = ET.fromstring(data)
    urls: list[str] = []
    for el in root.findall(".//sm:loc", SITEMAP_NS):
        loc = (el.text or "").strip()
        if "/products/" in loc and loc.rstrip("/") != SHOP.rstrip("/"):
            urls.append(loc)
    return urls


def parse_product_page(page_html: str, url: str) -> dict[str, str] | None:
    title = ""
    images: list[str] = []
    cate = ""

    for match in LD_JSON_PATTERN.finditer(page_html):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("@type") or "")
            if item_type == "Product":
                title = str(item.get("name") or "").strip()
                img = item.get("image")
                if isinstance(img, str) and img.strip():
                    images.append(img.strip().replace("http://", "https://"))
                elif isinstance(img, list):
                    for part in img:
                        if isinstance(part, str) and part.strip():
                            images.append(part.strip().replace("http://", "https://"))
            if item_type == "BreadcrumbList":
                elems = item.get("itemListElement") or []
                names = [
                    str(e.get("name") or "").strip()
                    for e in elems
                    if isinstance(e, dict) and str(e.get("name") or "").strip()
                ]
                if len(names) >= 2:
                    cate = names[-2]

    for img in STATICDJ_PATTERN.findall(page_html):
        img = img.replace("http://", "https://")
        if img not in images:
            images.append(img)

    if not title:
        m = re.search(r'<meta property="og:title" content="([^"]*)"', page_html)
        if m:
            title = html.unescape(m.group(1)).strip()

    if not title:
        return None

    return {
        "title": html.unescape(title),
        "cate": html.unescape(cate),
        "link": url,
        "images": " | ".join(images),
    }


def fetch_one(url: str) -> dict[str, str] | None:
    for attempt in range(MAX_RETRIES):
        resp = curl_get(url, timeout=60)
        if resp is not None and resp.status_code == 200:
            row = parse_product_page(resp.text, url)
            if row:
                return row
        if attempt + 1 < MAX_RETRIES:
            time.sleep(RETRY_DELAY * (attempt + 1))
    return None


def main() -> None:
    print(f"[start] {SHOP} workers={WORKERS}", flush=True)
    t0 = time.time()
    urls = fetch_sitemap_urls()
    print(f"[sitemap] product urls={len(urls)}", flush=True)

    rows: list[dict[str, str]] = []
    lock = Lock()
    done = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_one, url): url for url in urls}
        for future in as_completed(futures):
            done += 1
            row = future.result()
            if row:
                with lock:
                    rows.append(row)
            if done == 1 or done == len(urls) or done % 100 == 0:
                print(f"[crawl] {done}/{len(urls)} parsed={len(rows)}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["title", "link", "images", "cate"])
        writer.writeheader()
        writer.writerows(rows)

    elapsed = time.time() - t0
    print(f"[done] rows={len(rows):,} elapsed={elapsed:.1f}s file={OUT}", flush=True)


if __name__ == "__main__":
    main()
