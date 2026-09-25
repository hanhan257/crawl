"""UI helpers — chọn trường crawl Bước 1."""

from __future__ import annotations

import streamlit as st

from models.crawl_fields import ALL_CRAWL_FIELD_KEYS, CRAWL_FIELD_LABELS


def _set_all_crawl_field_flags(value: bool) -> None:
    for key in ALL_CRAWL_FIELD_KEYS:
        st.session_state[f"crawl_field_{key}"] = value


def _on_crawl_fields_all_change() -> None:
    if st.session_state.get("crawl_fields_all"):
        _set_all_crawl_field_flags(True)


def _on_crawl_field_change() -> None:
    all_on = all(
        st.session_state.get(f"crawl_field_{key}", True) for key in ALL_CRAWL_FIELD_KEYS
    )
    st.session_state.crawl_fields_all = all_on
    st.session_state.crawl_include_product_url = bool(
        st.session_state.get("crawl_field_url", True)
    )


def init_crawl_field_session_defaults() -> None:
    for key in ALL_CRAWL_FIELD_KEYS:
        st.session_state.setdefault(f"crawl_field_{key}", True)
    st.session_state.setdefault("crawl_fields_all", True)
    _on_crawl_field_change()


def render_crawl_fields_selector() -> None:
    """Checkbox chọn title / ảnh / link / mô tả / category — hoặc toàn bộ."""
    st.caption(
        "Chọn phần cần lấy. ShopBase: bỏ **Description** giúp crawl nhanh "
        "(chỉ danh sách catalog, không gọi API mô tả từng SP)."
    )
    st.checkbox(
        "Toàn bộ",
        key="crawl_fields_all",
        on_change=_on_crawl_fields_all_change,
        help="Bật tất cả: Title, Link ảnh, Link SP, Description, Category.",
    )

    cols = st.columns(len(ALL_CRAWL_FIELD_KEYS))
    for col, key in zip(cols, ALL_CRAWL_FIELD_KEYS):
        with col:
            st.checkbox(
                CRAWL_FIELD_LABELS[key],
                key=f"crawl_field_{key}",
                on_change=_on_crawl_field_change,
                disabled=bool(st.session_state.get("crawl_fields_all")),
            )

    _on_crawl_field_change()
