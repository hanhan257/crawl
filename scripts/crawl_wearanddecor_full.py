"""Crawl toàn bộ wearanddecor.com — sitemap (~158k SP) + ShopBase detail API."""
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

from curl_cffi import requests as creq  # noqa: E402

from crawlers.shopbase import ShopBaseCrawler  # noqa: E402
from utils.http_session import DEFAULT_USER_AGENT, create_http_session, parse_proxy_list  # noqa: E402

SHOP = "https://wearanddecor.com"
OUT = ROOT / "output" / "wearanddecor_products_full.csv"
SITEMAP_CACHE = ROOT / "output" / "wearanddecor_sitemap_handles.jsonl"
SITEMAP_WORKERS = 6
DETAIL_WORKERS = 500
DETAIL_BATCH = 5000

PRODUCT_RE = re.compile(r"/products/([^/?#]+)", re.I)
IMG_BLOCK_RE = re.compile(
    r"<loc>\s*(https?://[^<\s]+/products/[^<\s]+)\s*</loc>\s*"
    r"(?:<[^>]+>\s*)*<image:loc>\s*([^<\s]+)\s*</image:loc>\s*"
    r"(?:<[^>]+>\s*)*<image:title>\s*([^<]*?)\s*</image:title>",
    re.I | re.S,
)
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
SITEMAP_PRODUCT_RE = re.compile(r"sitemap_product", re.I)

HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/xml,text/xml,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def load_working_proxy() -> str:
    cfg_path = ROOT / "config.json"
    proxy_text = ""
    if cfg_path.is_file():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if isinstance(cfg.get("proxy"), dict):
            proxy_text = str(cfg["proxy"].get("url") or "")
        else:
            proxy_text = str(cfg.get("proxy") or "")
    proxies = parse_proxy_list(proxy_text)
    # Proxy đã test OK cho wearanddecor (index 34).
    preferred = "http://b8c97e4cea:zBTeEa4M@69.33.240.19:4444"
    if preferred in proxies:
        return preferred
    for proxy in proxies:
        try:
            r = creq.get(
                f"{SHOP}/api/catalog/next/products.json",
                params={"limit": 1, "page": 1},
                timeout=20,
                impersonate="chrome120",
                headers={**HEADERS, "Accept": "application/json"},
                proxies={"http": proxy, "https": proxy},
            )
            if (r.text or "").strip().startswith("{"):
                return proxy
        except Exception:
            continue
    return preferred


def fetch_url(url: str, proxy: str, *, accept_json: bool = False) -> str:
    url = html.unescape(url)
    headers = dict(HEADERS)
    if accept_json:
        headers["Accept"] = "application/json"
    proxies = {"http": proxy, "https": proxy}
    last_text = ""
    for attempt in range(6):
        try:
            resp = creq.get(
                url,
                timeout=60,
                headers=headers,
                impersonate="chrome120",
                proxies=proxies,
            )
            if resp.status_code == 429:
                time.sleep(min(3.0 * (attempt + 1), 20.0))
                continue
            if resp.status_code != 200:
                time.sleep(min(2.0 * (attempt + 1), 12.0))
                continue
            text = resp.text or ""
            last_text = text
            if "Security Checking" in text:
                time.sleep(min(3.0 * (attempt + 1), 20.0))
                continue
            if accept_json:
                if text.strip().startswith("{"):
                    return text
            elif any(
                marker in text.lower()
                for marker in ("<urlset", "<sitemapindex", "sitemap_product", "/products/")
            ):
                return text
            time.sleep(min(2.0 * (attempt + 1), 12.0))
        except Exception:
            time.sleep(min(2.0 * (attempt + 1), 12.0))
    raise RuntimeError(f"Không tải được {url} (body={last_text[:80]!r})")


def parse_sitemap_xml(xml: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in IMG_BLOCK_RE.finditer(xml):
        link, img, title = match.group(1), match.group(2), match.group(3)
        handle_m = PRODUCT_RE.search(link)
        if not handle_m:
            continue
        handle = unquote(handle_m.group(1)).strip()
        if not handle or handle in seen:
            continue
        seen.add(handle)
        entries.append(
            {
                "handle": handle,
                "link": link.strip(),
                "title": html.unescape(title.strip()),
                "images": img.strip(),
            }
        )
    if entries:
        return entries
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
    return entries


def collect_product_sitemap_urls(proxy: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for root in (f"{SHOP}/sitemap.xml", f"{SHOP}/sitemap_products.xml"):
        xml = fetch_url(root, proxy)
        locs = LOC_RE.findall(xml)
        if not locs:
            continue
        if any("/products/" in loc for loc in locs):
            return [root]
        for loc in locs:
            clean = html.unescape(loc.strip())
            if SITEMAP_PRODUCT_RE.search(clean) and clean not in seen:
                seen.add(clean)
                found.append(clean)
        if found:
            return found
    return found


def collect_sitemap_entries(proxy: str) -> list[dict[str, str]]:
    sitemap_urls = collect_product_sitemap_urls(proxy)
    print(f"[sitemap] files={len(sitemap_urls)}", flush=True)
    by_handle: dict[str, dict[str, str]] = {}
    t0 = time.time()
    failed_urls: list[str] = []

    def fetch_one(url: str) -> list[dict[str, str]]:
        return parse_sitemap_xml(fetch_url(url, proxy))

    done = 0
    with ThreadPoolExecutor(max_workers=SITEMAP_WORKERS) as executor:
        futures = {executor.submit(fetch_one, url): url for url in sitemap_urls}
        for future in as_completed(futures):
            url = futures[future]
            done += 1
            try:
                batch = future.result()
            except Exception:
                failed_urls.append(url)
                batch = []
            for entry in batch:
                by_handle.setdefault(entry["handle"], entry)
            if done % 50 == 0 or done == len(sitemap_urls):
                print(
                    f"[sitemap] {done}/{len(sitemap_urls)} unique={len(by_handle)} "
                    f"failed={len(failed_urls)}",
                    flush=True,
                )

    if failed_urls:
        print(f"[sitemap] retry {len(failed_urls)} files...", flush=True)
        for index, url in enumerate(failed_urls, start=1):
            try:
                for entry in fetch_one(url):
                    by_handle.setdefault(entry["handle"], entry)
            except Exception:
                pass
            if index % 25 == 0 or index == len(failed_urls):
                print(
                    f"[sitemap] retry {index}/{len(failed_urls)} unique={len(by_handle)}",
                    flush=True,
                )
            time.sleep(0.3)

    entries = list(by_handle.values())
    print(f"[sitemap] total={len(entries)} elapsed={time.time()-t0:.1f}s", flush=True)
    SITEMAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with SITEMAP_CACHE.open("w", encoding="utf-8") as cache_fh:
        for entry in entries:
            cache_fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[sitemap] cached -> {SITEMAP_CACHE}", flush=True)
    return entries


def load_sitemap_cache() -> list[dict[str, str]] | None:
    if not SITEMAP_CACHE.is_file():
        return None
    entries: list[dict[str, str]] = []
    with SITEMAP_CACHE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries or None


def fetch_detail_row(crawler: ShopBaseCrawler, entry: dict[str, str]) -> dict[str, str]:
    product = crawler._product_from_detail_api(entry["handle"])
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
            "link": product.url or entry["link"],
            "images": " | ".join(images),
            "cate": ", ".join(cats),
        }
    return {
        "title": entry.get("title") or "",
        "price": "",
        "link": entry["link"],
        "images": entry.get("images") or "",
        "cate": "",
    }


def load_done_links() -> set[str]:
    if not OUT.is_file():
        return set()
    done: set[str] = set()
    with OUT.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            link = (row.get("link") or "").strip().rstrip("/")
            if link:
                done.add(link)
    return done


def main() -> None:
    proxy = load_working_proxy()
    print(f"[start] {SHOP} proxy={proxy.split('@')[-1]} detail_workers={DETAIL_WORKERS}", flush=True)

    crawler = ShopBaseCrawler(site_url=SHOP, proxy_url=None)
    crawler._catalog_session = create_http_session(proxy)
    crawler._catalog_session.headers.update(HEADERS)

    t0 = time.time()
    cached = load_sitemap_cache()
    if cached:
        entries = cached
        print(f"[sitemap] loaded cache {len(entries)} handles", flush=True)
    else:
        entries = collect_sitemap_entries(proxy)
    total = len(entries)
    done_links = load_done_links()
    if done_links:
        entries = [e for e in entries if e["link"].rstrip("/") not in done_links]
        print(
            f"[resume] skip {len(done_links)} done, remaining {len(entries)}/{total}",
            flush=True,
        )
    total_remaining = len(entries)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows_written = len(done_links)
    ok_price = 0
    if done_links and OUT.is_file():
        with OUT.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            ok_price = sum(1 for row in reader if (row.get("price") or "").strip())

    file_mode = "a" if done_links else "w"
    with OUT.open(file_mode, encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "link", "images", "cate"]
        )
        if not done_links:
            writer.writeheader()

        if not entries:
            print(f"[done] already complete rows={rows_written:,} file={OUT}", flush=True)
            return

        for batch_start in range(0, total_remaining, DETAIL_BATCH):
            batch = entries[batch_start : batch_start + DETAIL_BATCH]
            with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
                futures = [executor.submit(fetch_detail_row, crawler, e) for e in batch]
                for future in as_completed(futures):
                    row = future.result()
                    writer.writerow(row)
                    rows_written += 1
                    if row.get("price"):
                        ok_price += 1
            print(
                f"[crawl] {rows_written}/{total} price_ok={ok_price} "
                f"elapsed={time.time()-t0:.1f}s",
                flush=True,
            )

    print(
        f"[done] rows={rows_written:,} file={OUT} elapsed={time.time()-t0:.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
