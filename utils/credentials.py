from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CrawlCredentials:
    shopify_token: str | None = None
    woocommerce_key: str | None = None
    woocommerce_secret: str | None = None
    proxy_url: str | None = None
    config_path: str | None = None

    @classmethod
    def load(cls, config_path: str | None = None) -> CrawlCredentials:
        path = Path(config_path or os.environ.get("CRAWL_CONFIG", "config.json"))
        creds = cls(config_path=str(path) if path.exists() else None)

        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            shopify = data.get("shopify") or {}
            woo = data.get("woocommerce") or {}
            proxy = data.get("proxy") or {}
            creds.shopify_token = shopify.get("token") or creds.shopify_token
            creds.woocommerce_key = woo.get("key") or creds.woocommerce_key
            creds.woocommerce_secret = woo.get("secret") or creds.woocommerce_secret
            creds.proxy_url = proxy.get("url") or creds.proxy_url
            proxies = proxy.get("proxies")
            if isinstance(proxies, list):
                lines = [str(p).strip() for p in proxies if str(p).strip()]
                if lines:
                    creds.proxy_url = "\n".join(lines)

        creds.shopify_token = creds.shopify_token or os.environ.get("SHOPIFY_TOKEN")
        creds.woocommerce_key = creds.woocommerce_key or os.environ.get("WC_KEY")
        creds.woocommerce_secret = creds.woocommerce_secret or os.environ.get("WC_SECRET")
        creds.proxy_url = (
            creds.proxy_url
            or os.environ.get("CRAWL_PROXY")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
        )
        creds.proxy_url = _normalize_proxy_storage(creds.proxy_url)
        return creds

    def save_proxy(self, proxy_url: str) -> Path:
        """Ghi danh sách proxy vào config.json (mỗi dòng 1 proxy)."""
        path = Path(self.config_path or os.environ.get("CRAWL_CONFIG", "config.json"))
        data: dict = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        cleaned = _normalize_proxy_storage(proxy_url) or ""
        data.setdefault("proxy", {})["url"] = cleaned
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.config_path = str(path)
        self.proxy_url = cleaned or None
        return path

    def with_proxy(self, proxy_url: str | None) -> CrawlCredentials:
        return CrawlCredentials(
            shopify_token=self.shopify_token,
            woocommerce_key=self.woocommerce_key,
            woocommerce_secret=self.woocommerce_secret,
            proxy_url=normalize_proxy_override(proxy_url) or self.proxy_url,
            config_path=self.config_path,
        )

    def shopify_token_for(self, shop_host: str) -> str | None:
        if not self.config_path:
            return self.shopify_token
        data = json.loads(Path(self.config_path).read_text(encoding="utf-8"))
        shops = (data.get("shopify") or {}).get("shops") or {}
        return shops.get(shop_host) or self.shopify_token

    def woocommerce_for(self, site_host: str) -> tuple[str | None, str | None]:
        if not self.config_path:
            return self.woocommerce_key, self.woocommerce_secret
        data = json.loads(Path(self.config_path).read_text(encoding="utf-8"))
        sites = (data.get("woocommerce") or {}).get("sites") or {}
        site_cfg = sites.get(site_host) or {}
        return (
            site_cfg.get("key") or self.woocommerce_key,
            site_cfg.get("secret") or self.woocommerce_secret,
        )


def normalize_proxy_override(proxy_url: str | None) -> str | None:
    if proxy_url is None:
        return None
    cleaned = str(proxy_url).strip()
    return cleaned or None


def _normalize_proxy_storage(proxy_url: str | None) -> str | None:
    """Chuẩn hóa proxy lưu file — giữ nhiều dòng, bỏ dòng trống."""
    if not proxy_url:
        return None
    lines = [line.strip() for line in str(proxy_url).splitlines() if line.strip()]
    if not lines:
        return None
    return "\n".join(lines)


def load_saved_proxy() -> str:
    return CrawlCredentials.load().proxy_url or ""
