"""Match all search1.txt rows against 7 ShopBase shops — full sheet + match %."""
from __future__ import annotations

import csv
import html
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopbase import CATALOG_LIST_API, ShopBaseCrawler  # noqa: E402

SEARCH_FILE = ROOT / "output" / "search1.txt"
OUT_SHEET = ROOT / "output" / "search1_matched_full.csv"
OUT_CLEAN = ROOT / "output" / "search1_matched_out.csv"
OUT_REPORT = ROOT / "output" / "search1_match_analysis.txt"
WORKERS = 32
MATCH_MIN = 0.68  # >= nay moi dien link/SP; duoi nay chi co % phan tich

SHOPS = [
    "https://wearandhome.com",
    "https://wearandhaven.com",
    "https://happynestwear.com",
    "https://trendyhomewear.com",
    "https://wearandjoy.com",
    "https://wearandbeyond.com",
    "https://dailyanddecor.com",
]

SHOP_SUFFIXES = (
    "wearandbeyond",
    "wearandhome",
    "wearandhaven",
    "wearandjoy",
    "happynestwear",
    "trendyhomewear",
    "dailyanddecor",
    "thw",
)

STOPWORDS = frozenset(
    {
        "for",
        "the",
        "a",
        "an",
        "and",
        "or",
        "with",
        "by",
        "gift",
        "gifts",
        "merch",
        "shirt",
        "2026",
        "best",
        "unique",
        "cool",
        "fan",
        "fans",
    }
)


@dataclass
class CatalogEntry:
    shop_url: str
    shop: str
    item: dict
    title: str
    base: str
    exact: str
    tokens: str
    sig_keys: tuple[str, ...]


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


def token_prefix(text: str, n: int = 12) -> str:
    words = re.findall(r"[a-z0-9]+", normalize_key(text))
    return " ".join(words[:n])


def sig_keys(text: str) -> tuple[str, ...]:
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", normalize_key(text))
        if len(w) > 2 and w not in STOPWORDS
    ]
    keys: set[str] = set()
    for n in (2, 3, 4):
        if len(words) >= n:
            keys.add(" ".join(words[:n]))
    if words:
        keys.add(words[0])
    return tuple(keys)


def match_score(query: str, catalog_title: str) -> float:
    if normalize_key(query) == normalize_key(catalog_title):
        return 1.0
    qb = base_title(query)
    cb = base_title(catalog_title)
    if qb == cb:
        return 1.0
    qt = token_prefix(query, 12)
    ct = token_prefix(catalog_title, 12)
    token_score = SequenceMatcher(None, qt, ct).ratio()
    fuzzy = SequenceMatcher(None, qb[:100], cb[:100]).ratio()
    best = max(token_score, fuzzy)
    if cb.startswith(qb) or qb.startswith(cb[: max(len(qb), 1)]):
        best = max(best, 0.9, len(qb) / max(len(cb), 1))
    return best


def load_search_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return [], []
    header = lines[0].split("\t")
    rows: list[dict[str, str]] = []
    for line_no, raw in enumerate(lines[1:], start=2):
        if not raw.strip():
            continue
        parts = raw.split("\t")
        row = {header[i]: (parts[i] if i < len(parts) else "") for i in range(len(header))}
        row["_line_no"] = str(line_no)
        rows.append(row)
    return header, rows


def format_images(urls: list[str]) -> str:
    return " | ".join(u.strip() for u in urls if u.strip())


def build_catalog(shop_url: str, items: list[dict]) -> list[CatalogEntry]:
    label = shop_label(shop_url)
    out: list[CatalogEntry] = []
    for item in items:
        title = html.unescape(str(item.get("title") or "").strip())
        if not title:
            continue
        out.append(
            CatalogEntry(
                shop_url=shop_url,
                shop=label,
                item=item,
                title=title,
                base=base_title(title),
                exact=normalize_key(title),
                tokens=token_prefix(title, 12),
                sig_keys=sig_keys(title),
            )
        )
    return out


def index_catalog(entries: list[CatalogEntry]) -> tuple[dict[str, CatalogEntry], dict[str, CatalogEntry], dict[str, list[int]]]:
    by_exact: dict[str, CatalogEntry] = {}
    by_base: dict[str, CatalogEntry] = {}
    by_sig: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(entries):
        by_exact.setdefault(e.exact, e)
        by_base.setdefault(e.base, e)
        for key in e.sig_keys:
            by_sig[key].append(i)
    return by_exact, by_base, by_sig


def find_best(
    query: str,
    entries: list[CatalogEntry],
    by_exact: dict[str, CatalogEntry],
    by_base: dict[str, CatalogEntry],
    by_sig: dict[str, list[int]],
) -> tuple[CatalogEntry | None, float, str]:
    q_exact = normalize_key(query)
    if q_exact in by_exact:
        return by_exact[q_exact], 1.0, "exact"

    q_base = base_title(query)
    if q_base in by_base:
        return by_base[q_base], 1.0, "base_exact"

    candidate_ids: set[int] = set()
    for key in sig_keys(query):
        candidate_ids.update(by_sig.get(key, []))

    best: CatalogEntry | None = None
    best_score = 0.0
    best_method = "fuzzy"
    for cid in candidate_ids:
        entry = entries[cid]
        score = match_score(query, entry.title)
        if score > best_score:
            best_score = score
            best = entry

    if best is not None and best_score >= 0.55:
        if best_score >= 0.9:
            best_method = "prefix"
        elif best_score >= 0.75:
            best_method = "token"
        return best, best_score, best_method
    return None, best_score, "none"


def search_api_best(
    crawler: ShopBaseCrawler,
    shop_url: str,
    query: str,
    entries: list[CatalogEntry],
) -> tuple[CatalogEntry | None, float, str]:
    words = query.split()
    queries = [" ".join(words[:8]), " ".join(words[:5]), query]
    seen: set[str] = set()
    best: CatalogEntry | None = None
    best_score = 0.0
    handle_map = {str(e.item.get("handle") or ""): e for e in entries}

    for q in queries:
        if not q or q in seen:
            continue
        seen.add(q)
        try:
            resp = crawler._catalog_session.get(
                f"{shop_url}{CATALOG_LIST_API}",
                params={"limit": 30, "page": 1, "q": q},
                timeout=25,
            )
            if resp.status_code != 200:
                continue
            items = (resp.json().get("result") or {}).get("items") or []
            for item in items:
                title = str(item.get("title") or "")
                score = match_score(query, title)
                if score > best_score:
                    best_score = score
                    handle = str(item.get("handle") or "")
                    best = handle_map.get(handle) or CatalogEntry(
                        shop_url=shop_url,
                        shop=shop_label(shop_url),
                        item=item,
                        title=html.unescape(title),
                        base=base_title(title),
                        exact=normalize_key(title),
                        tokens=token_prefix(title, 12),
                        sig_keys=sig_keys(title),
                    )
        except Exception:
            continue

    if best and best_score >= 0.55:
        method = "api_search" if best_score >= MATCH_MIN else "api_low"
        return best, best_score, method
    return None, best_score, "none"


def find_best_all_shops(
    query: str,
    shop_data: list[tuple[str, ShopBaseCrawler, list[CatalogEntry], dict, dict, dict]],
) -> tuple[CatalogEntry | None, float, str]:
    best: CatalogEntry | None = None
    best_score = 0.0
    best_method = "none"
    best_shop_idx = -1

    for i, (_shop_url, _crawler, entries, by_exact, by_base, by_sig) in enumerate(shop_data):
        hit, score, method = find_best(query, entries, by_exact, by_base, by_sig)
        if score > best_score:
            best_score = score
            best = hit
            best_method = method
            best_shop_idx = i

    if best_score >= MATCH_MIN:
        return best, best_score, best_method

    # API fallback: chi shop dang lead + wearandbeyond (nhieu SP trung search1)
    api_shops: list[int] = []
    if best_shop_idx >= 0:
        api_shops.append(best_shop_idx)
    for i, (url, *_rest) in enumerate(shop_data):
        if "wearandbeyond" in url and i not in api_shops:
            api_shops.append(i)

    for i in api_shops[:2]:
        shop_url, crawler, entries, *_rest = shop_data[i]
        hit, score, method = search_api_best(crawler, shop_url, query, entries)
        if score > best_score:
            best_score = score
            best = hit
            best_method = method

    return best, best_score, best_method


def main() -> None:
    t0 = time.time()
    header, search_rows = load_search_rows(SEARCH_FILE)
    if not search_rows:
        raise SystemExit("Khong co du lieu search1.txt")

    title_col = "Sản phẩm" if "Sản phẩm" in header else header[4]

    shop_data: list[tuple[str, ShopBaseCrawler, list[CatalogEntry], dict, dict, dict]] = []
    for shop_url in SHOPS:
        label = shop_label(shop_url)
        print(f"[catalog] {label} ...", flush=True)
        crawler = ShopBaseCrawler(site_url=shop_url, proxy_url=None)
        items = crawler._list_catalog_items(
            max_pages=None, limit=None, max_workers=WORKERS
        )
        entries = build_catalog(shop_url, items)
        by_exact, by_base, by_sig = index_catalog(entries)
        shop_data.append((shop_url, crawler, entries, by_exact, by_base, by_sig))
        print(f"  -> {len(entries)} SP", flush=True)

    # Match tung dong
    pending_products: dict[tuple[str, str], CatalogEntry] = {}
    match_results: list[tuple[CatalogEntry | None, float, str]] = []

    print(f"[match] {len(search_rows)} titles ...", flush=True)
    for row in search_rows:
        query = row.get(title_col, "").strip()
        hit, score, method = find_best_all_shops(query, shop_data)
        match_results.append((hit, score, method))
        if hit and score >= MATCH_MIN:
            handle = str(hit.item.get("handle") or "")
            pending_products[(hit.shop_url, handle)] = hit

    # Map products + descriptions
    products: dict[tuple[str, str], object] = {}
    crawlers = {url: c for url, c, *_ in shop_data}
    for (shop_url, handle), entry in pending_products.items():
        crawler = crawlers[shop_url]
        product = crawler._map_catalog_item(entry.item, skip_page_fallback=True)
        if product and product.url:
            products[(shop_url, handle)] = product

    by_shop: dict[str, list] = defaultdict(list)
    for key, product in products.items():
        by_shop[key[0]].append(product)

    for shop_url, prods in by_shop.items():
        print(f"[des] {shop_label(shop_url)} x{len(prods)} ...", flush=True)
        crawlers[shop_url]._enrich_descriptions_parallel(prods, max_workers=WORKERS)

    extra_cols = [
        "match_score",
        "match_method",
        "shop",
        "matched_title",
        "images",
        "link",
        "price",
        "cate",
        "des",
    ]
    out_header = header + extra_cols
    out_rows: list[dict[str, str]] = []
    score_buckets: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()

    for row, (hit, score, method) in zip(search_rows, match_results):
        out = {k: row.get(k, "") for k in header}
        pct = f"{score * 100:.0f}%"
        out["match_score"] = pct if score > 0 else "0%"
        out["match_method"] = method if score >= MATCH_MIN else ("low_" + method if score >= 0.55 else "none")
        out["shop"] = ""
        out["matched_title"] = ""
        out["images"] = ""
        out["link"] = ""
        out["price"] = ""
        out["cate"] = ""
        out["des"] = ""

        if score >= 1.0:
            score_buckets["100% exact"] += 1
        elif score >= 0.9:
            score_buckets["90-99%"] += 1
        elif score >= 0.8:
            score_buckets["80-89%"] += 1
        elif score >= 0.75:
            score_buckets["75-79%"] += 1
        elif score >= MATCH_MIN:
            score_buckets[f"{MATCH_MIN*100:.0f}-74%"] += 1
        elif score >= 0.55:
            score_buckets["55-67% (low)"] += 1
        else:
            score_buckets["<55% none"] += 1

        if hit and score >= MATCH_MIN:
            handle = str(hit.item.get("handle") or "")
            product = products.get((hit.shop_url, handle))
            if product:
                cats = [html.unescape(c) for c in (product.categories or []) if c]
                if not cats and product.product_type:
                    cats = [html.unescape(str(product.product_type))]
                cate = ", ".join(cats)
                out["shop"] = hit.shop
                out["matched_title"] = html.unescape(product.title or "")
                out["images"] = format_images(product.image_urls or [])
                out["link"] = product.url or ""
                out["price"] = (
                    f"{product.price:.2f}" if product.price is not None else ""
                )
                out["cate"] = cate
                out["des"] = html.unescape(product.description or "")
                method_counts[method] += 1
        out_rows.append(out)

    OUT_SHEET.parent.mkdir(parents=True, exist_ok=True)
    with OUT_SHEET.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)

    filled = sum(1 for r in out_rows if r.get("link"))
    with OUT_CLEAN.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["shop", "title", "images", "link", "price", "cate", "des", "match_score"],
        )
        writer.writeheader()
        for r in out_rows:
            if r.get("link"):
                writer.writerow(
                    {
                        "shop": r["shop"],
                        "title": r["matched_title"],
                        "images": r["images"],
                        "link": r["link"],
                        "price": r["price"],
                        "cate": r["cate"],
                        "des": r["des"],
                        "match_score": r["match_score"],
                    }
                )

    total = len(out_rows)
    report = [
        "=== SEARCH1 MATCH ANALYSIS ===",
        f"Tong dong search1     : {total}",
        f"Co link SP (>= {MATCH_MIN*100:.0f}%) : {filled}/{total} ({filled/total*100:.1f}%)",
        f"Khong match           : {total - filled}",
        "",
        "--- PHAN BO MATCH SCORE ---",
    ]
    for bucket, count in sorted(score_buckets.items()):
        report.append(f"  {bucket:20s}: {count:4d} ({count/total*100:5.1f}%)")
    report.extend(["", "--- METHOD (>= threshold) ---"])
    for method, count in method_counts.most_common():
        report.append(f"  {method}: {count}")
    report.extend(
        [
            "",
            f"Full sheet            : {OUT_SHEET.name}",
            f"Clean matched         : {OUT_CLEAN.name}",
            f"Elapsed               : {time.time() - t0:.1f}s",
        ]
    )
    OUT_REPORT.write_text("\n".join(report), encoding="utf-8")
    for line in report:
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", errors="replace").decode("ascii"))


if __name__ == "__main__":
    main()
