"""Match titles from output/search1.txt against wearandhome.com catalog."""
from __future__ import annotations

import csv
import html
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopbase import CATALOG_LIST_API, ShopBaseCrawler  # noqa: E402

SHOP = "https://wearandhome.com"
SEARCH_FILE = ROOT / "output" / "search1.txt"
CATALOG_CSV = ROOT / "output" / "wearandhome_products.csv"
OUT_MATCHED = ROOT / "output" / "wearandhome_from_search1.csv"
OUT_CLEAN = ROOT / "output" / "wearandhome_from_search1_out.csv"
OUT_REPORT = ROOT / "output" / "wearandhome_search1_report.txt"
WORKERS = 32


def normalize_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("…", "...").replace("–", "-").replace("—", "-")
    text = re.sub(r"\.{2,}$", "", text.strip())
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def base_title(text: str) -> str:
    t = normalize_key(text)
    t = re.sub(r"\s*-\s*wearandbeyond\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*-\s*thw\s*$", "", t, flags=re.I)
    t = re.sub(
        r",?\s*(xs|s|m|l|xl|xxl|2xl|3xl|4xl|5xl)\s*/\s*[^,]+$",
        "",
        t,
    )
    t = re.sub(r",?\s*/\s*[^,]+$", "", t)
    return t.strip(" ,/")


def load_search_titles(path: Path) -> list[tuple[int, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    try:
        col = header.index("Sản phẩm")
    except ValueError:
        col = 4
    rows: list[tuple[int, str]] = []
    for line_no, raw in enumerate(lines[1:], start=2):
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if col < len(parts):
            title = parts[col].strip()
            if title:
                rows.append((line_no, title))
    return rows


def format_images(urls: list[str]) -> str:
    return " | ".join(u.strip() for u in urls if u.strip())


def build_catalog_index(items: list[dict]) -> tuple[list[dict], dict[str, list[int]], dict[str, int]]:
    indexed: list[dict] = []
    by_base: dict[str, list[int]] = {}
    by_exact: dict[str, int] = {}
    for i, item in enumerate(items):
        title = html.unescape(str(item.get("title") or "").strip())
        key = base_title(title)
        exact = normalize_key(title)
        indexed.append({"title": title, "base": key, "item": item})
        by_base.setdefault(key, []).append(i)
        by_exact[exact] = i
    return indexed, by_base, by_exact


def match_in_index(
    query: str,
    indexed: list[dict],
    by_base: dict[str, list[int]],
    by_exact: dict[str, int],
) -> tuple[dict | None, float, str]:
    q_exact = normalize_key(query)
    if q_exact in by_exact:
        return indexed[by_exact[q_exact]], 1.0, "exact"

    q_base = base_title(query)
    if q_base in by_base:
        return indexed[by_base[q_base][0]], 1.0, "base_exact"

    return None, 0.0, "unmatched"


def search_api_exact(crawler: ShopBaseCrawler, query: str) -> dict | None:
    words = query.split()
    queries = [query]
    if len(words) > 8:
        queries.append(" ".join(words[:8]))
    if len(words) > 5:
        queries.append(" ".join(words[:5]))

    seen: set[str] = set()
    target = normalize_key(query)
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        try:
            resp = crawler._catalog_session.get(
                f"{SHOP}{CATALOG_LIST_API}",
                params={"limit": 50, "page": 1, "q": q},
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            items = (resp.json().get("result") or {}).get("items") or []
            for item in items:
                if normalize_key(str(item.get("title") or "")) == target:
                    return item
        except Exception:
            continue
    return None


def product_row(
    product,
    source_title: str,
    line_no: int,
    score: float,
    method: str,
) -> dict[str, str]:
    cats = [html.unescape(c) for c in (product.categories or []) if c]
    if not cats and product.product_type:
        cats = [html.unescape(str(product.product_type))]
    cate = ", ".join(cats)
    if cate == html.unescape(product.title or ""):
        cate = html.unescape(str(product.product_type or ""))
    return {
        "line_no": str(line_no),
        "title": html.unescape(product.title or ""),
        "images": format_images(product.image_urls or []),
        "link": product.url or "",
        "price": f"{product.price:.2f}" if product.price is not None else "",
        "cate": cate,
        "des": html.unescape(product.description or ""),
        "match_score": f"{score:.0%}",
        "match_method": method,
        "source_title": source_title,
    }


def empty_row(source_title: str, line_no: int) -> dict[str, str]:
    return {
        "line_no": str(line_no),
        "title": "",
        "images": "",
        "link": "",
        "price": "",
        "cate": "",
        "des": "",
        "match_score": "",
        "match_method": "unmatched",
        "source_title": source_title,
    }


def main() -> None:
    t0 = time.time()
    search_rows = load_search_titles(SEARCH_FILE)
    if not search_rows:
        raise SystemExit(f"Khong co title trong {SEARCH_FILE}")

    crawler = ShopBaseCrawler(site_url=SHOP, proxy_url=None)
    print(f"[catalog] listing {SHOP} ...", flush=True)
    items = crawler._list_catalog_items(
        max_pages=None,
        limit=None,
        max_workers=WORKERS,
    )
    print(f"[catalog] raw_items={len(items)}", flush=True)

    indexed, by_base, by_exact = build_catalog_index(items)
    match_cache: dict[str, tuple[dict | None, float, str]] = {}
    matched_items: dict[str, dict] = {}
    method_counts: Counter[str] = Counter()

    for _line_no, raw_title in search_rows:
        cache_key = base_title(raw_title)
        if cache_key not in match_cache:
            hit, score, method = match_in_index(
                raw_title, indexed, by_base, by_exact
            )
            if hit is None:
                api_item = search_api_exact(crawler, raw_title)
                if api_item:
                    hit = {"title": api_item.get("title", ""), "item": api_item}
                    score, method = 1.0, "api_search"
            match_cache[cache_key] = (hit, score, method)

        hit, _score, method = match_cache[cache_key]
        if hit is None:
            method_counts["unmatched"] += 1
            continue
        handle = str(hit["item"].get("handle") or "").strip()
        if handle:
            matched_items[handle] = hit["item"]
            method_counts[method] += 1

    products_by_handle: dict[str, object] = {}
    for handle, item in matched_items.items():
        product = crawler._map_catalog_item(item, skip_page_fallback=True)
        if product and product.url:
            products_by_handle[handle] = product

    if products_by_handle:
        print(
            f"[des] fetching descriptions for {len(products_by_handle)} SP ...",
            flush=True,
        )
        crawler._enrich_descriptions_parallel(
            list(products_by_handle.values()),
            max_workers=WORKERS,
        )

    final_rows: list[dict[str, str]] = []
    unmatched: list[str] = []
    for line_no, raw_title in search_rows:
        cache_key = base_title(raw_title)
        hit, score, method = match_cache[cache_key]
        if hit is None:
            unmatched.append(raw_title)
            final_rows.append(empty_row(raw_title, line_no))
            continue
        handle = str(hit["item"].get("handle") or "").strip()
        product = products_by_handle.get(handle)
        if not product:
            unmatched.append(raw_title)
            final_rows.append(empty_row(raw_title, line_no))
            continue
        final_rows.append(product_row(product, raw_title, line_no, score, method))

    OUT_MATCHED.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "line_no",
        "title",
        "images",
        "link",
        "price",
        "cate",
        "des",
        "match_score",
        "match_method",
        "source_title",
    ]
    with OUT_MATCHED.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(final_rows)

    hits = sum(1 for r in final_rows if r.get("link"))
    with OUT_CLEAN.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "images", "link", "price", "cate", "des"]
        )
        writer.writeheader()
        for row in final_rows:
            if row.get("link"):
                writer.writerow(
                    {
                        "title": row["title"],
                        "images": row["images"],
                        "link": row["link"],
                        "price": row["price"],
                        "cate": row["cate"],
                        "des": row["des"],
                    }
                )

    by_handle: dict[str, object] = {}
    for item in items:
        product = crawler._map_catalog_item(item, skip_page_fallback=True)
        if not product or not product.url:
            continue
        handle = product.url.rstrip("/").rsplit("/", 1)[-1]
        by_handle[handle] = product

    with CATALOG_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "price", "link", "images", "cate"]
        )
        writer.writeheader()
        for product in by_handle.values():
            cats = [html.unescape(c) for c in (product.categories or []) if c]
            if not cats and product.product_type:
                cats = [html.unescape(str(product.product_type))]
            writer.writerow(
                {
                    "title": html.unescape(product.title or ""),
                    "price": f"{product.price:.2f}" if product.price is not None else "",
                    "link": product.url or "",
                    "images": format_images(product.image_urls or []),
                    "cate": ", ".join(cats),
                }
            )

    total = len(search_rows)
    report_lines = [
        "=== WEARANDHOME SEARCH1 REPORT ===",
        f"Input file       : {SEARCH_FILE.name} ({total} dong)",
        f"Catalog crawl    : {len(items)} SP ({len(by_handle)} unique)",
        f"Shop             : {SHOP}",
        "",
        "--- MATCH ---",
        f"Match duoc         : {hits}/{total} ({hits/total*100:.1f}%)",
        f"Khong match        : {total - hits}",
        "Match theo method:",
    ]
    for method, count in method_counts.most_common():
        report_lines.append(f"  {method}: {count}")
    report_lines.extend(
        [
            "",
            f"Output matched   : {OUT_MATCHED.name}",
            f"Output clean     : {OUT_CLEAN.name} ({hits} dong co link)",
            f"Catalog CSV      : {CATALOG_CSV.name}",
            f"Elapsed          : {time.time() - t0:.1f}s",
        ]
    )
    if unmatched[:8]:
        report_lines.append("")
        report_lines.append("Mot so title khong match:")
        for line in unmatched[:10]:
            report_lines.append(f"  - {line[:100]}")

    OUT_REPORT.write_text("\n".join(report_lines), encoding="utf-8")
    for line in report_lines:
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", errors="replace").decode("ascii"))


if __name__ == "__main__":
    main()
