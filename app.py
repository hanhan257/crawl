#!/usr/bin/env python3
"""Web UI for product crawler - chay: streamlit run app.py"""
from __future__ import annotations

import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import html
import os
import re

import pandas as pd
import streamlit as st

from app_brand import APP_NAME, APP_PAGE_ICON, APP_TAGLINE, APP_VERSION
from exporters.export import default_output_path, export_products
from exporters.gmc_feed_import import GMC_FEED_COLUMNS, GMC_VARIANT_MODES, count_gmc_export_rows
from exporters.image_urls import IMAGE_EXPORT_MODES
from models.export_fields import PRESET_LABELS, EXPORT_FIELD_LABELS, ExportFields
from models.export_rules import ExportRules, apply_export_rules, apply_rules_to_products
from models.product import Product
from utils.credentials import CrawlCredentials
from utils.crawl_runner import CrawlError, run_auto_crawl
from utils.csv_import import (
    filter_products_by_category,
    list_categories,
    parse_csv_to_products,
    slug_from_category,
)
from utils.detector import UnsupportedUrlError, resolve_url
from utils.export_preview import (
    all_export_columns,
    build_export_file_preview,
    build_product_preview_table,
    image_batch_summary,
)
from utils.image_fetch import fetch_image_bytes
from utils.http_session import mask_proxy_url, parse_proxy_list, test_proxy_connection
from utils.edit_validation import (
    EDIT_FIELD_LABELS,
    column_header_label,
    required_columns_for_format,
    validate_edited_df,
)
from utils.gmc_feed_editor import (
    build_gmc_feed_edit_rows,
    merge_gmc_feed_edits,
)
from utils.gmc_taxonomy import (
    GMC_AGE_GROUP_DEFAULT,
    GMC_AGE_GROUP_OPTIONS,
    GMC_CATEGORY_DROPDOWN_OPTIONS,
    GMC_DEFAULT_GENDER,
    GMC_DEFAULT_GOOGLE_CATEGORY,
    GMC_GENDER_OPTIONS,
    category_id_from_option,
)
from utils.export_selection import (
    EXPORT_ORDER_LABELS,
    EXPORT_ORDER_OPTIONS,
    describe_edit_selection,
)
from utils.product_editor import (
    EDIT_COLUMNS,
    GMC_EDIT_COLUMNS,
    build_edit_subset_rows,
    edit_columns_for_format,
    merge_edited_rows,
    products_from_edit_table,
    products_to_rows,
)
from utils.shop_probe import build_category_crawl_url, probe_shop_stats
from utils.ui_theme import inject_pastel_pink_theme, render_app_hero
from ui.proxy_panel import render_proxy_panel
from ui.redbubble_workflow import render_redbubble_crawl_panel, render_redbubble_export_selector
from utils.url_preview import describe_url


st.set_page_config(
    page_title=APP_NAME,
    page_icon=APP_PAGE_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

FORMAT_OPTIONS = {
    "csv": "CSV thô (dữ liệu crawl)",
    "json": "JSON",
    "woocommerce-csv": "CSV import WooCommerce",
    "shopify-csv": "CSV import Shopify",
    "web-feed-csv": "CSV feed web thuần",
    "gmc-feed-csv": "CSV feed GMC (Google Merchant)",
}

FORMAT_NOTES = {
    "woocommerce-csv": "Template WooCommerce: Name, Regular price, Categories, Images.",
    "shopify-csv": "Template Shopify: Handle, Title, Variant Price, Image Src.",
    "web-feed-csv": "Feed web: title, description, images, category, price, brand, tag, vendor.",
    "gmc-feed-csv": "Google Merchant: 16 cột — id, title, link, price, image link, brand, size, color…",
}

GENERAL_EXPORT_FORMATS = ["woocommerce-csv", "shopify-csv", "web-feed-csv"]
SHOP_EXPORT_FORMATS = GENERAL_EXPORT_FORMATS + ["gmc-feed-csv"]
DEFAULT_EXPORT_WORKERS = min(16, max(4, (os.cpu_count() or 4) * 2))

WORKFLOW_MODES = {
    "crawl": "Crawl từ link (Shopify / Woo / web)",
    "import": "Import CSV có sẵn → xuất file",
    "crawl-gmc": "Crawl → file feed GMC",
    "redbubble": "Crawl Redbubble → xuất file",
}

RULE_DEFAULTS = {
    "price_multiplier": 1.0,
    "price_add": 0.0,
    "round_to_99": False,
    "title_remove": "",
    "title_remove_ignore_case": True,
    "title_prefix": "",
    "title_suffix": "",
    "category_override": "",
    "category_add": "",
    "tags_add": "",
    "vendor_override": "",
    "product_type_override": "",
    "description_action": "find_replace",
    "description_format": "html",
    "description_override": "",
    "description_find": "",
    "description_replace": "",
    "description_replace_ignore_case": True,
    "description_remove": "",
    "description_remove_ignore_case": True,
    "description_convert_h_to_p": True,
}

BULK_CUSTOMIZE_SECTIONS = {
    "price": "Giá bán",
    "title": "Title",
    "meta": "Category, tags & shop",
    "description": "Mô tả (description)",
}


def init_session() -> None:
    creds = CrawlCredentials.load()
    defaults = {
        "products_raw": None,
        "edited_df": None,
        "output_bytes": None,
        "output_name": None,
        "last_error": None,
        "last_info": None,
        "export_format": "woocommerce-csv",
        "export_name": "products",
        "export_field_preset": "shop",
        "export_fields_custom": list(ExportFields.from_preset("shop").enabled),
        "proxy_url": creds.proxy_url or "",
        "image_cache_nonce": 0,
        "image_export_mode": "comma",
        "max_images_per_product": 0,
        "workflow_mode": "crawl",
        "imported_products_all": None,
        "import_source_name": "",
        "import_selected_category": "Tất cả",
        "csv_import_sig": "",
        "crawl_limit": 0,
        "crawl_pages": 0,
        "crawl_category_pill": "Tất cả",
        "gmc_currency": "USD",
        "gmc_condition": "new",
        "gmc_google_category": GMC_DEFAULT_GOOGLE_CATEGORY,
        "gmc_default_gender": GMC_DEFAULT_GENDER,
        "gmc_default_age_group": GMC_AGE_GROUP_DEFAULT,
        "gmc_gtin_default": "",
        "gmc_variant_mode": "all_variations",
        "edit_quantity": 0,
        "edit_order": "original",
        "export_workers": DEFAULT_EXPORT_WORKERS,
        "pending_rule_reset": False,
        "bulk_customize_section": "price",
        **RULE_DEFAULTS,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if st.session_state.get("image_export_mode") == "pipe":
        st.session_state.image_export_mode = "comma"

    if not st.session_state.get("_defaults_v2"):
        st.session_state.description_convert_h_to_p = True
        st.session_state._defaults_v2 = True

    if not st.session_state.get("_gmc_feed_defaults_v1"):
        if not str(st.session_state.get("gmc_google_category") or "").strip():
            st.session_state.gmc_google_category = GMC_DEFAULT_GOOGLE_CATEGORY
        if not str(st.session_state.get("gmc_default_gender") or "").strip():
            st.session_state.gmc_default_gender = GMC_DEFAULT_GENDER
        if not str(st.session_state.get("gmc_default_age_group") or "").strip():
            st.session_state.gmc_default_age_group = GMC_AGE_GROUP_DEFAULT
        st.session_state._gmc_feed_defaults_v1 = True

    action = st.session_state.get("description_action")
    if action not in {"find_replace", "replace_all"}:
        st.session_state.description_action = "find_replace"

    if st.session_state.get("workflow_mode") == "crawl-gmc":
        st.session_state.export_format = "gmc-feed-csv"
    elif st.session_state.get("workflow_mode") == "redbubble":
        if st.session_state.get("export_format") not in (
            "web-feed-csv",
            "woocommerce-csv",
            "shopify-csv",
        ):
            st.session_state.export_format = "web-feed-csv"


def is_redbubble_workflow(mode: str | None = None) -> bool:
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    return active == "redbubble"


def is_gmc_workflow(mode: str | None = None) -> bool:
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    return active == "crawl-gmc"


def default_export_format(mode: str | None = None) -> str:
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    if is_gmc_workflow(active):
        return "gmc-feed-csv"
    if is_redbubble_workflow(active):
        return "web-feed-csv"
    return "woocommerce-csv"


def _ensure_export_format_for_workflow(mode: str | None = None) -> None:
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    if is_gmc_workflow(active):
        st.session_state.export_format = "gmc-feed-csv"
        return
    if is_redbubble_workflow(active):
        if st.session_state.get("export_format") not in (
            "web-feed-csv",
            "woocommerce-csv",
            "shopify-csv",
        ):
            st.session_state.export_format = "web-feed-csv"
        return
    if st.session_state.get("export_format") not in GENERAL_EXPORT_FORMATS:
        st.session_state.export_format = "woocommerce-csv"


def _clear_image_cache() -> None:
    st.session_state.image_cache_nonce += 1
    _cached_product_image.clear()


def _on_workflow_mode_change() -> None:
    _ensure_export_format_for_workflow()
    if st.session_state.products_raw is not None:
        apply_edit_filter()


def _is_stale_homepage_crawl_error(message: str) -> bool:
    text = str(message or "")
    markers = (
        "Trang chủ Redbubble",
        "Chưa xác định loại link",
        "Link chưa đủ thông tin",
        "không crawl được",
        "loại link 'unknown'",
    )
    return any(marker in text for marker in markers)


def _refresh_crawl_messages(url: str) -> None:
    """Xóa thông báo lỗi/info cũ khi link hiện tại đã hợp lệ."""
    cleaned = url.strip()
    if st.session_state.get("_crawl_url_sig") != cleaned:
        st.session_state._crawl_url_sig = cleaned
        st.session_state.last_error = None
        last_info = st.session_state.get("last_info") or ""
        if last_info.startswith(("Nhận diện:", "Crawl xong")) or "Trang chủ" in last_info:
            st.session_state.last_info = None

    if not cleaned:
        return

    info = describe_url(cleaned)
    if not info.get("ok") or not info.get("crawl_ready", True):
        return

    err = st.session_state.get("last_error")
    if err and _is_stale_homepage_crawl_error(str(err)):
        st.session_state.last_error = None

    last_info = st.session_state.get("last_info") or ""
    if last_info.startswith("Nhận diện:") and "Chưa xác định" in last_info:
        st.session_state.last_info = None


def _on_crawl_url_change() -> None:
    _refresh_crawl_messages(str(st.session_state.get("crawl_url") or ""))


def _current_crawl_error() -> str | None:
    err = st.session_state.get("last_error")
    if not err:
        return None
    url = str(st.session_state.get("crawl_url") or "").strip()
    if not url:
        return str(err)
    info = describe_url(url)
    if info.get("ok") and info.get("crawl_ready", True) and _is_stale_homepage_crawl_error(str(err)):
        return None
    return str(err)


@st.cache_data(show_spinner=False, ttl=1800)
def _cached_product_image(url: str, proxy_url: str, cache_nonce: int) -> bytes | None:
    return fetch_image_bytes(url, proxy_url or None)


@st.cache_data(show_spinner=False, ttl=900)
def _cached_describe_url(url: str) -> dict:
    return describe_url(url)


@st.cache_data(show_spinner=False, ttl=600)
def _cached_generic_product_count(url: str) -> int | None:
    try:
        target = resolve_url(url.strip())
        if target.platform != "generic-web" or target.crawl_type == "product":
            return None
        from crawlers.generic_web import GenericWebCrawler

        return GenericWebCrawler(target.base_url or url).count_sitemap_products()
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=600)
def _cached_probe_shop(
    url: str,
    woo_key: str,
    woo_secret: str,
    shopify_token: str,
    proxy_url: str,
) -> dict:
    creds = CrawlCredentials.load()
    if shopify_token:
        creds.shopify_token = shopify_token
    if woo_key:
        creds.woocommerce_key = woo_key
    if woo_secret:
        creds.woocommerce_secret = woo_secret
    if proxy_url.strip():
        creds.proxy_url = proxy_url.strip()
    return probe_shop_stats(url, creds).to_dict()


def _url_ready_for_probe(url: str) -> bool:
    cleaned = url.strip()
    if len(cleaned) < 4:
        return False
    if cleaned.startswith("http") or "." in cleaned:
        return True
    return len(cleaned) >= 10


def load_products_into_session(products: list[Product], export_name: str, info: str) -> None:
    st.session_state.products_raw = deepcopy(products)
    apply_edit_filter(products)
    st.session_state.export_name = export_name
    st.session_state.output_bytes = None
    st.session_state.output_name = None
    st.session_state.last_error = None
    st.session_state.last_info = info
    st.session_state.pending_rule_reset = False


def _current_export_format() -> str:
    return st.session_state.get("export_format", default_export_format())


def apply_edit_filter(products: list[Product] | None = None) -> None:
    """Lọc số lượng/thứ tự vào bảng Bước 6; gộp chỉnh sửa hiện tại vào products_raw trước."""
    if st.session_state.products_raw is None:
        return

    export_format = _current_export_format()
    if st.session_state.edited_df is not None:
        st.session_state.products_raw = merge_edited_rows(
            st.session_state.products_raw,
            st.session_state.edited_df.to_dict("records"),
            export_format=export_format,
        )

    source = products if products is not None else st.session_state.products_raw
    quantity = int(st.session_state.get("edit_quantity", 0))
    order = st.session_state.get("edit_order", "original")
    gmc_google_default = ""
    gmc_defaults: dict[str, str] | None = None
    if export_format == "gmc-feed-csv":
        gmc_kw = _gmc_preview_kwargs()
        gmc_google_default = category_id_from_option(gmc_kw["gmc_google_category"])
        gmc_defaults = gmc_kw["gmc_defaults"]
    rows = build_edit_subset_rows(
        source,
        quantity=quantity,
        order=order,
        export_format=export_format,
        google_category_default=gmc_google_default,
        gmc_defaults=gmc_defaults,
    )
    st.session_state.edited_df = pd.DataFrame(rows)
    st.session_state.output_bytes = None
    st.session_state.output_name = None


def apply_import_category(category: str) -> None:
    all_products = st.session_state.imported_products_all or []
    filtered = filter_products_by_category(all_products, category)
    st.session_state.import_selected_category = category

    if category == "Tất cả":
        export_name = st.session_state.import_source_name or "import_all"
    else:
        export_name = slug_from_category(category)

    load_products_into_session(
        filtered,
        export_name,
        f"Category **{category}** · **{len(filtered)}** / {len(all_products)} sản phẩm — sẵn sàng xuất file",
    )


def run_csv_import(uploaded_file) -> None:
    st.session_state.last_error = None
    try:
        products, df = parse_csv_to_products(uploaded_file.getvalue())
        if not products:
            st.session_state.last_error = (
                "Không đọc được sản phẩm nào. Cần cột title/name và dữ liệu hợp lệ."
            )
            return

        stem = Path(uploaded_file.name).stem
        st.session_state.imported_products_all = deepcopy(products)
        st.session_state.import_source_name = stem
        st.session_state.import_selected_category = "Tất cả"
        st.session_state.export_format = st.session_state.get("export_format", "woocommerce-csv")

        load_products_into_session(
            products,
            stem,
            f"Import **{uploaded_file.name}** · **{len(products)}** SP · **{len(df.columns)}** cột",
        )
    except Exception as exc:
        st.session_state.last_error = f"Lỗi đọc CSV: {exc}"


def _csv_upload_signature(uploaded) -> str:
    return f"{uploaded.name}:{uploaded.size}"


def _maybe_auto_import_csv(uploaded) -> None:
    sig = _csv_upload_signature(uploaded)
    if st.session_state.get("csv_import_sig") == sig:
        return
    with st.spinner(f"Đang đọc {uploaded.name}..."):
        run_csv_import(uploaded)
    st.session_state.csv_import_sig = sig
    st.session_state["import_category_pill"] = "Tất cả"


def _category_pill_label(name: str, total: int, counts: dict[str, int]) -> str:
    if name == "Tất cả":
        return f"Tất cả ({total})"
    return f"{name} ({counts.get(name, 0)})"


def render_category_pills(products: list[Product]) -> None:
    total = len(products)
    selected = st.session_state.import_selected_category
    categories = list_categories(products)
    counts = {name: count for name, count in categories}
    options = ["Tất cả", *[name for name, _ in categories]]

    st.markdown(
        """
<style>
    div[data-testid="stPills"] > div,
    div[data-testid="stPills"] [data-baseweb="button-group"] {
        flex-wrap: wrap !important;
        row-gap: 0.35rem !important;
    }
</style>
        """,
        unsafe_allow_html=True,
    )

    picked = st.pills(
        "Category",
        options=options,
        default=selected if selected in options else "Tất cả",
        format_func=lambda name: _category_pill_label(name, total, counts),
        selection_mode="single",
        key="import_category_pill",
        label_visibility="collapsed",
        width="content",
    )
    if picked and picked != selected:
        apply_import_category(picked)


def render_import_panel() -> None:
    st.markdown("### Bước 1 · Import CSV · Chọn category")

    uploaded = st.file_uploader(
        "Chọn file CSV",
        type=["csv"],
        key="csv_import_uploader",
        label_visibility="collapsed",
        help="Hỗ trợ UTF-8, cột tiếng Anh hoặc Việt không dấu.",
    )
    if uploaded is not None:
        _maybe_auto_import_csv(uploaded)

    products = st.session_state.imported_products_all
    if products:
        source = st.session_state.import_source_name or "import"
        selected = st.session_state.import_selected_category
        st.caption(
            f"**{source}** · **{len(products)}** SP · đang chọn **{selected}** · "
            "upload file khác để thay thế."
        )
        render_category_pills(products)
    elif uploaded is None:
        st.caption(
            "Upload CSV: **name/title**, **price**, **Category**, **Tag**, **description**, **Images**."
        )

    with st.expander("Cột CSV được nhận diện"):
        st.markdown(
            """
| Trường | Tên cột (ví dụ) |
|--------|------------------|
| Title | title, name, product name |
| Price | price, regular price, variant price |
| Category | categories, category |
| Tags | tags, tag |
| Ảnh | **Images** (URL cách nhau `,`) · **Images[0], Images[1]…** |
| SKU | sku |
| Mô tả | description, descriptio, body |
| Vendor | vendor, brand |
            """
        )


def render_product_image(product: Product, proxy_url: str) -> None:
    if not product.image_urls:
        st.caption("Không có ảnh")
        return

    image_url = product.image_urls[0]
    try:
        if "redbubble.net" in image_url.lower():
            image_bytes = _cached_product_image(
                image_url,
                proxy_url,
                st.session_state.image_cache_nonce,
            )
            if image_bytes:
                st.image(image_bytes, use_container_width=True)
            else:
                st.caption("Ảnh Redbubble — thêm Proxy")
        else:
            st.image(image_url, use_container_width=True)
    except Exception:
        st.caption("Không tải được ảnh preview")


def _gmc_preview_kwargs() -> dict:
    return {
        "gmc_currency": st.session_state.get("gmc_currency", "USD"),
        "gmc_condition": st.session_state.get("gmc_condition", "new"),
        "gmc_google_category": category_id_from_option(
            st.session_state.get("gmc_google_category", GMC_DEFAULT_GOOGLE_CATEGORY)
        ),
        "gmc_variant_mode": st.session_state.get("gmc_variant_mode", "all_variations"),
        "gmc_defaults": {
            "gender": st.session_state.get("gmc_default_gender", GMC_DEFAULT_GENDER),
            "age group": st.session_state.get("gmc_default_age_group", GMC_AGE_GROUP_DEFAULT),
            "gtin": st.session_state.get("gmc_gtin_default", ""),
        },
    }


def _gmc_data_editor_column_config(hr: dict[str, str]) -> dict:
    return {
        "row_id": None,
        "title": st.column_config.TextColumn(column_header_label("title", hr), width="large"),
        "price": st.column_config.NumberColumn(
            column_header_label("price", hr),
            min_value=0.0,
            format="%.2f",
        ),
        "vendor": st.column_config.TextColumn(column_header_label("vendor", hr)),
        "sku": st.column_config.TextColumn(column_header_label("sku", hr)),
        "gtin": st.column_config.TextColumn(column_header_label("gtin", hr)),
        "google_product_category": st.column_config.SelectboxColumn(
            column_header_label("google_product_category", hr),
            options=GMC_CATEGORY_DROPDOWN_OPTIONS,
            help=(
                "Gợi ý tự động theo title/tags/description. "
                "Tra thêm mã taxonomy tại productcategory.net"
            ),
        ),
        "gmc_size": st.column_config.TextColumn(column_header_label("gmc_size", hr)),
        "gmc_color": st.column_config.TextColumn(column_header_label("gmc_color", hr)),
        "gmc_gender": st.column_config.SelectboxColumn(
            column_header_label("gmc_gender", hr),
            options=GMC_GENDER_OPTIONS,
            help="male / female / unisex",
        ),
        "gmc_age_group": st.column_config.SelectboxColumn(
            column_header_label("gmc_age_group", hr),
            options=GMC_AGE_GROUP_OPTIONS,
            help=f"Mặc định {GMC_AGE_GROUP_DEFAULT} nếu để trống",
        ),
    }


def _gmc_feed_data_editor_column_config() -> dict:
    return {
        "feed_key": None,
        "id": st.column_config.TextColumn("ID", disabled=True),
        "title": st.column_config.TextColumn("Title", disabled=True, width="medium"),
        "brand": st.column_config.TextColumn("Brand", width="small"),
        "size": st.column_config.TextColumn("Size", width="small"),
        "color": st.column_config.TextColumn("Color", width="small"),
        "gender": st.column_config.SelectboxColumn("Gender", options=GMC_GENDER_OPTIONS),
        "age group": st.column_config.SelectboxColumn(
            "Age group",
            options=GMC_AGE_GROUP_OPTIONS,
            help=f"Mặc định {GMC_AGE_GROUP_DEFAULT}",
        ),
    }


def _required_fields_caption(export_format: str) -> str:
    labels = [
        EDIT_FIELD_LABELS.get(col, col)
        for col in required_columns_for_format(export_format)
    ]
    return ", ".join(labels)


def render_export_file_preview_block(
    products: list[Product],
    export_format: str,
    export_fields: ExportFields,
    rules: ExportRules,
    count: int,
    *,
    limit: int = 3,
    image_mode: str = "comma",
    max_images_per_product: int = 0,
) -> None:
    max_img = None if max_images_per_product <= 0 else max_images_per_product
    sample_rows, sample_cols, label = build_export_file_preview(
        products,
        export_format,
        rules,
        export_fields,
        limit=min(limit, count),
        image_mode=image_mode,
        max_images_per_product=max_img,
        **_gmc_preview_kwargs(),
    )
    st.info(
        f"**{label}** — {count} dòng sản phẩm · "
        f"{len(all_export_columns(export_format))} cột template · "
        f"Trường đang chọn: {', '.join(export_fields.labels())}"
    )
    if export_format == "woocommerce-csv":
        st.caption(image_batch_summary(products, max_img))
    if sample_rows:
        st.dataframe(
            pd.DataFrame(sample_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.warning("Chưa có mẫu CSV — kiểm tra lại trường xuất.")

    with st.expander("Tất cả cột trong file import"):
        all_cols = all_export_columns(
            export_format,
            products,
            image_mode,
            max_img,
        )
        st.code(", ".join(all_cols), language=None)


def render_gmc_variant_mode_selector() -> None:
    st.selectbox(
        "Biến thể sản phẩm",
        options=list(GMC_VARIANT_MODES.keys()),
        format_func=lambda key: GMC_VARIANT_MODES[key],
        key="gmc_variant_mode",
        help=(
            "**All Variations** — mỗi size/màu = 1 dòng feed (khuyên dùng cho GMC). "
            "**Variable Products (Parent)** — chỉ 1 dòng / sản phẩm cha."
        ),
    )


def render_gmc_feed_settings(*, include_variant_mode: bool = True) -> None:
    if include_variant_mode:
        render_gmc_variant_mode_selector()
    st.caption(
        "File GMC — **16 cột**: id, title, description, link, condition, price, availability, "
        "image link, gtin, mpn, brand, google product category, size, gender, age group, color."
    )
    gmc_col1, gmc_col2, gmc_col3 = st.columns(3)
    with gmc_col1:
        st.text_input("Currency", key="gmc_currency", help="Ví dụ: USD")
    with gmc_col2:
        st.selectbox(
            "Condition",
            options=["new", "refurbished", "used"],
            key="gmc_condition",
        )
    with gmc_col3:
        st.selectbox(
            "Google product category mặc định",
            options=GMC_CATEGORY_DROPDOWN_OPTIONS,
            key="gmc_google_category",
            help=(
                "Mã taxonomy Google — áp dụng khi SP không khớp gợi ý tự động. "
                "Tra cứu thêm tại productcategory.net"
            ),
        )
    gmc_def1, gmc_def2 = st.columns(2)
    with gmc_def1:
        st.selectbox(
            "Gender mặc định",
            options=GMC_GENDER_OPTIONS,
            key="gmc_default_gender",
            help="male / female / unisex",
        )
    with gmc_def2:
        st.selectbox(
            "Age group mặc định",
            options=GMC_AGE_GROUP_OPTIONS,
            key="gmc_default_age_group",
            help=f"Mặc định {GMC_AGE_GROUP_DEFAULT} cho SP chưa có giá trị",
        )
    st.text_input(
        "GTIN mặc định (hàng loạt)",
        key="gmc_gtin_default",
        help="Áp dụng cho SP chưa có GTIN — vẫn sửa từng dòng ở Bước 6.",
    )


def render_gmc_step2_panel() -> str:
    st.markdown("### Bước 2 · Cấu hình feed GMC")
    st.info(f"**{FORMAT_OPTIONS['gmc-feed-csv']}** — crawl xong xuất thẳng file Google Merchant.")
    render_gmc_feed_settings(include_variant_mode=False)
    variant_mode = st.session_state.get("gmc_variant_mode", "all_variations")
    st.caption(
        f"Biến thể đã chọn ở **Bước 1**: **{GMC_VARIANT_MODES.get(variant_mode, variant_mode)}**."
    )
    return "gmc-feed-csv"


def render_export_format_selector() -> str:
    st.markdown("### Bước 2 · Chọn file cần xuất")
    _ensure_export_format_for_workflow()
    export_format = st.radio(
        "Xuất sang",
        options=GENERAL_EXPORT_FORMATS,
        format_func=lambda key: FORMAT_OPTIONS[key],
        horizontal=True,
        key="export_format",
    )
    if export_format in FORMAT_NOTES:
        st.caption(FORMAT_NOTES[export_format])
    return export_format


def render_preview_section(
    products: list[Product],
    export_format: str,
    export_fields: ExportFields,
    rules: ExportRules,
    image_mode: str = "comma",
    max_images_per_product: int = 0,
) -> None:
    count = len(products)
    if count == 0:
        st.warning("Crawl xong nhưng không có sản phẩm để xem trước.")
        return

    preview_n = min(10, count)
    format_label = FORMAT_OPTIONS.get(export_format, export_format)
    display_products = apply_rules_to_products(products, rules)

    st.markdown(f"### Bước 5 · Xem trước {preview_n}/{count} sản phẩm")
    st.caption(
        f"Định dạng xuất: **{format_label}** · "
        "Dữ liệu **đã áp dụng** từ Bước 3. "
        "Chỉnh thêm từng SP → tab **File feed xuất** ở Bước 6."
    )

    tab_products, tab_csv = st.tabs(["Sản phẩm", f"File CSV ({format_label})"])

    max_img = None if max_images_per_product <= 0 else max_images_per_product
    if export_format == "woocommerce-csv" and image_mode == "split_columns":
        st.caption(image_batch_summary(products, max_img))

    with tab_products:
        preview_df = pd.DataFrame(build_product_preview_table(display_products, limit=preview_n))
        st.dataframe(
            preview_df,
            column_config={
                "url": st.column_config.LinkColumn("URL"),
                "price": st.column_config.NumberColumn("Price", format="%.2f"),
                "image": st.column_config.TextColumn("Ảnh URL"),
            },
            use_container_width=True,
            hide_index=True,
            height=min(420, 80 + preview_n * 36),
        )

        with st.expander(f"Xem ảnh thumbnail ({preview_n} SP)", expanded=True):
            cols = st.columns(min(preview_n, 5))
            for idx, product in enumerate(display_products[:preview_n]):
                with cols[idx % len(cols)]:
                    with st.container(border=True):
                        render_product_image(product, st.session_state.proxy_url)
                        st.markdown(f"**{html.unescape(product.title or '')[:45]}**")
                        price_text = f"${product.price:.2f}" if product.price else "—"
                        st.caption(price_text)

    with tab_csv:
        render_export_file_preview_block(
            products,
            export_format,
            export_fields,
            rules,
            count,
            limit=min(3, count),
            image_mode=image_mode,
            max_images_per_product=max_images_per_product,
        )


def build_export_rules(section: str | None = None) -> ExportRules:
    full = ExportRules(
        price_multiplier=float(st.session_state.price_multiplier),
        price_add=float(st.session_state.price_add),
        round_to_99=bool(st.session_state.round_to_99),
        title_remove=str(st.session_state.title_remove or "").strip(),
        title_remove_ignore_case=bool(st.session_state.title_remove_ignore_case),
        title_prefix=str(st.session_state.title_prefix or "").strip(),
        title_suffix=str(st.session_state.title_suffix or "").strip(),
        category_override=str(st.session_state.category_override or "").strip(),
        category_add=str(st.session_state.category_add or "").strip(),
        tags_add=str(st.session_state.tags_add or "").strip(),
        vendor_override=str(st.session_state.vendor_override or "").strip(),
        product_type_override=str(st.session_state.product_type_override or "").strip(),
        description_action=str(st.session_state.get("description_action", "find_replace")),
        description_format=str(st.session_state.get("description_format", "html")),
        description_override=str(st.session_state.description_override or "").strip(),
        description_find=str(st.session_state.description_find or "").strip(),
        description_replace=str(st.session_state.description_replace or ""),
        description_replace_ignore_case=bool(st.session_state.description_replace_ignore_case),
        description_remove=str(st.session_state.description_remove or "").strip(),
        description_remove_ignore_case=bool(st.session_state.description_remove_ignore_case),
        description_convert_h_to_p=bool(st.session_state.description_convert_h_to_p),
    )
    if section is None:
        return full
    if section == "price":
        return ExportRules(
            price_multiplier=full.price_multiplier,
            price_add=full.price_add,
            round_to_99=full.round_to_99,
        )
    if section == "title":
        return ExportRules(
            title_remove=full.title_remove,
            title_remove_ignore_case=full.title_remove_ignore_case,
            title_prefix=full.title_prefix,
            title_suffix=full.title_suffix,
        )
    if section == "meta":
        return ExportRules(
            category_override=full.category_override,
            category_add=full.category_add,
            tags_add=full.tags_add,
            vendor_override=full.vendor_override,
            product_type_override=full.product_type_override,
        )
    if section == "description":
        return ExportRules(
            description_action=full.description_action,
            description_format=full.description_format,
            description_override=full.description_override,
            description_find=full.description_find,
            description_replace=full.description_replace,
            description_replace_ignore_case=full.description_replace_ignore_case,
            description_remove=full.description_remove,
            description_remove_ignore_case=full.description_remove_ignore_case,
            description_convert_h_to_p=full.description_convert_h_to_p,
        )
    return full


def get_working_products() -> list[Product]:
    """Gộp dữ liệu gốc với chỉnh sửa từng dòng trong bảng."""
    if st.session_state.products_raw is None:
        return []
    edited_df = st.session_state.edited_df
    if edited_df is None:
        return deepcopy(st.session_state.products_raw)
    return merge_edited_rows(
        deepcopy(st.session_state.products_raw),
        edited_df.to_dict("records"),
        export_format=_current_export_format(),
    )


def reset_rule_widgets_if_needed() -> None:
    """Reset ô tùy chỉnh trước khi vẽ widget (Streamlit không cho sửa key sau khi widget đã tạo)."""
    if not st.session_state.get("pending_rule_reset"):
        return
    for key, value in RULE_DEFAULTS.items():
        st.session_state[key] = value
    st.session_state.description_action = "find_replace"
    st.session_state.pending_rule_reset = False


def _on_description_action_change() -> None:
    """Dọn state khi đổi chế độ mô tả — tránh xung đột widget Streamlit."""
    action = st.session_state.get("description_action")
    if action == "find_replace":
        st.session_state["description_override"] = ""
    elif action == "replace_all":
        st.session_state["description_find"] = ""
        st.session_state["description_replace"] = ""
        st.session_state["description_remove"] = ""


def apply_bulk_customization() -> None:
    """Ghi tùy chỉnh hàng loạt vào bảng chỉnh sửa và dữ liệu gốc."""
    if st.session_state.products_raw is None:
        st.session_state.last_error = "Chưa có sản phẩm. Hãy crawl hoặc import CSV trước."
        return

    section = st.session_state.get("bulk_customize_section", "price")
    rules = build_export_rules(section=section)
    products = get_working_products()
    applied = apply_rules_to_products(products, rules)

    st.session_state.products_raw = deepcopy(applied)
    apply_edit_filter(applied)

    st.session_state.pending_rule_reset = True

    shown = len(st.session_state.edited_df)
    total = len(applied)
    section_label = BULK_CUSTOMIZE_SECTIONS.get(section, section)
    st.session_state.output_bytes = None
    st.session_state.output_name = None
    st.session_state.last_error = None
    msg = (
        f"Đã áp dụng **{section_label}** cho **{total}** sản phẩm — "
        f"bảng Bước 6 hiển thị **{shown}** SP — kiểm tra tab **File CSV**."
    )
    st.session_state.last_info = msg
    st.session_state.last_bulk_apply = msg


def _preview_price(base_price: float | None, rules: ExportRules) -> str:
    if base_price is None:
        return "—"
    sample = apply_export_rules(
        Product(source="preview", id="0", title="Preview", price=base_price),
        rules,
    )
    return f"${base_price:.2f} → ${sample.price:.2f}"


def _preview_description_change(product: Product, rules: ExportRules) -> str:
    before = re.sub(r"<[^>]+>", " ", product.description or "")
    before = " ".join(before.split())[:50] or "—"
    after_html = apply_export_rules(product, rules).description or ""
    after = re.sub(r"<[^>]+>", " ", after_html)
    after = " ".join(after.split())[:50] or "—"
    if before == after:
        return after
    return f"{before} → {after}"


def _preview_title_change(product: Product, rules: ExportRules) -> str:
    before = _truncate_preview(product.title or "—", 45)
    after = _truncate_preview(apply_export_rules(product, rules).title or "—", 45)
    if before == after:
        return after
    return f"{before} → {after}"


def _preview_text_field(product: Product, rules: ExportRules, field: str) -> str:
    updated = apply_export_rules(product, rules)
    if field == "title":
        return _truncate_preview(updated.title or "—", 60)
    if field == "tags":
        return ", ".join(updated.tags[:8]) or "—"
    if field == "categories":
        return ", ".join(updated.categories[:5]) or "—"
    return "—"


def _truncate_preview(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text or "—"


def _render_bulk_price_section() -> None:
    preset_col1, preset_col2, preset_col3, preset_col4, preset_col5 = st.columns(5)
    if preset_col1.button("Giá gốc", use_container_width=True, key="bulk_price_reset"):
        st.session_state.price_multiplier = 1.0
        st.session_state.price_add = 0.0
        st.session_state.round_to_99 = False
    if preset_col2.button("×1.5", use_container_width=True, key="bulk_price_x15"):
        st.session_state.price_multiplier = 1.5
    if preset_col3.button("×2", use_container_width=True, key="bulk_price_x2"):
        st.session_state.price_multiplier = 2.0
    if preset_col4.button("+ $5", use_container_width=True, key="bulk_price_add5"):
        st.session_state.price_add = 5.0
    if preset_col5.button("Làm tròn .99", use_container_width=True, key="bulk_price_round99"):
        st.session_state.round_to_99 = True

    col1, col2, col3 = st.columns(3)
    with col1:
        st.number_input(
            "Nhân giá (×)",
            min_value=0.0,
            step=0.1,
            format="%.2f",
            key="price_multiplier",
        )
    with col2:
        st.number_input(
            "Cộng thêm ($)",
            step=0.5,
            format="%.2f",
            key="price_add",
        )
    with col3:
        st.checkbox("Làm tròn về .99", key="round_to_99")


def _render_bulk_title_section() -> None:
    st.text_input(
        "Xóa chữ trong title",
        placeholder="RustyThread, OldBrand",
        help="Xóa hàng loạt từ/cụm trong tiêu đề. Nhiều cụm cách nhau dấu phẩy.",
        key="title_remove",
    )
    st.checkbox(
        "Xóa title không phân biệt hoa/thường",
        key="title_remove_ignore_case",
    )
    prefix_col, suffix_col = st.columns(2)
    with prefix_col:
        st.text_input(
            "Tiền tố title (thêm đầu)",
            placeholder="VEEXOVAR - ",
            help="Thêm sau bước xóa chữ",
            key="title_prefix",
        )
    with suffix_col:
        st.text_input("Hậu tố title (thêm cuối)", placeholder="", key="title_suffix")


def _render_bulk_meta_section() -> None:
    cat_col, tag_col = st.columns(2)
    with cat_col:
        st.text_input(
            "Category (thay thế)",
            placeholder="Women's Tote Bags",
            help="Ghi đè toàn bộ category cũ",
            key="category_override",
        )
        st.text_input(
            "Vendor / thương hiệu (thay thế)",
            help="Ghi đè vendor cũ",
            key="vendor_override",
        )
    with tag_col:
        st.text_input(
            "Tags (thay thế)",
            placeholder="15/06/2026",
            help="Ghi đè toàn bộ tag cũ — nhiều tag cách nhau bằng dấu phẩy",
            key="tags_add",
        )
        st.text_input(
            "Product type (thay thế)",
            placeholder="Baseball Cap",
            key="product_type_override",
        )


def _render_bulk_description_section() -> None:
    st.radio(
        "Chế độ chỉnh description",
        options=["find_replace", "replace_all"],
        format_func=lambda key: {
            "find_replace": "Tìm & thay từng đoạn (trên mô tả gốc)",
            "replace_all": "Thay tất cả SP — dán 1 mô tả chung",
        }[key],
        horizontal=True,
        key="description_action",
        on_change=_on_description_action_change,
    )
    st.checkbox(
        "Convert tất cả thẻ h1–h6 → thẻ p",
        help="Ví dụ: <h3 style=\"...\">text</h3> → <p style=\"...\">text</p>.",
        key="description_convert_h_to_p",
    )

    if st.session_state.get("description_action", "find_replace") == "replace_all":
        st.info(
            "Dán **1 mô tả** bên dưới → bấm **Áp dụng** → **tất cả** sản phẩm "
            "dùng chung mô tả này (mô tả cũ bị thay hết)."
        )
        st.radio(
            "Định dạng mô tả",
            options=["html", "text"],
            format_func=lambda key: "HTML" if key == "html" else "Text thuần",
            horizontal=True,
            key="description_format",
        )
        st.text_area(
            "Mô tả chung cho tất cả SP",
            placeholder=(
                "<p>Free shipping worldwide.</p>\n"
                "<p>Premium quality canvas tote bag.</p>"
            ),
            height=200,
            key="description_override",
        )
    else:
        find_col, replace_col = st.columns(2)
        with find_col:
            st.text_input(
                "Tìm",
                placeholder="<h3>Celebrate the stress-free joy",
                key="description_find",
            )
        with replace_col:
            st.text_input(
                "Thay bằng",
                placeholder="<p>Celebrate the stress-free joy",
                help="Để trống = xóa đoạn tìm được",
                key="description_replace",
            )
        st.checkbox(
            "Tìm/thay không phân biệt hoa/thường",
            key="description_replace_ignore_case",
        )
        remove_col, ignore_col = st.columns([3, 1])
        with remove_col:
            st.text_input(
                "Xóa chữ trong mô tả",
                placeholder="RustyThread, OldBrand",
                key="description_remove",
            )
        with ignore_col:
            st.checkbox(
                "Xóa không phân biệt hoa/thường",
                key="description_remove_ignore_case",
            )


def _render_data_status_banner() -> None:
    """Nhắc rõ đã crawl hay chưa — tránh nhầm Bước 3/4 là đã có dữ liệu."""
    products = st.session_state.products_raw
    mode = st.session_state.get("workflow_mode", "crawl")

    if products is None:
        if is_redbubble_workflow(mode):
            st.warning(
                "**Chưa có sản phẩm.** Dán link Redbubble `/shop/...` hoặc chọn **Link mẫu**, "
                "nhập **Proxy** nếu cần, rồi bấm **Crawl Redbubble**."
            )
        elif mode in ("crawl", "crawl-gmc"):
            st.warning(
                "**Chưa có sản phẩm.** Ở **Bước 1** nhập link shop/category, "
                "rồi bấm **Crawl dữ liệu**."
            )
        return

    if not products:
        st.warning("Crawl xong nhưng không có sản phẩm nào trong danh sách.")
        return

    sample = products[0]
    count = len(products)
    title = _truncate_preview(sample.title or "—", 55)
    if sample.price is not None:
        price_text = f"${sample.price:.2f}"
    else:
        price_text = "chưa có giá"

    thumb_col, info_col = st.columns([1, 5])
    with thumb_col:
        img_url = (sample.image_urls or [None])[0]
        if img_url:
            proxy = st.session_state.get("proxy_url", "").strip() or None
            img_bytes = _cached_product_image(
                img_url,
                proxy or "",
                st.session_state.get("image_cache_nonce", 0),
            )
            if img_bytes:
                st.image(img_bytes, width=72)
            else:
                st.caption("Ảnh")
        else:
            st.caption("Ảnh")
    with info_col:
        st.success(
            f"**{count}** sản phẩm sẵn sàng · **{title}** · Giá mẫu: **{price_text}** · "
            "Cuộn xuống **Bước 5** (xem trước) và **Bước 6** (bảng chỉnh sửa) sau Bước 4."
        )


def _render_bulk_section_preview(
    section: str,
    rules: ExportRules,
    sample_product: Product,
) -> None:
    previews: list[str] = []
    if section == "price":
        working = get_working_products()
        sample_prices = [p.price for p in working if p.price is not None][:3]
        if sample_prices:
            price_text = ", ".join(_preview_price(price, rules) for price in sample_prices)
            previews.append(f"**Giá:** {price_text}")
        else:
            previews.append(
                "**Giá:** SP chưa có giá — crawl lại (bật proxy) hoặc nhập giá ở **Bước 6**."
            )
    elif section == "title":
        previews.append(f"**Title:** {_preview_title_change(sample_product, rules)}")
    elif section == "meta":
        previews.append(f"**Tags:** {_preview_text_field(sample_product, rules, 'tags')}")
        previews.append(
            f"**Categories:** {_preview_text_field(sample_product, rules, 'categories')}"
        )
        if rules.vendor_override:
            previews.append(f"**Vendor:** {rules.vendor_override}")
        if rules.product_type_override:
            previews.append(f"**Type:** {rules.product_type_override}")
    elif section == "description":
        if (
            (rules.description_action == "replace_all" and rules.description_override)
            or rules.description_find
            or rules.description_remove
            or rules.description_convert_h_to_p
        ):
            previews.append(
                f"**Mô tả:** {_preview_description_change(sample_product, rules)}"
            )
    if previews:
        st.info(" · ".join(previews))


def render_customization_panel() -> ExportRules:
    reset_rule_widgets_if_needed()

    st.markdown("### Bước 3 · Tùy chỉnh hàng loạt")
    st.caption(
        "Chọn loại tùy chỉnh → chỉ hiện phần liên quan. "
        "Bấm **Áp dụng** để ghi **đúng mục đang chọn** vào bảng Bước 6."
    )

    section = st.radio(
        "Tùy chỉnh",
        options=list(BULK_CUSTOMIZE_SECTIONS.keys()),
        format_func=lambda key: BULK_CUSTOMIZE_SECTIONS[key],
        horizontal=True,
        key="bulk_customize_section",
        label_visibility="collapsed",
    )

    with st.container(border=True):
        st.markdown(f"**{BULK_CUSTOMIZE_SECTIONS[section]}**")
        if section == "price":
            _render_bulk_price_section()
        elif section == "title":
            _render_bulk_title_section()
        elif section == "meta":
            _render_bulk_meta_section()
        elif section == "description":
            _render_bulk_description_section()

    with st.expander("Hướng dẫn chi tiết", expanded=False):
        st.markdown(
            """
**Giá** — Nhân (×), cộng ($), làm tròn .99. Preset nhanh phía trên.

**Title** — Xóa từ/cụm, thêm tiền tố/hậu tố. Nhiều cụm cách nhau dấu phẩy.

**Category, tags & shop** — Điền giá trị mới → **thay thế hoàn toàn** giá trị cũ.

**Mô tả** — Tìm & thay trong HTML gốc, hoặc dán 1 mô tả chung cho tất cả SP.
            """
        )

    rules = build_export_rules(section=section)

    has_products = bool(st.session_state.products_raw)
    apply_col, preview_col = st.columns([1, 2])
    with apply_col:
        apply_clicked = st.button(
            f"Áp dụng {BULK_CUSTOMIZE_SECTIONS[section]}",
            type="primary",
            use_container_width=True,
            disabled=not has_products,
        )
    with preview_col:
        if not has_products:
            st.caption(
                "Chưa có dữ liệu — quay **Bước 1** bấm **Crawl dữ liệu** "
                "(Redbubble cần link `/shop/...` + proxy nếu bị chặn)."
            )
        else:
            working = get_working_products()
            if working:
                _render_bulk_section_preview(section, rules, working[0])
            st.caption("Bấm **Áp dụng** để ghi vào bảng **Bước 6** (cuộn xuống sau Bước 4).")

    bulk_msg = st.session_state.pop("last_bulk_apply", None)
    if bulk_msg:
        st.success(bulk_msg)

    if apply_clicked:
        apply_bulk_customization()
        st.rerun()

    return rules


def render_export_fields_panel() -> ExportFields:
    export_format = st.session_state.get("export_format", "woocommerce-csv")
    gmc_mode = is_gmc_workflow()

    if gmc_mode:
        st.markdown("### Bước 4 · Trường feed GMC")
        variant_mode = st.session_state.get("gmc_variant_mode", "all_variations")
        st.caption(
            f"Chế độ biến thể: **{GMC_VARIANT_MODES.get(variant_mode, variant_mode)}** · "
            f"**{len(GMC_FEED_COLUMNS)}** cột cố định theo chuẩn Google Merchant."
        )
        st.info("Cột: " + " · ".join(GMC_FEED_COLUMNS))
        return ExportFields.from_preset("all")

    st.markdown("### Bước 4 · Chọn trường trong file xuất")
    st.caption(
        "Chọn preset nhanh hoặc tích từng trường. "
        "File xuất giữ đúng template — trường không chọn sẽ để trống."
    )

    if export_format == "woocommerce-csv":
        st.selectbox(
            "Định dạng ảnh (WooCommerce)",
            options=list(IMAGE_EXPORT_MODES.keys()),
            format_func=lambda key: IMAGE_EXPORT_MODES[key],
            key="image_export_mode",
        )
        st.number_input(
            "Giới hạn ảnh / SP (0 = tự động lấy hết)",
            min_value=0,
            max_value=50,
            value=int(st.session_state.get("max_images_per_product", 0)),
            help="0 = lấy hết ảnh crawl được. Chỉ ảnh hưởng khi chọn chế độ tách cột Image 2…N.",
            key="max_images_per_product",
        )
    elif export_format == "web-feed-csv":
        st.caption("Cột **images**: nhiều URL cách nhau dấu phẩy (chuẩn feed web).")
        st.number_input(
            "Giới hạn ảnh / SP (0 = tự động lấy hết)",
            min_value=0,
            max_value=50,
            value=int(st.session_state.get("max_images_per_product", 0)),
            help="0 = lấy hết ảnh crawl được vào cột images.",
            key="max_images_per_product",
        )
    elif export_format == "gmc-feed-csv":
        render_gmc_feed_settings()

    preset = st.radio(
        "Preset",
        options=list(PRESET_LABELS.keys()),
        format_func=lambda key: PRESET_LABELS[key],
        horizontal=True,
        key="export_field_preset",
    )

    if preset == "custom":
        selected = st.multiselect(
            "Trường xuất",
            options=list(EXPORT_FIELD_LABELS.keys()),
            default=st.session_state.export_fields_custom or list(EXPORT_FIELD_LABELS.keys()),
            format_func=lambda key: EXPORT_FIELD_LABELS[key],
            key="export_fields_custom",
        )
        if not selected:
            st.warning("Chọn ít nhất một trường (ví dụ: Title + Giá).")
            selected = ["title", "price"]
    else:
        selected = None

    fields = ExportFields.from_preset(preset, custom=selected)
    st.info(f"File sẽ gồm: **{', '.join(fields.labels())}**")
    return fields


def run_crawl_only(
    url: str,
    limit: int,
    pages: int,
    shopify_token: str,
    woo_key: str,
    woo_secret: str,
    category_slug: str = "Tất cả",
) -> None:
    st.session_state.last_error = None
    st.session_state.last_info = None
    st.session_state.output_bytes = None
    st.session_state.output_name = None

    if not url.strip():
        st.session_state.last_error = "Vui lòng nhập link cần crawl."
        return

    creds = CrawlCredentials.load()
    if shopify_token:
        creds.shopify_token = shopify_token
    if woo_key:
        creds.woocommerce_key = woo_key
    if woo_secret:
        creds.woocommerce_secret = woo_secret

    try:
        crawl_url = url.strip()
        if is_redbubble_workflow():
            from ui.redbubble_workflow import validate_redbubble_url

            rb_check = validate_redbubble_url(crawl_url)
            if not rb_check.get("crawl_ready"):
                st.session_state.last_info = None
                st.session_state.last_error = str(
                    rb_check.get("message") or "Link Redbubble chưa hợp lệ."
                )
                return

        preview = describe_url(crawl_url)
        category_filter: str | None = None
        target = preview.get("target") if preview.get("ok") else None
        rb_mode = is_redbubble_workflow() or (
            target is not None and getattr(target, "platform", "") == "redbubble"
        )
        if preview["ok"] and category_slug and category_slug != "Tất cả" and not rb_mode:
            target = preview["target"]
            if target.platform == "generic-web":
                category_filter = category_slug
            else:
                crawl_url = build_category_crawl_url(
                    target.base_url or crawl_url,
                    target.platform,
                    category_slug,
                )
                preview = describe_url(crawl_url)

        if preview["ok"]:
            if preview.get("crawl_ready", True):
                st.session_state.last_info = f"Nhận diện: **{preview['message']}**"
            else:
                st.session_state.last_info = None
                st.session_state.last_error = (
                    preview.get("notice")
                    or preview.get("hint")
                    or "Link chưa đủ thông tin để crawl."
                )
                return
        progress_callback = None
        progress_bar = None
        progress_text = None
        target_preview = preview.get("target") if preview.get("ok") else None
        rb_platform = (
            target_preview
            and getattr(target_preview, "platform", "") == "redbubble"
        )
        if target_preview and getattr(target_preview, "platform", "") == "generic-web":
            progress_bar = st.progress(0.0, text="Đang chuẩn bị crawl web thuần…")
            progress_text = st.empty()

            def progress_callback(current: int, total: int, matched: int) -> None:
                if progress_bar is not None and total:
                    progress_bar.progress(
                        min(current / total, 1.0),
                        text=f"Đang crawl {current}/{total} link · {matched} SP khớp",
                    )
                if progress_text is not None and total:
                    est_min = max(1, int(total * 1.4 / 60))
                    progress_text.caption(
                        f"Web thuần quét từng link trong sitemap — crawl hết **~{est_min} phút**. "
                        f"Đặt **Giới hạn SP = 10–50** để test nhanh."
                    )

        crawl_kwargs = dict(
            url=crawl_url,
            credentials=creds,
            limit=limit if limit > 0 else None,
            max_pages=pages if pages > 0 else None,
            proxy_url=st.session_state.proxy_url.strip() or None,
            category_slug=category_filter,
            progress_callback=progress_callback,
        )
        if rb_platform:
            spinner_msg = (
                "Đang crawl Redbubble… (~82 SP/trang). "
                "Nếu lỗi, bật **Proxy & Redbubble** rồi thử lại."
            )
            with st.spinner(spinner_msg):
                products, target = run_auto_crawl(**crawl_kwargs)
        else:
            products, target = run_auto_crawl(**crawl_kwargs)
        if progress_bar is not None:
            progress_bar.empty()
        if progress_text is not None:
            progress_text.empty()
        st.session_state.products_raw = deepcopy(products)
        apply_edit_filter(products)
        st.session_state.export_name = (
            target.category_slug or target.handle or target.query or "products"
        )
        variant_mode = st.session_state.get("gmc_variant_mode", "all_variations")
        feed_rows = count_gmc_export_rows(products, variant_mode)
        if is_gmc_workflow() or st.session_state.get("export_format") == "gmc-feed-csv":
            st.session_state.last_info = (
                f"Crawl xong **{len(products)}** SP → **{feed_rows}** dòng feed GMC "
                f"({GMC_VARIANT_MODES.get(variant_mode, variant_mode)}) · "
                f"{preview.get('message', '')}"
            )
        else:
            st.session_state.last_info = (
                f"Crawl xong **{len(products)}** sản phẩm · {preview.get('message', '')}"
            )
        st.session_state.imported_products_all = None
    except UnsupportedUrlError as exc:
        st.session_state.last_error = str(exc)
    except CrawlError as exc:
        st.session_state.last_error = str(exc)
    except Exception as exc:
        st.session_state.last_error = (
            f"Lỗi: {exc}. Nếu vẫn lặp lại, tắt app và mở lại **{APP_NAME}**."
        )


def export_current_products(
    export_format: str,
    rules: ExportRules,
    edited_df: pd.DataFrame,
    fields: ExportFields,
    progress_bar,
    status_text,
    max_workers: int = DEFAULT_EXPORT_WORKERS,
) -> None:
    if st.session_state.products_raw is None:
        st.session_state.last_error = "Chưa có dữ liệu. Hãy crawl trước."
        return

    products = products_from_edit_table(
        deepcopy(st.session_state.products_raw),
        edited_df.to_dict("records"),
        export_format=export_format,
    )
    if export_format == "gmc-feed-csv":
        feed_edits = st.session_state.get("gmc_feed_edits") or []
        merge_gmc_feed_edits(st.session_state.products_raw, feed_edits)
        products = products_from_edit_table(
            deepcopy(st.session_state.products_raw),
            edited_df.to_dict("records"),
            export_format=export_format,
        )
    total_raw = len(st.session_state.products_raw)

    validation = validate_edited_df(edited_df, export_format)
    if not validation["ok"]:
        missing = ", ".join(
            EDIT_FIELD_LABELS.get(col, col)
            for col in validation["columns_missing"]
        )
        st.session_state.last_error = (
            f"Không xuất file — còn thiếu: {missing}. Điền đủ ở Bước 6."
        )
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{st.session_state.export_name}_{stamp}"
    output_path = Path(default_output_path("export", name, export_format))

    def on_progress(value: float, message: str) -> None:
        try:
            progress_bar.progress(value, text=message)
        except TypeError:
            progress_bar.progress(value)
        status_text.caption(message)

    exported = export_products(
        products,
        str(output_path),
        fmt=export_format,
        rules=rules,
        fields=fields,
        progress=on_progress,
        proxy_url=st.session_state.proxy_url.strip() or None,
        image_mode=st.session_state.get("image_export_mode", "comma"),
        max_images_per_product=int(st.session_state.get("max_images_per_product", 0)) or None,
        max_workers=max_workers,
        gmc_currency=st.session_state.get("gmc_currency", "USD"),
        gmc_condition=st.session_state.get("gmc_condition", "new"),
        gmc_google_category=category_id_from_option(
            st.session_state.get("gmc_google_category", GMC_DEFAULT_GOOGLE_CATEGORY)
        ),
        gmc_defaults={
            "gender": st.session_state.get("gmc_default_gender", GMC_DEFAULT_GENDER),
            "age group": st.session_state.get("gmc_default_age_group", GMC_AGE_GROUP_DEFAULT),
            "gtin": st.session_state.get("gmc_gtin_default", ""),
        },
        gmc_variant_mode=st.session_state.get("gmc_variant_mode", "all_variations"),
    )

    st.session_state.output_bytes = exported.read_bytes()
    st.session_state.output_name = Path(exported).name

    st.session_state.last_error = None
    if export_format == "gmc-feed-csv":
        variant_mode = st.session_state.get("gmc_variant_mode", "all_variations")
        feed_rows = count_gmc_export_rows(products, variant_mode)
        st.session_state.last_info = (
            f"Đã xuất **{feed_rows}** dòng feed GMC ({len(products)} SP gốc) · "
            f"`{st.session_state.output_name}`"
        )
    else:
        st.session_state.last_info = (
            f"Đã xuất **{len(products)}** SP từ bảng Bước 6 / {total_raw} SP gốc · "
            f"{len(fields.enabled)} trường → `{st.session_state.output_name}`"
        )


def _crawl_category_pill_label(slug: str, categories: list[dict]) -> str:
    if slug == "Tất cả":
        return "Tất cả shop"
    for cat in categories:
        if cat.get("slug") == slug:
            name = cat.get("name") or slug
            count = cat.get("count")
            return f"{name} ({count})" if count is not None else name
    return slug


def _format_sp_count(count: int | None) -> str:
    if count is None:
        return ""
    return f"{count:,}".replace(",", ".")


def render_crawl_category_pills(probe: dict) -> None:
    platform = probe.get("platform") or ""
    categories = probe.get("categories") or []
    if not categories or probe.get("crawl_type") == "product":
        return

    slugs = [cat["slug"] for cat in categories if cat.get("slug")]
    url_slug = probe.get("url_category_slug")

    if platform == "redbubble":
        st.caption("Danh mục crawl (theo link `/shop/...` bạn dán):")
        if len(categories) == 1:
            cat = categories[0]
            slug = cat.get("slug") or url_slug or slugs[0]
            st.session_state["crawl_category_pill"] = slug
            name = cat.get("name") or slug
            count = cat.get("count")
            count_text = f" · **{_format_sp_count(count)}** SP" if count is not None else ""
            st.info(
                f"Sẽ crawl danh mục **{name}**{count_text}. "
                "Redbubble **không có** crawl \"cả shop\" — chỉ lấy SP trong link bạn dán."
            )
            return

        options = slugs
        default = url_slug if url_slug in options else (slugs[0] if slugs else "Tất cả")
        st.session_state["crawl_category_pill"] = default
        st.pills(
            "Danh mục Redbubble",
            options=options,
            default=default,
            format_func=lambda slug: _crawl_category_pill_label(slug, categories),
            selection_mode="single",
            key="crawl_category_pill",
            label_visibility="collapsed",
            width="content",
        )
        return

    options = ["Tất cả", *slugs]
    url_slug = probe.get("url_category_slug")
    default = url_slug if url_slug in options else "Tất cả"

    st.caption(f"Chọn danh mục crawl ({len(categories)}):")
    st.markdown(
        """
<style>
    div[data-testid="stPills"] > div,
    div[data-testid="stPills"] [data-baseweb="button-group"] {
        flex-wrap: wrap !important;
        row-gap: 0.35rem !important;
    }
</style>
        """,
        unsafe_allow_html=True,
    )
    st.pills(
        "Crawl category",
        options=options,
        default=st.session_state.get("crawl_category_pill", default),
        format_func=lambda slug: _crawl_category_pill_label(slug, categories),
        selection_mode="single",
        key="crawl_category_pill",
        label_visibility="collapsed",
        width="content",
    )


def render_url_preview(
    url: str,
    shopify_token: str = "",
    woo_key: str = "",
    woo_secret: str = "",
) -> None:
    if not url.strip():
        return

    if not _url_ready_for_probe(url):
        st.caption("Nhập đủ link shop (vd. `yourstore.com`) để nhận diện nền tảng.")
        return

    cleaned = url.strip()
    info: dict
    probe: dict
    with st.status("Đang nhận diện shop...", expanded=False) as status:
        status.write("Đang kết nối và kiểm tra **Shopify · WooCommerce · Redbubble · Web thuần**…")
        info = _cached_describe_url(cleaned)
        target = info.get("target") if info.get("ok") else None
        platform = getattr(target, "platform", "") if target else ""

        if platform == "generic-web":
            status.write("Web thuần: đang đếm SP trong **sitemap-products**…")
            quick_total = _cached_generic_product_count(cleaned)
            if quick_total:
                status.write(
                    f"Đã đếm **{quick_total}** SP — đang lấy danh mục từ SP mẫu…"
                )
            else:
                status.write(
                    "Không thấy sitemap-products — không đếm được tổng SP shop."
                )
        elif platform == "redbubble":
            status.write("Redbubble: đang đếm SP trên trang **/shop/...**…")
        else:
            status.write("Đang đếm sản phẩm và danh mục…")

        probe = _cached_probe_shop(
            cleaned,
            woo_key,
            woo_secret,
            shopify_token,
            st.session_state.get("proxy_url", ""),
        )

        if info["ok"] and probe.get("ok"):
            if platform == "generic-web":
                total = probe.get("total_products")
                if total is not None:
                    status.write(f"Đã đếm **{total}** SP trong sitemap.")
                else:
                    status.write("Không đếm được SP — site thiếu sitemap-products.")
                categories = probe.get("categories") or []
                if categories:
                    status.write(f"Gom được **{len(categories)}** danh mục từ SP mẫu.")
                else:
                    status.write(
                        "Không có danh mục shop riêng — category nằm trong từng SP khi crawl."
                    )
            elif platform == "redbubble":
                if probe.get("total_products") is not None:
                    status.write(f"Đã đếm **{probe['total_products']}** SP trên Redbubble.")
                else:
                    status.write("Redbubble: dùng link /shop hoặc /i/... để crawl.")
            elif probe.get("total_products") is not None:
                status.write(
                    f"Đã đếm **{probe['total_products']}** SP"
                    + (
                        f" · **{len(probe.get('categories') or [])}** danh mục."
                        if probe.get("categories")
                        else "."
                    )
                )

        if info["ok"]:
            crawl_ready = info.get("crawl_ready", True)
            status.update(
                label=(
                    f"Nhận diện: {info['message']}"
                    if crawl_ready
                    else f"Chưa crawl được: {info['message']}"
                ),
                state="complete" if crawl_ready else "error",
                expanded=not crawl_ready,
            )
        else:
            status.update(label=info["message"], state="error", expanded=False)

    if info.get("notice") and not info.get("crawl_ready", True):
        st.error(info["notice"])
    elif info.get("ok") and not info.get("crawl_ready", True):
        st.error(
            "Link này **không crawl được**. Redbubble cần link dạng "
            "`/shop/từ-khóa` hoặc `/i/.../sản-phẩm` — không dùng trang chủ."
        )

    if info.get("ok") and probe.get("ok") and probe.get("message"):
        st.caption(probe["message"])
        if platform == "generic-web":
            total = probe.get("total_products")
            if total:
                est_min = max(1, int(total * 1.4 / 60))
                st.caption(
                    f"Crawl **{total}** SP có thể mất **~{est_min} phút** — không bị treo. "
                    f"Nên đặt **Giới hạn SP = 10–50** để test, hoặc chọn **Tất cả shop** để tải hết."
                )
            if not probe.get("categories"):
                st.caption(
                    "Web thuần không có collection shop — crawl **Tất cả** hoặc lọc danh mục nếu có pill."
                )
        elif not probe.get("categories") and probe.get("crawl_type") == "shop":
            st.caption("Shop không public danh mục — crawl **Tất cả shop**.")

    if info.get("ok") and probe.get("ok"):
        url_sig = cleaned
        if st.session_state.get("crawl_url_sig") != url_sig:
            st.session_state.crawl_url_sig = url_sig
            default_cat = probe.get("url_category_slug") or "Tất cả"
            if platform == "redbubble" and probe.get("categories"):
                first_slug = probe["categories"][0].get("slug")
                if first_slug:
                    default_cat = first_slug
            st.session_state["crawl_category_pill"] = default_cat
        render_crawl_category_pills(probe)

    if info.get("ok") and info.get("hint"):
        st.caption(info["hint"])


def render_download_button(label: str | None = None) -> None:
    if not (st.session_state.output_bytes and st.session_state.output_name):
        return
    name = st.session_state.output_name
    mime = (
        "text/csv"
        if name.endswith(".csv")
        else "application/json"
        if name.endswith(".json")
        else "application/octet-stream"
    )
    st.download_button(
        label=label or f"Tải {name}",
        data=st.session_state.output_bytes,
        file_name=name,
        mime=mime,
        type="primary",
        use_container_width=True,
        key=f"download_{name}",
    )


def render_edit_validation_alert(report: dict) -> bool:
    """Hiển thị cột thiếu dữ liệu — trả về True nếu đủ để xuất file."""
    if report["ok"]:
        st.success("✓ Đủ thông tin các cột — có thể xuất file ở Bước 7.")
        return True

    parts: list[str] = []
    for column, count in sorted(
        report["columns_missing"].items(),
        key=lambda item: EDIT_FIELD_LABELS.get(item[0], item[0]),
    ):
        label = EDIT_FIELD_LABELS.get(column, column)
        parts.append(
            f'<span class="val-missing">{label}</span>'
            f' <span class="val-missing-count">({count}/{report["total_rows"]} SP thiếu)</span>'
        )

    st.markdown(
        "**Cột còn thiếu dữ liệu — bổ sung ở bảng bên trên rồi mới xuất được:**",
        unsafe_allow_html=True,
    )
    st.markdown(" · ".join(parts), unsafe_allow_html=True)
    st.error(
        f"Còn **{report['rows_with_issues']}** sản phẩm thiếu thông tin. "
        "Cột báo 🔴 trong bảng = cần điền đủ."
    )
    return False


def render_export_actions(
    edit_count: int,
    total_raw: int,
    export_ready: bool = True,
) -> tuple[bool, Any, Any, Any]:
    """Xuất file, tiến trình và tải — Bước 7."""
    st.markdown("### Bước 7 · Xuất file")
    export_format = st.session_state.get("export_format", default_export_format())
    st.caption(
        f"Định dạng: **{FORMAT_OPTIONS.get(export_format, export_format)}** "
        + (
            "(chế độ GMC). "
            if is_gmc_workflow()
            else "(đổi ở Bước 2). "
        )
        + f"Xuất **{edit_count}** SP đang hiển thị ở Bước 6"
        + (f" (trong tổng **{total_raw}** SP gốc)" if edit_count != total_raw else "")
    )

    export_clicked = st.button(
        f"Xuất {FORMAT_OPTIONS[export_format]}",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.edited_df is None or not export_ready,
        help=f"Cần đủ {_required_fields_caption(export_format)} ở Bước 6.",
    )
    if not export_ready:
        st.caption("⛔ Chưa thể xuất — kiểm tra các cột 🔴 đỏ ở Bước 6.")

    worker_col, _ = st.columns([1, 2])
    with worker_col:
        st.number_input(
            "Luồng xử lý",
            min_value=1,
            max_value=32,
            value=int(st.session_state.get("export_workers", DEFAULT_EXPORT_WORKERS)),
            help="Số luồng song song khi xuất / tải ảnh backup. Tăng nếu máy mạnh.",
            key="export_workers",
        )

    progress_area = st.empty()
    status_area = st.empty()
    download_area = st.empty()

    with download_area:
        if st.session_state.output_bytes and st.session_state.output_name and not export_clicked:
            st.markdown(
                f'<div class="download-ready-banner">'
                f'✓ File sẵn sàng: <strong>{html.escape(st.session_state.output_name)}</strong>'
                f"</div>",
                unsafe_allow_html=True,
            )
            render_download_button()
            if st.session_state.imported_products_all:
                st.caption("Xuất category khác → chọn pill category phía trên.")

    return export_clicked, progress_area, status_area, download_area


def render_workflow_selector() -> str:
    with st.sidebar:
        st.markdown("##### Chế độ làm việc")
        st.caption("Chọn nguồn dữ liệu")
        return st.radio(
            "Nguồn dữ liệu",
            options=list(WORKFLOW_MODES.keys()),
            format_func=lambda key: WORKFLOW_MODES[key],
            key="workflow_mode",
            label_visibility="collapsed",
            on_change=_on_workflow_mode_change,
        )


def render_crawl_panel() -> tuple[str, int, int, str, str, str, bool]:
    """Bước 1 — full width, category pill xuống dòng khi nhiều."""
    gmc_mode = is_gmc_workflow()
    if gmc_mode:
        st.markdown("### Bước 1 · Crawl link → feed GMC")
        st.caption(
            "Chọn cách tách biến thể trước khi crawl. "
            "WooCommerce variable product: nhập **Woo key/secret** ở expander API để lấy đủ biến thể."
        )
        render_gmc_variant_mode_selector()
    else:
        st.markdown("### Bước 1 · Crawl link")
    url = st.text_input(
        "Link shop / category / sản phẩm",
        placeholder="https://www.redbubble.com/shop/shower-curtains",
        help=(
            "Redbubble: link /shop/từ-khóa hoặc /i/... (KHÔNG dùng trang chủ)\n"
            "Shopify/Woo: trang chủ shop, /collections/..., /products/...\n"
            "Web thuần: trang chủ hoặc /shop/..., /product/..."
        ),
        key="crawl_url",
        on_change=_on_crawl_url_change,
    )
    _refresh_crawl_messages(url)

    with st.expander("API (Shopify / Woo)", expanded=False):
        shopify_token = st.text_input("Shopify token", type="password", key="crawl_shopify_token")
        woo_key = st.text_input("WooCommerce key", type="password", key="crawl_woo_key")
        woo_secret = st.text_input("WooCommerce secret", type="password", key="crawl_woo_secret")
    shopify_token = st.session_state.get("crawl_shopify_token", "")
    woo_key = st.session_state.get("crawl_woo_key", "")
    woo_secret = st.session_state.get("crawl_woo_secret", "")

    render_url_preview(url, shopify_token, woo_key, woo_secret)

    if url.strip():
        url_info = describe_url(url.strip())
        if url_info.get("ok") and url_info.get("crawl_ready", True):
            st.caption(f"Link OK · **{url_info['message']}**")
        elif url_info.get("ok"):
            st.warning(
                url_info.get("notice")
                or url_info.get("hint")
                or "Link Redbubble chưa đúng — cần `/shop/từ-khóa` hoặc `/i/...`."
            )

    lim_col, page_col = st.columns(2)
    with lim_col:
        st.number_input(
            "Giới hạn SP",
            min_value=0,
            help="0 = lấy hết. Web thuần ~2000 SP có thể mất ~45 phút — nên thử 10–50 trước.",
            key="crawl_limit",
        )
    with page_col:
        st.number_input(
            "Số trang",
            min_value=0,
            help="0 = hết trang.",
            key="crawl_pages",
        )
    limit = int(st.session_state.get("crawl_limit", 0))
    pages = int(st.session_state.get("crawl_pages", 0))

    render_proxy_panel(
        expanded=False,
        title="Proxy & Redbubble",
        key_prefix="crawl_",
    )
    if st.button("Làm mới session HTTP", use_container_width=True, key="crawl_reset_session"):
        _clear_image_cache()
        st.session_state.last_info = "Đã reset session HTTP + cache ảnh."

    crawl_ready = True
    if url.strip():
        url_info = describe_url(url.strip())
        crawl_ready = bool(url_info.get("ok") and url_info.get("crawl_ready", True))

    crawl_clicked = st.button(
        "Crawl → GMC" if gmc_mode else "Crawl dữ liệu",
        type="primary",
        use_container_width=True,
        key="crawl_data_btn",
        disabled=not crawl_ready,
        help=(
            None
            if crawl_ready
            else "Dán link /shop/... hoặc /i/... — trang chủ Redbubble không crawl được."
        ),
    )
    return url, limit, pages, shopify_token, woo_key, woo_secret, crawl_clicked


def main() -> None:
    init_session()
    inject_pastel_pink_theme()

    workflow_mode = render_workflow_selector()
    gmc_mode = is_gmc_workflow(workflow_mode)
    rb_mode = is_redbubble_workflow(workflow_mode)
    _ensure_export_format_for_workflow(workflow_mode)

    render_app_hero(gmc_mode=gmc_mode, redbubble_mode=rb_mode)

    url = ""
    limit = 0
    pages = 0
    shopify_token = ""
    woo_key = ""
    woo_secret = ""
    crawl_clicked = False

    if workflow_mode == "redbubble":
        with st.container(border=True):
            url, limit, pages, crawl_clicked = render_redbubble_crawl_panel(
                render_url_preview=render_url_preview,
                refresh_crawl_messages=_refresh_crawl_messages,
                on_crawl_url_change=_on_crawl_url_change,
                clear_image_cache=_clear_image_cache,
            )
    elif workflow_mode in ("crawl", "crawl-gmc"):
        with st.container(border=True):
            url, limit, pages, shopify_token, woo_key, woo_secret, crawl_clicked = (
                render_crawl_panel()
            )
    else:
        with st.container(border=True):
            render_import_panel()

    with st.container(border=True):
        if gmc_mode:
            export_format = render_gmc_step2_panel()
            st.session_state.export_format = export_format
        elif rb_mode:
            export_format = render_redbubble_export_selector()
        else:
            export_format = render_export_format_selector()

    _render_main_workflow(
        workflow_mode,
        url,
        limit,
        pages,
        shopify_token,
        woo_key,
        woo_secret,
        crawl_clicked,
    )


def _render_main_workflow(
    workflow_mode: str,
    url: str,
    limit: int,
    pages: int,
    shopify_token: str,
    woo_key: str,
    woo_secret: str,
    crawl_clicked: bool,
) -> None:
    _refresh_crawl_messages(str(st.session_state.get("crawl_url") or url or ""))

    if workflow_mode in ("crawl", "crawl-gmc", "redbubble") and crawl_clicked:
        run_crawl_only(
            url,
            limit,
            pages,
            shopify_token,
            woo_key,
            woo_secret,
            category_slug=st.session_state.get("crawl_category_pill", "Tất cả"),
        )

    if st.session_state.last_info:
        st.success(st.session_state.last_info)
    crawl_error = _current_crawl_error()
    if crawl_error:
        st.error(crawl_error)

    _render_data_status_banner()

    export_format = st.session_state.get("export_format", default_export_format(workflow_mode))

    with st.container(border=True):
        rules = render_customization_panel()

    with st.container(border=True):
        export_fields = render_export_fields_panel()

    if st.session_state.products_raw is not None:
        working_products = get_working_products()
        render_preview_section(
            working_products,
            export_format,
            export_fields,
            build_export_rules(),
            image_mode=st.session_state.get("image_export_mode", "comma"),
            max_images_per_product=int(st.session_state.get("max_images_per_product", 0)),
        )

    export_clicked = False
    if st.session_state.edited_df is not None:
        if "_index" in st.session_state.edited_df.columns:
            st.session_state.edited_df = st.session_state.edited_df.rename(
                columns={"_index": "row_id"}
            )
        count = len(st.session_state.edited_df)
        total_raw = len(st.session_state.products_raw or [])
        edit_qty = int(st.session_state.get("edit_quantity", 0))
        edit_order = st.session_state.get("edit_order", "original")

        st.markdown(f"### Bước 6 · Chỉnh sửa chi tiết ({count} sản phẩm)")
        export_format = st.session_state.get("export_format", default_export_format())
        format_label = FORMAT_OPTIONS.get(export_format, export_format)
        is_gmc = export_format == "gmc-feed-csv"
        required_text = _required_fields_caption(export_format)
        if is_gmc:
            st.caption(
                "Chọn số lượng và thứ tự → **Áp dụng lọc**. "
                f"Bắt buộc: **{required_text}**. "
                "**Google product category** — dropdown + gợi ý tự động theo title/tags "
                "([productcategory.net](https://productcategory.net/)). "
                "**Gender · age group** — dropdown (age group mặc định **adult**). "
                "Tab **File feed xuất** = brand, size, color, gender, age group."
            )
        else:
            st.caption(
                "Chọn số lượng và thứ tự trước, bấm **Áp dụng lọc** — chỉ các SP trong bảng mới cần chỉnh sửa. "
                f"Bắt buộc trước khi xuất **{format_label}**: **{required_text}**. "
                "Type · Vendor · SKU tùy chọn — không báo đỏ. "
                "Tab **File feed xuất** = đúng cột file sẽ tải về (giống Bước 5)."
            )

        filt_qty, filt_order, filt_btn = st.columns([2, 2, 1])
        with filt_qty:
            st.number_input(
                "Số SP chỉnh sửa (0 = tất cả)",
                min_value=0,
                max_value=max(total_raw, 1),
                help="Ví dụ 20 = chỉ lấy 20 SP vào bảng để sửa chi tiết.",
                key="edit_quantity",
            )
        with filt_order:
            st.selectbox(
                "Thứ tự dữ liệu",
                options=EXPORT_ORDER_OPTIONS,
                format_func=lambda key: EXPORT_ORDER_LABELS[key],
                key="edit_order",
            )
        with filt_btn:
            st.write("")
            if st.button("Áp dụng lọc", type="secondary", use_container_width=True):
                apply_edit_filter()
                st.rerun()

        st.caption(describe_edit_selection(total_raw, edit_qty, edit_order, count))

        header_report = validate_edited_df(st.session_state.edited_df, export_format)
        hr = header_report

        tab_main, tab_desc, tab_feed = st.tabs(
            ["Thông tin chính", "Description (HTML)", f"File feed xuất ({FORMAT_OPTIONS.get(export_format, export_format)})"]
        )

        main_columns = [c for c in st.session_state.edited_df.columns if c != "description"]
        with tab_main:
            if is_gmc:
                gmc_config = _gmc_data_editor_column_config(hr)
            else:
                gmc_config = {
                    "row_id": None,
                    "title": st.column_config.TextColumn(
                        column_header_label("title", hr), width="large"
                    ),
                    "price": st.column_config.NumberColumn(
                        column_header_label("price", hr),
                        min_value=0.0,
                        format="%.2f",
                    ),
                    "categories": st.column_config.TextColumn(
                        column_header_label("categories", hr)
                    ),
                    "tags": st.column_config.TextColumn(column_header_label("tags", hr)),
                    "product_type": st.column_config.TextColumn(
                        column_header_label("product_type", hr)
                    ),
                    "vendor": st.column_config.TextColumn(column_header_label("vendor", hr)),
                    "sku": st.column_config.TextColumn(column_header_label("sku", hr)),
                }
            edited_main = st.data_editor(
                st.session_state.edited_df[main_columns],
                column_config=gmc_config,
                disabled=["row_id"],
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                key="edit_main_table",
                height=min(560, 100 + count * 36),
            )

        with tab_desc:
            st.caption("Sửa HTML mô tả từng sản phẩm — cuộn ngang nếu nội dung dài.")
            edited_desc = st.data_editor(
                st.session_state.edited_df[["row_id", "title", "description"]],
                column_config={
                    "row_id": None,
                    "title": st.column_config.TextColumn("Title", width="medium", disabled=True),
                    "description": st.column_config.TextColumn(
                        column_header_label("description", hr),
                        width="large",
                        help="Sửa trực tiếp HTML: <p>, <strong>, <img>…",
                    ),
                },
                disabled=["row_id", "title"],
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                key="edit_desc_table",
                height=min(900, 160 + count * 52),
            )

        edited_df = edited_main.merge(
            edited_desc[["row_id", "description"]],
            on="row_id",
            how="left",
        )
        column_order = [c for c in edit_columns_for_format(export_format) if c in edited_df.columns]
        edited_df = edited_df[column_order]
        st.session_state.edited_df = edited_df

        with tab_feed:
            export_products = products_from_edit_table(
                st.session_state.products_raw,
                edited_df.to_dict("records"),
                export_format=export_format,
            )
            if is_gmc:
                row_ids = [int(v) for v in edited_df["row_id"].tolist()]
                gmc_kw = _gmc_preview_kwargs()
                feed_rows = build_gmc_feed_edit_rows(
                    export_products,
                    row_ids,
                    variant_mode=gmc_kw["gmc_variant_mode"],
                    currency=gmc_kw["gmc_currency"],
                    condition=gmc_kw["gmc_condition"],
                    google_category_default=gmc_kw["gmc_google_category"],
                    gmc_defaults=gmc_kw["gmc_defaults"],
                )
                st.markdown("**Chỉnh feed GMC** — brand, size, color, gender, age group")
                st.caption(
                    f"Age group mặc định **{GMC_AGE_GROUP_DEFAULT}** nếu để trống · "
                    "Google category gợi ý tự động ở tab **Thông tin chính** "
                    "(tra thêm tại [productcategory.net](https://productcategory.net/))."
                )
                edited_feed = st.data_editor(
                    pd.DataFrame(feed_rows),
                    column_config=_gmc_feed_data_editor_column_config(),
                    disabled=["feed_key", "id", "title"],
                    hide_index=True,
                    use_container_width=True,
                    num_rows="fixed",
                    key="gmc_feed_edit_table",
                    height=min(420, 80 + len(feed_rows) * 36),
                )
                st.session_state.gmc_feed_edits = edited_feed.to_dict("records")
                merge_gmc_feed_edits(
                    st.session_state.products_raw,
                    st.session_state.gmc_feed_edits,
                )
                export_products = products_from_edit_table(
                    st.session_state.products_raw,
                    edited_df.to_dict("records"),
                    export_format=export_format,
                )
            feed_count = (
                count_gmc_export_rows(export_products, gmc_kw["gmc_variant_mode"])
                if is_gmc
                else len(export_products)
            )
            render_export_file_preview_block(
                export_products,
                export_format,
                export_fields,
                build_export_rules(),
                feed_count,
                limit=min(10, feed_count),
                image_mode=st.session_state.get("image_export_mode", "comma"),
                max_images_per_product=int(st.session_state.get("max_images_per_product", 0)),
            )

        validation_report = validate_edited_df(edited_df, export_format)
        export_ready = render_edit_validation_alert(validation_report)

        with st.container(border=True):
            export_clicked, progress_area, status_area, download_area = (
                render_export_actions(count, total_raw, export_ready=export_ready)
            )

        if export_clicked and not export_ready:
            st.session_state.last_error = (
                "Không xuất file — còn cột thiếu dữ liệu. Xem cảnh báo đỏ ở Bước 6."
            )
            export_clicked = False

        if export_clicked:
            export_format = st.session_state.get("export_format", "woocommerce-csv")
            max_workers = int(st.session_state.get("export_workers", DEFAULT_EXPORT_WORKERS))
            with progress_area.container():
                progress_bar = st.progress(0.0, text="Đang chuẩn bị...")
            with status_area:
                status_caption = st.empty()
            export_current_products(
                export_format,
                rules,
                edited_df,
                export_fields,
                progress_bar,
                status_caption,
                max_workers=max_workers,
            )
            with progress_area.container():
                st.progress(1.0, text="Hoàn tất xuất file")
            with status_area:
                st.caption(st.session_state.last_info or "Xuất file xong.")
            with download_area.container():
                st.markdown(
                    f'<div class="download-ready-banner">'
                    f'✓ File sẵn sàng: <strong>{html.escape(st.session_state.output_name or "")}</strong>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
                render_download_button()


if __name__ == "__main__":
    main()
