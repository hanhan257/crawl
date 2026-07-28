import json
import re
import time

from crawlers.shopbase import ShopBaseCrawler
from utils.http_session import DEFAULT_USER_AGENT, create_http_session, parse_proxy_list

cfg = json.load(open("config.json", encoding="utf-8"))
proxy = cfg.get("proxy", {}).get("url", "") if isinstance(cfg.get("proxy"), dict) else ""
proxies = parse_proxy_list(proxy)
sess = create_http_session(proxies[0] if proxies else None)
sess.headers["User-Agent"] = DEFAULT_USER_AGENT

url = (
    "https://wearanddecor.com/sitemap_products_1.xml"
    "?from=1000000671474361&to=1000000671336289"
)
t0 = time.time()
r = sess.get(url, timeout=40)
dt = time.time() - t0
locs = re.findall(r"<loc>([^<]+)</loc>", r.text)
imgs = re.findall(r"<image:loc>([^<]+)</image:loc>", r.text)
titles = re.findall(r"<image:title>([^<]+)</image:title>", r.text)
print(
    "sitemap1",
    r.status_code,
    "locs",
    len(locs),
    "images",
    len(imgs),
    "titles",
    len(titles),
    f"{dt:.2f}s",
)
print("has_price_in_xml", "price" in r.text.lower())

t0 = time.time()
idx = sess.get("https://wearanddecor.com/sitemap.xml", timeout=30)
smaps = [
    x.replace("&amp;", "&")
    for x in re.findall(r"<loc>([^<]+)</loc>", idx.text)
    if "sitemap_products" in x.lower()
]
seen: set[str] = set()
for smap in smaps:
    rr = sess.get(smap, timeout=40)
    for handle in re.findall(r"/products/([^<?#\s\"']+)", rr.text):
        seen.add(handle)
print("all_smaps", len(smaps), "unique_handles", len(seen), f"{time.time()-t0:.2f}s")

c = ShopBaseCrawler("https://wearanddecor.com", proxy_url=proxy or None)
t0 = time.time()
items = c._list_catalog_items(max_workers=64)
print("catalog_items", len(items), f"{time.time()-t0:.2f}s")

handles = list(seen)[:30]
t0 = time.time()
ok = 0
for handle in handles:
    product = c.crawl_product(handle)
    if product and product.price is not None:
        ok += 1
elapsed = max(time.time() - t0, 0.01)
print("detail30", ok, f"{elapsed:.2f}s", "cps", round(ok / elapsed, 1))
print(
    "est_full_detail_min",
    round(len(seen) / max(ok / elapsed, 0.01) / 60, 1),
)
