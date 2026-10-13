"""Match search1.txt titles against multiple ShopBase shops; merge to one CSV."""
from __future__ import annotations

import csv
import html
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopbase import ShopBaseCrawler  # noqa: E402

SEARCH_FILE = ROOT / "output" / "search1.txt"
OUT_COMBINED = ROOT / "output" / "search1_all_shops_out.csv"
OUT_FULL = ROOT / "output" / "search1_all_shops.csv"
OUT_REPORT = ROOT / "output" / "search1_all_shops_report.txt"
WORKERS = 32

SHOPS = [
    "https://wearandhaven.com",
    "https://happynestwear.com",
    "https://trendyhomewear.com",
    "https://wearandjoy.com",
    "https://wearandbeyond.com",
    "https://dailyanddecor.com",
]

SHOP_SUFFIXES = (
    r"wearandbeyond",
    r"wearandhome",
    r"wearandhaven",
    r"wearandjoy",
    r"happynestwear",
    r"trendyhomewear",
    r"dailyanddecor",
    r"thw",
)


def shop_label(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def normalize_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("…", "...").replace("–", "-").replace("—", "-")
    text = re.sub(r"\.{2,}$", "", text.strip())
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def base_title(text: str) -> str:
    t = normalize_key(text)
    for suffix in SHOP_SUFFIXES:
        t = re.sub(rf"\s*-\s*{suffix}\s*$", "", t, flags=re.I)
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


def build_index(items: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_exact: dict[str, dict] = {}
    by_base: dict[str, dict] = {}
    for item in items:
        title = html.unescape(str(item.get("title") or "").strip())
        if not title:
            continue
        by_exact[normalize_key(title)] = item
        by_base.setdefault(base_title(title), item)
    return by_exact, by_base


def find_item(query: str, by_exact: dict[str, dict], by_base: dict[str, dict]) -> tuple[dict | None, str]:
    if normalize_key(query) in by_exact:
        return by_exact[normalize_key(query)], "exact"
    key = base_title(query)
    if key in by_base:
        return by_base[key], "base_exact"
    return None, "unmatched"


def product_row(
    shop: str,
    product,
    source_title: str,
    line_no: int,
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
        "shop": shop,
        "title": html.unescape(product.title or ""),
        "images": format_images(product.image_urls or []),
        "link": product.url or "",
        "price": f"{product.price:.2f}" if product.price is not None else "",
        "cate": cate,
        "des": html.unescape(product.description or ""),
        "match_method": method,
        "source_title": source_title,
    }


def process_shop(
    shop_url: str,
    search_rows: list[tuple[int, str]],
    already_matched_lines: set[int],
) -> tuple[list[dict[str, str]], Counter[str], int, int]:
    label = shop_label(shop_url)
    t0 = time.time()
    print(f"\n=== [{label}] catalog crawl ===", flush=True)

    crawler = ShopBaseCrawler(site_url=shop_url, proxy_url=None)
    items = crawler._list_catalog_items(
        max_pages=None,
        limit=None,
        max_workers=WORKERS,
    )
    print(f"[{label}] catalog={len(items)} ({time.time() - t0:.1f}s)", flush=True)

    by_exact, by_base = build_index(items)
    matched_items: dict[str, dict] = {}
    pending: list[tuple[int, str, dict, str]] = []
    method_counts: Counter[str] = Counter()

    for line_no, raw_title in search_rows:
        if line_no in already_matched_lines:
            continue
        item, method = find_item(raw_title, by_exact, by_base)
        if item is None:
            continue
        handle = str(item.get("handle") or "").strip()
        if not handle:
            continue
        matched_items[handle] = item
        pending.append((line_no, raw_title, item, method))
        method_counts[method] += 1

    products_by_handle: dict[str, object] = {}
    for handle, item in matched_items.items():
        product = crawler._map_catalog_item(item, skip_page_fallback=True)
        if product and product.url:
            products_by_handle[handle] = product

    if products_by_handle:
        print(f"[{label}] descriptions x{len(products_by_handle)} ...", flush=True)
        crawler._enrich_descriptions_parallel(
            list(products_by_handle.values()),
            max_workers=WORKERS,
        )

    rows: list[dict[str, str]] = []
    for line_no, raw_title, item, method in pending:
        handle = str(item.get("handle") or "").strip()
        product = products_by_handle.get(handle)
        if not product:
            continue
        rows.append(product_row(label, product, raw_title, line_no, method))
        already_matched_lines.add(line_no)

    print(
        f"[{label}] matched={len(rows)} new, elapsed={time.time() - t0:.1f}s",
        flush=True,
    )
    return rows, method_counts, len(items), len(rows)


def merge_wearandhome(existing: Path) -> list[dict[str, str]]:
    if not existing.is_file():
        return []
    rows: list[dict[str, str]] = []
    with existing.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(
                {
                    "line_no": "",
                    "shop": "wearandhome.com",
                    "title": row.get("title", ""),
                    "images": row.get("images", ""),
                    "link": row.get("link", ""),
                    "price": row.get("price", ""),
                    "cate": row.get("cate", ""),
                    "des": row.get("des", ""),
                    "match_method": "exact",
                    "source_title": row.get("title", ""),
                }
            )
    return rows


def main() -> None:
    t0 = time.time()
    search_rows = load_search_titles(SEARCH_FILE)
    if not search_rows:
        raise SystemExit(f"Khong co title trong {SEARCH_FILE}")

    all_rows: list[dict[str, str]] = []
    shop_stats: list[str] = []
    already_matched: set[int] = set()

    # Include prior wearandhome results if present
    wh_file = ROOT / "output" / "wearandhome_from_search1_out.csv"
    wh_rows = merge_wearandhome(wh_file)
    if wh_rows:
        # Map by source title to line_no when possible
        title_to_line = {base_title(t): ln for ln, t in search_rows}
        for row in wh_rows:
            ln = title_to_line.get(base_title(row["source_title"]))
            if ln:
                row["line_no"] = str(ln)
                already_matched.add(ln)
        all_rows.extend(wh_rows)
        shop_stats.append(f"wearandhome.com: {len(wh_rows)} (from prior crawl)")

    for shop_url in SHOPS:
        rows, methods, catalog_n, hit_n = process_shop(
            shop_url, search_rows, already_matched
        )
        all_rows.extend(rows)
        parts = [f"{m}={c}" for m, c in methods.most_common()]
        shop_stats.append(
            f"{shop_label(shop_url)}: {hit_n} match / {catalog_n} SP ({', '.join(parts) or 'none'})"
        )

    OUT_COMBINED.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "line_no",
        "shop",
        "title",
        "images",
        "link",
        "price",
        "cate",
        "des",
        "match_method",
        "source_title",
    ]
    all_rows.sort(key=lambda r: (int(r["line_no"]) if r["line_no"].isdigit() else 9999, r["shop"]))

    with OUT_FULL.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    with OUT_COMBINED.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["shop", "title", "images", "link", "price", "cate", "des"]
        )
        writer.writeheader()
        for row in all_rows:
            if row.get("link"):
                writer.writerow(
                    {
                        "shop": row["shop"],
                        "title": row["title"],
                        "images": row["images"],
                        "link": row["link"],
                        "price": row["price"],
                        "cate": row["cate"],
                        "des": row["des"],
                    }
                )

    total_titles = len(search_rows)
    matched_lines = len({r["line_no"] for r in all_rows if r.get("link") and r["line_no"]})
    report = [
        "=== SEARCH1 ALL SHOPS REPORT ===",
        f"Input            : {SEARCH_FILE.name} ({total_titles} titles)",
        f"Shops checked    : wearandhome (cached) + {len(SHOPS)} shops",
        f"Total rows out   : {len(all_rows)}",
        f"Unique titles    : {matched_lines}/{total_titles} ({matched_lines/total_titles*100:.1f}%)",
        f"Combined CSV     : {OUT_COMBINED.name}",
        f"Full CSV         : {OUT_FULL.name}",
        f"Elapsed          : {time.time() - t0:.1f}s",
        "",
        "--- Per shop ---",
    ]
    report.extend(shop_stats)
    OUT_REPORT.write_text("\n".join(report), encoding="utf-8")
    for line in report:
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", errors="replace").decode("ascii"))


if __name__ == "__main__":
    main()
