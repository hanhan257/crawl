"""Crawl ipaintings.com — title, ảnh, giá, link (sitemap products + HTML)."""
from __future__ import annotations

import csv
import html as html_lib
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

from utils.http_session import (  # noqa: E402
    DEFAULT_USER_AGENT,
    create_http_session,
    parse_proxy_list,
)

SHOP = "https://ipaintings.com"
OUT = ROOT / "output" / "ipaintings_products.csv"
WORKERS = 80
PRODUCT_SITEMAPS = [f"{SHOP}/xmlsitemap/products{i}" for i in range(1, 12)]


def _load_proxy() -> str:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    if isinstance(cfg.get("proxy"), dict):
        return str(cfg["proxy"].get("url") or "")
    return str(cfg.get("proxy") or "")


def _session(proxy: str | None):
    sess = create_http_session(proxy)
    sess.headers["User-Agent"] = DEFAULT_USER_AGENT
    return sess


def collect_product_urls(proxies: list[str]) -> list[str]:
    sess = _session(proxies[0] if proxies else None)
    urls: list[str] = []
    seen: set[str] = set()
    for idx, sm in enumerate(PRODUCT_SITEMAPS):
        for attempt in range(3):
            try:
                pxy = proxies[idx % len(proxies)] if proxies else None
                s = _session(pxy)
                text = s.get(sm, timeout=60).text
                locs = re.findall(r"<loc>([^<]+)</loc>", text)
                for loc in locs:
                    loc = loc.strip()
                    if not loc or loc in seen:
                        continue
                    # bỏ trang danh mục / artist listing nếu lọt
                    if "/xmlsitemap/" in loc:
                        continue
                    seen.add(loc)
                    urls.append(loc)
                print(f"[sitemap] {sm.split('/')[-1]} +{len(locs)} total={len(urls)}", flush=True)
                break
            except Exception as exc:
                print(f"[sitemap] fail {sm}: {exc}", flush=True)
                time.sleep(0.8 * (attempt + 1))
    return urls


def _clean_title(title: str) -> str:
    title = html_lib.unescape(title or "").strip()
    for suffix in (
        " | iPaintings.com",
        " - iPaintings.com",
        " | iPaintings",
        " - iPaintings",
    ):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    return title


def parse_product(url: str, page_html: str) -> dict[str, str] | None:
    title_m = re.search(
        r'property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        page_html,
        re.I,
    )
    if not title_m:
        title_m = re.search(r"<title>([^<]+)</title>", page_html, re.I)
    title = _clean_title(title_m.group(1) if title_m else "")
    if not title:
        return None

    price = ""
    for pat in (
        r'itemprop=["\']price["\']\s+content=["\']([^"\']+)["\']',
        r'property=["\']product:price:amount["\']\s+content=["\']([^"\']+)["\']',
        r'"price"\s*:\s*"?(?P<p>[0-9]+(?:\.[0-9]+)?)"?',
        r'data-price-amount=["\']([^"\']+)["\']',
    ):
        m = re.search(pat, page_html, re.I)
        if m:
            price = (m.groupdict().get("p") if m.groupdict() else m.group(1)) or ""
            price = str(price).replace(",", "").strip()
            try:
                price = f"{float(price):.2f}"
            except ValueError:
                pass
            if price and price not in {"0", "0.00"}:
                break
            price = ""

    images: list[str] = []
    og = re.search(
        r'property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
        page_html,
        re.I,
    )
    if og:
        images.append(og.group(1).strip())

    for img in re.findall(
        r'(https://ipaintings\.com/images/[^"\'\s>]+)',
        page_html,
        re.I,
    ):
        img = unquote(img.split("?")[0].rstrip("\\"))
        if img not in images:
            images.append(img)
        if len(images) >= 8:
            break

    return {
        "title": title,
        "price": price,
        "link": url,
        "images": ",".join(images),
    }


def fetch_one(url: str, proxy: str | None) -> dict[str, str] | None:
    for attempt in range(3):
        try:
            sess = _session(proxy)
            resp = sess.get(url, timeout=45)
            if resp.status_code in (429, 503):
                time.sleep(1.2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                time.sleep(0.4 * (attempt + 1))
                continue
            return parse_product(url, resp.text)
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return None


def main() -> None:
    proxy = _load_proxy()
    proxies = parse_proxy_list(proxy)
    print(f"[start] {SHOP} workers={WORKERS} proxies={len(proxies)}", flush=True)
    t0 = time.time()

    urls = collect_product_urls(proxies)
    print(f"[urls] {len(urls)}", flush=True)
    if not urls:
        print("[done] no urls", flush=True)
        return

    rows: list[dict[str, str]] = []
    failed = 0

    def one(i: int, url: str) -> dict[str, str] | None:
        pxy = proxies[i % len(proxies)] if proxies else None
        return fetch_one(url, pxy)

    with ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(urls)))) as ex:
        futs = [ex.submit(one, i, u) for i, u in enumerate(urls)]
        done = 0
        for fut in as_completed(futs):
            row = fut.result()
            done += 1
            if row:
                rows.append(row)
            else:
                failed += 1
            if done % 200 == 0 or done == len(urls):
                print(
                    f"[crawl] {done}/{len(urls)} ok={len(rows)} fail={failed}",
                    flush=True,
                )

    # dedupe by link
    by_link: dict[str, dict[str, str]] = {}
    for row in rows:
        by_link[row["link"]] = row
    rows = list(by_link.values())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "link", "images"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"[done] {len(rows):,} rows fail={failed} in {time.time()-t0:.1f}s -> {OUT}",
        flush=True,
    )


if __name__ == "__main__":
    main()
