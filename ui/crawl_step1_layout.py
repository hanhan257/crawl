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

    fields: list[tuple[int, str, str, dict[str, Any]]] = [
        (
            1,
            "Giới hạn SP",
            "crawl_limit",
            {"min_value": 0, "help": "0 = lấy hết. Shop lớn nên thử 50–500 trước."},
        ),
        (
            2,
            "Số trang",
            "crawl_pages",
            {"min_value": 0, "help": "0 = hết trang."},
        ),
    ]
    if include_workers:
        fields.append(
            (
                3,
                "Luồng crawl",
                "crawl_workers",
                {
                    "min_value": 1,
                    "max_value": 32,
                    "help": (
                        "ShopBase (nebsport…): **24–32** luồng. "
                        "Web thuần HTML (Silverbene): **16–32** — quá cao dễ thiếu SP / lỗi ảnh."
                    ),
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
