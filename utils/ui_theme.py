"""Boutique rose UI theme for Streamlit app."""

from __future__ import annotations

import streamlit as st

from app_brand import APP_NAME, APP_TAGLINE, APP_VERSION

PASTEL_PINK_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');

:root {
    --rose-50: #FDF8FA;
    --rose-100: #F5E6EC;
    --rose-200: #E8C4D0;
    --rose-300: #D49AAD;
    --rose-400: #C9577A;
    --rose-500: #A84363;
    --rose-600: #8B3452;
    --ink: #2D1F28;
    --ink-muted: #7A6270;
    --surface: #FFFFFF;
    --canvas: #FAF7F9;
    --shadow-sm: 0 1px 3px rgba(45, 31, 40, 0.07);
    --shadow-md: 0 10px 40px rgba(168, 67, 99, 0.11);
    --shadow-btn: 0 4px 14px rgba(168, 67, 99, 0.28);
    --radius: 16px;
    --radius-sm: 12px;
    /* aliases — giữ tương thích class cũ */
    --pink-50: var(--rose-50);
    --pink-100: var(--rose-100);
    --pink-200: var(--rose-200);
    --pink-300: var(--rose-300);
    --pink-400: var(--rose-400);
    --pink-500: var(--rose-500);
    --pink-600: var(--rose-600);
    --pink-700: #6E2840;
    --rose-text: var(--ink-muted);
    --rose-dark: var(--ink);
    --rose-muted: var(--ink-muted);
    --shadow-soft: var(--shadow-md);
}

html, body, [class*="css"] {
    font-family: "Outfit", "Segoe UI", sans-serif !important;
}

.stApp {
    background-color: var(--canvas);
    background-image:
        radial-gradient(circle at 12% 8%, rgba(201, 87, 122, 0.07) 0%, transparent 42%),
        radial-gradient(circle at 88% 92%, rgba(168, 67, 99, 0.05) 0%, transparent 38%);
    font-family: "Outfit", "Segoe UI", sans-serif;
    color: var(--ink-muted);
}

/* Ẩn chrome Streamlit */
header[data-testid="stHeader"] {
    background: transparent !important;
    height: 0 !important;
    min-height: 0 !important;
    visibility: hidden !important;
    pointer-events: none !important;
}
[data-testid="stDecoration"],
[data-testid="stToolbar"],
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"],
button[kind="headerNoPadding"],
button[kind="header"] {
    display: none !important;
    visibility: hidden !important;
    pointer-events: none !important;
}
[data-testid="stAppViewContainer"] {
    padding-top: 0 !important;
    margin-top: 0 !important;
    display: flex !important;
    flex-wrap: nowrap !important;
    overflow-x: hidden !important;
}

.block-container {
    padding-top: 1.1rem;
    padding-left: 1.5rem;
    padding-right: 1.75rem;
}

/* ── Layout cố định 30 / 70 ── */
[data-testid="stAppViewContainer"] > div:has(> section[data-testid="stSidebar"]),
[data-testid="stAppViewContainer"] > .react-resizable:has([data-testid="stSidebar"]) {
    flex: 0 0 30vw !important;
    min-width: 30vw !important;
    max-width: 30vw !important;
    width: 30vw !important;
    resize: none !important;
}
[data-testid="stSidebar"],
[data-testid="stSidebar"][aria-expanded="true"],
[data-testid="stSidebar"][aria-expanded="false"] {
    flex: 0 0 30vw !important;
    min-width: 30vw !important;
    max-width: 30vw !important;
    width: 30vw !important;
    transform: none !important;
    resize: none !important;
    transition: none !important;
    background: var(--surface) !important;
    border-right: none !important;
    border-left: 4px solid var(--rose-400) !important;
    box-shadow: var(--shadow-md) !important;
    cursor: default !important;
}
[data-testid="stSidebar"] > div:first-child {
    width: 100% !important;
    padding: 1.25rem 1rem 1rem 1.1rem !important;
}
[data-testid="stMain"] {
    flex: 0 0 70vw !important;
    min-width: 70vw !important;
    width: 70vw !important;
    max-width: 70vw !important;
    padding-top: 0.5rem !important;
    background: transparent !important;
}
[data-testid="stMain"] .block-container {
    max-width: 100% !important;
}
[data-testid="stSidebar"] .react-resizable-handle,
.react-resizable-handle,
.react-resizable-handle-e,
[data-testid="stSidebarResizeHandle"] {
    display: none !important;
    pointer-events: none !important;
}

/* ── Sidebar menu ── */
[data-testid="stSidebar"] [data-testid="stMarkdown"] h1,
[data-testid="stSidebar"] [data-testid="stMarkdown"] h2,
[data-testid="stSidebar"] [data-testid="stMarkdown"] h3 {
    color: var(--ink) !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em;
}
[data-testid="stSidebar"] .stRadio > div {
    gap: 0.35rem;
}
[data-testid="stSidebar"] .stRadio label {
    background: var(--rose-50) !important;
    border: 1px solid var(--rose-100) !important;
    border-radius: var(--radius-sm) !important;
    padding: 0.6rem 0.9rem !important;
    font-weight: 500 !important;
    font-size: 0.86rem !important;
    line-height: 1.35 !important;
    white-space: normal !important;
    color: var(--ink-muted) !important;
    transition: background 0.15s, border-color 0.15s, color 0.15s !important;
    box-shadow: none !important;
}
[data-testid="stSidebar"] .stRadio label:hover {
    border-color: var(--rose-300) !important;
    background: var(--rose-100) !important;
    color: var(--ink) !important;
}
[data-testid="stSidebar"] .stRadio label[data-checked="true"],
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label:has(input:checked) {
    background: var(--rose-500) !important;
    border-color: var(--rose-500) !important;
    color: #fff !important;
    font-weight: 600 !important;
    box-shadow: var(--shadow-btn) !important;
}

/* ── Hero ── */
.app-hero {
    background: var(--surface);
    border: 1px solid var(--rose-100);
    border-top: 3px solid var(--rose-400);
    border-radius: var(--radius);
    padding: 1.4rem 1.65rem 1.25rem;
    margin-bottom: 1.1rem;
    box-shadow: var(--shadow-sm);
    position: relative;
}
.app-hero-badge {
    display: inline-block;
    background: var(--rose-50);
    color: var(--rose-500);
    border: 1px solid var(--rose-200);
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0.25rem 0.7rem;
    border-radius: 999px;
    margin-bottom: 0.6rem;
}
.app-title {
    margin: 0 0 0.4rem 0 !important;
    font-size: 1.95rem !important;
    font-weight: 700 !important;
    font-family: "Outfit", sans-serif !important;
    color: var(--ink) !important;
    -webkit-text-fill-color: var(--ink) !important;
    background: none !important;
    line-height: 1.15 !important;
    letter-spacing: -0.03em;
}
.app-subtitle {
    margin: 0;
    color: var(--ink-muted);
    font-size: 0.92rem;
    line-height: 1.6;
    font-weight: 400;
}
.app-subtitle strong {
    color: var(--rose-500);
    font-weight: 600;
}

/* ── Step headings ── */
.main h3, .main h2 {
    color: var(--ink) !important;
    font-weight: 700 !important;
    font-size: 1.05rem !important;
    letter-spacing: -0.01em;
    border-left: none !important;
    padding-left: 0 !important;
    padding-bottom: 0.45rem !important;
    margin-top: 0.15rem !important;
    border-bottom: 2px solid var(--rose-100) !important;
}
.main h1:not(.app-title) {
    color: var(--ink);
}

/* ── Step cards ── */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--surface) !important;
    border: 1px solid var(--rose-100) !important;
    border-radius: var(--radius) !important;
    box-shadow: var(--shadow-sm) !important;
    padding: 0.35rem 0.15rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: var(--rose-200) !important;
    box-shadow: var(--shadow-md) !important;
}

/* ── Buttons ── */
.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"] {
    background: var(--rose-500) !important;
    color: #fff !important;
    border: none !important;
    border-radius: 999px !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em;
    padding: 0.55rem 1.4rem !important;
    box-shadow: var(--shadow-btn) !important;
    transition: background 0.15s, transform 0.15s, box-shadow 0.15s !important;
}
.stButton > button[kind="primary"]:hover:not(:disabled),
.stDownloadButton > button[kind="primary"]:hover:not(:disabled) {
    background: var(--rose-600) !important;
    transform: translateY(-1px);
    box-shadow: 0 6px 18px rgba(168, 67, 99, 0.35) !important;
}
.stButton > button[kind="primary"]:disabled {
    opacity: 0.5;
    box-shadow: none !important;
}
.stButton > button[kind="secondary"] {
    background: var(--surface) !important;
    color: var(--rose-500) !important;
    border: 1.5px solid var(--rose-200) !important;
    border-radius: 999px !important;
    font-weight: 600 !important;
}
.stButton > button[kind="secondary"]:hover {
    background: var(--rose-50) !important;
    border-color: var(--rose-400) !important;
}

/* ── Alerts ── */
[data-testid="stAlert"] {
    border-radius: var(--radius-sm) !important;
}
[data-testid="stAlertContainer"] div[role="alert"][data-baseweb="notification"] {
    background-color: var(--rose-50);
    border-color: var(--rose-200);
    color: var(--ink);
}
.stSuccess, [data-testid="stAlert"]:has(svg[aria-label="Success"]) {
    background: #F0FDF4 !important;
    border-color: #86EFAC !important;
    color: #166534 !important;
    font-weight: 600;
}
.stWarning {
    background: #FFFBEB !important;
    border-color: #FCD34D !important;
    color: #92400E !important;
}
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
    border-color: var(--rose-200) !important;
    background: var(--surface) !important;
    font-family: "Outfit", sans-serif !important;
}
.stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
    border-color: var(--rose-400) !important;
    box-shadow: 0 0 0 3px rgba(201, 87, 122, 0.15) !important;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.25rem;
    background: var(--rose-50);
    border-radius: 999px;
    padding: 0.25rem;
    border: 1px solid var(--rose-100);
}
.stTabs [data-baseweb="tab"] {
    border-radius: 999px !important;
    color: var(--ink-muted) !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
}
.stTabs [aria-selected="true"] {
    background: var(--surface) !important;
    color: var(--rose-500) !important;
    box-shadow: var(--shadow-sm) !important;
}

.stRadio > div[role="radiogroup"][aria-label] {
    gap: 0.45rem;
}

[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    border-radius: var(--radius-sm);
    border: 1px solid var(--rose-100);
    overflow: hidden;
}

.streamlit-expanderHeader {
    background: var(--rose-50) !important;
    border-radius: var(--radius-sm) !important;
    font-weight: 600 !important;
    color: var(--ink) !important;
}

.stProgress > div > div {
    background: var(--rose-400) !important;
    border-radius: 999px;
}

.download-ready-banner {
    background: #F0FDF4;
    border: 1.5px solid #86EFAC;
    border-radius: var(--radius);
    padding: 1rem 1.2rem;
    margin: 0.5rem 0;
}
.download-ready-banner strong { color: #047857; }

.crawl-busy-btn {
    display: block;
    width: 100%;
    padding: 0.72rem 1rem;
    border-radius: 999px;
    border: 1px solid var(--rose-200);
    background: var(--rose-50);
    color: var(--rose-500);
    font-weight: 600;
    text-align: center;
}

.val-missing { color: #BE123C; font-weight: 700; }
.val-missing-count { color: #E11D48; }

[data-testid="stPills"] button {
    border-radius: 999px !important;
    border-color: var(--rose-200) !important;
    font-weight: 600 !important;
}
[data-testid="stPills"] button[aria-pressed="true"] {
    background: var(--rose-500) !important;
    color: white !important;
    border-color: transparent !important;
}

.stCaption, small {
    color: var(--ink-muted) !important;
    font-size: 0.84rem !important;
}

footer, #MainMenu { visibility: hidden; }

@media (max-width: 768px) {
    .app-title { font-size: 1.5rem !important; }
    .app-hero { padding: 1rem 1.15rem; }
}

/* ── Preview chips ── */
.preview-chip-title {
    font-size: 0.68rem;
    line-height: 1.25;
    margin: 0.15rem 0 0;
    font-weight: 600;
    color: var(--ink);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.preview-chip-price {
    font-size: 0.62rem;
    color: var(--ink-muted);
    margin: 0;
}
.preview-compact-wrap [data-testid="stImage"] img {
    border-radius: 10px;
    object-fit: cover;
    aspect-ratio: 1;
}

/* ── Crawl step 1 ── */
.crawl-step1-wrap { margin-top: 0.2rem; }
.crawl-step1-wrap [data-testid="column"] { align-self: flex-start; }
.crawl-limits-row {
    margin: 0.6rem 0 0.3rem;
    padding: 0.7rem 0.9rem 0.5rem;
    background: var(--rose-50);
    border: 1px solid var(--rose-100);
    border-radius: var(--radius-sm);
}
.crawl-num-field {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin-bottom: 0.15rem;
}
.crawl-num {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.2rem;
    height: 1.2rem;
    border-radius: 999px;
    background: var(--rose-500);
    color: #fff;
    font-size: 0.62rem;
    font-weight: 700;
    flex-shrink: 0;
}
.crawl-num-text {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--ink);
}
.crawl-limits-row [data-testid="stNumberInput"] { margin-bottom: 0; }
.crawl-limits-row [data-testid="stNumberInput"] input {
    min-height: 2rem;
    font-size: 0.85rem;
}

.gmc-bulk-wrap { margin: -0.15rem 0 0.15rem; }
.gmc-bulk-wrap .stCaption {
    margin-bottom: 0.35rem !important;
    font-size: 0.78rem !important;
}
.gmc-bulk-wrap [data-testid="stHorizontalBlock"] {
    gap: 0.35rem !important;
    align-items: flex-end !important;
}
.gmc-bulk-wrap [data-testid="column"] { min-width: 0; }
.gmc-bulk-wrap label p,
.gmc-bulk-wrap [data-testid="stWidgetLabel"] p {
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    color: var(--ink) !important;
}
.gmc-bulk-wrap [data-testid="stTextInput"] input,
.gmc-bulk-wrap [data-testid="stSelectbox"] [data-baseweb="select"] > div {
    min-height: 2rem !important;
    font-size: 0.82rem !important;
}

.gmc-title-bulk-wrap {
    margin: 0.45rem 0 0.25rem;
    padding: 0.45rem 0 0.15rem;
    border-top: 1px dashed var(--rose-200);
}
.gmc-title-bulk-wrap label p,
.gmc-title-bulk-wrap [data-testid="stWidgetLabel"] p {
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    color: var(--ink) !important;
}

.crawl-action-center {
    margin-top: 0.85rem;
    padding-top: 0.85rem;
    border-top: 1px dashed var(--rose-200);
}
.crawl-action-center [data-testid="stButton"] {
    display: flex;
    justify-content: center;
}
.crawl-action-center [data-testid="stButton"] button {
    min-width: 240px;
    max-width: 360px;
    border-radius: 999px !important;
}
.crawl-step1-wrap .crawl-side-tools [data-testid="stButton"] button {
    min-height: 2rem;
    font-size: 0.78rem !important;
    border-radius: 999px !important;
    background: var(--surface) !important;
    border: 1px solid var(--rose-200) !important;
    color: var(--ink) !important;
    box-shadow: none !important;
}
.crawl-step1-wrap .crawl-side-tools [data-testid="stButton"] button:hover {
    border-color: var(--rose-400) !important;
    background: var(--rose-50) !important;
}

@media (max-width: 1100px) {
    .gmc-bulk-wrap [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
    .gmc-bulk-wrap [data-testid="column"] {
        flex: 1 1 22% !important;
        min-width: 6.5rem !important;
    }
}
@media (max-width: 900px) {
    [data-testid="stSidebar"],
    [data-testid="stSidebar"][aria-expanded="true"],
    [data-testid="stSidebar"][aria-expanded="false"] {
        flex: 0 0 30vw !important;
        min-width: 30vw !important;
        max-width: 30vw !important;
        width: 30vw !important;
    }
    [data-testid="stMain"] {
        flex: 0 0 70vw !important;
        min-width: 70vw !important;
        width: 70vw !important;
        max-width: 70vw !important;
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
    st.components.v1.html(
        """
        <script>
        (function () {
            const doc = window.parent.document;
            const lock = () => {
                try { window.parent.localStorage.removeItem("sidebarWidth"); } catch (e) {}
                const sidebar = doc.querySelector('[data-testid="stSidebar"]');
                if (!sidebar) return;
                sidebar.setAttribute("aria-expanded", "true");
                [sidebar.closest(".react-resizable"), sidebar].filter(Boolean).forEach((el) => {
                    el.style.setProperty("width", "30vw", "important");
                    el.style.setProperty("min-width", "30vw", "important");
                    el.style.setProperty("max-width", "30vw", "important");
                    el.style.setProperty("flex", "0 0 30vw", "important");
                });
                doc.querySelectorAll(".react-resizable-handle").forEach((handle) => {
                    handle.style.display = "none";
                    handle.style.pointerEvents = "none";
                });
            };
            lock();
            doc.addEventListener("mouseup", lock, true);
            doc.addEventListener("touchend", lock, true);
            window.setInterval(lock, 1000);
        })();
        </script>
        """,
        height=0,
    )


def render_app_hero(
    *,
    gmc_mode: bool = False,
    redbubble_mode: bool = False,
    shopify_mode: bool = False,
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
    elif shopify_mode:
        subtitle = (
            "<strong>Quy trình Shopify:</strong> Bước 1 Crawl shop (trang chủ / collection / SP) → "
            "<strong>Bước 2 CSV import Shopify</strong> → "
            "Đổi giá / title → Xem trước → "
            "<strong>Tải file Shopify</strong> (template 48 cột, Color metafield)"
        )
        badge = "Shopify"
    elif trendsi_mode:
        subtitle = (
            "<strong>Quy trình Trendsi:</strong> Chỉ link <strong>trendsi.com</strong> · "
            "Bước 1 Crawl brand / category / SP → "
            "<strong>Bước 2 chọn file xuất</strong> → "
            "Đổi giá / title → Xem trước → "
            "<strong>Xuất web thuần / WooCommerce / Shopify</strong> "
            "(Size &amp; Fit HTML trong description; Shopify có cột Color metafield)"
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
