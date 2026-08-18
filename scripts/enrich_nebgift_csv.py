"""Bổ sung des + cate cho nebgift_all.csv qua ShopBase detail API (+ proxy)."""
from __future__ import annotations

import csv
import html
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawlers.shopbase import CATALOG_DETAIL_API  # noqa: E402
from utils.description_html import shopbase_description_from_item  # noqa: E402
from utils.http_session import (  # noqa: E402
    DEFAULT_USER_AGENT,
    create_http_session,
    parse_proxy_list,
)

SHOP = "https://nebgift.com"
CSV_PATH = ROOT / "output" / "nebgift_all.csv"
WORKERS = 80


def _load_proxy() -> str:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    if isinstance(cfg.get("proxy"), dict):
        return str(cfg["proxy"].get("url") or "")
    return str(cfg.get("proxy") or "")


def _cate_from_detail(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("product_type", "productType", "type"):
        val = str(data.get(key) or "").strip()
        if val:
            parts.append(html.unescape(val))
            break
    cats = data.get("categories") or data.get("category") or []
    if isinstance(cats, str) and cats.strip():
        parts.append(html.unescape(cats.strip()))
    elif isinstance(cats, list):
        for item in cats:
            if isinstance(item, str) and item.strip():
                parts.append(html.unescape(item.strip()))
            elif isinstance(item, dict):
                title = str(item.get("title") or item.get("name") or "").strip()
                if title:
                    parts.append(html.unescape(title))
    collections = data.get("collections") or []
    if isinstance(collections, list):
        for item in collections[:3]:
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("name") or "").strip()
                if title:
                    parts.append(html.unescape(title))
            elif isinstance(item, str) and item.strip():
                parts.append(html.unescape(item.strip()))
    # unique keep order
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return ", ".join(out)


def fetch_detail(handle: str, proxy: str | None) -> tuple[str, str]:
    sess = create_http_session(proxy)
    sess.headers["User-Agent"] = DEFAULT_USER_AGENT
    for attempt in range(3):
        try:
            resp = sess.get(
                f"{SHOP}{CATALOG_DETAIL_API}",
                params={"handle": handle},
                timeout=30,
            )
            if resp.status_code != 200:
                time.sleep(0.3 * (attempt + 1))
                continue
            data = resp.json().get("result")
            if not isinstance(data, dict):
                time.sleep(0.3 * (attempt + 1))
                continue
            des = shopbase_description_from_item(data) or ""
            cate = _cate_from_detail(data)
            return des, cate
        except Exception:
            time.sleep(0.4 * (attempt + 1))
    return "", ""


def main() -> None:
    proxy = _load_proxy()
    proxies = parse_proxy_list(proxy)
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    need = [
        i
        for i, r in enumerate(rows)
        if not (r.get("des") or "").strip() or not (r.get("cate") or "").strip()
    ]
    print(
        f"[start] rows={len(rows)} need_enrich={len(need)} proxies={len(proxies)}",
        flush=True,
    )
    t0 = time.time()
    updated = 0

    def one(i: int) -> tuple[int, str, str]:
        row = rows[i]
        handle = (row.get("link") or "").rstrip("/").rsplit("/", 1)[-1]
        pxy = proxies[i % len(proxies)] if proxies else None
        des, cate = fetch_detail(handle, pxy)
        return i, des, cate

    with ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(need)))) as ex:
        futs = [ex.submit(one, i) for i in need]
        done = 0
        for fut in as_completed(futs):
            i, des, cate = fut.result()
            done += 1
            row = rows[i]
            changed = False
            if des and not (row.get("des") or "").strip():
                row["des"] = des
                changed = True
            if cate and not (row.get("cate") or "").strip():
                row["cate"] = cate
                changed = True
            elif cate and cate not in (row.get("cate") or ""):
                # bổ sung nếu cate cũ trống một phần
                if not (row.get("cate") or "").strip():
                    row["cate"] = cate
                    changed = True
            if changed:
                updated += 1
            if done % 200 == 0 or done == len(need):
                print(
                    f"[enrich] {done}/{len(need)} updated={updated}",
                    flush=True,
                )

    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["title", "des", "cate", "price", "link"]
        )
        writer.writeheader()
        writer.writerows(rows)

    with_des = sum(1 for r in rows if (r.get("des") or "").strip())
    with_cate = sum(1 for r in rows if (r.get("cate") or "").strip())
    print(
        f"[done] updated={updated} with_des={with_des} with_cate={with_cate} "
        f"in {time.time()-t0:.1f}s -> {CSV_PATH}",
        flush=True,
    )


if __name__ == "__main__":
    main()
