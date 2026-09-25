#!/usr/bin/env python3
"""Web UI for product crawler - chay: streamlit run app.py"""
from __future__ import annotations

import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import html
import os
import re

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

from app_brand import APP_NAME, APP_PAGE_ICON, APP_TAGLINE, APP_VERSION
from exporters.export import export_download_name, export_products
from exporters.gmc_feed_import import GMC_FEED_COLUMNS, GMC_VARIANT_MODES, count_gmc_export_rows, is_supplier_vendor
from exporters.shopify_import import count_shopify_export_rows
from exporters.image_urls import IMAGE_EXPORT_MODES
from models.export_fields import PRESET_LABELS, EXPORT_FIELD_LABELS, ExportFields
from models.export_rules import ExportRules, apply_export_rules, apply_gmc_title_rules, apply_rules_to_products
from models.product import Product
from utils.credentials import CrawlCredentials
from utils.crawl_preflight import check_shop_reachable
from utils.crawl_report import save_crawl_report
from utils.export_paths import cleanup_export_artifacts, temp_export_path
from utils.crawl_runner import CrawlError, run_auto_crawl
from utils.csv_import import (
    filter_products_by_category,
    list_categories,
    parse_csv_to_products,
    slug_from_category,
)
from utils.title_filter import dataframe_to_csv_bytes, filter_products_by_titles
from utils.detector import UnsupportedUrlError, normalize_url, resolve_url
from utils.export_preview import (
    all_export_columns,
    build_export_file_preview,
    image_batch_summary,
)
from utils.image_fetch import fetch_image_bytes
from utils.http_session import mask_proxy_url, parse_proxy_list, test_proxy_connection
from utils.edit_validation import (
    EDIT_FIELD_LABELS,
    column_header_label,
    first_issue_row,
    required_columns_for_format,
    validate_edited_df,
)
from utils.gmc_feed_editor import (
    apply_bulk_to_gmc_feed_edits,
    build_gmc_feed_edit_rows,
    merge_gmc_feed_edits,
)
from utils.gmc_taxonomy import (
    GMC_AGE_GROUP_DEFAULT,
    GMC_AGE_GROUP_OPTIONS,
    GMC_DEFAULT_GENDER,
    GMC_DEFAULT_GOOGLE_CATEGORY_ID,
    GMC_GENDER_OPTIONS,
    category_id_from_option,
    category_options_hint,
)
from utils.export_selection import (
    EXPORT_ORDER_LABELS,
    EXPORT_ORDER_OPTIONS,
    describe_edit_selection,
)
from utils.feed_history import (
    delete_all_feed_history,
    delete_feed_history_entry,
    format_created_at,
    format_file_size,
    list_feed_history,
    register_feed_export,
    total_feed_history_bytes,
)
from utils.output_cleanup import (
    cleanup_legacy_output,
    legacy_output_summary,
    scan_legacy_output,
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
from utils.shop_probe import build_category_crawl_url, probe_shop_stats, shop_brand_from_url
from utils.ui_theme import inject_pastel_pink_theme, render_app_hero
from ui.crawl_step1_layout import (
    close_crawl_step1_wrap,
    open_crawl_step1_wrap,
    render_crawl_action_centered,
    render_crawl_description_format,
    render_crawl_include_product_url,
    render_crawl_limits_row,
)
from ui.dedupe_filter_panel import render_dedupe_filter_panel
from ui.proxy_dedupe_panel import render_proxy_dedupe_panel
from ui.proxy_panel import render_proxy_panel
from ui.redbubble_workflow import render_redbubble_crawl_panel, render_redbubble_export_selector
from ui.shopify_workflow import (
    SHOPIFY_EXPORT_FORMATS,
    render_shopify_crawl_panel,
    render_shopify_export_selector,
)
from ui.trendsi_workflow import (
    TRENDSI_EXPORT_FORMATS,
    render_trendsi_crawl_panel,
    render_trendsi_export_selector,
)
from utils.url_preview import describe_url
from utils.variant_expander import (
    build_variant_config,
    count_variants_per_product,
    expand_products_with_variants,
    preview_variant_lines,
)


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

GMC_CONDITION_OPTIONS = ["", "new", "refurbished", "used"]

GENERAL_EXPORT_FORMATS = ["woocommerce-csv", "shopify-csv", "web-feed-csv"]
SHOP_EXPORT_FORMATS = GENERAL_EXPORT_FORMATS + ["gmc-feed-csv"]
DEFAULT_EXPORT_WORKERS = min(32, max(16, (os.cpu_count() or 4) * 2))
DEFAULT_CRAWL_WORKERS = 500

WORKFLOW_MODES = {
    "crawl": "Crawl từ link (Shopify / Woo / ShopBase / FAA / web)",
    "sitemap": "Crawl theo sitemap",
    "import": "Import CSV có sẵn → xuất file",
    "filter-title": "Lọc SP theo file title",
    "filter-dedupe": "Lọc trùng file feed (title/giá/cate…)",
    "crawl-gmc": "Crawl → file feed GMC",
    "redbubble": "Crawl Redbubble → xuất file",
    "shopify": "Crawl Shopify → xuất file",
    "trendsi": "Crawl Trendsi → xuất file",
    "filter-proxy": "Lọc trùng proxy",
}

RULE_DEFAULTS = {
    "price_multiplier": 1.0,
    "price_add": 0.0,
    "round_to_99": False,
    "title_remove": "",
    "title_remove_ignore_case": True,
    "title_find": "",
    "title_replace": "",
    "title_replace_ignore_case": True,
    "title_prefix": "",
    "title_suffix": "",
    "gmc_title_prepend_brand": False,
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
        "export_format": None,
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
        "title_filter_result_bytes": None,
        "title_filter_result_name": "",
        "title_filter_stats": None,
        "dedupe_result": None,
        "proxy_dedupe_result": None,
        "variant_option_names": "Size, Color",
        "variant_attribute_lines": (
            "S,M=1,L=2,XL=3,2XL=4,3XL=5,4XL=6,5XL=7\n"
            "Black"
        ),
        "variant_base_price": 0.0,
        "crawl_limit": 0,
        "crawl_pages": 0,
        "crawl_workers": DEFAULT_CRAWL_WORKERS,
        "crawl_description_format": "html",
        "crawl_include_product_url": True,
        "crawl_via_sitemap": False,
        "crawl_report_json": None,
        "crawl_report_csv": None,
        "crawl_report_json_bytes": None,
        "crawl_report_csv_bytes": None,
        "crawl_report_json_name": None,
        "crawl_report_csv_name": None,
        "crawl_running": False,
        "crawl_matched_count": 0,
        "crawl_expected_total": None,
        "crawl_category_pill": "Tất cả",
        "gmc_currency": "USD",
        "gmc_condition": "new",
        "gmc_google_category": GMC_DEFAULT_GOOGLE_CATEGORY_ID,
        "gmc_default_gender": GMC_DEFAULT_GENDER,
        "gmc_default_age_group": GMC_AGE_GROUP_DEFAULT,
        "gmc_gtin_default": "",
        "gmc_variant_mode": "all_variations",
        "gmc_bulk_brand": "",
        "gmc_bulk_size": "",
        "gmc_bulk_color": "",
        "gmc_bulk_condition": "",
        "gmc_bulk_google_category": "",
        "gmc_bulk_gender": "",
        "gmc_bulk_age_group": "",
        "shop_brand_default": "",
        "gmc_bulk_only_empty": False,
        "edit_quantity": 0,
        "edit_order": "original",
        "edit_skip_validation": False,
        "export_workers": DEFAULT_EXPORT_WORKERS,
        "pending_rule_reset": False,
        "bulk_customize_section": "price",
        **RULE_DEFAULTS,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    # Mặc định 500 luồng; giữ giá trị user đã chỉnh trong session.
    if "crawl_workers" not in st.session_state or st.session_state.get(
        "_crawl_workers_default_v500"
    ) is not True:
        # Nâng mặc định cũ (16–32) lên 500 một lần khi mở app bản mới.
        current = int(st.session_state.get("crawl_workers", DEFAULT_CRAWL_WORKERS) or 0)
        if current <= 32:
            st.session_state.crawl_workers = DEFAULT_CRAWL_WORKERS
        st.session_state._crawl_workers_default_v500 = True
    st.session_state.crawl_workers = min(
        500,
        max(1, int(st.session_state.get("crawl_workers", DEFAULT_CRAWL_WORKERS))),
    )

    if not st.session_state.get("_export_pick_required_v1"):
        st.session_state.export_format = None
        st.session_state._export_pick_required_v1 = True

    if st.session_state.get("image_export_mode") == "pipe":
        st.session_state.image_export_mode = "comma"

    if not st.session_state.get("_defaults_v2"):
        st.session_state.description_convert_h_to_p = True
        st.session_state._defaults_v2 = True

    if not st.session_state.get("_gmc_feed_defaults_v1"):
        if not str(st.session_state.get("gmc_google_category") or "").strip():
            st.session_state.gmc_google_category = GMC_DEFAULT_GOOGLE_CATEGORY_ID
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


def is_redbubble_workflow(mode: str | None = None) -> bool:
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    return active == "redbubble"


def is_trendsi_workflow(mode: str | None = None) -> bool:
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    return active == "trendsi"


def is_shopify_workflow(mode: str | None = None) -> bool:
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    return active == "shopify"


def is_gmc_workflow(mode: str | None = None) -> bool:
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    return active == "crawl-gmc"


_GMC_INTERNAL_TO_DISPLAY: dict[int, int] = {
    1: 1,
    4: 2,
    5: 3,
    6: 4,
    7: 5,
}


def gmc_display_step(internal_step: int) -> int:
    """GMC bỏ Bước 2 (chọn format) và Bước 3 (tùy chỉnh) — hiển thị liên tục 1→5."""
    return _GMC_INTERNAL_TO_DISPLAY.get(internal_step, internal_step)


def gmc_workflow_step(step: int) -> int:
    return gmc_display_step(step)


def workflow_step_heading(step: int, title: str, *, gmc: bool | None = None) -> str:
    if gmc is None:
        gmc = is_gmc_workflow()
    number = gmc_display_step(step) if gmc else step
    return f"### Bước {number} · {title}"


def edit_step_number(*, gmc: bool | None = None) -> int:
    if gmc is None:
        gmc = is_gmc_workflow()
    return gmc_display_step(6) if gmc else 6


def export_step_number(*, gmc: bool | None = None) -> int:
    if gmc is None:
        gmc = is_gmc_workflow()
    return gmc_display_step(7) if gmc else 7


def default_export_format(mode: str | None = None) -> str | None:
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    if is_gmc_workflow(active):
        return "gmc-feed-csv"
    return st.session_state.get("export_format")


def _ensure_export_format_for_workflow(mode: str | None = None) -> None:
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    if is_gmc_workflow(active):
        st.session_state.export_format = "gmc-feed-csv"
        return
    current = st.session_state.get("export_format")
    if is_trendsi_workflow(active):
        allowed = TRENDSI_EXPORT_FORMATS
    elif is_shopify_workflow(active):
        allowed = SHOPIFY_EXPORT_FORMATS
        if not current or current not in allowed:
            st.session_state.export_format = "shopify-csv"
    elif is_redbubble_workflow(active):
        allowed = SHOP_EXPORT_FORMATS
    else:
        allowed = GENERAL_EXPORT_FORMATS
    if current and current not in allowed:
        st.session_state.export_format = None


def _clear_image_cache() -> None:
    st.session_state.image_cache_nonce += 1
    _cached_product_image.clear()


def _on_workflow_mode_change() -> None:
    _ensure_export_format_for_workflow()
    mode = st.session_state.get("workflow_mode", "crawl")
    # Sitemap là chế độ riêng — không còn checkbox trong Crawl từ link.
    st.session_state.crawl_via_sitemap = mode == "sitemap"
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
        "Không kết nối được",
        "kiểm tra lại link hoặc mạng/proxy",
    )
    return any(marker in text for marker in markers)


def _clear_url_probe_cache() -> None:
    _cached_describe_url.clear()
    _cached_probe_shop.clear()
    _cached_generic_product_count.clear()


def _refresh_crawl_messages(url: str) -> None:
    """Xóa thông báo lỗi/info cũ khi link hiện tại đã hợp lệ."""
    cleaned = url.strip()
    if st.session_state.get("crawl_url_sig") != cleaned:
        if cleaned and st.session_state.get("crawl_url_sig"):
            _clear_url_probe_cache()
        st.session_state.crawl_url_sig = cleaned
        st.session_state.crawl_expected_total = None
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
def _cached_describe_url(url: str, proxy_url: str = "") -> dict:
    return describe_url(url, proxy_url=proxy_url or None)


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
    probe_workers: int,
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
    workers = max(8, min(int(probe_workers or 16), 32))
    return probe_shop_stats(url, creds, max_workers=workers).to_dict()


def _url_ready_for_probe(url: str) -> bool:
    cleaned = url.strip()
    if len(cleaned) < 4:
        return False
    if cleaned.startswith("http") or "." in cleaned:
        return True
    return len(cleaned) >= 10


def _resolve_shop_brand_default(url: str | None = None) -> str:
    brand = str(st.session_state.get("shop_brand_default") or "").strip()
    if brand:
        return brand
    return shop_brand_from_url(url or str(st.session_state.get("crawl_url") or ""))


def _apply_shop_brand_defaults(
    products: list[Product],
    url: str | None = None,
) -> str:
    """Gán brand GMC = tên shop (từ domain), ghi đè vendor nhà cung cấp (vd. Trendsi)."""
    brand = shop_brand_from_url(url or str(st.session_state.get("crawl_url") or ""))
    if brand:
        st.session_state.shop_brand_default = brand
    if not brand:
        return ""
    if not (is_gmc_workflow() or _current_export_format() == "gmc-feed-csv"):
        return brand
    for product in products:
        vendor = str(product.vendor or "").strip()
        if not vendor or is_supplier_vendor(vendor):
            product.vendor = brand
    return brand


def _refresh_gmc_shop_brand_in_products() -> bool:
    """Thay Trendsi / vendor trống bằng tên shop — không cần crawl lại."""
    products = st.session_state.products_raw
    if not products:
        return False
    brand = _resolve_shop_brand_default()
    if not brand:
        return False
    changed = False
    for product in products:
        vendor = str(product.vendor or "").strip()
        if not vendor or is_supplier_vendor(vendor):
            if product.vendor != brand:
                product.vendor = brand
                changed = True
    if changed:
        st.session_state.shop_brand_default = brand
        apply_edit_filter(skip_merge=True)
    return changed


def load_products_into_session(products: list[Product], export_name: str, info: str) -> None:
    if _current_export_format() == "gmc-feed-csv" or is_gmc_workflow():
        default_condition = st.session_state.get("gmc_condition", "new")
        for product in products:
            if not str(product.gmc_condition or "").strip():
                product.gmc_condition = default_condition
        _apply_shop_brand_defaults(products)
    st.session_state.products_raw = deepcopy(products)
    # SP đã có URL nguồn → bật cột link (tránh tưởng là không crawl được link).
    if any(str(product.url or "").strip() for product in products):
        st.session_state.crawl_include_product_url = True
    apply_edit_filter(products)
    st.session_state.export_name = export_name
    st.session_state.output_bytes = None
    st.session_state.output_name = None
    st.session_state.last_error = None
    st.session_state.last_info = info
    st.session_state.pending_rule_reset = False
    st.session_state.edit_skip_validation = False


def _current_export_format() -> str | None:
    fmt = st.session_state.get("export_format")
    if fmt in SHOP_EXPORT_FORMATS:
        return fmt
    return None


def _resolved_export_format(mode: str | None = None) -> str:
    """Format xuất thực tế — fallback web-feed khi chưa chọn Bước 2."""
    fmt = _current_export_format()
    if fmt:
        return fmt
    active = mode if mode is not None else st.session_state.get("workflow_mode", "crawl")
    if is_gmc_workflow(active):
        return "gmc-feed-csv"
    return "web-feed-csv"


def export_validation_allows_export(validation_ok: bool) -> bool:
    return validation_ok or bool(st.session_state.get("edit_skip_validation"))


def apply_edit_filter(
    products: list[Product] | None = None,
    *,
    skip_merge: bool = False,
) -> None:
    """Lọc số lượng/thứ tự vào bảng Bước 6; gộp chỉnh sửa hiện tại vào products_raw trước."""
    if st.session_state.products_raw is None:
        return

    export_format = _current_export_format() or "web-feed-csv"
    if not skip_merge and st.session_state.edited_df is not None:
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
    gmc_condition = "new"
    if export_format == "gmc-feed-csv":
        gmc_kw = _gmc_preview_kwargs()
        gmc_google_default = category_id_from_option(gmc_kw["gmc_google_category"])
        gmc_defaults = gmc_kw["gmc_defaults"]
        gmc_condition = gmc_kw["gmc_condition"]
    rows = build_edit_subset_rows(
        source,
        quantity=quantity,
        order=order,
        export_format=export_format,
        google_category_default=gmc_google_default,
        gmc_defaults=gmc_defaults,
        gmc_condition=gmc_condition,
        include_url=bool(st.session_state.get("crawl_include_product_url")),
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
        apply_import_category(picked        )


def _parse_variant_base_price_input() -> float | None:
    raw = st.session_state.get("variant_base_price")
    if raw in (None, "", 0, 0.0):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _variant_preview_sample_price() -> float:
    override = _parse_variant_base_price_input()
    if override is not None:
        return override
    products = st.session_state.products_raw or []
    for product in products:
        if product.price is not None and float(product.price) > 0:
            return float(product.price)
    return 36.99


def _sync_imported_products_with_expanded(expanded: list[Product]) -> None:
    """Cập nhật imported_products_all khi áp biến thể — tránh mất khi đổi category."""
    all_products = st.session_state.imported_products_all
    if not all_products:
        return
    by_id = {str(product.id): product for product in expanded}
    st.session_state.imported_products_all = [
        deepcopy(by_id[str(product.id)]) if str(product.id) in by_id else product
        for product in all_products
    ]


def apply_variant_expansion_from_session() -> None:
    if st.session_state.products_raw is None:
        st.session_state.last_error = "Chưa có sản phẩm — import CSV hoặc crawl trước."
        return
    try:
        config = build_variant_config(
            str(st.session_state.get("variant_option_names") or ""),
            str(st.session_state.get("variant_attribute_lines") or ""),
            base_price_override=_parse_variant_base_price_input(),
        )
    except ValueError as exc:
        st.session_state.last_error = str(exc)
        return

    source = get_working_products()
    expanded = expand_products_with_variants(source, config)
    per_product = count_variants_per_product(config)
    st.session_state.products_raw = deepcopy(expanded)
    _sync_imported_products_with_expanded(expanded)
    apply_edit_filter(expanded, skip_merge=True)
    st.session_state.output_bytes = None
    st.session_state.output_name = None
    st.session_state.last_error = None
    st.session_state.last_info = (
        f"Đã sinh biến thể **{per_product}** biến thể/SP cho **{len(expanded)}** sản phẩm "
        f"(tổng **{len(expanded) * per_product:,}** dòng feed khi xuất GMC/Woo).".replace(
            ",", "."
        )
    )


def render_variant_expansion_panel() -> None:
    if st.session_state.products_raw is None:
        return

    with st.expander("Biến thể · Size / Color… (SP đơn → có biến thể)", expanded=False):
        st.caption(
            "Sinh biến thể cho feed **WooCommerce** (variable + variation) hoặc **GMC** "
            "(All Variations). **Giá sản phẩm** để trống = lấy giá từ sheet/CSV."
        )
        opt_col, price_col = st.columns([2, 1])
        with opt_col:
            st.text_input(
                "Danh sách Option",
                placeholder="Size, Color",
                help="Tên các thuộc tính, cách nhau dấu phẩy.",
                key="variant_option_names",
            )
        with price_col:
            st.number_input(
                "Giá sản phẩm (tuỳ chọn)",
                min_value=0.0,
                step=0.01,
                format="%.2f",
                help="Để **0** = dùng giá từng dòng trong sheet.",
                key="variant_base_price",
            )
        st.text_area(
            "Danh sách Attribute mỗi Option",
            help=(
                "Mỗi dòng = 1 option. Giá cộng thêm: M=1 nghĩa là +$1 so với giá gốc. "
                "Ví dụ dòng 1: S,M=1,L=2 · dòng 2: Black"
            ),
            height=110,
            key="variant_attribute_lines",
        )

        try:
            preview_config = build_variant_config(
                str(st.session_state.get("variant_option_names") or ""),
                str(st.session_state.get("variant_attribute_lines") or ""),
                base_price_override=_parse_variant_base_price_input(),
            )
            preview_lines = preview_variant_lines(
                preview_config,
                base_price=_variant_preview_sample_price(),
                limit=8,
            )
            per = count_variants_per_product(preview_config)
            st.caption(f"Mỗi SP → **{per}** biến thể · xem trước (giá mẫu):")
            st.code("\n".join(preview_lines), language=None)
        except ValueError as exc:
            st.warning(str(exc))

        if st.button(
            "Áp dụng biến thể",
            type="primary",
            use_container_width=True,
            key="variant_expansion_apply",
        ):
            apply_variant_expansion_from_session()
            st.rerun()


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


def render_title_filter_panel() -> None:
    st.markdown("### Bước 1 · Lọc sản phẩm theo file title")
    st.caption(
        "Upload **file title** (danh sách tên SP cần giữ) + **file sản phẩm**. "
        "App tạo file mới chỉ gồm SP có title nằm trong file title. "
        "Giới hạn upload **3 GB**/file (cần restart app sau khi đổi config)."
    )

    col_title, col_products = st.columns(2)
    with col_title:
        title_file = st.file_uploader(
            "1. File title",
            type=["csv", "txt", "tsv", "xlsx"],
            key="title_filter_titles_uploader",
            help="CSV có cột title/name, hoặc TXT mỗi dòng 1 title.",
        )
    with col_products:
        product_file = st.file_uploader(
            "2. File sản phẩm",
            type=["csv", "tsv", "xlsx"],
            key="title_filter_products_uploader",
            help="CSV sản phẩm đầy đủ (title, price, images…).",
        )

    match_mode = st.radio(
        "Kiểu khớp title",
        options=["exact", "contains"],
        format_func=lambda key: (
            "Khớp đúng (không phân biệt hoa thường)"
            if key == "exact"
            else "Title SP chứa title trong file (hoặc ngược lại)"
        ),
        horizontal=True,
        key="title_filter_match_mode",
    )

    run_clicked = st.button(
        "Lọc & tạo file",
        type="primary",
        use_container_width=True,
        key="title_filter_run_btn",
        disabled=not (title_file and product_file),
    )

    if run_clicked and title_file and product_file:
        try:
            with st.spinner("Đang lọc sản phẩm theo title..."):
                result = filter_products_by_titles(
                    product_file.getvalue(),
                    title_file.getvalue(),
                    product_filename=product_file.name,
                    title_filename=title_file.name,
                    match_mode=match_mode,
                )
            out_bytes = dataframe_to_csv_bytes(result.matched_df)
            stem = Path(product_file.name).stem
            out_name = f"{stem}_filtered_by_title.csv"
            st.session_state.title_filter_result_bytes = out_bytes
            st.session_state.title_filter_result_name = out_name
            st.session_state.title_filter_stats = {
                "title_count": result.title_count,
                "product_count": result.product_count,
                "matched_count": result.matched_count,
                "missing_count": len(result.missing_titles),
                "missing_titles": result.missing_titles[:50],
                "product_title_column": result.product_title_column,
                "title_column": result.title_column,
            }
            st.session_state.last_error = None
            st.session_state.last_info = (
                f"Lọc xong: **{result.matched_count}** / **{result.product_count}** SP "
                f"khớp **{result.title_count}** title "
                f"(cột SP `{result.product_title_column}`, cột title `{result.title_column}`)."
            )

            # Đưa SP khớp vào session để xuất Woo/Shopify nếu cần.
            if result.matched_count > 0:
                products, _ = parse_csv_to_products(out_bytes)
                if products:
                    st.session_state.imported_products_all = deepcopy(products)
                    st.session_state.import_source_name = Path(out_name).stem
                    st.session_state.import_selected_category = "Tất cả"
                    load_products_into_session(
                        products,
                        Path(out_name).stem,
                        st.session_state.last_info,
                    )
        except Exception as exc:
            st.session_state.last_error = f"Lỗi lọc title: {exc}"
            st.session_state.title_filter_result_bytes = None
            st.session_state.title_filter_stats = None

    stats = st.session_state.get("title_filter_stats")
    out_bytes = st.session_state.get("title_filter_result_bytes")
    out_name = st.session_state.get("title_filter_result_name") or "filtered_by_title.csv"
    if stats:
        st.success(
            f"**{stats['matched_count']}** SP khớp · "
            f"file title **{stats['title_count']}** · "
            f"file SP **{stats['product_count']}** · "
            f"thiếu **{stats['missing_count']}** title"
        )
        if out_bytes:
            st.download_button(
                f"Tải file đã lọc ({out_name})",
                data=out_bytes,
                file_name=out_name,
                mime="text/csv",
                type="primary",
                use_container_width=True,
                key="title_filter_download_btn",
            )
        missing = stats.get("missing_titles") or []
        if missing:
            with st.expander(f"Title trong file title nhưng không thấy trong file SP ({stats['missing_count']})"):
                st.code("\n".join(missing), language=None)
                if stats["missing_count"] > len(missing):
                    st.caption(f"… và {stats['missing_count'] - len(missing)} title khác.")
    elif not title_file or not product_file:
        st.caption(
            "Ví dụ: file title có cột `title` (hoặc mỗi dòng 1 tên) · "
            "file sản phẩm CSV crawl/export trước đó."
        )


def _preview_image_bytes(image_url: str, proxy_url: str) -> bytes | None:
    lower = image_url.lower()
    if "redbubble.net" in lower:
        return _cached_product_image(
            image_url,
            proxy_url,
            st.session_state.image_cache_nonce,
        )
    referer = None
    if "silverbene.com" in lower:
        referer = "https://silverbene.com/"
    elif "jomashop.com" in lower:
        referer = "https://www.jomashop.com/"
    return fetch_image_bytes(image_url, proxy_url or None, referer=referer)


def _needs_image_proxy_fetch(image_url: str) -> bool:
    lower = (image_url or "").lower()
    return any(
        host in lower
        for host in ("redbubble.net", "silverbene.com", "jomashop.com")
    )


def render_product_image(product: Product, proxy_url: str) -> None:
    if not product.image_urls:
        st.caption("Không có ảnh")
        return

    image_url = product.image_urls[0]
    try:
        if _needs_image_proxy_fetch(image_url):
            image_bytes = _preview_image_bytes(image_url, proxy_url)
            if image_bytes:
                st.image(image_bytes, use_container_width=True)
            else:
                st.caption("Không tải được ảnh preview")
        else:
            st.image(image_url, use_container_width=True)
    except Exception:
        st.caption("Không tải được ảnh preview")


def _render_preview_chip(product: Product, proxy_url: str, *, image_width: int = 58) -> None:
    title = html.unescape(product.title or "—")
    short_title = title if len(title) <= 20 else f"{title[:19]}…"
    price_text = f"${product.price:.2f}" if product.price is not None else "—"

    if product.image_urls:
        image_url = product.image_urls[0]
        try:
            if _needs_image_proxy_fetch(image_url):
                image_bytes = _preview_image_bytes(image_url, proxy_url or "")
                if image_bytes:
                    st.image(image_bytes, width=image_width)
                else:
                    st.caption("Ảnh")
            else:
                st.image(image_url, width=image_width)
        except Exception:
            st.caption("Ảnh")
    else:
        st.caption("—")

    st.markdown(
        f'<p class="preview-chip-title" title="{html.escape(title)}">{html.escape(short_title)}</p>'
        f'<p class="preview-chip-price">{html.escape(price_text)}</p>',
        unsafe_allow_html=True,
    )


def _render_compact_product_preview_row(
    products: list[Product],
    *,
    limit: int = 10,
    proxy_url: str = "",
) -> None:
    preview = products[:limit]
    if not preview:
        return

    st.markdown('<div class="preview-compact-wrap">', unsafe_allow_html=True)
    cols = st.columns(len(preview), gap="small")
    for col, product in zip(cols, preview):
        with col:
            _render_preview_chip(product, proxy_url)
    st.markdown("</div>", unsafe_allow_html=True)


def _gmc_preview_kwargs() -> dict:
    return {
        "gmc_currency": st.session_state.get("gmc_currency", "USD"),
        "gmc_condition": st.session_state.get("gmc_condition", "new"),
        "gmc_google_category": category_id_from_option(
            st.session_state.get("gmc_google_category", GMC_DEFAULT_GOOGLE_CATEGORY_ID)
        ),
        "gmc_variant_mode": st.session_state.get("gmc_variant_mode", "all_variations"),
        "gmc_defaults": {
            "gender": st.session_state.get("gmc_default_gender", GMC_DEFAULT_GENDER),
            "age group": st.session_state.get("gmc_default_age_group", GMC_AGE_GROUP_DEFAULT),
            "gtin": st.session_state.get("gmc_gtin_default", ""),
            "brand": _resolve_shop_brand_default(),
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
        "google_product_category": st.column_config.TextColumn(
            column_header_label("google_product_category", hr),
            help=(
                "Nhập mã taxonomy Google (vd. 178, 212, 188) hoặc chọn từ gợi ý. "
                f"Gợi ý: {category_options_hint()}"
            ),
        ),
        "gmc_size": st.column_config.TextColumn(column_header_label("gmc_size", hr)),
        "gmc_color": st.column_config.TextColumn(column_header_label("gmc_color", hr)),
        "gmc_condition": st.column_config.SelectboxColumn(
            column_header_label("gmc_condition", hr),
            options=["new", "refurbished", "used"],
            help="Tình trạng sản phẩm cho Google Merchant",
        ),
        "gmc_availability": st.column_config.TextColumn(
            column_header_label("gmc_availability", hr),
            help="in_stock / out_of_stock — lấy từ tồn kho khi crawl",
            disabled=True,
        ),
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


def _gmc_bulk_option_label(value: str) -> str:
    return "— giữ nguyên —" if not str(value or "").strip() else str(value)


def _build_current_gmc_feed_rows() -> list[dict[str, str]]:
    products_raw = st.session_state.products_raw
    edited_df = st.session_state.edited_df
    if not products_raw or edited_df is None:
        return []

    export_products = products_from_edit_table(
        products_raw,
        edited_df.to_dict("records"),
        export_format="gmc-feed-csv",
    )
    row_ids = [int(v) for v in edited_df["row_id"].tolist()]
    gmc_kw = _gmc_preview_kwargs()
    return build_gmc_feed_edit_rows(
        export_products,
        row_ids,
        variant_mode=gmc_kw["gmc_variant_mode"],
        currency=gmc_kw["gmc_currency"],
        condition=gmc_kw["gmc_condition"],
        google_category_default=gmc_kw["gmc_google_category"],
        gmc_defaults=gmc_kw["gmc_defaults"],
    )


def _render_gmc_feed_bulk_fields() -> None:
    st.markdown(
        '<div class="gmc-bulk-wrap">',
        unsafe_allow_html=True,
    )
    st.caption(
        "**Chỉnh hàng loạt feed** — điền cột cần đổi · để trống = giữ nguyên · "
        "Category: nhập mã (178, 212…)"
    )
    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(
        [1.1, 0.75, 0.9, 1, 1.35, 0.95, 1.05, 0.65],
        gap="small",
    )
    with c1:
        st.text_input("Brand", key="gmc_bulk_brand", label_visibility="visible")
    with c2:
        st.text_input("Size", key="gmc_bulk_size")
    with c3:
        st.text_input("Color", key="gmc_bulk_color")
    with c4:
        st.selectbox(
            "Condition",
            options=GMC_CONDITION_OPTIONS,
            format_func=_gmc_bulk_option_label,
            key="gmc_bulk_condition",
        )
    with c5:
        st.text_input(
            "Category",
            key="gmc_bulk_google_category",
            placeholder="178…",
            help=f"Mã Google taxonomy · {category_options_hint()}",
        )
    with c6:
        st.selectbox(
            "Gender",
            options=GMC_GENDER_OPTIONS,
            format_func=_gmc_bulk_option_label,
            key="gmc_bulk_gender",
        )
    with c7:
        st.selectbox(
            "Age group",
            options=GMC_AGE_GROUP_OPTIONS,
            format_func=_gmc_bulk_option_label,
            key="gmc_bulk_age_group",
        )
    with c8:
        st.checkbox("Chỉ trống", key="gmc_bulk_only_empty", help="Chỉ điền ô còn thiếu")
    st.markdown("</div>", unsafe_allow_html=True)


def _gmc_title_rules_active(rules: ExportRules) -> bool:
    return bool(
        rules.title_remove
        or rules.title_find
        or rules.title_prefix
        or rules.title_suffix
        or st.session_state.get("gmc_title_prepend_brand")
    )


def _render_gmc_title_bulk_fields() -> None:
    st.markdown(
        '<div class="gmc-title-bulk-wrap">',
        unsafe_allow_html=True,
    )
    st.caption(
        "**Title feed GMC** — xóa / tìm-thay / tiền tố-hậu tố · title xuất file tối đa **150** ký tự"
    )
    r1c1, r1c2 = st.columns([2.4, 1], gap="small")
    with r1c1:
        st.text_input(
            "Xóa chữ",
            placeholder="SupplierName, FREE SHIPPING",
            help="Nhiều cụm cách nhau dấu phẩy",
            key="title_remove",
        )
    with r1c2:
        st.checkbox(
            "Không phân biệt hoa/thường",
            key="title_remove_ignore_case",
        )
    r2c1, r2c2, r2c3, r2c4 = st.columns([1.1, 1.1, 1.2, 1.2], gap="small")
    with r2c1:
        st.text_input("Tìm", placeholder="925 Silver", key="title_find")
    with r2c2:
        st.text_input("Thay bằng", placeholder="Sterling Silver", key="title_replace")
    with r2c3:
        st.text_input("Tiền tố", placeholder="MyBrand - ", key="title_prefix")
    with r2c4:
        st.text_input("Hậu tố", placeholder=" | Free Ship", key="title_suffix")
    st.checkbox(
        "Thêm brand shop vào đầu title nếu chưa có",
        key="gmc_title_prepend_brand",
        help=f"Dùng brand shop: **{_resolve_shop_brand_default() or '(chưa đặt)'}**",
    )
    st.markdown("</div>", unsafe_allow_html=True)


def _apply_gmc_title_bulk_from_session() -> None:
    products_raw = st.session_state.products_raw
    edited_df = st.session_state.edited_df
    if products_raw is None:
        st.warning("Chưa có sản phẩm — crawl hoặc import CSV trước.")
        return

    if edited_df is not None:
        st.session_state.products_raw = merge_edited_rows(
            products_raw,
            edited_df.to_dict("records"),
            export_format="gmc-feed-csv",
        )

    rules = build_export_rules(section="title")
    if not _gmc_title_rules_active(rules):
        st.warning(
            "Nhập ít nhất một quy tắc title (xóa, tìm/thay, tiền tố, hậu tố hoặc tick thêm brand)."
        )
        return

    shop_brand = _resolve_shop_brand_default()
    prepend = bool(st.session_state.get("gmc_title_prepend_brand"))
    applied = [
        apply_gmc_title_rules(
            product,
            rules,
            shop_brand=shop_brand,
            prepend_brand=prepend,
        )
        for product in st.session_state.products_raw
    ]
    st.session_state.products_raw = applied
    apply_edit_filter(applied)
    st.session_state.output_bytes = None
    st.session_state.output_name = None
    st.session_state.last_error = None
    st.session_state.last_info = (
        f"Đã áp dụng quy tắc **title GMC** cho **{len(applied)}** sản phẩm — "
        "xem tab **File feed xuất** hoặc cột Title bảng bên dưới."
    )


def _apply_gmc_feed_bulk_from_session() -> None:
    products_raw = st.session_state.products_raw
    edited_df = st.session_state.edited_df
    if products_raw is None or edited_df is None:
        st.warning("Chưa có dòng feed — kiểm tra bảng SP bên dưới.")
        return

    # Gộp chỉnh sửa bảng trước — tránh mất title/description khi rebuild.
    st.session_state.products_raw = merge_edited_rows(
        products_raw,
        edited_df.to_dict("records"),
        export_format="gmc-feed-csv",
    )

    base_rows = _build_current_gmc_feed_rows()
    if not base_rows:
        st.warning("Chưa có dòng feed — kiểm tra bảng SP bên dưới.")
        return

    updated_rows, applied_fields = apply_bulk_to_gmc_feed_edits(
        base_rows,
        brand=str(st.session_state.get("gmc_bulk_brand") or ""),
        size=str(st.session_state.get("gmc_bulk_size") or ""),
        color=str(st.session_state.get("gmc_bulk_color") or ""),
        condition=str(st.session_state.get("gmc_bulk_condition") or ""),
        google_category=category_id_from_option(
            st.session_state.get("gmc_bulk_google_category")
        ),
        gender=str(st.session_state.get("gmc_bulk_gender") or ""),
        age_group=str(st.session_state.get("gmc_bulk_age_group") or ""),
        only_empty=bool(st.session_state.get("gmc_bulk_only_empty")),
    )
    if not applied_fields:
        st.warning(
            "Nhập ít nhất một trường (brand, size, color, condition, category, gender hoặc age group)."
        )
        return

    st.session_state.gmc_feed_edits = None
    merge_gmc_feed_edits(st.session_state.products_raw, updated_rows)
    # Không merge lại edited_df cũ — nó còn vendor trống và sẽ ghi đè brand vừa áp dụng.
    apply_edit_filter(skip_merge=True)
    st.session_state.output_bytes = None
    labels = ", ".join(applied_fields)
    st.session_state.last_info = (
        f"Đã áp dụng hàng loạt **{labels}** cho **{len(updated_rows)}** dòng feed GMC."
    )
    st.session_state.last_error = None


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
        f"{len(all_export_columns(export_format, include_url=bool(st.session_state.get('crawl_include_product_url'))))} cột template · "
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
            include_url=bool(st.session_state.get("crawl_include_product_url")),
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
    shop_brand = _resolve_shop_brand_default()
    if shop_brand:
        st.caption(
            f"**Brand mặc định:** {shop_brand} (từ tên shop / domain crawl) — "
            "ghi đè vendor nhà cung cấp (Trendsi…); sửa từng dòng hoặc hàng loạt ở Bước chỉnh sửa."
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
        st.text_input(
            "Google product category mặc định",
            key="gmc_google_category",
            placeholder="212",
            help=(
                "Mã taxonomy khi SP không khớp gợi ý tự động — nhập số (vd. 178, 212). "
                f"Gợi ý: {category_options_hint()}"
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
        help=f"Áp dụng cho SP chưa có GTIN — vẫn sửa từng dòng ở Bước {edit_step_number()}.",
    )


def render_gmc_bulk_feed_block() -> None:
    with st.container(border=True):
        _render_gmc_feed_bulk_fields()
        _render_gmc_title_bulk_fields()
        feed_col, title_col = st.columns(2, gap="small")
        with feed_col:
            if st.button(
                "Áp dụng hàng loạt feed",
                type="primary",
                use_container_width=True,
                key="gmc_feed_bulk_apply",
            ):
                _apply_gmc_feed_bulk_from_session()
                st.rerun()
        with title_col:
            if st.button(
                "Áp dụng title GMC",
                type="secondary",
                use_container_width=True,
                key="gmc_title_bulk_apply",
            ):
                _apply_gmc_title_bulk_from_session()
                st.rerun()


def render_export_format_selector() -> str | None:
    st.markdown("### Bước 2 · Chọn file cần xuất")
    _ensure_export_format_for_workflow()
    export_format = st.radio(
        "Xuất sang",
        options=GENERAL_EXPORT_FORMATS,
        format_func=lambda key: FORMAT_OPTIONS[key],
        horizontal=True,
        index=None,
        key="export_format",
    )
    if not export_format:
        st.caption("Chưa chọn định dạng — bấm một tuỳ chọn phía trên trước khi xuất file.")
        return None
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

    preview_step = gmc_display_step(5) if is_gmc_workflow() else 5
    edit_step = edit_step_number()
    st.markdown(f"### Bước {preview_step} · Xem trước {preview_n}/{count} sản phẩm")
    if is_gmc_workflow():
        st.caption(
            f"**{format_label}** · chỉnh feed ở **Bước {edit_step}**."
        )
    else:
        st.caption(
            f"**{format_label}** · chỉnh chi tiết ở **Bước {edit_step}**."
        )

    _render_compact_product_preview_row(
        display_products,
        limit=preview_n,
        proxy_url=st.session_state.get("proxy_url") or "",
    )

    max_img = None if max_images_per_product <= 0 else max_images_per_product
    with st.expander(f"Mẫu file {format_label}", expanded=False):
        if export_format == "woocommerce-csv" and image_mode == "split_columns":
            st.caption(image_batch_summary(products, max_img))
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
        title_find=str(st.session_state.title_find or "").strip(),
        title_replace=str(st.session_state.title_replace or ""),
        title_replace_ignore_case=bool(st.session_state.title_replace_ignore_case),
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
            title_find=full.title_find,
            title_replace=full.title_replace,
            title_replace_ignore_case=full.title_replace_ignore_case,
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
    find_col, replace_col = st.columns(2)
    with find_col:
        st.text_input(
            "Tìm trong title",
            placeholder="925 Silver",
            help="Thay thế trên toàn bộ title (sau bước xóa chữ)",
            key="title_find",
        )
    with replace_col:
        st.text_input(
            "Thay bằng",
            placeholder="Sterling Silver",
            key="title_replace",
        )
    st.checkbox(
        "Tìm/thay title không phân biệt hoa/thường",
        key="title_replace_ignore_case",
    )
    prefix_col, suffix_col = st.columns(2)
    with prefix_col:
        st.text_input(
            "Tiền tố title (thêm đầu)",
            placeholder="VEEXOVAR - ",
            help="Thêm sau bước tìm & thay",
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
        "Định dạng mô tả (áp dụng khi bấm Áp dụng)",
        options=["html", "text"],
        format_func=lambda key: "HTML" if key == "html" else "Text thuần",
        horizontal=True,
        key="description_format",
        help="HTML giữ thẻ <p>… — Text thuần bỏ hết thẻ HTML (phù hợp feed/GMC).",
    )
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
        help="Chỉ dùng khi định dạng = HTML. Ví dụ: <h3>…</h3> → <p>…</p>.",
        key="description_convert_h_to_p",
        disabled=st.session_state.get("description_format") == "text",
    )

    if st.session_state.get("description_action", "find_replace") == "replace_all":
        st.info(
            "Dán **1 mô tả** bên dưới → bấm **Áp dụng** → **tất cả** sản phẩm "
            "dùng chung mô tả này (mô tả cũ bị thay hết)."
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
        elif is_trendsi_workflow(mode):
            st.warning(
                "**Chưa có sản phẩm.** Dán link Trendsi `/brand?...` · `/classify/...` "
                "hoặc chọn **Link mẫu**, rồi bấm **Crawl Trendsi**."
            )
        elif is_shopify_workflow(mode):
            st.warning(
                "**Chưa có sản phẩm.** Dán link shop **Shopify** (trang chủ / collection / SP), "
                "rồi bấm **Crawl Shopify** — Bước 2 mặc định **CSV import Shopify**."
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
            if _needs_image_proxy_fetch(img_url):
                img_bytes = _preview_image_bytes(img_url, proxy or "")
            else:
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
        scroll_hint = (
            f"Cuộn xuống **Bước {gmc_display_step(5)}** (xem trước) và "
            f"**Bước {edit_step_number(gmc=True)}** (chỉnh hàng loạt feed + bảng SP)."
            if is_gmc_workflow(mode)
            else "Cuộn xuống **Bước 5** (xem trước) và **Bước 6** (bảng chỉnh sửa) sau Bước 4."
        )
        st.success(
            f"**{count}** sản phẩm sẵn sàng · **{title}** · Giá mẫu: **{price_text}** · {scroll_hint}"
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

**Title** — Xóa từ/cụm, tìm & thay, thêm tiền tố/hậu tố. Nhiều cụm cách nhau dấu phẩy.

**Category, tags & shop** — Điền giá trị mới → **thay thế hoàn toàn** giá trị cũ.

**Mô tả** — Chọn **HTML** hoặc **Text thuần**, tìm & thay trong mô tả gốc, hoặc dán 1 mô tả chung cho tất cả SP.
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
    export_format = _current_export_format()
    gmc_mode = is_gmc_workflow()

    if gmc_mode:
        st.markdown(workflow_step_heading(4, "Trường feed GMC", gmc=True))
        variant_mode = st.session_state.get("gmc_variant_mode", "all_variations")
        st.caption(
            f"Chế độ biến thể: **{GMC_VARIANT_MODES.get(variant_mode, variant_mode)}** · "
            f"**{len(GMC_FEED_COLUMNS)}** cột cố định theo chuẩn Google Merchant."
        )
        st.info("Cột: " + " · ".join(GMC_FEED_COLUMNS))
        return ExportFields.from_preset("all")

    if not export_format:
        st.markdown("### Bước 4 · Chọn trường trong file xuất")
        st.info("Chọn định dạng file ở **Bước 2** trước — sau đó mới chọn trường xuất.")
        return ExportFields.from_preset("shop")

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
    if st.session_state.get("crawl_include_product_url"):
        fields.enabled.add("url")
        st.caption("Đã bật **Lấy cột link sản phẩm** ở Bước 1 → thêm trường Link URL vào file xuất.")
    st.info(f"File sẽ gồm: **{', '.join(fields.labels())}**")
    return fields


def render_crawl_report_downloads() -> None:
    json_bytes = st.session_state.get("crawl_report_json_bytes")
    csv_bytes = st.session_state.get("crawl_report_csv_bytes")
    json_name = st.session_state.get("crawl_report_json_name") or "crawl_report.json"
    csv_name = st.session_state.get("crawl_report_csv_name") or "crawl_urls.csv"
    if not json_bytes and not csv_bytes:
        return

    st.caption("Báo cáo crawl — tải file để xem số SP và link từng sản phẩm:")
    col_json, col_csv = st.columns(2)
    if json_bytes:
        with col_json:
            st.download_button(
                "Tải báo cáo JSON",
                data=json_bytes,
                file_name=json_name,
                mime="application/json",
                use_container_width=True,
                key="download_crawl_report_json",
            )
    if csv_bytes:
        with col_csv:
            st.download_button(
                "Tải danh sách link SP (CSV)",
                data=csv_bytes,
                file_name=csv_name,
                mime="text/csv",
                use_container_width=True,
                key="download_crawl_report_csv",
            )
            st.caption("Cột **url** = link sản phẩm")


def run_crawl_only(
    url: str,
    limit: int,
    pages: int,
    shopify_token: str,
    woo_key: str,
    woo_secret: str,
    category_slug: str = "Tất cả",
    action_area=None,
    live_area=None,
) -> None:
    st.session_state.crawl_running = True
    st.session_state.crawl_matched_count = 0
    st.session_state.last_error = None
    st.session_state.last_info = None
    st.session_state.output_bytes = None
    st.session_state.output_name = None
    st.session_state.crawl_report_json = None
    st.session_state.crawl_report_csv = None
    st.session_state.crawl_report_json_bytes = None
    st.session_state.crawl_report_csv_bytes = None
    st.session_state.crawl_report_json_name = None
    st.session_state.crawl_report_csv_name = None

    if not url.strip():
        st.session_state.crawl_running = False
        st.session_state.last_error = "Vui lòng nhập link cần crawl."
        return

    creds = CrawlCredentials.load()
    if shopify_token:
        creds.shopify_token = shopify_token
    if woo_key:
        creds.woocommerce_key = woo_key
    if woo_secret:
        creds.woocommerce_secret = woo_secret

    expected_total: int | None = st.session_state.get("crawl_expected_total")
    _update_crawl_live_ui(action_area, live_area, 0, expected_total)
    if live_area is not None:
        live_area.info("Đang kết nối shop và tải trang 1…")

    try:
        crawl_url = url.strip()
        if st.session_state.get("crawl_via_sitemap"):
            from utils.sitemap_mode import apply_sitemap_mode

            crawl_url, sitemap_target, sitemap_note = apply_sitemap_mode(
                crawl_url, force=True
            )
            if sitemap_note:
                st.session_state.last_info = sitemap_note
            if live_area is not None and sitemap_target is not None:
                live_area.info(
                    f"Crawl theo sitemap · {crawl_url}"
                )
        try:
            early_target = resolve_url(crawl_url)
        except UnsupportedUrlError:
            early_target = None
        if (
            live_area is not None
            and early_target is not None
            and getattr(early_target, "platform", "") == "fineartamerica"
            and not st.session_state.get("crawl_via_sitemap")
        ):
            live_area.info(
                "Fine Art America: đang tải sitemap popular… "
                "(mặc định tối đa ~3.000 artwork — đặt Giới hạn SP nếu cần khác)"
            )
        preflight_error = check_shop_reachable(
            crawl_url,
            proxy_url=st.session_state.get("proxy_url") or None,
        )
        if preflight_error:
            st.session_state.last_error = preflight_error
            return

        if is_redbubble_workflow():
            from ui.redbubble_workflow import validate_redbubble_url

            rb_check = validate_redbubble_url(crawl_url)
            if not rb_check.get("crawl_ready"):
                st.session_state.last_info = None
                st.session_state.last_error = str(
                    rb_check.get("message") or "Link Redbubble chưa hợp lệ."
                )
                return

        if is_shopify_workflow():
            from ui.shopify_workflow import validate_shopify_url

            shopify_check = validate_shopify_url(crawl_url)
            if not shopify_check.get("crawl_ready"):
                st.session_state.last_info = None
                st.session_state.last_error = str(
                    shopify_check.get("message") or "Link Shopify chưa hợp lệ."
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
            if getattr(target, "crawl_type", "") == "search":
                pass
            elif target.platform == "trendsi":
                # Link /classify/... hoặc /brand đã đúng category — không rebuild
                # (trước đây build_category_crawl_url trả homepage → ra SP quần áo mặc định).
                pass
            elif target.platform == "woocommerce" and target.crawl_type in {
                "shop",
                "product",
                "category",
            }:
                # Giữ nguyên link user dán (homepage /shop /product /home-living/mug…).
                pass
            elif target.platform == "teemill" and target.crawl_type in {
                "shop",
                "product",
                "category",
            }:
                pass
            elif target.platform == "generic-web":
                if getattr(target, "crawl_type", "") == "shop":
                    category_filter = category_slug
                elif category_slug and category_slug != "Tất cả":
                    crawl_url = build_category_crawl_url(
                        target.base_url or crawl_url,
                        target.platform,
                        category_slug,
                    )
                    preview = describe_url(crawl_url)
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
        elif not preview.get("ok"):
            st.session_state.last_error = preview.get("message") or "Link chưa hợp lệ để crawl."
            return

        progress_callback = None
        progress_bar = None
        progress_text = None
        target_preview = preview.get("target") if preview.get("ok") else None
        crawl_platform = (
            getattr(target_preview, "platform", "") if target_preview else ""
        )
        rb_platform = crawl_platform == "redbubble"
        workers = int(st.session_state.get("crawl_workers", DEFAULT_CRAWL_WORKERS))
        if crawl_platform == "shopify":
            from crawlers.shopify_hydrogen import ShopifyHydrogenCrawler

            st.session_state.crawl_shopify_hydrogen = (
                ShopifyHydrogenCrawler.is_hydrogen_store(crawl_url)
            )
        else:
            st.session_state.crawl_shopify_hydrogen = False

        if target_preview and crawl_platform != "redbubble":
            progress_bar = st.progress(0.0, text="Đang chuẩn bị crawl…")
            progress_text = st.empty()

            def progress_callback(current: int, total: int, matched: int) -> None:
                _update_crawl_live_ui(action_area, live_area, matched, expected_total)
                if progress_bar is not None and total:
                    ratio = min(current / total, 1.0)
                    if crawl_platform == "generic-web":
                        label = f"Đang xử lý {current}/{total} · {matched} SP"
                    elif crawl_platform == "shopbase":
                        if total >= 300:
                            label = (
                                f"Mô tả {current}/{total} · {matched} SP · {workers} luồng"
                            )
                        else:
                            label = (
                                f"Trang {current}/{total} · {matched} SP · {workers} luồng"
                            )
                    elif crawl_platform == "trendsi":
                        label = (
                            f"Chi tiết {current}/{total} · {matched} SP · {workers} luồng"
                        )
                    elif crawl_platform == "fineartamerica":
                        label = (
                            f"Artwork {current}/{total} · {matched} SP · {workers} luồng"
                        )
                    elif (
                        target_preview is not None
                        and getattr(target_preview, "crawl_type", "") == "sitemap"
                    ):
                        label = (
                            f"Sitemap {current}/{total} · {matched} SP · {workers} luồng"
                        )
                    elif st.session_state.get("crawl_shopify_hydrogen"):
                        label = (
                            f"Athletes {current}/{total} · {matched} SP · {workers} luồng"
                        )
                    else:
                        label = f"Trang {current}/{total} · {matched} SP · {workers} luồng"
                    progress_bar.progress(ratio, text=label)
                if progress_text is not None:
                    if crawl_platform == "generic-web" and total:
                        est_min = max(1, int(total * 1.4 / 60 / max(workers, 1)))
                        progress_text.caption(
                            f"Web thuần — ước tính **~{est_min} phút** với {workers} luồng."
                        )
                    elif crawl_platform == "shopbase" and expected_total and expected_total >= 500:
                        est_min = max(
                            2,
                            int(expected_total / max(workers, 1) / 60) + 2,
                        )
                        progress_text.caption(
                            f"ShopBase — ~**{est_min} phút** cho **{expected_total:,}** SP "
                            f"(danh sách + mô tả song song · {workers} luồng).".replace(
                                ",", "."
                            )
                        )
                    elif expected_total:
                        progress_text.caption(
                            f"Đã lấy **{matched}** / ~**{expected_total:,}** SP dự kiến".replace(
                                ",", "."
                            )
                        )
                    else:
                        progress_text.caption(f"Đã lấy **{matched}** SP · {workers} luồng song song.")

        def _commit_products(products_list, *, export_slug: str = "products") -> None:
            st.session_state.products_raw = deepcopy(products_list)
            _apply_shop_brand_defaults(st.session_state.products_raw, crawl_url)
            apply_edit_filter(st.session_state.products_raw)
            st.session_state.export_name = export_slug or "products"

        def partial_callback(partial_products) -> None:
            if not partial_products:
                return
            # Checkpoint giữa chừng — lỗi/dừng vẫn còn data để xuất file.
            st.session_state.products_raw = deepcopy(partial_products)
            st.session_state.crawl_matched_count = len(partial_products)

        crawl_kwargs = dict(
            url=crawl_url,
            credentials=creds,
            limit=limit if limit > 0 else None,
            max_pages=pages if pages > 0 else None,
            proxy_url=st.session_state.proxy_url.strip() or None,
            category_slug=category_filter,
            progress_callback=progress_callback,
            max_workers=workers,
            description_format=str(
                st.session_state.get("crawl_description_format") or "html"
            ),
            partial_callback=partial_callback,
        )
        started_at = datetime.now(timezone.utc)
        if rb_platform:
            spinner_msg = (
                "Đang crawl Redbubble… (~82 SP/trang). "
                "Nếu lỗi, bật **Proxy & Redbubble** rồi thử lại."
            )
            with st.spinner(spinner_msg):
                products, target = run_auto_crawl(**crawl_kwargs)
            _update_crawl_live_ui(action_area, live_area, len(products), expected_total)
        else:
            products, target = run_auto_crawl(**crawl_kwargs)
            _update_crawl_live_ui(action_area, live_area, len(products), expected_total)
        if progress_bar is not None:
            progress_bar.empty()
        if progress_text is not None:
            progress_text.empty()
        export_slug = (
            target.category_slug or target.handle or target.query or "products"
        )
        _commit_products(products, export_slug=export_slug)
        report = save_crawl_report(
            products,
            target,
            crawl_url,
            workers=workers,
            limit=limit if limit > 0 else None,
            max_pages=pages if pages > 0 else None,
            expected_total=expected_total,
            started_at=started_at,
        )
        st.session_state.crawl_report_json_bytes = report.json_bytes
        st.session_state.crawl_report_csv_bytes = report.urls_csv_bytes
        st.session_state.crawl_report_json_name = report.json_name
        st.session_state.crawl_report_csv_name = report.urls_csv_name
        variant_mode = st.session_state.get("gmc_variant_mode", "all_variations")
        feed_rows = count_gmc_export_rows(products, variant_mode)
        report_note = "Báo cáo crawl — tải JSON / CSV link SP bên dưới."
        missing_note = ""
        if expected_total and len(products) < int(expected_total):
            missing_note = (
                f" · Thiếu ~**{int(expected_total) - len(products)}** so với dự kiến "
                "— **vẫn xuất/tải file được** ở Bước 2."
            )
        if is_gmc_workflow() or st.session_state.get("export_format") == "gmc-feed-csv":
            st.session_state.last_info = (
                f"Crawl xong **{len(products)}** SP → **{feed_rows}** dòng feed GMC "
                f"({GMC_VARIANT_MODES.get(variant_mode, variant_mode)}) · "
                f"{preview.get('message', '')} · {report.summary()} · {report_note}"
                f"{missing_note}"
            )
        else:
            st.session_state.last_info = (
                f"Crawl xong **{len(products)}** sản phẩm · {preview.get('message', '')} · "
                f"{report.summary()} · {report_note}{missing_note}"
            )
        st.session_state.imported_products_all = None
    except UnsupportedUrlError as exc:
        st.session_state.last_error = str(exc)
    except CrawlError as exc:
        partial = list(getattr(exc, "partial_products", None) or [])
        if not partial and st.session_state.get("products_raw"):
            partial = list(st.session_state.products_raw)
        if partial:
            _apply_shop_brand_defaults(partial, crawl_url)
            apply_edit_filter(partial)
            st.session_state.products_raw = deepcopy(partial)
            st.session_state.export_name = st.session_state.get("export_name") or "products"
            st.session_state.last_info = (
                f"Crawl dừng sớm — vẫn giữ **{len(partial)}** SP. "
                "Chọn format ở **Bước 2** rồi xuất/tải file bình thường."
            )
            st.session_state.last_error = str(exc)
        else:
            st.session_state.last_error = str(exc)
    except requests.exceptions.Timeout as exc:
        partial = list(st.session_state.get("products_raw") or [])
        if partial:
            st.session_state.last_info = (
                f"Timeout — vẫn giữ **{len(partial)}** SP đã crawl để xuất file (Bước 2)."
            )
        st.session_state.last_error = (
            f"Server phản hồi chậm (timeout): {exc}. "
            "Thử **Luồng crawl = 4–8**, giảm **Giới hạn SP**, hoặc crawl lại sau vài phút."
        )
    except requests.exceptions.ConnectionError as exc:
        partial = list(st.session_state.get("products_raw") or [])
        if partial:
            st.session_state.last_info = (
                f"Mất kết nối — vẫn giữ **{len(partial)}** SP đã crawl để xuất file (Bước 2)."
            )
        st.session_state.last_error = (
            f"Lỗi kết nối: {exc}. Thử giảm **Luồng crawl** hoặc bật **Proxy**."
        )
    except Exception as exc:
        err_text = str(exc).strip()
        if not err_text:
            err_text = f"{type(exc).__name__}"
        partial = list(st.session_state.get("products_raw") or [])
        if partial:
            st.session_state.last_info = (
                f"Crawl lỗi — vẫn giữ **{len(partial)}** SP đã crawl để xuất file (Bước 2)."
            )
        if "timed out" in err_text.lower() or "timeout" in err_text.lower():
            st.session_state.last_error = (
                f"Server phản hồi chậm: {err_text}. "
                "Thử **Luồng crawl = 4–8** và crawl lại."
            )
        else:
            st.session_state.last_error = (
                f"Lỗi: {err_text}. Nếu vẫn lặp lại, tắt app và mở lại **{APP_NAME}**."
            )
    finally:
        st.session_state.crawl_running = False


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
    st.session_state.products_raw = merge_edited_rows(
        st.session_state.products_raw,
        edited_df.to_dict("records"),
        export_format=export_format,
    )
    total_raw = len(st.session_state.products_raw)

    validation = validate_edited_df(edited_df, export_format)
    if not validation["ok"] and not st.session_state.get("edit_skip_validation"):
        missing = ", ".join(
            EDIT_FIELD_LABELS.get(col, col)
            for col in validation["columns_missing"]
        )
        st.session_state.last_error = (
            f"Không xuất file — còn thiếu: {missing}. Điền đủ ở Bước 6 "
            "hoặc bật **Bỏ qua — cho phép xuất file dù còn thiếu**."
        )
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{st.session_state.export_name}_{stamp}"
    download_name = export_download_name(name, export_format)
    output_path = temp_export_path(download_name)

    def on_progress(value: float, message: str) -> None:
        try:
            progress_bar.progress(value, text=message)
        except TypeError:
            progress_bar.progress(value)
        status_text.caption(message)

    try:
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
                st.session_state.get("gmc_google_category", GMC_DEFAULT_GOOGLE_CATEGORY_ID)
            ),
            gmc_defaults={
                "gender": st.session_state.get("gmc_default_gender", GMC_DEFAULT_GENDER),
                "age group": st.session_state.get("gmc_default_age_group", GMC_AGE_GROUP_DEFAULT),
                "gtin": st.session_state.get("gmc_gtin_default", ""),
                "brand": _resolve_shop_brand_default(),
            },
            gmc_variant_mode=st.session_state.get("gmc_variant_mode", "all_variations"),
        )
        st.session_state.output_bytes = exported.read_bytes()
        st.session_state.output_name = download_name
    finally:
        cleanup_export_artifacts(output_path)

    row_count: int | None = None
    if export_format == "gmc-feed-csv":
        variant_mode = st.session_state.get("gmc_variant_mode", "all_variations")
        row_count = count_gmc_export_rows(products, variant_mode)
    else:
        row_count = len(products)

    try:
        register_feed_export(
            exported,
            export_format=export_format,
            shop_label=str(st.session_state.export_name or ""),
            row_count=row_count,
        )
    except OSError:
        pass

    st.session_state.last_error = None
    if export_format == "gmc-feed-csv":
        st.session_state.last_info = (
            f"Đã xuất **{row_count}** dòng feed GMC ({len(products)} SP gốc) · "
            f"`{st.session_state.output_name}` — đã lưu vào **Lịch sử file feed**."
        )
    else:
        st.session_state.last_info = (
            f"Đã xuất **{len(products)}** SP từ bảng Bước 6 / {total_raw} SP gốc · "
            f"{len(fields.enabled)} trường → `{st.session_state.output_name}` — "
            "đã lưu vào **Lịch sử file feed**."
        )


def render_feed_history_panel(*, in_sidebar: bool = False) -> None:
    """Lịch sử file feed đã xuất — tải lại hoặc xóa để giảm dung lượng."""
    entries = list_feed_history()
    title = "Lịch sử file feed"
    ui = st

    legacy_scan = scan_legacy_output()

    with ui.expander(title, expanded=bool(entries) or legacy_scan.has_legacy):
        if legacy_scan.has_legacy:
            ui.warning(
                f"File output cũ (bản app trước): {legacy_output_summary(legacy_scan)}. "
                "Giữ nguyên **Lịch sử file feed**."
            )
            if ui.button(
                "Dọn file output cũ",
                type="secondary",
                use_container_width=True,
                key="legacy_output_cleanup",
            ):
                result = cleanup_legacy_output()
                msg = (
                    f"Đã dọn **{result.removed_files}** file, "
                    f"**{result.removed_dirs}** thư mục "
                    f"(~{format_file_size(result.freed_bytes)})."
                )
                if result.errors:
                    msg += f" **{result.errors}** mục không xóa được."
                st.session_state.last_info = msg
                st.rerun()
            if entries:
                ui.divider()

        if not entries:
            ui.caption(
                "Chưa có file trong lịch sử. Bấm **Xuất file** ở bước cuối — "
                "file lưu tại đây để tải lại hoặc xóa."
            )
            if not legacy_scan.has_legacy:
                return
            return

        total_size = total_feed_history_bytes()
        ui.caption(f"**{len(entries)}** file · **{format_file_size(total_size)}**")

        if ui.button(
            "Xóa toàn bộ",
            type="secondary",
            use_container_width=True,
            key="feed_history_clear_all",
        ):
            removed = delete_all_feed_history()
            st.session_state.output_bytes = None
            st.session_state.output_name = None
            st.session_state.last_info = f"Đã xóa **{removed}** file feed khỏi lịch sử."
            st.rerun()

        for index, entry in enumerate(entries):
            entry_id = str(entry.get("id") or "")
            filename = str(entry.get("filename") or "feed.csv")
            short_name = filename if len(filename) <= 28 else filename[:25] + "…"
            fmt_key = str(entry.get("export_format") or "")
            fmt_label = FORMAT_OPTIONS.get(fmt_key, fmt_key)
            created = format_created_at(str(entry.get("created_at") or ""))
            size_text = format_file_size(int(entry.get("size_bytes") or 0))
            rows = entry.get("row_count")
            meta_parts = [created, size_text]
            if rows is not None:
                meta_parts.append(f"{rows} dòng")

            ui.markdown(f"**{html.escape(short_name)}**")
            ui.caption(f"{fmt_label} · {' · '.join(meta_parts)}")

            dl_col, del_col = ui.columns(2)
            file_path = Path(str(entry.get("path") or ""))
            with dl_col:
                if file_path.is_file():
                    mime = (
                        "text/csv"
                        if filename.endswith(".csv")
                        else "application/json"
                        if filename.endswith(".json")
                        else "application/octet-stream"
                    )
                    ui.download_button(
                        "Tải",
                        data=file_path.read_bytes(),
                        file_name=filename,
                        mime=mime,
                        use_container_width=True,
                        key=f"feed_hist_dl_{entry_id}",
                    )
            with del_col:
                if ui.button(
                    "Xóa",
                    type="secondary",
                    use_container_width=True,
                    key=f"feed_hist_del_{entry_id}",
                ):
                    delete_feed_history_entry(entry_id)
                    if st.session_state.get("output_name") == filename:
                        st.session_state.output_bytes = None
                        st.session_state.output_name = None
                    st.rerun()

            if in_sidebar and index < len(entries) - 1:
                ui.markdown(
                    '<hr style="margin:0.35rem 0;border:none;border-top:1px solid rgba(255,255,255,0.45);">',
                    unsafe_allow_html=True,
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


def _crawl_status_label(matched: int, expected_total: int | None = None) -> str:
    count_text = _format_sp_count(matched)
    if expected_total is not None and expected_total > 0:
        return (
            f"Đang crawl dữ liệu · {count_text} / ~{_format_sp_count(expected_total)} SP"
        )
    return f"Đang crawl dữ liệu · {count_text} SP"


def _update_crawl_live_ui(
    action_area,
    live_area,
    matched: int,
    expected_total: int | None = None,
) -> None:
    st.session_state.crawl_matched_count = matched
    label = _crawl_status_label(matched, expected_total)
    if action_area is not None:
        action_area.markdown(
            f'<div class="crawl-busy-btn">{html.escape(label)}</div>',
            unsafe_allow_html=True,
        )
    if live_area is not None:
        parts = [f"**{_format_sp_count(matched)}** SP đã crawl"]
        if expected_total is not None and expected_total > 0:
            parts.append(f"/ ~**{_format_sp_count(expected_total)}** dự kiến")
        live_area.info(" · ".join(parts))


def render_crawl_category_pills(probe: dict) -> None:
    platform = probe.get("platform") or ""
    categories = probe.get("categories") or []
    if not categories or probe.get("crawl_type") in ("product", "search"):
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
        status.write("Đang kết nối và kiểm tra **Shopify · ShopBase · WooCommerce · Teemill · Trendsi · Magento · Redbubble · Web thuần**…")
        info = _cached_describe_url(
            cleaned,
            st.session_state.get("proxy_url", "") or "",
        )
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
            int(st.session_state.get("crawl_workers", DEFAULT_CRAWL_WORKERS)),
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

    if info.get("ok") and probe.get("ok"):
        st.session_state.crawl_expected_total = probe.get("total_products")
    elif info.get("ok") is False or not probe.get("ok"):
        st.session_state.crawl_expected_total = None

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
        elif platform == "trendsi" and probe.get("crawl_type") == "category":
            total = probe.get("total_products")
            if total:
                st.caption(
                    f"Danh mục Trendsi — crawl đúng link `/classify/...` "
                    f"(~**{total:,}** SP trong category này).".replace(",", ".")
                )
            else:
                st.caption(
                    "Danh mục Trendsi — crawl đúng link `/classify/...`, không lấy cả catalog."
                )
        elif platform == "trendsi" and probe.get("crawl_type") == "brand":
            st.caption("Brand Trendsi — crawl đúng link `/brand?...`, không lấy cả catalog.")
        elif platform == "shopify" and probe.get("crawl_type") == "shop":
            probe_msg = str(probe.get("message") or "")
            if "Hydrogen" in probe_msg:
                st.caption(
                    "**Shopify Hydrogen (Remix)** — crawl catalog athlete "
                    "(~40–50 SP/athlete). Để **Giới hạn=0**, **Luồng=32**, "
                    "có thể mất **30–90 phút** với shop lớn."
                )
            else:
                total = probe.get("total_products")
                if total and int(total) > 5000:
                    st.caption(
                        f"Shop Shopify lớn (~**{int(total):,}** SP) — để **Giới hạn=0** · "
                        f"**Số trang=0**. Tool crawl qua **sitemap** (~1–3 phút, "
                        f"32–64 luồng; bật **Proxy** nếu bị 429).".replace(",", ".")
                    )
        elif not probe.get("categories") and probe.get("crawl_type") == "shop":
            st.caption("Shop không public danh mục — crawl **Tất cả shop**.")
        elif probe.get("crawl_type") == "search" and probe.get("url_category_slug"):
            st.caption(
                f"Tìm kiếm **`{probe['url_category_slug']}`** — "
                "đặt **Giới hạn SP** (vd. 24–100) rồi bấm **Crawl dữ liệu**."
            )

    if info.get("ok") and probe.get("ok"):
        url_sig = cleaned
        if probe.get("crawl_type") == "search":
            st.session_state["crawl_category_pill"] = "Tất cả"
        if st.session_state.get("crawl_url_sig") != url_sig:
            st.session_state.crawl_url_sig = url_sig
            default_cat = probe.get("url_category_slug") or "Tất cả"
            if probe.get("crawl_type") == "search":
                default_cat = "Tất cả"
            # Trendsi classify/brand: không có pill danh mục — giữ "Tất cả"
            # để khỏi rebuild URL về homepage (bug ra SP quần áo mặc định).
            if platform == "trendsi" and not probe.get("categories"):
                default_cat = "Tất cả"
            if platform == "redbubble" and probe.get("categories"):
                first_slug = probe["categories"][0].get("slug")
                if first_slug:
                    default_cat = first_slug
            if platform == "shopify" and probe.get("total_products"):
                total = int(probe["total_products"])
                has_proxy = bool((st.session_state.get("proxy_url") or "").strip())
                if not has_proxy:
                    st.session_state["crawl_workers"] = min(
                        int(st.session_state.get("crawl_workers") or 500),
                        64 if total > 10000 else 32,
                    )
                elif total > 5000 and int(st.session_state.get("crawl_limit") or 0) == 0:
                    st.session_state["crawl_workers"] = max(
                        int(st.session_state.get("crawl_workers") or 500),
                        64 if total > 20000 else 32,
                    )
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
    """Hiển thị cột thiếu dữ liệu — bấm để cuộn tới dòng thiếu trong bảng."""
    if report["ok"]:
        export_step = export_step_number()
        st.success(f"✓ Đủ thông tin các cột — có thể xuất file ở Bước {export_step}.")
        return True

    st.markdown(
        "**Cột còn thiếu dữ liệu — bấm vào cột 🔴 để cuộn tới dòng thiếu đầu tiên:**",
        unsafe_allow_html=True,
    )

    missing_items = sorted(
        report["columns_missing"].items(),
        key=lambda item: EDIT_FIELD_LABELS.get(item[0], item[0]),
    )
    if missing_items:
        cols = st.columns(min(len(missing_items), 4))
        for index, (column, count) in enumerate(missing_items):
            label = EDIT_FIELD_LABELS.get(column, column)
            with cols[index % len(cols)]:
                if st.button(
                    f"🔴 {label} ({count}/{report['total_rows']})",
                    key=f"val_jump_{column}",
                    use_container_width=True,
                    help=f"Cuộn tới SP đầu tiên thiếu {label}",
                ):
                    row_id = first_issue_row(report, column)
                    if row_id is not None:
                        st.session_state["edit_jump_row_id"] = row_id
                        st.session_state["edit_jump_column"] = column
                        st.rerun()

    if st.button(
        f"📍 Cuộn tới SP thiếu đầu tiên ({report['rows_with_issues']} SP)",
        key="val_jump_first",
        use_container_width=True,
        type="primary",
        help="Cuộn bảng Bước 6 tới dòng đầu tiên còn thiếu thông tin",
    ):
        row_id = first_issue_row(report)
        if row_id is not None:
            st.session_state["edit_jump_row_id"] = row_id
            st.session_state.pop("edit_jump_column", None)
            st.rerun()

    skip = st.checkbox(
        "Bỏ qua — cho phép xuất file dù còn thiếu (sửa sau trong Excel/Sheets)",
        key="edit_skip_validation",
        help="Ô trống vẫn xuất ra file CSV; bạn có thể điền thêm sau khi tải về.",
    )
    if skip:
        export_step = export_step_number()
        st.warning(
            f"Đã bỏ qua kiểm tra — có thể xuất file ở Bước {export_step} "
            f"(còn **{report['rows_with_issues']}** SP thiếu thông tin)."
        )
        return True

    st.error(
        f"Còn **{report['rows_with_issues']}** sản phẩm thiếu thông tin. "
        "Cột báo 🔴 trong bảng = cần điền đủ — hoặc tick **Bỏ qua** để xuất ngay."
    )
    return False


def _row_index_in_edit_table(edited_df: pd.DataFrame, row_id: int) -> int | None:
    if edited_df is None or edited_df.empty or "row_id" not in edited_df.columns:
        return None
    mask = edited_df["row_id"].astype(int) == int(row_id)
    positions = mask.to_numpy().nonzero()[0]
    return int(positions[0]) if len(positions) else None


def _scroll_edit_table_to_row(
    edited_df: pd.DataFrame,
    row_id: int,
    *,
    column: str | None = None,
) -> None:
    """Cuộn data editor tới hàng thiếu dữ liệu (Bước 6)."""
    row_index = _row_index_in_edit_table(edited_df, row_id)
    if row_index is None:
        st.warning(f"Không tìm thấy dòng #{row_id} trong bảng đang hiển thị.")
        return

    title = ""
    if "title" in edited_df.columns:
        title_series = edited_df.loc[edited_df["row_id"] == row_id, "title"]
        if not title_series.empty:
            title = str(title_series.iloc[0])[:80]

    editor_index = 1 if column == "description" else 0
    switch_tab_js = ""
    if column == "description":
        st.info(
            f"Tab **Description (HTML)** — dòng **#{row_id}**"
            + (f" · {title}" if title else "")
        )
        switch_tab_js = """
                const tabButtons = doc.querySelectorAll('[data-testid="stTabs"] button[role="tab"]');
                for (const btn of tabButtons) {
                    if ((btn.textContent || "").toLowerCase().includes("description")) {
                        btn.click();
                        break;
                    }
                }
        """

    label = EDIT_FIELD_LABELS.get(column or "", column or "dữ liệu")
    if column != "description":
        st.caption(
            f"Đã cuộn tới dòng **#{row_id}**"
            + (f" · thiếu **{label}**" if column else "")
            + (f" · {title}" if title else "")
        )

    components.html(
        f"""
        <script>
        (function () {{
            const doc = window.parent.document;
            const rowIndex = {row_index};
            const editorIndex = {editor_index};
            const rowHeight = {52 if column == "description" else 36};

            function scrollEditor() {{
                {switch_tab_js}
                const editors = doc.querySelectorAll('[data-testid="stDataEditor"]');
                const editor = editors[editorIndex];
                if (!editor) return false;

                editor.scrollIntoView({{ behavior: "smooth", block: "center" }});

                const scroller =
                    editor.querySelector(".dvn-scroller") ||
                    editor.querySelector('[class*="scroller"]') ||
                    editor.querySelector('[style*="overflow"]');
                if (scroller) {{
                    scroller.scrollTop = Math.max(0, rowIndex * rowHeight - 48);
                }}

                editor.style.transition = "box-shadow 0.2s ease";
                editor.style.boxShadow = "0 0 0 3px #FDA4AF";
                setTimeout(() => {{ editor.style.boxShadow = ""; }}, 2200);
                return true;
            }}

            let tries = 0;
            const timer = setInterval(() => {{
                if (scrollEditor() || ++tries > 40) clearInterval(timer);
            }}, 120);
        }})();
        </script>
        """,
        height=0,
    )


def render_export_actions(
    edit_count: int,
    total_raw: int,
    export_ready: bool = True,
) -> tuple[bool, Any, Any, Any]:
    """Xuất file, tiến trình và tải."""
    export_step = export_step_number()
    edit_step = edit_step_number()
    st.markdown(f"### Bước {export_step} · Xuất file")
    export_format = _resolved_export_format()
    can_export = export_validation_allows_export(export_ready)

    st.caption(
        f"Định dạng: **{FORMAT_OPTIONS.get(export_format, export_format)}** "
        + (
            "(chế độ GMC). "
            if is_gmc_workflow()
            else "(đổi ở Bước 2). "
        )
        + f"Xuất **{edit_count}** SP đang hiển thị ở Bước {edit_step}"
        + (f" (trong tổng **{total_raw}** SP gốc)" if edit_count != total_raw else "")
    )

    skip_validation = st.session_state.get("edit_skip_validation", False)
    export_clicked = st.button(
        f"Xuất {FORMAT_OPTIONS[export_format]}",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.edited_df is None or not can_export,
        help=(
            "Đã bỏ qua kiểm tra — file xuất được dù còn ô trống."
            if skip_validation
            else f"Cần đủ {_required_fields_caption(export_format)} ở Bước {edit_step}, "
            "hoặc tick **Bỏ qua** phía trên."
        ),
    )
    if not can_export:
        st.caption(
            f"⛔ Chưa thể xuất — kiểm tra các cột 🔴 ở Bước {edit_step}, "
            "hoặc tick **Bỏ qua — cho phép xuất file dù còn thiếu**."
        )
    elif skip_validation and not export_ready:
        st.caption("Đã bỏ qua kiểm tra — bấm **Xuất file** để tải CSV (ô trống sửa sau).")
    elif not st.session_state.output_bytes:
        st.caption(
            "Sau khi chỉnh sửa ở Bước trước, bấm **Xuất file** lại để tải bản mới — "
            "file cũ sẽ không còn hiển thị."
        )

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
        st.markdown(
            """
            <div class="sidebar-mode-header">
                <p class="sidebar-mode-title">Chế độ làm việc</p>
                <p class="sidebar-mode-subtitle">Chọn nguồn dữ liệu</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        current = st.session_state.get("workflow_mode", "crawl")
        keys = list(WORKFLOW_MODES.keys())

        # Desktop — danh sách pill (ẩn trên mobile bằng CSS)
        for key, label in WORKFLOW_MODES.items():
            is_active = key == current
            if st.button(
                label,
                key=f"sidebar_wf_{key}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                if not is_active:
                    st.session_state.workflow_mode = key
                    st.session_state.workflow_mode_mobile = key
                    _on_workflow_mode_change()
                    st.rerun()

        # Mobile — dropdown gọn (ẩn trên desktop bằng CSS)
        if st.session_state.get("workflow_mode_mobile") != current:
            st.session_state.workflow_mode_mobile = current
        picked = st.selectbox(
            "Nguồn dữ liệu",
            options=keys,
            format_func=lambda key: WORKFLOW_MODES[key],
            key="workflow_mode_mobile",
            label_visibility="collapsed",
        )
        if picked != current:
            st.session_state.workflow_mode = picked
            _on_workflow_mode_change()
            st.rerun()

        st.divider()
        render_feed_history_panel(in_sidebar=True)
        return st.session_state.get("workflow_mode", current)


def render_crawl_panel(*, sitemap_mode: bool = False) -> tuple[str, int, int, str, str, str, bool]:
    """Bước 1 — link & nhận diện bên trái, cài đặt crawl bên phải."""
    st.session_state.crawl_via_sitemap = bool(sitemap_mode)
    gmc_mode = is_gmc_workflow()
    if sitemap_mode:
        st.markdown("### Bước 1 · Crawl theo sitemap")
        st.caption(
            "Shopify / ShopBase / Fine Art America lớn hoặc bị **429** trên `/products.json`. "
            "Dán **link shop** (tự chuyển `/sitemap.xml`) hoặc dán thẳng "
            "`sitemap_products_*.xml` / `/sitemap.xml`. Luồng tối đa **500**."
        )
    elif gmc_mode:
        st.markdown("### Bước 1 · Crawl link → feed GMC")
        st.caption(
            "Chọn cách tách biến thể bên phải trước khi crawl. "
            "WooCommerce variable product: nhập **Woo key/secret** ở expander API."
        )
    else:
        st.markdown("### Bước 1 · Crawl link")

    open_crawl_step1_wrap()
    left_col, right_col = st.columns([3, 2], gap="medium")

    with left_col:
        url = st.text_input(
            "Link shop / sitemap" if sitemap_mode else "Link shop / category / sản phẩm",
            placeholder=(
                "https://shop.com/ hoặc …/sitemap.xml · sitemap_products_1.xml"
                if sitemap_mode
                else "https://www.printyourwear.com/ hoặc …/collections/..."
            ),
            help=(
                "Shopify/ShopBase: dán trang chủ hoặc file sitemap_products_*.xml /sitemap.xml\n"
                "Fine Art America: trang chủ hoặc link sitemap"
                if sitemap_mode
                else (
                    "Redbubble: link /shop/từ-khóa hoặc /i/... (KHÔNG dùng trang chủ)\n"
                    "Shopify/ShopBase: trang chủ, /collections/..., /products/...\n"
                    "Sitemap lớn: dùng chế độ sidebar **Crawl theo sitemap**\n"
                    "WooCommerce search: /shop/?s=từ-khóa&post_type=product\n"
                    "Magento/Jomashop: /jewelry.html, /watches-for-men.html, /sku-slug.html\n"
                    "Web thuần: trang chủ, /shop/..., /product/..., link danh mục .html\n"
                    "ShopBase: nebsport.com, wc.luminaaura.us (API catalog + mô tả HTML)"
                )
            ),
            key="crawl_url",
            on_change=_on_crawl_url_change,
        )
        _refresh_crawl_messages(url)

        with st.expander("API (Shopify / Woo)", expanded=False):
            shopify_token = st.text_input(
                "Shopify token", type="password", key="crawl_shopify_token"
            )
            woo_key = st.text_input("WooCommerce key", type="password", key="crawl_woo_key")
            woo_secret = st.text_input(
                "WooCommerce secret", type="password", key="crawl_woo_secret"
            )

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

    with right_col:
        st.markdown('<div class="crawl-side-tools">', unsafe_allow_html=True)
        if gmc_mode:
            render_gmc_variant_mode_selector()
        render_proxy_panel(
            expanded=False,
            title="Proxy & Redbubble",
            key_prefix="crawl_",
        )
        if st.button(
            "Làm mới session HTTP",
            use_container_width=True,
            key="crawl_reset_session",
        ):
            _clear_image_cache()
            st.session_state.last_info = "Đã reset session HTTP + cache ảnh."
        st.markdown("</div>", unsafe_allow_html=True)

    if sitemap_mode:
        st.info(
            "Đang dùng **crawl theo sitemap** — shop lớn / 429: nên bật proxy + "
            "luồng **100–500**. Số trang = số file `sitemap_products` tối đa (0 = hết)."
        )
    render_crawl_limits_row(include_workers=True)
    render_crawl_description_format()
    render_crawl_include_product_url()

    limit = int(st.session_state.get("crawl_limit", 0))
    pages = int(st.session_state.get("crawl_pages", 0))

    crawl_ready = True
    preview_url = url.strip()
    if preview_url and sitemap_mode:
        from utils.sitemap_mode import apply_sitemap_mode

        preview_url, _, sitemap_note = apply_sitemap_mode(
            preview_url, force=True
        )
        if sitemap_note:
            st.caption(sitemap_note)
    if preview_url:
        url_info = describe_url(preview_url)
        crawl_ready = bool(url_info.get("ok") and url_info.get("crawl_ready", True))

    if st.session_state.get("crawl_running") and st.session_state.products_raw is None:
        st.session_state.crawl_running = False

    crawl_clicked, crawl_action, crawl_live = render_crawl_action_centered(
        label=(
            "Crawl sitemap"
            if sitemap_mode
            else ("Crawl → GMC" if gmc_mode else "Crawl dữ liệu")
        ),
        button_key="crawl_data_btn",
        crawl_ready=crawl_ready,
        not_ready_help=(
            "Dán link shop hoặc /sitemap.xml · sitemap_products_*.xml."
            if sitemap_mode
            else "Dán link /shop/... hoặc /i/... — trang chủ Redbubble không crawl được."
        ),
        is_running=bool(st.session_state.get("crawl_running")),
        update_live_ui=_update_crawl_live_ui,
        matched_count=int(st.session_state.get("crawl_matched_count", 0)),
    )
    close_crawl_step1_wrap()

    if crawl_clicked and crawl_ready:
        run_crawl_only(
            url,
            limit,
            pages,
            shopify_token,
            woo_key,
            woo_secret,
            category_slug=st.session_state.get("crawl_category_pill", "Tất cả"),
            action_area=crawl_action,
            live_area=crawl_live,
        )
        st.rerun()

    return url, limit, pages, shopify_token, woo_key, woo_secret, False


def main() -> None:
    init_session()
    inject_pastel_pink_theme()

    workflow_mode = render_workflow_selector()
    st.session_state.crawl_via_sitemap = workflow_mode == "sitemap"
    gmc_mode = is_gmc_workflow(workflow_mode)
    rb_mode = is_redbubble_workflow(workflow_mode)
    trendsi_mode = is_trendsi_workflow(workflow_mode)
    shopify_mode = is_shopify_workflow(workflow_mode)
    _ensure_export_format_for_workflow(workflow_mode)

    import inspect

    hero_kwargs = {
        "gmc_mode": gmc_mode,
        "redbubble_mode": rb_mode,
    }
    hero_sig = inspect.signature(render_app_hero).parameters
    if "trendsi_mode" in hero_sig:
        hero_kwargs["trendsi_mode"] = trendsi_mode
    if "shopify_mode" in hero_sig:
        hero_kwargs["shopify_mode"] = shopify_mode
    render_app_hero(**hero_kwargs)

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
                run_crawl=run_crawl_only,
                update_crawl_ui=_update_crawl_live_ui,
            )
    elif workflow_mode == "trendsi":
        with st.container(border=True):
            url, limit, pages, crawl_clicked = render_trendsi_crawl_panel(
                render_url_preview=render_url_preview,
                refresh_crawl_messages=_refresh_crawl_messages,
                on_crawl_url_change=_on_crawl_url_change,
                clear_image_cache=_clear_image_cache,
                run_crawl=run_crawl_only,
                update_crawl_ui=_update_crawl_live_ui,
            )
    elif workflow_mode == "shopify":
        with st.container(border=True):
            url, limit, pages, crawl_clicked = render_shopify_crawl_panel(
                render_url_preview=render_url_preview,
                refresh_crawl_messages=_refresh_crawl_messages,
                on_crawl_url_change=_on_crawl_url_change,
                clear_image_cache=_clear_image_cache,
                run_crawl=run_crawl_only,
                update_crawl_ui=_update_crawl_live_ui,
            )
    elif workflow_mode == "filter-title":
        with st.container(border=True):
            render_title_filter_panel()
    elif workflow_mode == "filter-dedupe":
        with st.container(border=True):
            render_dedupe_filter_panel()
    elif workflow_mode == "filter-proxy":
        with st.container(border=True):
            render_proxy_dedupe_panel()
    elif workflow_mode in ("crawl", "crawl-gmc"):
        with st.container(border=True):
            url, limit, pages, shopify_token, woo_key, woo_secret, crawl_clicked = (
                render_crawl_panel(sitemap_mode=False)
            )
    elif workflow_mode == "sitemap":
        with st.container(border=True):
            url, limit, pages, shopify_token, woo_key, woo_secret, crawl_clicked = (
                render_crawl_panel(sitemap_mode=True)
            )
    else:
        with st.container(border=True):
            render_import_panel()

    with st.container(border=True):
        if gmc_mode:
            _ensure_export_format_for_workflow(workflow_mode)
        elif rb_mode:
            export_format = render_redbubble_export_selector()
        elif trendsi_mode:
            export_format = render_trendsi_export_selector()
        elif shopify_mode:
            export_format = render_shopify_export_selector()
        elif workflow_mode == "filter-title":
            st.markdown("### Bước 2 · Xuất file (tuỳ chọn)")
            st.caption(
                "Sau khi lọc, SP khớp đã vào bảng bên dưới — chọn định dạng nếu muốn xuất Woo/Shopify/feed."
            )
            export_format = render_export_format_selector()
        elif workflow_mode == "filter-dedupe":
            st.markdown("### Bước 2 · File kết quả")
            st.caption(
                "File đã lọc lưu trong thư mục `output`. "
                "File nhỏ có nút tải ở Bước 1; file rất lớn mở trực tiếp trên ổ đĩa."
            )
        elif workflow_mode == "filter-proxy":
            st.markdown("### Bước 2 · Kết quả proxy")
            st.caption(
                "Kết quả lọc nằm ở Bước 1 (ô kết quả + nút tải). "
                "File `.txt` cũng lưu trong thư mục `output`."
            )
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

    if st.session_state.last_info:
        st.success(st.session_state.last_info)
    render_crawl_report_downloads()
    crawl_error = _current_crawl_error()
    if crawl_error:
        st.error(crawl_error)

    if workflow_mode in ("filter-dedupe", "filter-proxy"):
        return

    _render_data_status_banner()

    export_format = st.session_state.get("export_format", default_export_format(workflow_mode))
    gmc_mode = is_gmc_workflow(workflow_mode)

    if not gmc_mode:
        with st.container(border=True):
            render_customization_panel()

    if st.session_state.products_raw is not None:
        with st.container(border=True):
            render_variant_expansion_panel()

    with st.container(border=True):
        export_fields = render_export_fields_panel()

    if st.session_state.products_raw is not None:
        working_products = get_working_products()
        preview_format = export_format or "web-feed-csv"
        render_preview_section(
            working_products,
            preview_format,
            export_fields,
            build_export_rules(),
            image_mode=st.session_state.get("image_export_mode", "comma"),
            max_images_per_product=int(st.session_state.get("max_images_per_product", 0)),
        )
        if not export_format:
            st.caption("Xem trước mẫu feed web — chọn định dạng **Bước 2** để xuất đúng template.")

    export_clicked = False
    if st.session_state.edited_df is not None:
        want_url = bool(st.session_state.get("crawl_include_product_url"))
        has_url = "url" in st.session_state.edited_df.columns
        if want_url != has_url and not is_gmc_workflow():
            apply_edit_filter()
            st.rerun()
        if "_index" in st.session_state.edited_df.columns:
            st.session_state.edited_df = st.session_state.edited_df.rename(
                columns={"_index": "row_id"}
            )
        count = len(st.session_state.edited_df)
        total_raw = len(st.session_state.products_raw or [])
        edit_qty = int(st.session_state.get("edit_quantity", 0))
        edit_order = st.session_state.get("edit_order", "original")

        export_format = _resolved_export_format(workflow_mode)
        format_label = FORMAT_OPTIONS.get(export_format, export_format)
        is_gmc = export_format == "gmc-feed-csv"

        st.markdown(
            workflow_step_heading(
                6,
                "Chỉnh sửa feed GMC" if is_gmc else "Chỉnh sửa chi tiết",
                gmc=is_gmc,
            )
            + f" ({count} sản phẩm)"
        )
        if is_gmc:
            render_gmc_bulk_feed_block()
            _refresh_gmc_shop_brand_in_products()
        required_text = _required_fields_caption(export_format)
        if is_gmc:
            st.caption(
                "**Chỉnh hàng loạt feed** / **Title GMC** phía trên → **Áp dụng**. "
                "Chọn số lượng → **Áp dụng lọc**. "
                f"Bắt buộc: **{required_text}**. "
                "Tab **File feed xuất** = xem trước file GMC cuối (16 cột)."
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
                    "url": st.column_config.TextColumn(
                        "Link sản phẩm",
                        width="large",
                        help="URL sản phẩm nguồn (Shopify/Woo/Trendsi…)",
                    ),
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
        column_order = [
            c
            for c in edit_columns_for_format(
                export_format,
                include_url=bool(st.session_state.get("crawl_include_product_url")),
            )
            if c in edited_df.columns
        ]
        edited_df = edited_df[column_order]
        prev_fp = st.session_state.get("_edited_df_fingerprint")
        new_fp = edited_df.to_json()
        if prev_fp is not None and prev_fp != new_fp:
            st.session_state.output_bytes = None
            st.session_state.output_name = None
        st.session_state._edited_df_fingerprint = new_fp
        st.session_state.edited_df = edited_df

        with tab_feed:
            export_products = products_from_edit_table(
                st.session_state.products_raw,
                edited_df.to_dict("records"),
                export_format=export_format,
            )
            gmc_kw = _gmc_preview_kwargs() if is_gmc else {}
            feed_count = (
                count_gmc_export_rows(export_products, gmc_kw.get("gmc_variant_mode", "all_variations"))
                if is_gmc
                else count_shopify_export_rows(export_products)
                if export_format == "shopify-csv"
                else len(export_products)
            )
            if is_gmc:
                st.caption(
                    f"File GMC cuối — **{feed_count}** dòng · **{len(GMC_FEED_COLUMNS)}** cột "
                    f"(trùng file tải về) · age group mặc định **{GMC_AGE_GROUP_DEFAULT}** nếu để trống."
                )
            elif export_format == "shopify-csv":
                st.caption(
                    f"File Shopify cuối — **{feed_count}** dòng biến thể "
                    f"(mỗi Size/Color = 1 dòng · trùng file tải về)."
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
        export_ready = export_validation_allows_export(
            render_edit_validation_alert(validation_report)
        )

        jump_row_id = st.session_state.pop("edit_jump_row_id", None)
        jump_column = st.session_state.pop("edit_jump_column", None)
        if jump_row_id is not None:
            _scroll_edit_table_to_row(
                edited_df,
                int(jump_row_id),
                column=jump_column,
            )

        with st.container(border=True):
            export_clicked, progress_area, status_area, download_area = (
                render_export_actions(count, total_raw, export_ready=export_ready)
            )

        if export_clicked and not export_ready:
            st.session_state.last_error = (
                f"Không xuất file — còn cột thiếu dữ liệu. "
                f"Tick **Bỏ qua** ở Bước {edit_step_number(gmc=is_gmc)} hoặc điền đủ cột 🔴."
            )
            export_clicked = False

        rules = build_export_rules()

        if export_clicked:
            export_format = _resolved_export_format(workflow_mode)
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
