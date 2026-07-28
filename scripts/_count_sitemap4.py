import re

import requests

url = (
    "https://wearanddecor.com/sitemap_products_4.xml"
    "?from=1000000671241085&to=1000000671239213"
)
r = requests.get(
    url,
    timeout=40,
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    },
)
text = r.text
locs = re.findall(r"<loc>([^<]+)</loc>", text)
prod_locs = [x for x in locs if "/products/" in x]
handles = re.findall(r"/products/([^<?#\s\"']+)", text)
uniq = set(handles)
url_blocks = len(re.findall(r"<url>", text, flags=re.I))
imgs = len(re.findall(r"<image:loc>", text, flags=re.I))
print("status", r.status_code, "bytes", len(text))
print("all_loc", len(locs), "product_loc", len(prod_locs))
print("handles", len(handles), "unique", len(uniq))
print("url_blocks", url_blocks, "image_loc", imgs)

# full index
idx = requests.get("https://wearanddecor.com/sitemap.xml", timeout=30, headers={"User-Agent": "Mozilla/5.0"})
smaps = [
    x.replace("&amp;", "&")
    for x in re.findall(r"<loc>([^<]+)</loc>", idx.text)
    if "sitemap_products" in x.lower()
]
print("product_sitemaps_total", len(smaps))
for i, smap in enumerate(smaps, 1):
    rr = requests.get(smap, timeout=40, headers={"User-Agent": "Mozilla/5.0"})
    n = len(set(re.findall(r"/products/([^<?#\s\"']+)", rr.text)))
    print(f"  sitemap{i}: {n} unique handles")
