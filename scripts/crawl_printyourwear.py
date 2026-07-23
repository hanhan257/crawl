"""Crawl toan bo printyourwear.com (Shopify) — sitemap + GraphQL batch.

Xuat CSV: title, price, cate, link, images (anh cach nhau bang dau phay).
"""
from __future__ import annotations

import csv
import json
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import unquote

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.http_session import (  # noqa: E402
    DEFAULT_USER_AGENT,
    create_http_session,
    parse_proxy_list,
)

SHOP_URL = "https://www.printyourwear.com"
WORKERS = 500
BATCH_SIZE = 5
SITEMAP_WORKERS = 64
CHUNK_IN_FLIGHT = 2000
OUT_CSV = ROOT / "output" / "printyourwear_all.csv"
HANDLES_FILE = ROOT / "output" / "printyourwear_handles.txt"
GOOD_PROXIES_FILE = ROOT / "output" / "printyourwear_good_proxies.txt"
PROGRESS_EVERY = 1000
FLUSH_EVERY = 200
PRODUCT_PATH = __import__("re").compile(r"/products/([^/?#]+)", __import__("re").I)
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
GRAPHQL_URL = f"{SHOP_URL}/api/2024-10/graphql.json"

NODE_SEL = """
id
title
handle
productType
featuredImage { url }
images(first: 50) { edges { node { url } } }
variants(first: 1) {
  edges {
    node {
      price { amount }
    }
  }
}
"""

_tls = threading.local()
_proxy_lock = threading.Lock()
_proxy_index = 0
_write_lock = threading.Lock()


def load_proxy_text() -> str:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    proxy = cfg.get("proxy") or {}
    if isinstance(proxy, dict):
        return str(proxy.get("url") or "")
    return str(proxy or "")


def filter_good_proxies(proxy_text: str) -> str:
    if GOOD_PROXIES_FILE.exists() and GOOD_PROXIES_FILE.stat().st_size > 0:
        text = GOOD_PROXIES_FILE.read_text(encoding="utf-8").strip()
        print(f"[proxy] load good cache: {len(text.splitlines())}", flush=True)
        return text

    proxies = parse_proxy_list(proxy_text)
    print(f"[proxy] probing {len(proxies)} proxies...", flush=True)
    sample_handle = "test"
    if HANDLES_FILE.exists():
        lines = [
            ln.strip()
            for ln in HANDLES_FILE.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        if lines:
            sample_handle = lines[0]

    good: list[str] = []

    def probe(proxy: str) -> str | None:
        sess = create_http_session(proxy)
        sess.headers["User-Agent"] = DEFAULT_USER_AGENT
        try:
            # uu tien GraphQL
            resp = sess.post(
                GRAPHQL_URL,
                json={
                    "query": (
                        "query($h:String!){ product(handle:$h){ handle title } }"
                    ),
                    "variables": {"h": sample_handle},
                },
                timeout=20,
            )
            if resp.status_code == 200 and isinstance(resp.json().get("data"), dict):
                return proxy
            # fallback product.js
            resp2 = sess.get(
                f"{SHOP_URL}/products/{sample_handle}.js", timeout=20
            )
            if resp2.status_code == 200:
                return proxy
        except Exception:
            return None
        finally:
            sess.close()
        return None

    with ThreadPoolExecutor(max_workers=min(90, max(1, len(proxies)))) as ex:
        for result in ex.map(probe, proxies):
            if result:
                good.append(result)

    if not good:
        print("[proxy] khong proxy nao OK — dung toan bo pool", flush=True)
        return proxy_text

    # luu dang host:port:user:pass neu co the; khong thi raw url
    GOOD_PROXIES_FILE.write_text("\n".join(good) + "\n", encoding="utf-8")
    print(f"[proxy] good={len(good)}/{len(proxies)} saved", flush=True)
    return "\n".join(good)


def next_proxy(proxies: list[str]) -> str | None:
    global _proxy_index
    if not proxies:
        return None
    with _proxy_lock:
        proxy = proxies[_proxy_index % len(proxies)]
        _proxy_index += 1
        return proxy


def page_session(proxies: list[str]) -> requests.Session:
    if getattr(_tls, "session", None) is None:
        sess = create_http_session(next_proxy(proxies))
        sess.headers["User-Agent"] = DEFAULT_USER_AGENT
        sess.headers["Content-Type"] = "application/json"
        sess.headers["Accept"] = "application/json"
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=8,
            pool_maxsize=8,
            max_retries=0,
        )
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        _tls.session = sess
    return _tls.session


def reset_tls_session(proxies: list[str]) -> requests.Session:
    old = getattr(_tls, "session", None)
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
    _tls.session = None
    return page_session(proxies)


def collect_product_sitemaps(session: requests.Session) -> list[str]:
    response = session.get(f"{SHOP_URL}/sitemap.xml", timeout=60)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    return [
        (el.text or "").strip().replace("&amp;", "&")
        for el in root.findall(".//sm:loc", SITEMAP_NS)
        if el.text and "sitemap_products" in el.text.lower()
    ]


def parse_sitemap_handles(session: requests.Session, sitemap_url: str) -> list[str]:
    try:
        response = session.get(sitemap_url, timeout=60)
        if response.status_code != 200:
            return []
        root = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError):
        return []
    handles: list[str] = []
    seen: set[str] = set()
    for el in root.findall(".//sm:loc", SITEMAP_NS):
        loc = (el.text or "").strip()
        match = PRODUCT_PATH.search(loc)
        if not match:
            continue
        handle = unquote(match.group(1)).strip()
        if not handle or handle in seen:
            continue
        seen.add(handle)
        handles.append(handle)
    return handles


def discover_handles(proxies: list[str]) -> list[str]:
    if HANDLES_FILE.exists() and HANDLES_FILE.stat().st_size > 0:
        handles = [
            line.strip()
            for line in HANDLES_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        print(f"[handles] load cache: {len(handles):,}", flush=True)
        return handles

    seed = create_http_session(proxies[0] if proxies else None)
    seed.headers["User-Agent"] = DEFAULT_USER_AGENT
    smaps = collect_product_sitemaps(seed)
    seed.close()
    print(f"[handles] product sitemaps: {len(smaps):,}", flush=True)

    all_handles: list[str] = []
    seen: set[str] = set()
    done = 0

    def fetch_one(url: str) -> list[str]:
        sess = page_session(proxies)
        for attempt in range(3):
            try:
                return parse_sitemap_handles(sess, url)
            except Exception:
                reset_tls_session(proxies)
                time.sleep(0.4 * (attempt + 1))
        return []

    with ThreadPoolExecutor(max_workers=SITEMAP_WORKERS) as executor:
        futures = {executor.submit(fetch_one, url): url for url in smaps}
        from concurrent.futures import as_completed

        for future in as_completed(futures):
            batch = future.result() or []
            for handle in batch:
                if handle in seen:
                    continue
                seen.add(handle)
                all_handles.append(handle)
            done += 1
            if done % 50 == 0 or done == len(smaps):
                print(
                    f"[handles] sitemap {done}/{len(smaps)} | unique {len(all_handles):,}",
                    flush=True,
                )

    HANDLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    HANDLES_FILE.write_text("\n".join(all_handles) + "\n", encoding="utf-8")
    print(f"[handles] saved {len(all_handles):,} -> {HANDLES_FILE}", flush=True)
    return all_handles


def load_done_handles() -> set[str]:
    done: set[str] = set()
    if not OUT_CSV.exists():
        return done
    with OUT_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            link = (row.get("link") or "").rstrip("/")
            if "/products/" in link:
                done.add(link.rsplit("/", 1)[-1])
    return done


def abs_image(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    return text


def fetch_batch_graphql(
    handles: list[str], proxies: list[str]
) -> list[dict[str, str]]:
    if not handles:
        return []

    var_defs = ", ".join(f"$h{i}: String!" for i in range(len(handles)))
    selections = "\n".join(
        f"p{i}: product(handle: $h{i}) {{\n{NODE_SEL}\n}}"
        for i in range(len(handles))
    )
    query = f"query BatchProducts({var_defs}) {{\n{selections}\n}}"
    variables = {f"h{i}": handle for i, handle in enumerate(handles)}

    for attempt in range(4):
        sess = page_session(proxies)
        try:
            response = sess.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                timeout=40,
            )
            if response.status_code in (429, 430, 503):
                reset_tls_session(proxies)
                time.sleep(min(1.2 * (attempt + 1), 6.0))
                continue
            if response.status_code >= 400:
                reset_tls_session(proxies)
                continue
            payload = response.json()
            if payload.get("errors"):
                # throttle / partial — thu proxy khac
                reset_tls_session(proxies)
                time.sleep(min(0.8 * (attempt + 1), 4.0))
                continue
            data = payload.get("data") or {}
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            reset_tls_session(proxies)
            time.sleep(min(0.5 * (attempt + 1), 3.0))
            continue

        rows: list[dict[str, str]] = []
        for i, handle in enumerate(handles):
            node = data.get(f"p{i}")
            if not isinstance(node, dict):
                continue
            title = str(node.get("title") or "").strip()
            handle_val = str(node.get("handle") or handle).strip()
            cate = str(node.get("productType") or "").strip()
            price = ""
            edges = ((node.get("variants") or {}).get("edges")) or []
            if edges:
                amount = (
                    ((edges[0].get("node") or {}).get("price") or {}).get("amount")
                )
                if amount is not None:
                    try:
                        price = f"{float(amount):.2f}"
                    except (TypeError, ValueError):
                        price = str(amount)

            images: list[str] = []
            featured = (node.get("featuredImage") or {}).get("url")
            if featured:
                images.append(abs_image(str(featured)))
            for edge in ((node.get("images") or {}).get("edges")) or []:
                url = ((edge.get("node") or {}).get("url")) if isinstance(edge, dict) else None
                abs_url = abs_image(str(url or ""))
                if abs_url and abs_url not in images:
                    images.append(abs_url)

            rows.append(
                {
                    "title": title,
                    "price": price,
                    "cate": cate,
                    "link": f"{SHOP_URL}/products/{handle_val}",
                    "images": ",".join(images),
                }
            )
        return rows

    return []


def main() -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    proxy_text = filter_good_proxies(load_proxy_text())
    proxies = parse_proxy_list(proxy_text)
    print(
        f"[start] shop={SHOP_URL} workers={WORKERS} batch={BATCH_SIZE} proxies={len(proxies)}",
        flush=True,
    )

    handles = discover_handles(proxies)
    done_set = load_done_handles()
    todo = [h for h in handles if h not in done_set]
    batches = [todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    print(
        f"[crawl] total={len(handles):,} done={len(done_set):,} "
        f"todo={len(todo):,} batches={len(batches):,}",
        flush=True,
    )
    if not todo:
        print("[done] khong con handle can crawl", flush=True)
        return

    new_file = not OUT_CSV.exists() or OUT_CSV.stat().st_size == 0
    ok = 0
    fail_handles = 0
    processed_handles = 0
    t0 = time.time()

    with OUT_CSV.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["title", "price", "cate", "link", "images"],
            extrasaction="ignore",
        )
        if new_file:
            writer.writeheader()
            fh.flush()

        def ingest(batch: list[str], rows: list[dict[str, str]]) -> None:
            nonlocal ok, fail_handles, processed_handles
            got_handles = {
                (row.get("link") or "").rstrip("/").rsplit("/", 1)[-1]
                for row in rows
            }
            with _write_lock:
                for row in rows:
                    writer.writerow(row)
                    ok += 1
                missed = len(batch) - len(got_handles)
                if missed > 0:
                    fail_handles += missed
                processed_handles += len(batch)
                if ok % FLUSH_EVERY == 0:
                    fh.flush()
                if (
                    processed_handles % PROGRESS_EVERY < BATCH_SIZE
                    or processed_handles >= len(todo)
                ):
                    elapsed = max(time.time() - t0, 0.001)
                    speed = processed_handles / elapsed
                    eta_min = ((len(todo) - processed_handles) / max(speed, 0.01)) / 60
                    print(
                        f"[crawl] {processed_handles:,}/{len(todo):,} "
                        f"ok={ok:,} miss={fail_handles:,} "
                        f"{speed:.1f} sp/s eta={eta_min:.0f}m",
                        flush=True,
                    )

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            idx = 0
            in_flight: dict = {}
            while idx < len(batches) or in_flight:
                while idx < len(batches) and len(in_flight) < CHUNK_IN_FLIGHT:
                    batch = batches[idx]
                    fut = executor.submit(fetch_batch_graphql, batch, proxies)
                    in_flight[fut] = batch
                    idx += 1

                done_futs, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for fut in done_futs:
                    batch = in_flight.pop(fut)
                    try:
                        rows = fut.result() or []
                    except Exception:
                        rows = []
                    ingest(batch, rows)

        fh.flush()

    elapsed = time.time() - t0
    print(
        f"[done] ok={ok:,} miss={fail_handles:,} in {elapsed/60:.1f} phut -> {OUT_CSV}",
        flush=True,
    )


if __name__ == "__main__":
    main()
