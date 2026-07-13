from __future__ import annotations

import html
import json
import re
from typing import Any


def normalize_description_html(fragment: str) -> str:
    text = html.unescape(fragment.strip())
    text = re.sub(r"[\r\n]+", "", text)
    text = re.sub(r">\s+<", "><", text)
    return text.strip()


def html_to_plain_text(fragment: str) -> str:
    """Chuyển mô tả HTML → text thuần (giữ xuống dòng hợp lý)."""
    if not fragment or not str(fragment).strip():
        return ""
    text = html.unescape(str(fragment))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*p\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*div\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*li\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "• ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


_BLOCK_HTML_RE = re.compile(
    r"(?i)</?(?:p|div|ul|ol|li|h[1-6]|table|section|article|blockquote)\b"
)


def sparse_html_to_paragraphs(fragment: str) -> str | None:
    """
    Magento/Jomashop thường trả text + <br/> (không có <p>).
    Chế độ HTML → bọc thành các thẻ <p>…</p>.
    """
    raw = (fragment or "").strip()
    if not raw:
        return None

    # Đã có HTML khối thật → giữ nguyên (chuẩn hóa nhẹ).
    if _BLOCK_HTML_RE.search(raw):
        return description_from_api_value(raw)

    # Text thuần hoặc chỉ có <br>/<hr> → tách đoạn rồi bọc <p>.
    parts = re.split(r"(?i)<br\s*/?>|<hr\s*/?>", raw)
    paragraphs: list[str] = []
    for part in parts:
        cleaned = re.sub(r"<[^>]+>", " ", part)
        cleaned = html.unescape(cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            paragraphs.append(f"<p>{html.escape(cleaned)}</p>")
    if paragraphs:
        return "".join(paragraphs)

    return description_from_api_value(raw)


def format_description_output(content: str | None, fmt: str) -> str | None:
    """Chuẩn hóa mô tả theo lựa chọn user: html | text."""
    raw = (content or "").strip()
    if not raw:
        return None
    mode = (fmt or "html").strip().lower()
    if mode == "text":
        plain = html_to_plain_text(raw)
        return plain or None
    # html — bọc đoạn cho mô tả Magento (text + br)
    return sparse_html_to_paragraphs(raw)


def description_from_api_value(value: Any) -> str | None:
    """Giữ HTML từ API Woo/Shopify — không strip thành plain text."""
    if value is None:
        return None
    if isinstance(value, list):
        value = " ".join(str(part) for part in value if part is not None)
    if not isinstance(value, str):
        value = str(value)
    text = normalize_description_html(value)
    if not text:
        return None
    if text in {"<p></p>", "<p><br></p>", "<p><br/></p>"}:
        return None
    if "<" not in text or ">" not in text:
        if "\n" in text or "•" in text or "·" in text:
            return text_bullets_to_html(text)
        return f"<p>{html.escape(text)}</p>"
    return text


def woo_description_from_item(item: dict[str, Any]) -> str | None:
    """Ưu tiên description đầy đủ, fallback short_description — giữ HTML WP."""
    full = description_from_api_value(item.get("description"))
    if full:
        return full
    return description_from_api_value(item.get("short_description"))


def shopify_description_from_item(item: dict[str, Any]) -> str | None:
    return description_from_api_value(item.get("body_html"))


def shopbase_description_from_item(item: dict[str, Any]) -> str | None:
    """ShopBase / Shopify-like JSON — giữ HTML mô tả."""
    for key in ("body_html", "bodyHtml", "description", "content", "content_html"):
        desc = description_from_api_value(item.get(key))
        if desc:
            return desc
    return None


def generic_payload_description(data: dict[str, Any]) -> str | None:
    """JSON payload web thuần — thử nhiều key mô tả phổ biến."""
    for key in (
        "body_html",
        "bodyHtml",
        "description_html",
        "description",
        "short_description",
        "content",
        "summary",
        "detail",
        "product_description",
    ):
        desc = description_from_api_value(data.get(key))
        if desc:
            return desc
    return None


def text_bullets_to_html(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return text.strip()
    if len(lines) == 1 and "<" in lines[0] and ">" in lines[0]:
        return normalize_description_html(lines[0])
    items = "".join(f"<li>{html.escape(ln)}</li>" for ln in lines)
    return f"<ul>{items}</ul>"


def description_from_json_ld(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "<" in text and ">" in text:
        return description_from_api_value(text)
    if "\n" in text or "•" in text or "·" in text:
        return text_bullets_to_html(text)
    return f"<p>{html.escape(text)}</p>"


def resolve_product_description(
    *,
    data: dict[str, Any] | None = None,
    page_html: str = "",
) -> str | None:
    if data:
        for resolver in (
            shopbase_description_from_item,
            shopify_description_from_item,
            woo_description_from_item,
            generic_payload_description,
        ):
            desc = resolver(data)
            if desc:
                return desc
    if page_html:
        return extract_generic_description_from_html(page_html)
    return None

def extract_magento_description_from_html(html_text: str) -> str | None:
    """Magento / Silverbene — itemprop description, bullet list."""
    for pattern in (
        r'<div[^>]*class="[^"]*\bvalue\b[^"]*"[^>]*itemprop="description"[^>]*>(.*?)</div>',
        r'itemprop="description"[^>]*>\s*(<ul>.*?</ul>)',
        r'class="[^"]*product attribute overview[^"]*"[^>]*>.*?'
        r'<div[^>]*class="[^"]*\bvalue\b[^"]*"[^>]*>(.*?)</div>',
    ):
        match = re.search(pattern, html_text, re.I | re.S)
        if match:
            inner = normalize_description_html(match.group(1))
            if inner and "consent-description" not in inner.lower():
                return inner
    return None


def _meta_content(tag_html: str) -> str:
    match = re.search(r'content="([^"]*)"', tag_html, re.I | re.S)
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def extract_generic_description_from_html(html_text: str) -> str | None:
    """Trích mô tả HTML từ trang SP — web thuần, ShopBase, Woo theme, Shopify theme."""
    found = extract_magento_description_from_html(html_text)
    if found:
        return found

    for tag in re.findall(r"<meta[^>]+itemprop=[\"']description[\"'][^>]*>", html_text, re.I):
        text = _meta_content(tag)
        if text:
            return text_bullets_to_html(text)

    for tag in re.findall(r'<meta[^>]+name=["\']description["\'][^>]*>', html_text, re.I):
        text = _meta_content(tag)
        if text and ("main stone:" in text.lower() or len(text) > 80):
            return text_bullets_to_html(text)

    for extractor in (
        extract_woo_description_from_html,
        extract_shopify_description_from_html,
    ):
        found = extractor(html_text)
        if found:
            return found

    found = _first_html_match(
        html_text,
        (
            r'<div[^>]*class="[^"]*product-detail-description[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*product-content[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*product__content[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*\brte\b[^"]*"[^>]*>(.*?)</div>',
            r'<section[^>]*class="[^"]*product-description[^"]*"[^>]*>(.*?)</section>',
        ),
    )
    if found:
        return found

    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text,
        re.I | re.S,
    ):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("@type") or "").lower() != "product":
                continue
            desc = description_from_json_ld(item.get("description"))
            if desc:
                return desc
    return None


def _first_html_match(html_text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, html_text, re.I | re.S)
        if not match:
            continue
        inner = normalize_description_html(match.group(1))
        if inner and len(re.sub(r"<[^>]+>", "", inner).strip()) >= 12:
            return inner
    return None


def extract_woo_description_from_html(html_text: str) -> str | None:
    """Lấy mô tả từ trang SP WooCommerce khi API trống."""
    return _first_html_match(
        html_text,
        (
            r'<div[^>]*class="[^"]*woocommerce-Tabs-panel--description[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*id="tab-description"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*woocommerce-product-details__short-description[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*product-description[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*entry-content[^"]*"[^>]*>(.*?)</div>',
        ),
    )


def extract_shopify_description_from_html(html_text: str) -> str | None:
    """Lấy mô tả từ trang SP Shopify khi body_html API trống."""
    found = _first_html_match(
        html_text,
        (
            r'<div[^>]*class="[^"]*product__description[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*product-single__description[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*product-description[^"]*"[^>]*>(.*?)</div>',
        ),
    )
    if found:
        return found

    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text,
        re.I | re.S,
    ):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("@type") or "").lower() != "product":
                continue
            desc = description_from_json_ld(item.get("description"))
            if desc:
                return desc
    return None
