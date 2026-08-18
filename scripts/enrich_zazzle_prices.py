"""Gắn giá Zazzle từ trang sản phẩm — 500 luồng, stream CSV."""
from __future__ import annotations

import csv
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from curl_cffi import requests as creq  # noqa: E402

INP = ROOT / "output" / "zazzle_products.csv"
OUT = ROOT / "output" / "zazzle_products.csv"
TMP = ROOT / "output" / "zazzle_products.pricing.tmp.csv"
WORKERS = 500
BATCH = 1000
# 0 = enrich tất cả dòng thiếu giá (rất lâu với ~8M SP)
LIMIT = 100000

_TLS = threading.local()
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


def fetch_price(link: str) -> str:
    if not link:
        return ""
    for attempt in range(3):
        try:
            r = _session().get(link, timeout=35)
            if r.status_code in (429, 503, 502):
                time.sleep(1.2 * (attempt + 1))
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


def enrich_batch(rows: list[dict]) -> int:
    need = [(i, r) for i, r in enumerate(rows) if not (r.get("price") or "").strip()]
    if not need:
        return 0
    filled = 0
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(need))) as ex:
        futs = {ex.submit(fetch_price, r.get("link") or ""): i for i, r in need}
        for fut in as_completed(futs):
            i = futs[fut]
            price = fut.result()
            if price:
                rows[i]["price"] = price
                filled += 1
    return filled


def main() -> None:
    if not INP.exists():
        print(f"missing {INP}", flush=True)
        return
    t0 = time.time()
    total = 0
    filled_total = 0
    already = 0
    fields = ["title", "price", "link", "images"]

    print(f"[start] enrich workers={WORKERS} batch={BATCH} limit={LIMIT}", flush=True)
    with INP.open("r", encoding="utf-8-sig", newline="") as fin, TMP.open(
        "w", encoding="utf-8-sig", newline=""
    ) as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=fields)
        writer.writeheader()
        buf: list[dict] = []
        enrich_left = LIMIT if LIMIT > 0 else 10**12

        def flush_buf() -> None:
            nonlocal filled_total, enrich_left, total, already
            if not buf:
                return
            to_enrich = sum(1 for r in buf if not (r.get("price") or "").strip())
            already += len(buf) - to_enrich
            if to_enrich and enrich_left > 0:
                # optionally trim enrich in last batch
                if to_enrich > enrich_left:
                    # only enrich first enrich_left needing price
                    count = 0
                    subset_idx = []
                    for i, r in enumerate(buf):
                        if not (r.get("price") or "").strip():
                            subset_idx.append(i)
                            count += 1
                            if count >= enrich_left:
                                break
                    sub = [buf[i] for i in subset_idx]
                    n = enrich_batch(sub)
                    for j, i in enumerate(subset_idx):
                        buf[i]["price"] = sub[j].get("price") or ""
                    filled_total += n
                    enrich_left = 0
                else:
                    n = enrich_batch(buf)
                    filled_total += n
                    enrich_left -= to_enrich
            for r in buf:
                writer.writerow({k: r.get(k) or "" for k in fields})
            total += len(buf)
            buf.clear()
            print(
                f"[enrich] written={total} filled={filled_total} "
                f"had_price={already} elapsed={time.time() - t0:.0f}s",
                flush=True,
            )

        for row in reader:
            buf.append(row)
            if len(buf) >= BATCH:
                flush_buf()
                if LIMIT > 0 and enrich_left <= 0:
                    # drain rest without enrich
                    for r in buf:
                        writer.writerow({k: r.get(k) or "" for k in fields})
                        total += 1
                    buf.clear()
                    for r in reader:
                        writer.writerow({k: r.get(k) or "" for k in fields})
                        total += 1
                        if total % 200000 == 0:
                            print(f"[copy] written={total}", flush=True)
                    break
        else:
            flush_buf()

    TMP.replace(OUT)
    print(
        f"[done] rows={total} newly_priced={filled_total} "
        f"elapsed={time.time() - t0:.1f}s file={OUT}",
        flush=True,
    )


if __name__ == "__main__":
    main()
