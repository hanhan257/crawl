"""Crawl mainelyurns.com (BigCommerce) via sitemap + HTML -> CSV."""
from __future__ import annotations

import csv
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from curl_cffi import requests as curl_requests  # noqa: E402

SHOP = "https://mainelyurns.com"
OUT = ROOT / "output" / "mainelyurns_products.csv"
IMPERSONATE = "safari17_0"
WORKERS = 12
MAX_RETRIES = 3
RETRY_DELAY = 0.35
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def fetch_sitemap_urls() -> list[str]:
    urls: list[str] = []
    page = 1
    while True:
        resp = curl_requests.get(
            f"{SHOP}/xmlsitemap.php?type=products&page={page}",
            impersonate=IMPERSONATE,
            timeout=60,
        )
        if resp.status_code != 200:
            break
        root = ET.fromstring(resp.text)
        batch = [
            (el.text or "").strip()
            for el in root.findall(".//sm:loc", SITEMAP_NS)
            if (el.text or "").strip().endswith(".html")
        ]
        if not batch:
            break
        urls.extend(batch)
        page += 1
    return list(dict.fromkeys(urls))


def parse_product(html: str, url: str) -> dict[str, str] | None:
    title = ""
    cate = ""
    images: list[str] = []

    og = re.search(r'property="og:title"\s+content="([^"]+)"', html, re.I)
    if og:
        title = unescape(og.group(1)).strip()

    for match in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "Product":
                title = str(item.get("name") or title).strip()
                img = item.get("image")
                if isinstance(img, str) and img.strip():
                    images.append(img.strip())
                elif isinstance(img, list):
                    images.extend(str(x).strip() for x in img if str(x).strip())
            if item.get("@type") == "BreadcrumbList":
                elems = item.get("itemListElement") or []
                names = [
                    str(e.get("name") or "").strip()
                    for e in elems
                    if isinstance(e, dict) and str(e.get("name") or "").strip()
                ]
                if len(names) >= 2:
                    cate = names[-2]

    product_id = ""
    id_match = re.search(r"/products/(\d+)/", html)
    if id_match:
        product_id = id_match.group(1)

    for img in re.findall(r'data-zoom-image="([^"]+)"', html, re.I):
        img = img.strip()
        if not img:
            continue
        if product_id and f"/products/{product_id}/" not in img:
            continue
        if img not in images:
            images.append(img)

    if not title:
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        if h1:
            title = unescape(re.sub(r"<[^>]+>", " ", h1.group(1))).strip()

    if not title:
        return None

    return {
        "title": title,
        "cate": cate,
        "link": url,
        "images": " | ".join(images),
    }


def fetch_one(url: str) -> dict[str, str] | None:
    for attempt in range(MAX_RETRIES):
        try:
            resp = curl_requests.get(url, impersonate=IMPERSONATE, timeout=60)
            if resp.status_code == 200:
                row = parse_product(resp.text, url)
                if row:
                    return row
        except Exception:
            pass
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
            if done == 1 or done == len(urls) or done % 200 == 0:
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
