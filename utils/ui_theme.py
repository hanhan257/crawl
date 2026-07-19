"""Pastel pink UI theme for Streamlit app."""

from __future__ import annotations

import streamlit as st

from app_brand import APP_NAME, APP_TAGLINE, APP_VERSION

PASTEL_PINK_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&display=swap');

:root {
    --pink-50: #FFF8FA;
    --pink-100: #FFF0F5;
    --pink-150: #FCE7F0;
    --pink-200: #FBCFE8;
    --pink-300: #F9A8D4;
    --pink-400: #F472B6;
    --pink-500: #EC4899;
    --pink-600: #DB2777;
    --rose-text: #6B4A5A;
    --rose-dark: #4A3040;
    --rose-muted: #9D7A8A;
    --surface: #FFFFFF;
    --shadow-soft: 0 4px 24px rgba(236, 72, 153, 0.08);
    --shadow-btn: 0 6px 20px rgba(236, 72, 153, 0.28);
    --radius: 14px;
    --radius-sm: 10px;
}

.stApp {
    background: linear-gradient(165deg, #FFF8FA 0%, #FFF0F5 45%, #FDF2F8 100%);
    font-family: "DM Sans", sans-serif;
    color: var(--rose-text);
}

.block-container {
    padding-top: 1.5rem;
    padding-left: 1.75rem;
    padding-right: 2rem;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #FFF5F9 0%, #FFECF4 100%);
    border-right: 1px solid var(--pink-200);
}
[data-testid="stSidebar"] [data-testid="stMarkdown"] h1,
[data-testid="stSidebar"] [data-testid="stMarkdown"] h2 {
    color: var(--rose-dark);
    font-weight: 700;
}
[data-testid="stSidebar"] .stRadio > div {
    gap: 0.45rem;
}
[data-testid="stSidebar"] .stRadio label {
    background: var(--surface);
    border: 1.5px solid var(--pink-200);
    border-radius: var(--radius-sm);
    padding: 0.55rem 0.85rem !important;
    transition: all 0.2s ease;
    font-weight: 500;
}
[data-testid="stSidebar"] .stRadio label:hover {
    border-color: var(--pink-400);
    background: var(--pink-50);
}
[data-testid="stSidebar"] .stRadio label[data-checked="true"],
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label:has(input:checked) {
    background: linear-gradient(135deg, #FDF2F8, #FCE7F3) !important;
    border-color: var(--pink-500) !important;
    color: var(--pink-600) !important;
    font-weight: 700 !important;
    box-shadow: var(--shadow-soft);
}

/* ── Hero header ── */
.app-hero {
    background: linear-gradient(135deg, #FFFFFF 0%, #FFF5F9 55%, #FCE7F3 100%);
    border: 1.5px solid var(--pink-200);
    border-radius: 18px;
    padding: 1.35rem 1.6rem 1.2rem;
    margin-bottom: 1.25rem;
    box-shadow: var(--shadow-soft);
    position: relative;
    overflow: hidden;
}
.app-hero::before {
    content: "";
    position: absolute;
    top: -40px;
    right: -40px;
    width: 140px;
    height: 140px;
    background: radial-gradient(circle, rgba(244,114,182,0.18) 0%, transparent 70%);
    pointer-events: none;
}
.app-hero-badge {
    display: inline-block;
    background: linear-gradient(135deg, var(--pink-400), var(--pink-500));
    color: white;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 0.28rem 0.75rem;
    border-radius: 999px;
    margin-bottom: 0.55rem;
    box-shadow: 0 2px 10px rgba(236, 72, 153, 0.35);
}
.app-title {
    margin: 0 0 0.35rem 0 !important;
    font-size: 2rem !important;
    font-weight: 700 !important;
    background: linear-gradient(135deg, #BE185D, #EC4899, #F472B6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    line-height: 1.2 !important;
}
.app-subtitle {
    margin: 0;
    color: var(--rose-muted);
    font-size: 0.95rem;
    line-height: 1.55;
}
.app-subtitle strong {
    color: var(--pink-600);
    font-weight: 600;
}

/* ── Step headings ── */
.main h3, .main h2 {
    color: var(--rose-dark) !important;
    font-weight: 700 !important;
    border-left: 4px solid var(--pink-400);
    padding-left: 0.65rem !important;
    margin-top: 0.2rem !important;
}
.main h1:not(.app-title) {
    color: var(--rose-dark);
}

/* ── Bordered step cards ── */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--surface) !important;
    border: 1.5px solid var(--pink-200) !important;
    border-radius: var(--radius) !important;
    box-shadow: var(--shadow-soft) !important;
    padding: 0.25rem 0.1rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: var(--pink-300) !important;
}

/* ── Primary CTA buttons (Crawl, Xuất file, Tải) ── */
.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"] {
    background: linear-gradient(135deg, var(--pink-400) 0%, var(--pink-500) 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: var(--radius-sm) !important;
    font-weight: 700 !important;
    letter-spacing: 0.02em;
    box-shadow: var(--shadow-btn) !important;
    transition: transform 0.15s ease, box-shadow 0.15s ease !important;
}
.stButton > button[kind="primary"]:hover:not(:disabled),
.stDownloadButton > button[kind="primary"]:hover:not(:disabled) {
    transform: translateY(-1px);
    box-shadow: 0 8px 26px rgba(236, 72, 153, 0.38) !important;
    background: linear-gradient(135deg, var(--pink-500) 0%, var(--pink-600) 100%) !important;
}
.stButton > button[kind="primary"]:disabled {
    opacity: 0.55;
    box-shadow: none !important;
}

/* Secondary buttons */
.stButton > button[kind="secondary"] {
    background: var(--pink-50) !important;
    color: var(--pink-600) !important;
    border: 1.5px solid var(--pink-300) !important;
    border-radius: var(--radius-sm) !important;
    font-weight: 600 !important;
}
.stButton > button[kind="secondary"]:hover {
    background: var(--pink-100) !important;
    border-color: var(--pink-400) !important;
}

/* ── Alerts ── */
[data-testid="stAlert"] {
    border-radius: var(--radius-sm) !important;
    border-width: 1.5px !important;
}
[data-testid="stAlert"][data-baseweb="notification"] {
    border-radius: var(--radius-sm);
}
div[data-testid="stNotification"]:has([data-testid="stAlertContainer"]) {
    border-radius: var(--radius-sm);
}

/* Info */
[data-testid="stAlertContainer"] div[role="alert"][data-baseweb="notification"] {
    background-color: #FDF2F8;
    border-color: var(--pink-300);
    color: var(--rose-dark);
}

/* Success — nổi bật xuất file OK */
.stSuccess, [data-testid="stAlert"]:has(svg[aria-label="Success"]) {
    background: linear-gradient(135deg, #ECFDF5, #D1FAE5) !important;
    border-color: #6EE7B7 !important;
    color: #065F46 !important;
    font-weight: 600;
}

/* Warning */
.stWarning {
    background: #FFF7ED !important;
    border-color: #FDBA74 !important;
    color: #9A3412 !important;
}

/* Error — cột thiếu */
.stError {
    background: #FFF1F2 !important;
    border-color: #FDA4AF !important;
    color: #BE123C !important;
    font-weight: 600;
}

/* ── Inputs ── */
.stTextInput input, .stNumberInput input, .stTextArea textarea,
.stSelectbox [data-baseweb="select"] > div {
    border-radius: var(--radius-sm) !important;
    border-color: var(--pink-200) !important;
    background: var(--surface) !important;
}
.stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
    border-color: var(--pink-400) !important;
    box-shadow: 0 0 0 3px rgba(244, 114, 182, 0.18) !important;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.35rem;
    background: var(--pink-50);
    border-radius: var(--radius-sm);
    padding: 0.3rem;
    border: 1px solid var(--pink-200);
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px !important;
    color: var(--rose-muted) !important;
    font-weight: 600 !important;
}
.stTabs [aria-selected="true"] {
    background: white !important;
    color: var(--pink-600) !important;
    box-shadow: 0 2px 8px rgba(236, 72, 153, 0.12) !important;
}

/* ── Radio horizontal (Bước 2) ── */
.stRadio > div[role="radiogroup"][aria-label] {
    gap: 0.5rem;
}

/* ── Dataframe / editor ── */
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    border-radius: var(--radius-sm);
    border: 1px solid var(--pink-200);
    overflow: hidden;
}

/* ── Expander ── */
.streamlit-expanderHeader {
    background: var(--pink-50) !important;
    border-radius: var(--radius-sm) !important;
    font-weight: 600 !important;
    color: var(--rose-dark) !important;
}

/* ── Progress bar ── */
.stProgress > div > div {
    background: linear-gradient(90deg, var(--pink-300), var(--pink-500)) !important;
    border-radius: 999px;
}

/* ── Download highlight block ── */
.download-ready-banner {
    background: linear-gradient(135deg, #ECFDF5 0%, #D1FAE5 100%);
    border: 2px solid #6EE7B7;
    border-radius: var(--radius);
    padding: 1rem 1.2rem;
    margin: 0.5rem 0;
    box-shadow: 0 4px 16px rgba(16, 185, 129, 0.12);
}
.download-ready-banner strong {
    color: #047857;
}

.crawl-busy-btn {
    display: block;
    width: 100%;
    padding: 0.72rem 1rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--pink-300);
    background: linear-gradient(135deg, #FDF2F8, #FBCFE8);
    color: var(--pink-600);
    font-weight: 700;
    text-align: center;
    box-shadow: var(--shadow-soft);
    opacity: 0.92;
}

/* ── Validation missing labels ── */
.val-missing {
    color: #BE123C;
    font-weight: 700;
}
.val-missing-count {
    color: #E11D48;
}

/* ── Category pills ── */
[data-testid="stPills"] button {
    border-radius: 999px !important;
    border-color: var(--pink-200) !important;
    font-weight: 600 !important;
}
[data-testid="stPills"] button[aria-pressed="true"] {
    background: linear-gradient(135deg, var(--pink-400), var(--pink-500)) !important;
    color: white !important;
    border-color: transparent !important;
}

/* ── Caption ── */
.stCaption, small {
    color: var(--rose-muted) !important;
}

/* Hide Streamlit footer branding */
footer { visibility: hidden; }
#MainMenu { visibility: hidden; }

@media (max-width: 768px) {
    .app-title { font-size: 1.55rem !important; }
    .app-hero { padding: 1rem 1.1rem; }
}

/* ── Compact product preview row (Bước xem trước) ── */
.preview-chip-title {
    font-size: 0.68rem;
    line-height: 1.25;
    margin: 0.15rem 0 0;
    font-weight: 600;
    color: var(--rose-dark);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.preview-chip-price {
    font-size: 0.62rem;
    color: var(--rose-muted);
    margin: 0;
    line-height: 1.2;
}
.preview-compact-wrap [data-testid="stImage"] {
    margin-bottom: 0.15rem;
}
.preview-compact-wrap [data-testid="stImage"] img {
    border-radius: 8px;
    object-fit: cover;
    aspect-ratio: 1;
}

/* ── Bước 1 crawl — 2 cột + hàng 1·2·3 + nút giữa ── */
.crawl-step1-wrap {
    margin-top: 0.25rem;
}
.crawl-step1-wrap [data-testid="column"] {
    align-self: flex-start;
}
.crawl-limits-row {
    margin: 0.65rem 0 0.35rem;
    padding: 0.65rem 0.85rem 0.45rem;
    background: linear-gradient(180deg, #FFFFFF 0%, #FFF8FB 100%);
    border: 1px solid var(--pink-200);
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-soft);
}
.crawl-num-field {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin-bottom: 0.15rem;
    min-height: 1.35rem;
}
.crawl-num {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.15rem;
    height: 1.15rem;
    border-radius: 999px;
    background: linear-gradient(135deg, var(--pink-400), var(--pink-500));
    color: #fff;
    font-size: 0.62rem;
    font-weight: 700;
    flex-shrink: 0;
    box-shadow: 0 2px 6px rgba(236, 72, 153, 0.35);
}
.crawl-num-text {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--rose-dark);
    line-height: 1.2;
}
.crawl-limits-row [data-testid="stNumberInput"] {
    margin-bottom: 0;
}
.crawl-limits-row [data-testid="stNumberInput"] input {
    min-height: 2rem;
    padding-top: 0.25rem;
    padding-bottom: 0.25rem;
    font-size: 0.85rem;
}

/* ── GMC bulk feed — 1 hàng gọn ── */
.gmc-bulk-wrap {
    margin: -0.15rem 0 0.15rem;
}
.gmc-bulk-wrap .stCaption {
    margin-bottom: 0.35rem !important;
    font-size: 0.78rem !important;
    line-height: 1.35 !important;
}
.gmc-bulk-wrap [data-testid="stHorizontalBlock"] {
    gap: 0.35rem !important;
    align-items: flex-end !important;
}
.gmc-bulk-wrap [data-testid="column"] {
    min-width: 0;
}
.gmc-bulk-wrap label p,
.gmc-bulk-wrap [data-testid="stWidgetLabel"] p {
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    color: var(--rose-dark) !important;
    margin-bottom: 0.15rem !important;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.gmc-bulk-wrap [data-testid="stTextInput"] input,
.gmc-bulk-wrap [data-testid="stSelectbox"] [data-baseweb="select"] > div {
    min-height: 2rem !important;
    font-size: 0.82rem !important;
    padding-top: 0.25rem !important;
    padding-bottom: 0.25rem !important;
}
.gmc-bulk-wrap [data-testid="stCheckbox"] {
    padding-bottom: 0.45rem;
}
.gmc-bulk-wrap [data-testid="stCheckbox"] label p {
    font-size: 0.72rem !important;
    white-space: nowrap;
}
@media (max-width: 1100px) {
    .gmc-bulk-wrap [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
    }
    .gmc-bulk-wrap [data-testid="column"] {
        flex: 1 1 22% !important;
        min-width: 6.5rem !important;
    }
}

/* ── GMC title bulk ── */
.gmc-title-bulk-wrap {
    margin: 0.45rem 0 0.25rem;
    padding: 0.45rem 0 0.15rem;
    border-top: 1px dashed var(--pink-200);
}
.gmc-title-bulk-wrap .stCaption {
    margin-bottom: 0.3rem !important;
    font-size: 0.76rem !important;
}
.gmc-title-bulk-wrap [data-testid="stHorizontalBlock"] {
    gap: 0.35rem !important;
    align-items: flex-end !important;
}
.gmc-title-bulk-wrap label p,
.gmc-title-bulk-wrap [data-testid="stWidgetLabel"] p {
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    color: var(--rose-dark) !important;
}
.gmc-title-bulk-wrap [data-testid="stTextInput"] input {
    min-height: 2rem !important;
    font-size: 0.82rem !important;
}

.crawl-action-center {
    margin-top: 0.85rem;
    padding-top: 0.85rem;
    border-top: 1px dashed var(--pink-200);
}
.crawl-action-center [data-testid="stButton"] {
    display: flex;
    justify-content: center;
}
.crawl-action-center [data-testid="stButton"] button {
    min-width: 240px;
    max-width: 360px;
    font-weight: 700 !important;
    letter-spacing: 0.01em;
    box-shadow: var(--shadow-btn) !important;
}
.crawl-step1-wrap .crawl-side-tools [data-testid="stButton"] button {
    min-height: 2rem;
    font-size: 0.78rem !important;
    padding: 0.35rem 0.65rem !important;
    background: var(--pink-50) !important;
    border: 1px solid var(--pink-200) !important;
    color: var(--rose-dark) !important;
    box-shadow: none !important;
}
.crawl-step1-wrap .crawl-side-tools [data-testid="stButton"] button:hover {
    border-color: var(--pink-400) !important;
    background: #FFF0F7 !important;
}
@media (max-width: 900px) {
    .crawl-limits-row [data-testid="column"] {
        min-width: 0;
    }
    .crawl-action-center [data-testid="stButton"] button {
        min-width: 100%;
        max-width: 100%;
    }
}
</style>
"""


def inject_pastel_pink_theme() -> None:
    st.markdown(PASTEL_PINK_CSS, unsafe_allow_html=True)


def render_app_hero(
    *,
    gmc_mode: bool = False,
    redbubble_mode: bool = False,
    trendsi_mode: bool = False,
    **_unused,
) -> None:
    if gmc_mode:
        subtitle = (
            "<strong>Quy trình GMC:</strong> "
            "Bước 1 Crawl → Bước 2 trường feed → Bước 3 xem trước → "
            "Bước 4 chỉnh feed → Bước 5 xuất file Google Merchant"
        )
        badge = "Google Merchant Center"
    elif redbubble_mode:
        subtitle = (
            "<strong>Quy trình Redbubble:</strong> Bước 1 Crawl `/shop/...` → "
            "<strong>Bước 2 chọn file xuất</strong> → "
            "Đổi giá / title → Xem trước → "
            "<strong>Xuất feed web / Woo / Shopify</strong>"
        )
        badge = "Redbubble"
    elif trendsi_mode:
        subtitle = (
            "<strong>Quy trình Trendsi:</strong> Bước 1 Crawl brand / category / SP → "
            "<strong>Bước 2 chọn file xuất</strong> → "
            "Đổi giá / title → Xem trước → "
            "<strong>Xuất web thuần / WooCommerce / WordPress</strong> "
            "(Size &amp; Fit HTML trong description)"
        )
        badge = "Trendsi"
    else:
        subtitle = (
            "<strong>Quy trình:</strong> Bước 1 Crawl / Import → "
            "<strong>Bước 2 chọn file xuất</strong> → "
            "Tùy chỉnh → Xem trước → "
            "<strong>Xuất Woo / Shopify / Feed web</strong>"
        )
        badge = f"v{APP_VERSION}"

    st.markdown(
        f"""
        <div class="app-hero">
            <div class="app-hero-badge">{badge}</div>
            <h1 class="app-title">{APP_NAME}</h1>
            <p class="app-subtitle">{APP_TAGLINE}<br>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
