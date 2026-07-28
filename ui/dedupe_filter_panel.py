"""UI: Lọc trùng file feed (1 file) + so sánh 2 file → Excel 2 sheet."""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from utils.dedupe_filter import (
    DEDUP_FIELDS,
    FIELD_LABELS,
    compare_two_feed_files,
    filter_feed_file,
    preview_csv_head,
    save_upload_streaming,
)

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
DOWNLOAD_BYTES_LIMIT = 180 * 1024 * 1024  # ~180MB


def _render_one_file_mode() -> None:
    st.caption(
        "Upload 1 file CSV/TSV (tối đa **3 GB**). "
        "Tích các ô trùng cần so — **tích nhiều ô = AND**. "
        "Có thể kèm lọc title chứa từ khóa."
    )

    uploaded = st.file_uploader(
        "File feed cần lọc",
        type=["csv", "tsv", "txt"],
        key="dedupe_filter_uploader",
        help="CSV feed lớn — app ghi tạm ra ổ đĩa rồi stream.",
    )

    st.markdown("##### Cột lọc trùng (AND)")
    cols = st.columns(5)
    checks: dict[str, bool] = {}
    for col, field_name in zip(cols, DEDUP_FIELDS):
        with col:
            checks[field_name] = st.checkbox(
                FIELD_LABELS[field_name],
                key=f"dedupe_field_{field_name}",
            )

    selected_fields = [f for f, on in checks.items() if on]

    st.markdown("##### Lọc từ trong title (tuỳ chọn)")
    kw_col, mode_col = st.columns([2, 1])
    with kw_col:
        keywords = st.text_input(
            "Từ khóa trong title",
            key="dedupe_title_keywords",
            placeholder="ronaldo, messi",
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
                "kind": "one_file",
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


def _render_two_file_mode() -> None:
    st.caption(
        "Upload **2 file** CSV/TSV → chọn cột so (title / giá / des…) "
        "hoặc **Trùng hết**. Xuất Excel **2 sheet**: "
        "`trung` (khớp giữa 2 file) và `khong_trung` (chỉ có ở 1 file)."
    )

    c1, c2 = st.columns(2)
    with c1:
        file1 = st.file_uploader(
            "File 1",
            type=["csv", "tsv", "txt"],
            key="dedupe_two_file1",
        )
    with c2:
        file2 = st.file_uploader(
            "File 2",
            type=["csv", "tsv", "txt"],
            key="dedupe_two_file2",
        )

    compare_all = st.checkbox(
        "So sánh trùng hết (title + giá + cate + tag + des)",
        key="dedupe_two_compare_all",
        help="Bật = so cả 5 cột (AND). Tắt để chọn từng cột bên dưới.",
    )

    selected_fields: list[str] = []
    if compare_all:
        selected_fields = list(DEDUP_FIELDS)
        st.caption("Đang so: **Title + Giá + Cate + Tag + Des**")
    else:
        st.markdown("##### Cột so sánh (AND — tích nhiều = phải giống hết)")
        # Ưu tiên title / giá / des theo yêu cầu
        primary = ("title", "price", "description")
        extra = ("categories", "tags")
        cols = st.columns(3)
        checks: dict[str, bool] = {}
        for col, field_name in zip(cols, primary):
            with col:
                checks[field_name] = st.checkbox(
                    FIELD_LABELS[field_name],
                    key=f"dedupe_two_field_{field_name}",
                )
        cols2 = st.columns(2)
        for col, field_name in zip(cols2, extra):
            with col:
                checks[field_name] = st.checkbox(
                    FIELD_LABELS[field_name],
                    key=f"dedupe_two_field_{field_name}",
                )
        selected_fields = [f for f, on in checks.items() if on]

    and_hint = " + ".join(FIELD_LABELS[f] for f in selected_fields) if selected_fields else "(chưa chọn)"
    st.caption(f"Khóa so sánh: **{and_hint}**")

    can_run = bool(file1) and bool(file2) and bool(selected_fields)
    run_clicked = st.button(
        "So sánh 2 file",
        type="primary",
        use_container_width=True,
        key="dedupe_two_run_btn",
        disabled=not can_run,
    )

    if run_clicked and file1 and file2:
        tmp1 = tmp2 = None
        try:
            with st.spinner("Đang so sánh 2 file & ghi Excel…"):
                tmp1 = save_upload_streaming(
                    file1, suffix=Path(file1.name).suffix or ".csv"
                )
                tmp2 = save_upload_streaming(
                    file2, suffix=Path(file2.name).suffix or ".csv"
                )
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d_%H%M%S")
                stem1 = Path(file1.name).stem[:40]
                stem2 = Path(file2.name).stem[:40]
                out_name = f"so_sanh_{stem1}_vs_{stem2}_{stamp}.xlsx"
                out_path = OUTPUT_DIR / out_name
                result = compare_two_feed_files(
                    tmp1,
                    tmp2,
                    out_path,
                    dedupe_fields=selected_fields,
                    file1_label=file1.name,
                    file2_label=file2.name,
                )
            st.session_state.dedupe_result = {
                "kind": "two_file",
                "output_path": str(result.output_path),
                "output_name": result.output_path.name,
                "file1_rows": result.file1_rows,
                "file2_rows": result.file2_rows,
                "match_rows": result.match_rows,
                "only_file1_rows": result.only_file1_rows,
                "only_file2_rows": result.only_file2_rows,
                "dedupe_fields": result.dedupe_fields,
                "field_columns_file1": result.field_columns_file1,
                "field_columns_file2": result.field_columns_file2,
            }
            st.session_state.last_error = None
            st.session_state.last_info = (
                f"So sánh xong: trùng **{result.match_rows}** dòng · "
                f"chỉ file1 **{result.only_file1_rows}** · "
                f"chỉ file2 **{result.only_file2_rows}**."
            )
        except Exception as exc:
            st.session_state.dedupe_result = None
            st.session_state.last_error = f"Lỗi so sánh 2 file: {exc}"
        finally:
            for p in (tmp1, tmp2):
                if p is not None:
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass


def _render_result() -> None:
    result_state = st.session_state.get("dedupe_result")
    if not result_state:
        return

    kind = result_state.get("kind") or "one_file"
    out_path = Path(result_state["output_path"])
    out_name = result_state.get("output_name") or out_path.name

    if kind == "two_file":
        st.success(
            f"File 1: **{result_state['file1_rows']:,}** dòng · "
            f"File 2: **{result_state['file2_rows']:,}** · "
            f"Sheet `trung`: **{result_state['match_rows']:,}** · "
            f"Sheet `khong_trung`: "
            f"**{result_state['only_file1_rows'] + result_state['only_file2_rows']:,}** "
            f"(chỉ F1 {result_state['only_file1_rows']:,} + "
            f"chỉ F2 {result_state['only_file2_rows']:,})"
            .replace(",", ".")
        )
        fields = result_state.get("dedupe_fields") or []
        if fields:
            st.caption(
                "So theo: **"
                + " + ".join(FIELD_LABELS.get(f, f) for f in fields)
                + "**"
            )
    else:
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

    if not out_path.exists():
        st.warning("Không tìm thấy file kết quả trên ổ đĩa.")
        return

    size = out_path.stat().st_size
    st.caption(f"Đã lưu: `{out_path}` ({size / (1024 * 1024):.1f} MB)")
    mime = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if out_path.suffix.lower() == ".xlsx"
        else "text/csv"
    )
    if size <= DOWNLOAD_BYTES_LIMIT:
        st.download_button(
            f"Tải file ({out_name})",
            data=out_path.read_bytes(),
            file_name=out_name,
            mime=mime,
            type="primary",
            use_container_width=True,
            key="dedupe_download_btn",
        )
    else:
        st.info(
            f"File lớn (**{size / (1024 * 1024):.0f} MB**) — "
            f"mở thư mục `output` lấy **{out_name}**."
        )

    if out_path.suffix.lower() != ".xlsx":
        try:
            columns, rows = preview_csv_head(out_path, limit=15)
            if rows:
                with st.expander("Xem nhanh 15 dòng đầu", expanded=False):
                    st.dataframe(rows, use_container_width=True, hide_index=True)
        except Exception:
            pass
    else:
        st.caption("Excel gồm 2 sheet: **trung** · **khong_trung**.")


def render_dedupe_filter_panel() -> None:
    st.markdown("### Bước 1 · Lọc trùng / so sánh file feed")

    mode = st.radio(
        "Chế độ",
        options=["one", "two"],
        format_func=lambda k: (
            "1 file — lọc trùng trong file"
            if k == "one"
            else "2 file — so sánh & xuất Excel 2 sheet"
        ),
        horizontal=True,
        key="dedupe_panel_mode",
    )

    if mode == "two":
        _render_two_file_mode()
    else:
        _render_one_file_mode()

    if not st.session_state.get("dedupe_result"):
        if mode == "two":
            st.caption("Upload đủ File 1 + File 2 và chọn cột so để chạy.")
        else:
            st.caption("Upload file feed và chọn cột trùng / từ khóa title.")
        return

    _render_result()
