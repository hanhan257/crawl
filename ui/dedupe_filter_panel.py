"""UI: Lọc trùng file feed + lọc từ khóa trong title."""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from utils.dedupe_filter import (
    DEDUP_FIELDS,
    FIELD_LABELS,
    filter_feed_file,
    preview_csv_head,
    save_upload_streaming,
)

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
DOWNLOAD_BYTES_LIMIT = 180 * 1024 * 1024  # ~180MB — trên mức này chỉ lưu ổ đĩa


def render_dedupe_filter_panel() -> None:
    st.markdown("### Bước 1 · Lọc trùng / lọc từ khóa file feed")
    st.caption(
        "Upload 1 file CSV/TSV (tối đa **3 GB**). "
        "Tích các ô trùng cần so — **tích nhiều ô = AND** "
        "(vd. title giống **và** giá giống mới tính trùng). "
        "Có thể kèm lọc title chứa từ khóa (vd. `ronaldo`)."
    )

    uploaded = st.file_uploader(
        "File feed cần lọc",
        type=["csv", "tsv", "txt"],
        key="dedupe_filter_uploader",
        help="CSV feed lớn — app ghi tạm ra ổ đĩa rồi stream, không load hết 3GB vào RAM.",
    )

    st.markdown("##### Cột lọc trùng (AND)")
    cols = st.columns(5)
    checks: dict[str, bool] = {}
    for col, field_name in zip(cols, DEDUP_FIELDS):
        with col:
            checks[field_name] = st.checkbox(
                FIELD_LABELS[field_name],
                key=f"dedupe_field_{field_name}",
                help=(
                    f"So trùng theo {FIELD_LABELS[field_name]}. "
                    "Tích cùng ô khác = phải giống hết các cột đã chọn."
                ),
            )

    selected_fields = [f for f, on in checks.items() if on]

    st.markdown("##### Lọc từ trong title (tuỳ chọn)")
    kw_col, mode_col = st.columns([2, 1])
    with kw_col:
        keywords = st.text_input(
            "Từ khóa trong title",
            key="dedupe_title_keywords",
            placeholder="ronaldo, messi",
            help="Lấy các dòng title chứa từ khóa. Nhiều từ cách nhau dấu phẩy.",
        )
    with mode_col:
        keyword_mode = st.selectbox(
            "Khớp từ khóa",
            options=["any", "all"],
            format_func=lambda k: (
                "Chứa bất kỳ từ" if k == "any" else "Phải chứa đủ mọi từ"
            ),
            key="dedupe_keyword_mode",
        )

    if selected_fields:
        mode = st.radio(
            "Kiểu xuất khi lọc trùng",
            options=["unique", "duplicates"],
            format_func=lambda k: (
                "Giữ bản đầu — xóa dòng trùng"
                if k == "unique"
                else "Chỉ lấy các dòng bị trùng (≥2 lần)"
            ),
            horizontal=True,
            key="dedupe_output_mode",
        )
    else:
        mode = "keyword_only"
        if keywords.strip():
            st.info("Chỉ lọc theo từ khóa title (chưa chọn ô trùng).")

    and_hint = " + ".join(FIELD_LABELS[f] for f in selected_fields) if selected_fields else "(không)"
    st.caption(f"Khóa trùng hiện tại: **{and_hint}**" + (
        f" · từ khóa title: `{keywords.strip()}`" if keywords.strip() else ""
    ))

    can_run = bool(uploaded) and (bool(selected_fields) or bool(keywords.strip()))
    run_clicked = st.button(
        "Chạy lọc",
        type="primary",
        use_container_width=True,
        key="dedupe_run_btn",
        disabled=not can_run,
    )

    if run_clicked and uploaded:
        tmp_path = None
        try:
            with st.spinner("Đang ghi file tạm & lọc (stream)…"):
                suffix = Path(uploaded.name).suffix or ".csv"
                tmp_path = save_upload_streaming(uploaded, suffix=suffix)
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                stem = Path(uploaded.name).stem
                stamp = time.strftime("%Y%m%d_%H%M%S")
                out_name = f"{stem}_deduped_{stamp}.csv"
                out_path = OUTPUT_DIR / out_name
                result = filter_feed_file(
                    tmp_path,
                    out_path,
                    dedupe_fields=selected_fields,
                    mode=mode,
                    title_keywords=keywords,
                    keyword_mode=keyword_mode,
                )
            st.session_state.dedupe_result = {
                "output_path": str(result.output_path),
                "output_name": result.output_path.name,
                "total_rows": result.total_rows,
                "kept_rows": result.kept_rows,
                "removed_rows": result.removed_rows,
                "keyword_matched": result.keyword_matched,
                "duplicate_groups": result.duplicate_groups,
                "mode": result.mode,
                "dedupe_fields": result.dedupe_fields,
                "keywords": result.keywords,
                "field_columns": result.field_columns,
            }
            st.session_state.last_error = None
            st.session_state.last_info = (
                f"Lọc xong: giữ **{result.kept_rows:,}** / **{result.total_rows:,}** dòng "
                f"(bỏ {result.removed_rows:,})."
            ).replace(",", ".")
        except Exception as exc:
            st.session_state.dedupe_result = None
            st.session_state.last_error = f"Lỗi lọc trùng: {exc}"
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    result_state = st.session_state.get("dedupe_result")
    if not result_state:
        if not uploaded:
            st.caption("Upload file feed để bắt đầu.")
        elif not selected_fields and not keywords.strip():
            st.caption("Tích ít nhất một ô trùng hoặc nhập từ khóa title.")
        return

    st.success(
        f"**{result_state['kept_rows']:,}** dòng giữ lại · "
        f"**{result_state['total_rows']:,}** dòng gốc · "
        f"bỏ **{result_state['removed_rows']:,}** · "
        f"nhóm trùng **{result_state['duplicate_groups']:,}**"
        .replace(",", ".")
    )
    if result_state.get("keywords"):
        st.caption(
            f"Từ khóa title khớp trước khi dedupe: "
            f"**{result_state['keyword_matched']:,}** dòng".replace(",", ".")
        )
    if result_state.get("field_columns"):
        mapped = ", ".join(
            f"{FIELD_LABELS.get(k, k)}→`{v}`"
            for k, v in result_state["field_columns"].items()
        )
        st.caption(f"Cột map: {mapped}")

    out_path = Path(result_state["output_path"])
    out_name = result_state.get("output_name") or out_path.name
    if out_path.exists():
        size = out_path.stat().st_size
        st.caption(f"Đã lưu: `{out_path}` ({size / (1024 * 1024):.1f} MB)")
        if size <= DOWNLOAD_BYTES_LIMIT:
            st.download_button(
                f"Tải file đã lọc ({out_name})",
                data=out_path.read_bytes(),
                file_name=out_name,
                mime="text/csv",
                type="primary",
                use_container_width=True,
                key="dedupe_download_btn",
            )
        else:
            st.info(
                f"File lớn (**{size / (1024 * 1024):.0f} MB**) — "
                "mở thư mục `output` trên máy để lấy file "
                f"**{out_name}** (không tải qua trình duyệt)."
            )
        try:
            columns, rows = preview_csv_head(out_path, limit=15)
            if rows:
                with st.expander("Xem nhanh 15 dòng đầu", expanded=False):
                    st.dataframe(rows, use_container_width=True, hide_index=True)
        except Exception:
            pass
