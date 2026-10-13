"""Crawl pawsionate.com (Shopify products.json via curl_cffi) -> CSV."""
from __future__ import annotations

import csv
import html
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.curl_http import curl_get  # noqa: E402

SHOP = "https://pawsionate.com"
OUT = ROOT / "output" / "pawsionate_products.csv"
PER_PAGE = 250


def image_urls(product: dict) -> list[str]:
    urls: list[str] = []
    for img in product.get("images") or []:
        if isinstance(img, dict):
            src = str(img.get("src") or "").strip()
            if src:
                urls.append(src)
    featured = product.get("image")
    if isinstance(featured, dict):
        src = str(featured.get("src") or "").strip()
        if src and src not in urls:
            urls.insert(0, src)
    return urls


def fetch_page(page: int) -> list[dict]:
    resp = curl_get(
        f"{SHOP}/products.json",
        params={"limit": PER_PAGE, "page": page},
        timeout=60,
    )
    if resp is None or resp.status_code != 200:
        return []
    batch = resp.json().get("products") or []
    return batch if isinstance(batch, list) else []


def main() -> None:
    print(f"[start] {SHOP} (curl_cffi)", flush=True)
    t0 = time.time()
    all_products: list[dict] = []
    page = 1

    while True:
        batch = fetch_page(page)
        if not batch:
            break
        all_products.extend(batch)
        print(f"[crawl] page {page} batch={len(batch)} total={len(all_products)}", flush=True)
        if len(batch) < PER_PAGE:
            break
        page += 1
        time.sleep(0.12)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "link", "images", "cate"]
        )
        writer.writeheader()
        for p in all_products:
            handle = str(p.get("handle") or "").strip()
            link = urljoin(SHOP + "/", f"products/{handle}") if handle else ""
            variants = p.get("variants") or []
            price = ""
            if variants and isinstance(variants[0], dict):
                price = str(variants[0].get("price") or "").strip()
            product_type = str(p.get("product_type") or "").strip()
            tags = p.get("tags")
            if isinstance(tags, str):
                cate = product_type or (tags.split(",")[0].strip() if tags else "")
            else:
                cate = product_type
            writer.writerow(
                {
                    "title": html.unescape(str(p.get("title") or "")),
                    "price": price,
                    "link": link,
                    "images": " | ".join(image_urls(p)),
                    "cate": html.unescape(cate),
                }
            )

    elapsed = time.time() - t0
    print(f"[done] rows={len(all_products):,} elapsed={elapsed:.1f}s file={OUT}", flush=True)


if __name__ == "__main__":
    main()
