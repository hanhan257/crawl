"""Chế độ Crawl Redbubble — UI tách riêng để dễ bảo trì."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

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
) -> tuple[str, int, int, bool]:
    """Bước 1 — panel crawl chỉ dành cho Redbubble."""
    st.markdown("### Bước 1 · Crawl Redbubble")
    st.caption(
        "Chỉ hỗ trợ link **Redbubble**: `/shop/từ-khóa` (nhiều SP) hoặc `/i/...` (1 SP). "
        "**Không** crawl được từ trang chủ `redbubble.com`."
    )

    example_col, pick_col = st.columns([3, 2])
    with pick_col:
        picked = st.selectbox(
            "Link mẫu",
            options=[""] + list(REDBUBBLE_LINK_EXAMPLES.keys()),
            format_func=lambda key: "— Chọn mẫu —" if not key else key,
            key="redbubble_example_pick",
        )
        if picked and st.button("Dùng link mẫu", use_container_width=True, key="redbubble_use_example"):
            st.session_state.crawl_url = REDBUBBLE_LINK_EXAMPLES[picked]
            refresh_crawl_messages(st.session_state.crawl_url)
            st.rerun()

    with example_col:
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

    lim_col, page_col = st.columns(2)
    with lim_col:
        st.number_input(
            "Giới hạn SP",
            min_value=0,
            help="0 = lấy hết trang hiện tại. Nên thử **10–50** trước. Mỗi trang ~82 SP.",
            key="crawl_limit",
        )
    with page_col:
        st.number_input(
            "Số trang",
            min_value=0,
            help="Redbubble ~82 SP/trang. Ví dụ 1000 SP ≈ **13 trang**.",
            key="crawl_pages",
        )
    limit = int(st.session_state.get("crawl_limit", 0))
    pages = int(st.session_state.get("crawl_pages", 0))

    render_proxy_panel(
        expanded=True,
        title="Proxy (bắt buộc nếu bị chặn IP)",
        key_prefix="rb_",
    )

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("Làm mới session HTTP", use_container_width=True, key="rb_reset_session"):
            clear_image_cache()
            st.session_state.last_info = "Đã reset session HTTP + cache ảnh."

    crawl_ready = bool(check.get("crawl_ready"))
    crawl_clicked = st.button(
        "Crawl Redbubble",
        type="primary",
        use_container_width=True,
        key="redbubble_crawl_btn",
        disabled=not crawl_ready,
        help=(
            None
            if crawl_ready
            else "Dán link /shop/... hoặc /i/... — trang chủ Redbubble không crawl được."
        ),
    )
    return url, limit, pages, crawl_clicked


def render_redbubble_export_selector() -> str:
    """Bước 2 — định dạng xuất phổ biến khi bán lại SP Redbubble."""
    st.markdown("### Bước 2 · Chọn file xuất")
    st.caption(
        "Mặc định **Feed web** (title, description, images, price…). "
        "Có thể đổi sang WooCommerce / Shopify."
    )
    options = {
        "web-feed-csv": "CSV feed web thuần",
        "woocommerce-csv": "CSV import WooCommerce",
        "shopify-csv": "CSV import Shopify",
    }
    current = st.session_state.get("export_format", "web-feed-csv")
    if current not in options:
        current = "web-feed-csv"

    export_format = st.radio(
        "Định dạng file",
        options=list(options.keys()),
        format_func=lambda key: options[key],
        index=list(options.keys()).index(current),
        key="redbubble_export_format",
        horizontal=True,
    )
    st.session_state.export_format = export_format
    return export_format
