"""Panel proxy dùng chung — nạp/lưu từ config.json."""

from __future__ import annotations

import streamlit as st

from utils.credentials import CrawlCredentials, load_saved_proxy
from utils.http_session import mask_proxy_url, parse_proxy_list, test_proxy_connection


def ensure_proxy_from_config() -> None:
    """Nạp proxy đã lưu vào session (chỉ khi ô proxy đang trống)."""
    saved = load_saved_proxy()
    if not saved:
        return
    current = str(st.session_state.get("proxy_url") or "").strip()
    if not current:
        st.session_state.proxy_url = saved


def save_proxy_to_config() -> str | None:
    raw = str(st.session_state.get("proxy_url") or "")
    path = CrawlCredentials.load().save_proxy(raw)
    st.session_state._proxy_saved_sig = raw.strip()
    return path.name


def render_proxy_panel(
    *,
    expanded: bool = True,
    title: str = "Proxy",
    caption: str | None = None,
    key_prefix: str = "",
) -> None:
    ensure_proxy_from_config()

    default_caption = (
        "Redbubble hay chặn IP → nhập proxy. **Mỗi dòng 1 proxy** — tool tự đổi khi lỗi. "
        "Bấm **Lưu proxy** một lần → lần sau mở app **tự nạp** từ `config.json`."
    )
    with st.expander(title, expanded=expanded):
        st.caption(caption or default_caption)
        saved = load_saved_proxy()
        if saved and not str(st.session_state.get("proxy_url") or "").strip():
            st.session_state.proxy_url = saved
        if saved:
            saved_count = len(parse_proxy_list(saved))
            st.info(
                f"Đã lưu **{saved_count}** proxy trong `config.json` — "
                "mở app sẽ tự nạp, không cần dán lại."
            )

        st.text_area(
            "Proxy",
            placeholder="host:port:user:pass",
            height=120,
            key="proxy_url",
        )
        proxy_pool = parse_proxy_list(st.session_state.proxy_url)
        if proxy_pool:
            st.caption(f"Đang dùng **{len(proxy_pool)}** proxy · luân phiên khi crawl/đếm SP")
            st.caption(f"Proxy hiện tại: `{mask_proxy_url(st.session_state.proxy_url)}`")

        save_col, test_col, reload_col = st.columns(3)
        with save_col:
            if st.button(
                "Lưu proxy vào config",
                use_container_width=True,
                key=f"{key_prefix}save_proxy_btn",
                type="primary",
            ):
                name = save_proxy_to_config()
                count = len(parse_proxy_list(st.session_state.proxy_url))
                st.session_state.last_info = (
                    f"Đã lưu **{count}** proxy vào **{name}** — lần sau mở app tự nạp."
                )
                st.rerun()
        with test_col:
            if st.button(
                "Test proxy",
                use_container_width=True,
                key=f"{key_prefix}test_proxy_btn",
            ):
                ok, message = test_proxy_connection(st.session_state.proxy_url)
                if ok:
                    st.success(message)
                else:
                    st.warning(message)
        with reload_col:
            if st.button(
                "Nạp lại từ config",
                use_container_width=True,
                key=f"{key_prefix}reload_proxy_btn",
            ):
                st.session_state.proxy_url = load_saved_proxy()
                st.rerun()
