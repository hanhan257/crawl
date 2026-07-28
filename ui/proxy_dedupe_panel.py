"""UI: Lọc trùng proxy giữa nhiều danh sách."""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from utils.proxy_dedupe import dedupe_proxy_lists

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
MAX_LISTS = 8


def render_proxy_dedupe_panel() -> None:
    st.markdown("### Bước 1 · Lọc trùng proxy")
    st.caption(
        "Dán **nhiều danh sách** proxy. Tool chỉ so phần "
        "`host:port:user:pass` — **bỏ ngày giờ** phía sau "
        "(vd. `…:pass 2026-05-08 02:01:35`)."
    )

    if "proxy_dedupe_list_count" not in st.session_state:
        st.session_state.proxy_dedupe_list_count = 3

    ctrl1, ctrl2, _ = st.columns([1, 1, 2])
    with ctrl1:
        if st.button("＋ Thêm list", use_container_width=True, key="proxy_dedupe_add"):
            st.session_state.proxy_dedupe_list_count = min(
                MAX_LISTS, int(st.session_state.proxy_dedupe_list_count) + 1
            )
            st.rerun()
    with ctrl2:
        if st.button("－ Bớt list", use_container_width=True, key="proxy_dedupe_del"):
            st.session_state.proxy_dedupe_list_count = max(
                2, int(st.session_state.proxy_dedupe_list_count) - 1
            )
            st.rerun()

    n = int(st.session_state.proxy_dedupe_list_count)
    texts: list[tuple[str, str]] = []
    for i in range(1, n + 1):
        label = f"Proxy list {i}"
        value = st.text_area(
            label,
            height=140,
            key=f"proxy_dedupe_list_{i}",
            placeholder=(
                "69.33.242.208:4444:b8c97e4cea:zBTeEa4M\n"
                "64.16.172.101:4444:b8c97e4cea:zBTeEa4M 2026-05-08 02:01:35"
            ),
            help="Mỗi dòng 1 proxy. Ngày tháng sau proxy (nếu có) sẽ bị bỏ khi so.",
        )
        texts.append((label, value))

    mode = st.radio(
        "Kiểu xuất",
        options=["unique", "duplicates"],
        format_func=lambda k: (
            "Chỉ proxy **không trùng** (có đúng trong 1 list)"
            if k == "unique"
            else "Chỉ proxy **trùng** (xuất hiện ≥ 2 list)"
        ),
        horizontal=True,
        key="proxy_dedupe_mode",
    )

    filled = sum(1 for _, t in texts if str(t or "").strip())
    can_run = filled >= 2
    if not can_run:
        st.caption("Cần ít nhất **2** danh sách có nội dung để so trùng.")

    run = st.button(
        "Chạy lọc proxy",
        type="primary",
        use_container_width=True,
        key="proxy_dedupe_run",
        disabled=not can_run,
    )

    if run and can_run:
        result = dedupe_proxy_lists(texts, mode=mode)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_name = f"proxy_{mode}_{stamp}.txt"
        out_path = OUTPUT_DIR / out_name
        out_path.write_text(
            "\n".join(result.output_lines) + ("\n" if result.output_lines else ""),
            encoding="utf-8",
        )
        st.session_state.proxy_dedupe_result = {
            "mode": result.mode,
            "output_lines": result.output_lines,
            "output_path": str(out_path),
            "output_name": out_name,
            "total_unique_keys": result.total_unique_keys,
            "duplicate_keys": result.duplicate_keys,
            "unique_only_keys": result.unique_only_keys,
            "list_stats": [
                {
                    "label": s.label,
                    "raw_lines": s.raw_lines,
                    "valid": s.valid,
                    "invalid": s.invalid,
                    "unique_in_list": len(s.keys),
                }
                for s in result.list_stats
            ],
        }
        st.session_state.last_error = None
        st.session_state.last_info = (
            f"Lọc proxy xong: **{len(result.output_lines)}** dòng "
            f"({ 'không trùng' if mode == 'unique' else 'trùng' })."
        )

    result_state = st.session_state.get("proxy_dedupe_result")
    if not result_state:
        return

    st.success(
        f"Tổng proxy khác nhau: **{result_state['total_unique_keys']}** · "
        f"trùng (≥2 list): **{result_state['duplicate_keys']}** · "
        f"chỉ 1 list: **{result_state['unique_only_keys']}** · "
        f"xuất: **{len(result_state['output_lines'])}**"
    )

    for stat in result_state.get("list_stats") or []:
        st.caption(
            f"{stat['label']}: {stat['raw_lines']} dòng · "
            f"hợp lệ {stat['valid']} · bỏ {stat['invalid']} · "
            f"unique trong list {stat['unique_in_list']}"
        )

    out_text = "\n".join(result_state["output_lines"])
    st.text_area(
        "Kết quả",
        value=out_text,
        height=220,
        key="proxy_dedupe_result_view",
    )
    st.download_button(
        f"Tải {result_state.get('output_name', 'proxy_filtered.txt')}",
        data=(out_text + "\n").encode("utf-8") if out_text else b"",
        file_name=result_state.get("output_name", "proxy_filtered.txt"),
        mime="text/plain",
        type="primary",
        use_container_width=True,
        key="proxy_dedupe_download",
        disabled=not bool(result_state["output_lines"]),
    )
    if result_state.get("output_path"):
        st.caption(f"Đã lưu: `{result_state['output_path']}`")
