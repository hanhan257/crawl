import json

from utils.http_session import DEFAULT_USER_AGENT, create_http_session, parse_proxy_list

cfg = json.load(open("config.json", encoding="utf-8"))
proxy = cfg.get("proxy", {}).get("url", "") if isinstance(cfg.get("proxy"), dict) else ""
proxies = parse_proxy_list(proxy)
s = create_http_session(proxies[0] if proxies else None)
s.headers["User-Agent"] = DEFAULT_USER_AGENT
api = "https://wearanddecor.com/api/catalog/next/products.json"


def probe(page: int):
    r = s.get(
        api,
        params={"page": page, "limit": 50, "collection_handle": "shirts-tops"},
        timeout=30,
    )
    try:
        data = r.json().get("result") or {}
        items = data.get("items") or []
        h = items[0].get("handle") if items else None
        return r.status_code, len(items), data.get("count"), h
    except Exception:
        return r.status_code, -1, None, (r.text or "")[:100].replace("\n", " ")


for page in [1, 50, 100, 150, 180, 190, 200, 201, 220, 250, 300]:
    print("page", page, probe(page), flush=True)

lo, hi = 1, 800
last = 1
while lo <= hi:
    mid = (lo + hi) // 2
    st, n, cnt, h = probe(mid)
    print("bin", mid, st, n, flush=True)
    if n and n > 0:
        last = mid
        lo = mid + 1
    else:
        hi = mid - 1
print("last_ok_page", last, "est_products", last * 50, flush=True)

# also whole-shop no collection
r = s.get(api, params={"page": 1, "limit": 1}, timeout=20)
print("shop_count", (r.json().get("result") or {}).get("count"), flush=True)
