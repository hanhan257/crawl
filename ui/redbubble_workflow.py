"""Chế độ Crawl Redbubble — UI tách riêng để dễ bảo trì."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

from ui.crawl_step1_layout import (
    close_crawl_step1_wrap,
    open_crawl_step1_wrap,
    render_crawl_action_centered,
    render_crawl_limits_row,
)
from ui.proxy_panel import render_proxy_panel
from utils.url_preview import describe_url

REDBUBBLE_LINK_EXAMPLES: dict[str, str] = {
    "Kids t-shirts": "https://www.redbubble.com/shop/kids-tshirts",
    "Shower curtains": "https://www.redbubble.com/shop/shower-curtains",
    "T-shirts": "https://www.redbubble.com/shop/t-shirts",
    "Stickers": "https://www.redbubble.com/shop/stickers",
    "Hoodies": "https://www.redbubble.com/shop/hoodies",
}

REDBUBBLE_EXPORT_FORMATS = ["web-feed-csv", "woocommerce-csv", "shopify-csv"]


def validate_redbubble_url(url: str) -> dict[str, Any]:
    cleaned = url.strip()
    if not cleaned:
        return {"ok": False, "crawl_ready": False, "message": "Nhập link Redbubble."}

    info = describe_url(cleaned)
    target = info.get("target")
    platform = getattr(target, "platform", "") if target else ""

    if not info.get("ok"):
        return {
            "ok": False,
            "crawl_ready": False,
            "message": info.get("message", "Link không hợp lệ."),
        }

    if platform != "redbubble":
        return {
            "ok": False,
            "crawl_ready": False,
            "message": (
                "Link này không phải **Redbubble**. "
                "Chọn chế độ **Crawl từ link** nếu crawl shop khác."
            ),
        }

    if not info.get("crawl_ready", True):
        return {
            "ok": True,
            "crawl_ready": False,
            "message": info.get("notice")
            or info.get("hint")
            or "Dán link `/shop/từ-khóa` hoặc `/i/.../sản-phẩm` — không dùng trang chủ.",
        }

    return {
        "ok": True,
        "crawl_ready": True,
        "message": info.get("message", ""),
        "info": info,
    }


def render_redbubble_crawl_panel(
    *,
    render_url_preview: Callable[..., None],
    refresh_crawl_messages: Callable[[str], None],
    on_crawl_url_change: Callable[[], None],
    clear_image_cache: Callable[[], None],
    run_crawl: Callable[..., None] | None = None,
    update_crawl_ui: Callable[..., None] | None = None,
) -> tuple[str, int, int, bool]:
    """Bước 1 — link bên trái, cài đặt crawl bên phải."""
    st.markdown("### Bước 1 · Crawl Redbubble")
    st.caption(
        "Chỉ hỗ trợ link **Redbubble**: `/shop/từ-khóa` (nhiều SP) hoặc `/i/...` (1 SP). "
        "**Không** crawl được từ trang chủ `redbubble.com`."
    )

    open_crawl_step1_wrap()
    left_col, right_col = st.columns([3, 2], gap="medium")

    with left_col:
        url = st.text_input(
            "Link Redbubble",
            placeholder="https://www.redbubble.com/shop/kids-tshirts",
            help="Ví dụ category: /shop/kids-tshirts · 1 SP: /i/.../.../123456",
            key="crawl_url",
            on_change=on_crawl_url_change,
        )
        refresh_crawl_messages(url)

        check = validate_redbubble_url(url)
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
            options=[""] + list(REDBUBBLE_LINK_EXAMPLES.keys()),
            format_func=lambda key: "— Chọn mẫu —" if not key else key,
            key="redbubble_example_pick",
        )
        if picked and st.button(
            "Dùng link mẫu", use_container_width=True, key="redbubble_use_example"
        ):
            st.session_state.crawl_url = REDBUBBLE_LINK_EXAMPLES[picked]
            refresh_crawl_messages(st.session_state.crawl_url)
            st.rerun()

        render_proxy_panel(
            expanded=True,
            title="Proxy (bắt buộc nếu bị chặn IP)",
            key_prefix="rb_",
        )

        if st.button(
            "Làm mới session HTTP", use_container_width=True, key="rb_reset_session"
        ):
            clear_image_cache()
            st.session_state.last_info = "Đã reset session HTTP + cache ảnh."
        st.markdown("</div>", unsafe_allow_html=True)

    render_crawl_limits_row(include_workers=False)

    limit = int(st.session_state.get("crawl_limit", 0))
    pages = int(st.session_state.get("crawl_pages", 0))
    crawl_ready = bool(check.get("crawl_ready"))

    crawl_clicked, crawl_action, crawl_live = render_crawl_action_centered(
        label="Crawl Redbubble",
        button_key="redbubble_crawl_btn",
        crawl_ready=crawl_ready,
        not_ready_help="Dán link /shop/... hoặc /i/... — trang chủ Redbubble không crawl được.",
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


def render_redbubble_export_selector() -> str | None:
    """Bước 2 — định dạng xuất phổ biến khi bán lại SP Redbubble."""
    st.markdown("### Bước 2 · Chọn file xuất")
    options = {
        "web-feed-csv": "CSV feed web thuần",
        "woocommerce-csv": "CSV import WooCommerce",
        "shopify-csv": "CSV import Shopify",
    }

    export_format = st.radio(
        "Định dạng file",
        options=list(options.keys()),
        format_func=lambda key: options[key],
        index=None,
        key="export_format",
        horizontal=True,
    )
    if not export_format:
        st.caption("Chưa chọn định dạng — bấm một tuỳ chọn phía trên trước khi xuất file.")
        return None
    return export_format
