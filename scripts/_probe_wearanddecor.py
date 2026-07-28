import json
import re

from utils.detector import resolve_url
from utils.http_session import DEFAULT_USER_AGENT, create_http_session, parse_proxy_list

cfg = json.load(open("config.json", encoding="utf-8"))
proxy = cfg.get("proxy", {}).get("url", "") if isinstance(cfg.get("proxy"), dict) else ""
proxies = parse_proxy_list(proxy)
sess = create_http_session(proxies[0] if proxies else None)
sess.headers["User-Agent"] = DEFAULT_USER_AGENT

print("detect", resolve_url("https://wearanddecor.com/"))

r = sess.get(
    "https://wearanddecor.com/sitemap_products_1.xml?from=1000000671474361&to=1000000671336289",
    timeout=30,
)
handles = re.findall(r"/products/([^<?#\s\"']+)", r.text)
print("handles", len(handles), handles[0] if handles else None)
h = handles[0] if handles else ""

paths = [
    f"/products/{h}.js",
    f"/products/{h}.json",
    f"/api/catalog/products.json",
    f"/api/products/{h}.json",
    "/collections/all/products.json",
]
for path in paths:
    try:
        rr = sess.get("https://wearanddecor.com" + path, timeout=20)
        print(path[:70], rr.status_code, len(rr.content), (rr.headers.get("content-type") or "")[:40])
        ct = rr.headers.get("content-type") or ""
        if rr.status_code == 200 and "json" in ct:
            print(" ", rr.text[:280].replace("\n", " "))
    except Exception as exc:
        print(path, type(exc).__name__, exc)

home = sess.get("https://wearanddecor.com/", timeout=25)
t = home.text
for tip in [
    "Shopify",
    "cdn.shopify",
    "myshopify",
    "shopbase",
    "ShopBase",
    "woocommerce",
    "wp-content",
    "window.ShopifyAnalytics",
    "Shopify.theme",
    "shopline",
    "gempages",
]:
    print(tip, tip.lower() in t.lower())

# count all handles
idx = sess.get("https://wearanddecor.com/sitemap.xml", timeout=25)
smaps = [
    x.replace("&amp;", "&")
    for x in re.findall(r"<loc>([^<]+)</loc>", idx.text)
    if "sitemap_products" in x.lower()
]
seen = set()
for smap in smaps:
    rr = sess.get(smap, timeout=40)
    for h2 in re.findall(r"/products/([^<?#\s\"']+)", rr.text):
        seen.add(h2)
print("unique_handles", len(seen), "smaps", len(smaps))
