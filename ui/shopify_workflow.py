"""Chế độ Crawl Shopify — crawl shop Shopify, xuất CSV import Shopify."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

from ui.crawl_step1_layout import (
    close_crawl_step1_wrap,
    open_crawl_step1_wrap,
    render_crawl_action_centered,
    render_crawl_description_format,
    render_crawl_include_product_url,
    render_crawl_limits_row,
)
from ui.proxy_panel import render_proxy_panel
from utils.url_preview import describe_url

SHOPIFY_LINK_EXAMPLES: dict[str, str] = {
    "Trang chủ shop": "https://wearandhome.com/",
    "Collection": "https://wearandhome.com/collections/all",
    "1 sản phẩm": "https://wearandhome.com/products/example",
    "Tìm kiếm": "https://wearandhome.com/search?q=shirt",
}

SHOPIFY_EXPORT_FORMATS = [
    "shopify-csv",
    "web-feed-csv",
    "woocommerce-csv",
]


def validate_shopify_url(url: str) -> dict[str, Any]:
    cleaned = url.strip()
    if not cleaned:
        return {"ok": False, "crawl_ready": False, "message": "Nhập link shop Shopify."}

    info = describe_url(cleaned)
    target = info.get("target")
    platform = getattr(target, "platform", "") if target else ""

    if not info.get("ok"):
        return {
            "ok": False,
            "crawl_ready": False,
            "message": info.get("message", "Link không hợp lệ."),
        }

    if platform != "shopify":
        return {
            "ok": False,
            "crawl_ready": False,
            "message": (
                "Link này không phải **Shopify**. "
                "Chọn **Crawl từ link** nếu shop Woo / ShopBase / web khác, "
                "hoặc **Crawl Trendsi** nếu link trendsi.com."
            ),
        }

    if not info.get("crawl_ready", True):
        return {
            "ok": True,
            "crawl_ready": False,
            "message": info.get("notice")
            or info.get("hint")
            or (
                "Shopify: dán trang chủ · `/collections/...` · `/products/...` · "
                "`/search?q=...` — shop lớn / 429: dùng **Crawl theo sitemap**."
            ),
        }

    return {
        "ok": True,
        "crawl_ready": True,
        "message": info.get("message", ""),
        "info": info,
    }


def render_shopify_crawl_panel(
    *,
    render_url_preview: Callable[..., None],
    refresh_crawl_messages: Callable[[str], None],
    on_crawl_url_change: Callable[[], None],
    clear_image_cache: Callable[[], None],
    run_crawl: Callable[..., None] | None = None,
    update_crawl_ui: Callable[..., None] | None = None,
) -> tuple[str, int, int, bool]:
    """Bước 1 — link Shopify bên trái, cài đặt crawl bên phải."""
    st.markdown("### Bước 1 · Crawl Shopify")
    st.caption(
        "Crawl shop **Shopify** (trang chủ · collection · 1 SP · tìm kiếm). "
        "Xuất **CSV import Shopify** ở Bước 2 (template `fileShopify.txt`, Color metafield). "
        "**Không** dùng chế độ này cho trendsi.com — chọn **Crawl Trendsi**."
    )

    open_crawl_step1_wrap()
    left_col, right_col = st.columns([3, 2], gap="medium")

    with left_col:
        url = st.text_input(
            "Link Shopify",
            placeholder="https://example.myshopify.com/ hoặc https://example.com/collections/all",
            help="Trang chủ, collection, sản phẩm hoặc /search?q=...",
            key="crawl_url",
            on_change=on_crawl_url_change,
        )
        refresh_crawl_messages(url)

        check = validate_shopify_url(url)
        if url.strip():
            if check.get("crawl_ready"):
                st.success(f"Link OK · **{check['message']}**")
            elif check.get("ok"):
                st.warning(check["message"])
            else:
                st.error(check["message"])

        render_url_preview(url, "", "", "")

    with right_col:
        st.markdown('<div class="crawl-side-tools">', unsafe_allow_html=True)
        picked = st.selectbox(
            "Link mẫu",
            options=[""] + list(SHOPIFY_LINK_EXAMPLES.keys()),
            format_func=lambda key: "— Chọn mẫu —" if not key else key,
            key="shopify_example_pick",
        )
        if picked and st.button(
            "Dùng link mẫu", use_container_width=True, key="shopify_use_example"
        ):
            st.session_state.crawl_url = SHOPIFY_LINK_EXAMPLES[picked]
            refresh_crawl_messages(st.session_state.crawl_url)
            st.rerun()

        render_proxy_panel(
            expanded=True,
            title="Proxy (nếu bị 429 / chặn IP)",
            key_prefix="shopify_",
        )

        if st.button(
            "Làm mới session HTTP", use_container_width=True, key="shopify_reset_session"
        ):
            clear_image_cache()
            st.session_state.last_info = "Đã reset session HTTP + cache ảnh."
        st.markdown("</div>", unsafe_allow_html=True)

    render_crawl_limits_row(include_workers=True)
    render_crawl_description_format()
    render_crawl_include_product_url()

    limit = int(st.session_state.get("crawl_limit", 0))
    pages = int(st.session_state.get("crawl_pages", 0))
    crawl_ready = bool(check.get("crawl_ready"))

    crawl_clicked, crawl_action, crawl_live = render_crawl_action_centered(
        label="Crawl Shopify",
        button_key="shopify_crawl_btn",
        crawl_ready=crawl_ready,
        not_ready_help="Dán link shop Shopify hợp lệ (trang chủ, collection, SP…).",
        is_running=bool(st.session_state.get("crawl_running")),
        update_live_ui=update_crawl_ui,
        matched_count=int(st.session_state.get("crawl_matched_count", 0)),
    )
    close_crawl_step1_wrap()

    if crawl_clicked and crawl_ready and run_crawl is not None:
        run_crawl(
            url,
            limit,
            pages,
            "",
            "",
            "",
            category_slug=st.session_state.get("crawl_category_pill", "Tất cả"),
            action_area=crawl_action,
            live_area=crawl_live,
        )
        st.rerun()

    return url, limit, pages, False


def render_shopify_export_selector() -> str | None:
    """Bước 2 — mặc định CSV import Shopify."""
    st.markdown("### Bước 2 · Chọn file xuất")
    st.caption(
        "Mặc định **CSV import Shopify** (48 cột, 1 dòng/biến thể Color/Size). "
        "Có thể chọn feed web hoặc Woo nếu cần."
    )
    options = {
        "shopify-csv": "CSV import Shopify",
        "web-feed-csv": "CSV feed web thuần",
        "woocommerce-csv": "CSV import WooCommerce",
    }

    export_format = st.radio(
        "Định dạng file",
        options=list(options.keys()),
        format_func=lambda key: options[key],
        index=0,
        key="export_format",
        horizontal=False,
    )
    return export_format
