"""UI helpers — chọn trường crawl Bước 1."""

from __future__ import annotations

import streamlit as st

from models.crawl_fields import (
    ALL_CRAWL_FIELD_KEYS,
    CRAWL_FIELD_LABELS,
    DEFAULT_CRAWL_FIELD_ON,
)

# Title, ảnh, link, giá (luôn bật), category. Description để cuối, tắt sẵn.
_FIELD_DISPLAY_ORDER: tuple[str, ...] = (
    "title",
    "images",
    "url",
    "price",
    "categories",
    "description",
)


def _on_crawl_field_change() -> None:
    _sync_crawl_include_url()
    if not st.session_state.get("crawl_field_description"):
        st.session_state.pop("crawl_description_format", None)


def _sync_crawl_include_url() -> None:
    st.session_state.crawl_include_product_url = bool(
        st.session_state.get("crawl_field_url", True)
    )


def init_crawl_field_session_defaults() -> None:
    if not st.session_state.get("_crawl_fields_v3"):
        st.session_state.crawl_fields_all = False
        for key in ALL_CRAWL_FIELD_KEYS:
            st.session_state[f"crawl_field_{key}"] = key in DEFAULT_CRAWL_FIELD_ON
        st.session_state.crawl_field_price_always = True
        st.session_state.crawl_include_product_url = True
        st.session_state.pop("crawl_description_format", None)
        st.session_state._crawl_fields_v3 = True
        return
    for key in ALL_CRAWL_FIELD_KEYS:
        st.session_state.setdefault(
            f"crawl_field_{key}", key in DEFAULT_CRAWL_FIELD_ON
        )
    st.session_state.setdefault("crawl_field_price_always", True)
    st.session_state.setdefault("crawl_include_product_url", True)


def render_crawl_fields_selector() -> None:
    """Checkbox title / ảnh / link / giá / category. Mô tả chỉ khi khách bật."""
    st.caption(
        "Mặc định lấy **title, link ảnh, link sản phẩm, giá, category**. "
        "Giá luôn được lấy. Bật **Description** rồi chọn HTML hoặc Text thuần thì mới lấy mô tả."
    )
    cols = st.columns(len(_FIELD_DISPLAY_ORDER))
    for col, key in zip(cols, _FIELD_DISPLAY_ORDER):
        with col:
            if key == "price":
                st.checkbox("Giá", key="crawl_field_price_always", disabled=True)
                continue
            st.checkbox(
                CRAWL_FIELD_LABELS[key],
                key=f"crawl_field_{key}",
                on_change=_on_crawl_field_change,
            )
