"""Crawl zazzle.com — title, price, link, images. 500 luồng.

- Product sitemap: catalog chính (title/link/ảnh)
- RSS theo category: gắn giá khi trùng product id
- (tuỳ chọn) enrich giá từ trang SP cho bản ghi còn thiếu — giới hạn ENRICH_LIMIT
"""
from __future__ import annotations

import csv
import gzip
import html
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from curl_cffi import requests as creq  # noqa: E402

OUT = ROOT / "output" / "zazzle_products.csv"
WORKERS = 500
# Gzip sitemap ~3–20MB/file — quá nhiều luồng dễ drop connection
SITEMAP_WORKERS = 48
RSS_WORKERS = 80
PS = 100
MAX_RSS_PAGES = 8
# Enrich giá từ HTML product page (0 = tắt)
ENRICH_LIMIT = 0

PRODUCT_INDEXES = [
    "https://www.zazzle.com/sitemap_products_accessories_index.xml.gz",
    "https://www.zazzle.com/sitemap_products_art_index.xml.gz",
    "https://www.zazzle.com/sitemap_products_baby_kids_index.xml.gz",
    "https://www.zazzle.com/sitemap_products_cards_stamps_index.xml.gz",
    "https://www.zazzle.com/sitemap_products_clothing_index.xml.gz",
    "https://www.zazzle.com/sitemap_products_crafts_party_index.xml.gz",
    "https://www.zazzle.com/sitemap_products_electronics_index.xml.gz",
    "https://www.zazzle.com/sitemap_products_home_living_index.xml.gz",
    "https://www.zazzle.com/sitemap_products_office_school_index.xml.gz",
    "https://www.zazzle.com/sitemap_products_sports_games_index.xml.gz",
    "https://www.zazzle.com/sitemap_products_weddings_index.xml.gz",
]

_TLS = threading.local()
_PID_RE = re.compile(r"-(\d{15,})$")
_URL_BLOCK_RE = re.compile(
    rb"<url>\s*<loc>([^<]+)</loc>\s*"
    rb"(?:<image:image>\s*"
    rb"<image:loc>([^<]*)</image:loc>\s*"
    rb"(?:<image:title>([^<]*)</image:title>\s*)?"
    rb"</image:image>\s*)?",
    re.I | re.S,
)
_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_PRICE_META_RE = re.compile(
    r'content="([\d.]+)"\s+property="product:price:amount"',
    re.I,
)
_PRICE_META_RE2 = re.compile(
    r'property="product:price:amount"\s+content="([\d.]+)"',
    re.I,
)


def _session() -> creq.Session:
    s = getattr(_TLS, "sess", None)
    if s is None:
        s = creq.Session(impersonate="chrome131")
        _TLS.sess = s
    return s


def _get_bytes(url: str, timeout: int = 120) -> bytes:
    last_err: Exception | None = None
    for attempt in range(8):
        try:
            # recreate session periodically to avoid sticky bad state
            if attempt and attempt % 3 == 0:
                _TLS.sess = creq.Session(impersonate="chrome131")
            r = _session().get(url, timeout=timeout)
            if r.status_code in (429, 503, 502, 520, 521, 522, 524):
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code != 200:
                time.sleep(0.8 * (attempt + 1))
                continue
            if len(r.content) < 200:
                time.sleep(0.8 * (attempt + 1))
                continue
            return r.content
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"GET fail {url}: {last_err}")


def _maybe_gunzip(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    try:
        return gzip.decompress(data)
    except Exception:
        return data


def _locs(xml_bytes: bytes) -> list[str]:
    return [
        unquote(m.decode("utf-8", "replace")).replace("&amp;", "&")
        for m in re.findall(rb"<loc>([^<]+)</loc>", xml_bytes)
    ]


def product_id_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    m = _PID_RE.search(path)
    return m.group(1) if m else ""


def title_from_slug(url: str) -> str:
    path = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
    path = _PID_RE.sub("", path)
    path = path.replace("+", " ").replace("_", " ").strip()
    path = re.sub(r"\s+", " ", path)
    return path.title() if path else ""


def fetch_child_sitemap_urls(index_url: str) -> list[str]:
    data = _maybe_gunzip(_get_bytes(index_url, timeout=60))
    return _locs(data)


def parse_product_sitemap(sitemap_url: str) -> list[tuple[str, str, str, str]]:
    try:
        body = _maybe_gunzip(_get_bytes(sitemap_url, timeout=120))
    except Exception as e:
        print(f"[sitemap ERR] {sitemap_url[:90]} {e}", flush=True)
        return []
    rows: list[tuple[str, str, str, str]] = []
    for m in _URL_BLOCK_RE.finditer(body):
        link = unquote(m.group(1).decode("utf-8", "replace")).replace("&amp;", "&")
        if "zazzle.com/" not in link:
            continue
        pid = product_id_from_url(link)
        if not pid:
            continue
        img = ""
        if m.group(2):
            img = unquote(m.group(2).decode("utf-8", "replace")).replace("&amp;", "&")
        title = ""
        if m.group(3):
            title = html.unescape(m.group(3).decode("utf-8", "replace")).strip()
        if not title:
            title = title_from_slug(link)
        rows.append((pid, title, link, img))
    return rows


def collect_category_seeds() -> list[str]:
    seeds: set[str] = set()
    text = _get_bytes("https://www.zazzle.com/sitemap", timeout=40).decode(
        "utf-8", "replace"
    )
    for c in re.findall(r'href="https://www\.zazzle\.com/c/([^"?#]+)"', text):
        seeds.add(unquote(c).replace("+", " ").strip())
    for q in (
        "tshirt",
        "hoodie",
        "mug",
        "poster",
        "wedding invitation",
        "business card",
        "sticker",
        "phone case",
        "pillow",
        "tote bag",
        "canvas print",
        "greeting card",
        "notebook",
        "hat",
        "socks",
        "blanket",
        "calendar",
        "mousepad",
        "water bottle",
        "ornament",
    ):
        seeds.add(q)
    return sorted(s for s in seeds if s)


def parse_rss_items(xml_text: str) -> list[dict]:
    rows: list[dict] = []
    for block in _ITEM_RE.findall(xml_text):
        link_m = re.search(r"<link>([^<]+)</link>", block, re.I)
        title_m = re.search(r"<title>([^<]+)</title>", block, re.I)
        if not link_m:
            continue
        link = html.unescape(link_m.group(1).strip())
        pid = product_id_from_url(link)
        if not pid:
            continue
        title = html.unescape(title_m.group(1).strip()) if title_m else title_from_slug(link)
        desc_m = re.search(r"<description>(.*?)</description>", block, re.I | re.S)
        desc = html.unescape(desc_m.group(1)) if desc_m else ""
        desc_txt = _TAG_RE.sub(" ", desc)
        price_m = re.search(r"\$[\d,]+(?:\.\d{2})?", desc_txt)
        price = price_m.group(0) if price_m else ""
        img = ""
        media_m = re.search(r'<media:content[^>]+url="([^"]+)"', block, re.I)
        if media_m:
            img = media_m.group(1)
        else:
            thumb_m = re.search(r'<media:thumbnail[^>]+url="([^"]+)"', block, re.I)
            if thumb_m:
                img = thumb_m.group(1)
        rows.append(
            {"id": pid, "title": title, "price": price, "link": link, "images": img}
        )
    return rows


def fetch_rss_page(qs: str, pg: int) -> list[dict]:
    url = f"https://feed.zazzle.com/rss?qs={quote_plus(qs)}&ps={PS}&pg={pg}&isz=huge"
    for attempt in range(3):
        try:
            r = _session().get(url, timeout=40)
            if r.status_code in (429, 503):
                time.sleep(1.2 * (attempt + 1))
                continue
            if r.status_code != 200:
                time.sleep(0.4 * (attempt + 1))
                continue
            return parse_rss_items(r.text)
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return []


def crawl_rss_prices(seeds: list[str]) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    lock = threading.Lock()
    done = 0

    def handle(qs: str, pg: int) -> tuple[str, int, list[dict]]:
        return qs, pg, fetch_rss_page(qs, pg)

    print(f"[rss] seeds={len(seeds)} workers={RSS_WORKERS}", flush=True)
    with ThreadPoolExecutor(max_workers=RSS_WORKERS) as ex:
        pending: list[tuple[str, int]] = [(q, 1) for q in seeds]
        while pending:
            wave = pending
            pending = []
            futs = [ex.submit(handle, qs, pg) for qs, pg in wave]
            for fut in as_completed(futs):
                qs, pg, items = fut.result()
                done += 1
                if done % 100 == 0:
                    with lock:
                        n = len(by_id)
                    print(f"[rss] jobs={done} unique={n}", flush=True)
                if not items:
                    continue
                with lock:
                    for it in items:
                        old = by_id.get(it["id"])
                        if not old:
                            by_id[it["id"]] = it
                        else:
                            if not old.get("price") and it.get("price"):
                                old["price"] = it["price"]
                            if it.get("images") and not old.get("images"):
                                old["images"] = it["images"]
                if len(items) >= max(1, PS // 2) and pg < MAX_RSS_PAGES:
                    pending.append((qs, pg + 1))
    print(f"[rss] done jobs={done} unique={len(by_id)}", flush=True)
    return by_id


def crawl_product_shards() -> list[str]:
    child: list[str] = []
    with ThreadPoolExecutor(max_workers=min(32, len(PRODUCT_INDEXES))) as ex:
        futs = {ex.submit(fetch_child_sitemap_urls, u): u for u in PRODUCT_INDEXES}
        for fut in as_completed(futs):
            try:
                part = fut.result()
                child.extend(part)
                print(f"[idx] {futs[fut].split('/')[-1]} -> {len(part)}", flush=True)
            except Exception as e:
                print(f"[idx ERR] {futs[fut]} {e}", flush=True)
    seen: set[str] = set()
    out: list[str] = []
    for u in child:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_product_price(link: str) -> str:
    for attempt in range(3):
        try:
            r = _session().get(link, timeout=35)
            if r.status_code in (429, 503):
                time.sleep(1.0 * (attempt + 1))
                continue
            if r.status_code != 200:
                time.sleep(0.3 * (attempt + 1))
                continue
            m = _PRICE_META_RE.search(r.text) or _PRICE_META_RE2.search(r.text)
            if m:
                return f"${m.group(1)}"
            m2 = re.search(r'"price"\s*:\s*"?([\d.]+)"?', r.text)
            if m2:
                return f"${m2.group(1)}"
            return ""
        except Exception:
            time.sleep(0.4 * (attempt + 1))
    return ""


def enrich_prices_in_csv(path: Path, limit: int) -> int:
    if limit <= 0:
        return 0
    print(f"[enrich] loading CSV for up to {limit} missing prices", flush=True)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    targets = [(i, r) for i, r in enumerate(rows) if not (r.get("price") or "").strip()]
    targets = targets[:limit]
    print(f"[enrich] targets={len(targets)} workers={WORKERS}", flush=True)
    done = 0
    filled = 0
    lock = threading.Lock()

    def one(item: tuple[int, dict]) -> tuple[int, str]:
        idx, row = item
        return idx, fetch_product_price(row.get("link") or "")

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(one, t) for t in targets]
        for fut in as_completed(futs):
            idx, price = fut.result()
            with lock:
                done += 1
                if price:
                    rows[idx]["price"] = price
                    filled += 1
                if done % 1000 == 0 or done == len(targets):
                    print(f"[enrich] {done}/{len(targets)} filled={filled}", flush=True)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["title", "price", "link", "images"])
        w.writeheader()
        w.writerows(rows)
    return filled


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print(f"[start] zazzle workers={WORKERS} out={OUT}", flush=True)

    print("[phase1] category RSS prices", flush=True)
    seeds = collect_category_seeds()
    print(f"[seeds] {len(seeds)}", flush=True)
    rss_by_id = crawl_rss_prices(seeds)

    print("[phase2] product sitemaps", flush=True)
    shards = crawl_product_shards()
    print(f"[shards] {len(shards)}", flush=True)

    written = 0
    priced = 0
    seen: set[str] = set()
    write_lock = threading.Lock()

    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["title", "price", "link", "images"])
        w.writeheader()

        def flush_rows(rows: list[tuple[str, str, str, str]]) -> tuple[int, int]:
            local_w = 0
            local_p = 0
            with write_lock:
                for pid, title, link, img in rows:
                    if pid in seen:
                        continue
                    seen.add(pid)
                    rss = rss_by_id.pop(pid, None)
                    price = (rss or {}).get("price") or ""
                    if rss:
                        if len(rss.get("title") or "") > len(title or ""):
                            title = rss["title"]
                        if rss.get("images") and not img:
                            img = rss["images"]
                    w.writerow(
                        {
                            "title": title or "",
                            "price": price,
                            "link": link or "",
                            "images": img or "",
                        }
                    )
                    local_w += 1
                    if price:
                        local_p += 1
            return local_w, local_p

        parsed = 0
        failed: list[str] = []
        with ThreadPoolExecutor(max_workers=min(SITEMAP_WORKERS, len(shards))) as ex:
            futs = {ex.submit(parse_product_sitemap, u): u for u in shards}
            for fut in as_completed(futs):
                url = futs[fut]
                rows = fut.result()
                if not rows:
                    failed.append(url)
                nw, np = flush_rows(rows)
                written += nw
                priced += np
                parsed += 1
                if parsed % 5 == 0 or parsed == len(shards):
                    print(
                        f"[sitemap] {parsed}/{len(shards)} written={written} "
                        f"priced={priced} failed={len(failed)}",
                        flush=True,
                    )

        if failed:
            print(f"[retry] {len(failed)} failed shards", flush=True)
            with ThreadPoolExecutor(max_workers=min(16, len(failed))) as ex:
                futs = {ex.submit(parse_product_sitemap, u): u for u in failed}
                for fut in as_completed(futs):
                    rows = fut.result()
                    nw, np = flush_rows(rows)
                    written += nw
                    priced += np
                    print(
                        f"[retry] +{nw} total={written} ok={bool(rows)} "
                        f"{futs[fut][-60:]}",
                        flush=True,
                    )

        leftover = 0
        for pid, row in rss_by_id.items():
            if pid in seen:
                continue
            seen.add(pid)
            w.writerow(
                {
                    "title": row.get("title") or "",
                    "price": row.get("price") or "",
                    "link": row.get("link") or "",
                    "images": row.get("images") or "",
                }
            )
            written += 1
            leftover += 1
            if row.get("price"):
                priced += 1
        print(f"[rss-only] appended={leftover}", flush=True)

    print(
        f"[sitemap done] rows={written} priced={priced} elapsed={time.time() - t0:.1f}s",
        flush=True,
    )

    # Enrich: load CSV needs RAM — skip full reload if file huge (>2M rows)
    if ENRICH_LIMIT > 0 and written <= 2_000_000:
        filled = enrich_prices_in_csv(OUT, ENRICH_LIMIT)
        priced += filled
    elif ENRICH_LIMIT > 0:
        print(
            f"[enrich] skip full reload (rows={written}>2M). "
            f"CSV đã có title/link/images; giá chủ yếu từ RSS.",
            flush=True,
        )

    print(
        f"[done] rows={written} priced~={priced} elapsed={time.time() - t0:.1f}s file={OUT}",
        flush=True,
    )


if __name__ == "__main__":
    main()
