"""Layout Bước 1 crawl — hàng số 1·2·3 và nút crawl giữa."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st


def open_crawl_step1_wrap() -> None:
    st.markdown('<div class="crawl-step1-wrap">', unsafe_allow_html=True)


def close_crawl_step1_wrap() -> None:
    st.markdown("</div>", unsafe_allow_html=True)


def render_crawl_limits_row(*, include_workers: bool = True) -> None:
    st.markdown('<div class="crawl-limits-row">', unsafe_allow_html=True)
    col_count = 3 if include_workers else 2
    cols = st.columns(col_count, gap="small")
    sitemap_mode = bool(st.session_state.get("crawl_via_sitemap"))
    worker_max = 500
    worker_help = (
        "Tối đa **500** luồng. Sitemap Shopify lớn: **100–500** + proxy. "
        "Số trang = số file sitemap_products tối đa (0 = hết)."
        if sitemap_mode
        else (
            "Tối đa **500** luồng (nên bật proxy khi >32). "
            "ShopBase/HTML: **24–64** thường đủ — quá cao dễ thiếu SP / lỗi ảnh."
        )
    )

    fields: list[tuple[int, str, str, dict[str, Any]]] = [
        (
            1,
            "Giới hạn SP",
            "crawl_limit",
            {"min_value": 0, "help": "0 = lấy hết. Shop >5k SP: tool tự dùng sitemap nếu cần."},
        ),
        (
            2,
            "Số trang",
            "crawl_pages",
            {
                "min_value": 0,
                "help": (
                    "Sitemap: 0 = hết file sitemap_products. "
                    "API thường: 0 = hết trang."
                    if sitemap_mode
                    else "0 = hết trang."
                ),
            },
        ),
    ]
    if include_workers:
        fields.append(
            (
                3,
                "Luồng crawl",
                "crawl_workers_n",
                {
                    "min_value": 1,
                    "max_value": worker_max,
                    "help": worker_help,
                },
            )
        )

    for col, (number, label, key, options) in zip(cols, fields):
        with col:
            st.markdown(
                (
                    f'<div class="crawl-num-field">'
                    f'<span class="crawl-num">{number}</span>'
                    f'<span class="crawl-num-text">{label}</span>'
                    f"</div>"
                ),
                unsafe_allow_html=True,
            )
            st.number_input(label, label_visibility="collapsed", key=key, **options)

    st.markdown("</div>", unsafe_allow_html=True)


def _on_crawl_description_format_change() -> None:
    fmt = str(st.session_state.get("crawl_description_format") or "").strip().lower()
    if fmt in {"html", "text"}:
        st.session_state.crawl_field_description = True


def render_crawl_description_format() -> None:
    """HTML / text không chọn sẵn. Khách chọn thì mới lấy mô tả."""
    radio_kwargs: dict = {}
    if "crawl_description_format" not in st.session_state:
        radio_kwargs["index"] = None
    st.radio(
        "Mô tả sản phẩm khi crawl",
        options=["html", "text"],
        format_func=lambda key: "HTML" if key == "html" else "Text thuần",
        horizontal=True,
        key="crawl_description_format",
        on_change=_on_crawl_description_format_change,
        help=(
            "Không chọn thì không lấy mô tả. "
            "**HTML** giữ thẻ (`<p>…</p>`). **Text thuần** chỉ còn chữ."
        ),
        **radio_kwargs,
    )
    picked = str(st.session_state.get("crawl_description_format") or "").strip().lower()
    if picked not in {"html", "text"}:
        st.caption("Chọn **HTML** hoặc **Text thuần** thì mới lấy mô tả.")


def render_crawl_include_product_url() -> None:
    """Giữ cho workflow Shopify/Trendsi/Redbubble — chỉ link SP."""
    st.checkbox(
        "Lấy cột link sản phẩm",
        key="crawl_include_product_url",
        help=(
            "Bật (mặc định) để giữ **URL sản phẩm nguồn** trong bảng sửa và file xuất. "
            "Tắt nếu không cần cột link."
        ),
    )


def render_crawl_via_sitemap() -> None:
    """Deprecated — sitemap là chế độ sidebar riêng. Giữ stub tránh import lỗi."""
    return


def render_crawl_action_centered(
    *,
    label: str,
    button_key: str,
    crawl_ready: bool,
    not_ready_help: str,
    is_running: bool,
    update_live_ui: Callable[..., None] | None,
    matched_count: int = 0,
) -> tuple[bool, Any, Any]:
    st.markdown('<div class="crawl-action-center">', unsafe_allow_html=True)
    pad_left, btn_col, pad_right = st.columns([2, 3, 2])
    crawl_clicked = False

    with btn_col:
        crawl_action = st.empty()
        crawl_live = st.empty()
        if is_running and update_live_ui is not None:
            update_live_ui(crawl_action, crawl_live, matched_count)
        else:
            crawl_clicked = crawl_action.button(
                label,
                type="primary",
                use_container_width=True,
                key=button_key,
                disabled=not crawl_ready,
                help=None if crawl_ready else not_ready_help,
            )

    st.markdown("</div>", unsafe_allow_html=True)
    return crawl_clicked, crawl_action, crawl_live
