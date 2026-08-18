"""Crawl familiro.com (Next.js marketplace) → file feed GMC."""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exporters.export import export_products  # noqa: E402
from models.product import Product  # noqa: E402
from utils.http_session import (  # noqa: E402
    DEFAULT_USER_AGENT,
    create_http_session,
    parse_proxy_list,
)

SHOP = "https://familiro.com"
OUT = ROOT / "output" / "gmc_feed_familiro.csv"
WORKERS = 32
SITEMAPS = [
    f"{SHOP}/sitemap-products-1.xml",
    f"{SHOP}/sitemap-products-2.xml",
]


def _load_proxy() -> str:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    if isinstance(cfg.get("proxy"), dict):
        return str(cfg["proxy"].get("url") or "")
    return str(cfg.get("proxy") or "")


def _session(proxy: str | None):
    sess = create_http_session(proxy)
    sess.headers["User-Agent"] = DEFAULT_USER_AGENT
    return sess


def collect_product_urls(proxy: str) -> list[str]:
    proxies = parse_proxy_list(proxy)
    sess = _session(proxies[0] if proxies else None)
    urls: list[str] = []
    seen: set[str] = set()
    for sm in SITEMAPS:
        text = sess.get(sm, timeout=45).text
        for loc in re.findall(r"<loc>([^<]+)</loc>", text):
            loc = loc.strip()
            if "/product/" not in loc or loc in seen:
                continue
            seen.add(loc)
            urls.append(loc)
    return urls


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                for key in ("url", "src", "image"):
                    if item.get(key):
                        out.append(str(item[key]).strip())
                        break
        return out
    return []


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    """Bỏ surrogate / ký tự lỗi UTF-8 từ HTML Next.js."""
    text = str(value or "")
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="ignore")


def map_detail(url: str, page_props: dict[str, Any]) -> Product | None:
    detail = page_props.get("detail") or {}
    product = detail.get("product") or {}
    mapped = detail.get("mappedProduct") or {}
    default_v = detail.get("defaultVariant") or {}
    structured = page_props.get("structuredData") or {}

    title = _clean_text(
        product.get("title") or product.get("name") or mapped.get("title") or ""
    ).strip()
    if not title:
        return None

    price = (
        _safe_float(default_v.get("price"))
        or _safe_float(mapped.get("price"))
        or _safe_float(product.get("minPrice"))
        or _safe_float(product.get("basePrice"))
    )
    images = [
        _clean_text(x)
        for x in (
            _as_list(product.get("images"))
            or _as_list(mapped.get("image"))
            or _as_list(default_v.get("images"))
            or _as_list(default_v.get("image"))
            or _as_list(product.get("featuredImage") or product.get("thumbnail"))
            or _as_list(structured.get("image"))
        )
        if x
    ]
    description = _clean_text(
        product.get("description")
        or product.get("shortDescription")
        or structured.get("description")
        or ""
    ).strip() or None
    sku = _clean_text(
        default_v.get("sku") or product.get("sku") or structured.get("sku") or ""
    ).strip() or None
    brand = _clean_text(
        product.get("brand")
        or mapped.get("brand")
        or product.get("vendor")
        or "Familiro"
    ).strip()
    category = _clean_text(
        product.get("category") or structured.get("category") or ""
    ).strip()
    handle = _clean_text(product.get("handle") or product.get("slug") or "").strip()
    currency = _clean_text(product.get("currency") or "USD").strip() or "USD"

    variants: list[dict[str, Any]] = []
    for var in detail.get("variants") or []:
        if not isinstance(var, dict):
            continue
        raw_vid = str(var.get("id") or var.get("variantKey") or "").strip()
        var_sku = _clean_text(var.get("sku") or "") or None
        # GMC id = handle (unique); nhiều biến thể thì handle-variantKey.
        base = handle or var_sku or url.rstrip("/").rsplit("/", 1)[-1]
        if raw_vid and raw_vid.lower() not in {"default", "0", "null"}:
            unique_vid = f"{base}-{raw_vid}"
        else:
            unique_vid = base
        variants.append(
            {
                "id": unique_vid,
                "title": _clean_text(var.get("title") or var.get("name") or ""),
                "price": _safe_float(var.get("price")),
                "sku": var_sku,
                "available": bool(var.get("available", True)),
                "image_url": (
                    _as_list(var.get("images") or var.get("image") or [])[:1] or [None]
                )[0],
            }
        )

    product_id = handle or sku or url.rstrip("/").rsplit("/", 1)[-1]
    return Product(
        source="familiro",
        id=product_id,
        title=title,
        description=description,
        price=price,
        currency=currency,
        url=url,
        image_urls=images,
        vendor=brand,
        sku=sku,
        categories=[category] if category else [],
        product_type=category or None,
        status="active",
        variants=variants,
    )


def fetch_product(url: str, proxy: str | None, build_id: str | None) -> Product | None:
    sess = _session(proxy)
    for attempt in range(3):
        try:
            page_props: dict[str, Any] | None = None
            if build_id:
                path = url.replace(SHOP, "").split("?")[0].rstrip("/")
                api = f"{SHOP}/_next/data/{build_id}{path}.json"
                resp = sess.get(api, timeout=40)
                if resp.status_code == 200:
                    payload = resp.json()
                    page_props = (payload.get("pageProps") or {}) if isinstance(payload, dict) else None
            if not page_props:
                html = sess.get(url, timeout=40).text
                m = re.search(
                    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                    html,
                    re.S,
                )
                if not m:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                data = json.loads(m.group(1))
                page_props = (data.get("props") or {}).get("pageProps") or {}
            return map_detail(url, page_props)
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return None


def detect_build_id(proxy: str, sample_url: str) -> str | None:
    proxies = parse_proxy_list(proxy)
    sess = _session(proxies[0] if proxies else None)
    html = sess.get(sample_url, timeout=40).text
    m = re.search(r"/_next/static/([^/]+)/_buildManifest", html)
    return m.group(1) if m else None


def main() -> None:
    proxy = _load_proxy()
    proxies = parse_proxy_list(proxy)
    print(f"[start] {SHOP} proxies={len(proxies)}", flush=True)
    t0 = time.time()

    urls = collect_product_urls(proxy)
    print(f"[sitemap] {len(urls)} product URLs", flush=True)
    if not urls:
        print("[done] no urls", flush=True)
        return

    build_id = detect_build_id(proxy, urls[0])
    print(f"[build_id] {build_id}", flush=True)

    products: list[Product] = []
    failed = 0

    def one(idx: int, url: str) -> tuple[int, Product | None]:
        pxy = proxies[idx % len(proxies)] if proxies else None
        return idx, fetch_product(url, pxy, build_id)

    with ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(urls)))) as ex:
        futs = [ex.submit(one, i, u) for i, u in enumerate(urls)]
        done = 0
        for fut in as_completed(futs):
            _, product = fut.result()
            done += 1
            if product:
                products.append(product)
            else:
                failed += 1
            if done % 50 == 0 or done == len(urls):
                print(
                    f"[crawl] {done}/{len(urls)} ok={len(products)} fail={failed}",
                    flush=True,
                )

    # Retry fails once (slower / HTML fallback)
    if failed:
        print(f"[retry] {failed} failed urls…", flush=True)
        got_ids = {p.id for p in products}
        missing = [u for u in urls if u.rstrip("/").rsplit("/", 1)[-1] not in got_ids]
        # also match by full url
        got_urls = {p.url for p in products if p.url}
        missing = [u for u in urls if u not in got_urls]
        with ThreadPoolExecutor(max_workers=min(12, max(1, len(missing)))) as ex:
            futs = []
            for i, u in enumerate(missing):
                pxy = proxies[i % len(proxies)] if proxies else None
                futs.append(ex.submit(fetch_product, u, pxy, None))  # force HTML
            for fut in as_completed(futs):
                product = fut.result()
                if product:
                    products.append(product)
        print(f"[retry] now {len(products)} products", flush=True)

    print(f"[map] {len(products)} products in {time.time() - t0:.1f}s", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out_path = export_products(
        products,
        str(OUT),
        fmt="gmc-feed-csv",
        gmc_currency="USD",
        gmc_condition="new",
        gmc_variant_mode="all_variations",
    )
    rows = sum(1 for _ in Path(out_path).open(encoding="utf-8-sig")) - 1
    print(f"[done] {rows} GMC rows -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
