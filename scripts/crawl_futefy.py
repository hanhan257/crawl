"""Crawl futefy.com (Shopify public JSON) -> CSV feed web thuần (Trendsi-style)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.shopify import ShopifyCrawler  # noqa: E402
from exporters.web_feed_import import export_web_feed_import  # noqa: E402
from models.export_fields import ExportFields  # noqa: E402
from utils.shopify_color_swatches import attach_shopify_color_swatches  # noqa: E402

SHOP = "https://futefy.com"
OUT = ROOT / "output" / "futefy_web_feed.csv"
WORKERS = 32
BRAND = "Futefy"


def main() -> None:
    print(f"[start] {SHOP} -> web feed (Trendsi-style)", flush=True)
    t0 = time.time()

    crawler = ShopifyCrawler(
        shop_url=SHOP,
        use_public_api=True,
        proxy_url=None,
        request_delay=0.05,
    )

    def progress(current: int, total: int, matched: int) -> None:
        if current == 1 or current == total or current % 200 == 0:
            print(f"[crawl] page {current}/{total} products={matched}", flush=True)

    products = crawler.crawl(max_workers=WORKERS, progress_callback=progress)
    print(f"[crawl] raw={len(products)} in {time.time() - t0:.1f}s", flush=True)

    for product in products:
        product.vendor = BRAND
        attach_shopify_color_swatches(product)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    export_web_feed_import(
        products,
        str(OUT),
        fields=ExportFields.from_preset("shop"),
        max_images_per_product=10,
    )

    print(
        f"[done] rows={len(products):,} elapsed={time.time() - t0:.1f}s file={OUT}",
        flush=True,
    )


if __name__ == "__main__":
    main()
