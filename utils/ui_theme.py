"""Boutique rose UI theme for Streamlit app."""

from __future__ import annotations

import streamlit as st

from app_brand import APP_NAME, APP_TAGLINE, APP_VERSION, app_build_id

PASTEL_PINK_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root {
    /* Brand tone — #FF7675 */
    --brand: #FF7675;
    --brand-rgb: 255, 118, 117;
    --coral-50: #FFF5F5;
    --coral-100: #FFE3E3;
    --coral-200: #FFCACA;
    --coral-300: #FFABAA;
    --coral-400: #FF9190;
    --coral-500: var(--brand);
    --coral-600: #F05F5E;
    --coral-700: #E04F4E;
    --coral-800: #C94645;
    --coral-900: #B33D3C;
    --rose-50: var(--coral-50);
    --rose-100: var(--coral-100);
    --rose-200: var(--coral-200);
    --rose-300: var(--coral-300);
    --rose-400: var(--coral-400);
    --rose-500: var(--coral-500);
    --rose-600: var(--coral-600);
    --ink: #1A1A1A;
    --ink-muted: #9CA3AF;
    --surface: #FFFFFF;
    --canvas: #FFF5F5;
    --grid-line: rgba(var(--brand-rgb), 0.12);
    --border-soft: rgba(var(--brand-rgb), 0.22);
    --shadow-sm: 0 1px 3px rgba(26, 26, 26, 0.04);
    --shadow-md: 0 4px 18px rgba(var(--brand-rgb), 0.12);
    --shadow-btn: 0 2px 10px rgba(var(--brand-rgb), 0.32);
    --radius: 14px;
    --radius-sm: 10px;
    --radius-pill: 999px;
    /* aliases — giữ tương thích class cũ */
    --pink-50: var(--coral-50);
    --pink-100: var(--coral-100);
    --pink-200: var(--coral-200);
    --pink-300: var(--coral-300);
    --pink-400: var(--coral-400);
    --pink-500: var(--coral-500);
    --pink-600: var(--coral-600);
    --pink-700: var(--coral-700);
    --sidebar-bg: linear-gradient(180deg, var(--coral-700) 0%, var(--coral-800) 52%, var(--coral-900) 100%);
    --sidebar-text: #FFFFFF;
    --sidebar-muted: rgba(255, 255, 255, 0.88);
    --sidebar-item-bg: rgba(var(--brand-rgb), 0.12);
    --sidebar-item-border: rgba(var(--brand-rgb), 0.35);
    --sidebar-item-hover: rgba(var(--brand-rgb), 0.22);
    --sidebar-item-active-bg: var(--brand);
    --sidebar-item-active-text: #FFFFFF;
    --sidebar-item-active-shadow: 0 4px 16px rgba(var(--brand-rgb), 0.45), 0 0 0 1px rgba(255, 255, 255, 0.22);
    --rose-text: var(--ink-muted);
    --rose-dark: var(--ink);
    --rose-muted: var(--ink-muted);
    --shadow-soft: var(--shadow-md);
    --shadow-sidebar: 4px 0 22px rgba(var(--brand-rgb), 0.28);
}

html, body, [class*="css"] {
    font-family: "Inter", "Segoe UI", sans-serif !important;
    -webkit-font-smoothing: antialiased;
}

.stApp {
    background-color: var(--canvas);
    background-image:
        linear-gradient(var(--grid-line) 1px, transparent 1px),
        linear-gradient(90deg, var(--grid-line) 1px, transparent 1px),
        radial-gradient(ellipse 55% 45% at 92% 8%, rgba(var(--brand-rgb), 0.08) 0%, transparent 70%),
        radial-gradient(ellipse 40% 35% at 8% 95%, rgba(var(--brand-rgb), 0.06) 0%, transparent 65%);
    background-size: 22px 22px, 22px 22px, 100% 100%, 100% 100%;
    font-family: "Inter", "Segoe UI", sans-serif;
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

/* ── Sidebar chung (desktop + mobile) ── */
[data-testid="stSidebar"],
[data-testid="stSidebar"][aria-expanded="true"],
[data-testid="stSidebar"][aria-expanded="false"] {
    transform: none !important;
    resize: none !important;
    transition: none !important;
    background: var(--sidebar-bg) !important;
    border-left: none !important;
    box-shadow: var(--shadow-sidebar) !important;
    cursor: default !important;
    color: var(--sidebar-text) !important;
}

/* ── Desktop: layout cố định 20 / 80 ── */
@media (min-width: 901px) {
    [data-testid="stAppViewContainer"] > div:has(> section[data-testid="stSidebar"]),
    [data-testid="stAppViewContainer"] > .react-resizable:has([data-testid="stSidebar"]) {
        flex: 0 0 20vw !important;
        min-width: 20vw !important;
        max-width: 20vw !important;
        width: 20vw !important;
        resize: none !important;
    }
    [data-testid="stSidebar"],
    [data-testid="stSidebar"][aria-expanded="true"],
    [data-testid="stSidebar"][aria-expanded="false"] {
        flex: 0 0 20vw !important;
        min-width: 20vw !important;
        max-width: 20vw !important;
        width: 20vw !important;
        border-right: 1px solid rgba(255, 255, 255, 0.08) !important;
    }
    [data-testid="stMain"] {
        flex: 0 0 80vw !important;
        min-width: 80vw !important;
        width: 80vw !important;
        max-width: 80vw !important;
        padding-top: 0.5rem !important;
        background: transparent !important;
    }
}
[data-testid="stSidebar"] > div:first-child {
    width: 100% !important;
    padding: 1.35rem 0.9rem 1.1rem 1rem !important;
    background: transparent !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdown"] p,
[data-testid="stSidebar"] [data-testid="stMarkdown"] li {
    color: var(--sidebar-muted) !important;
}
[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
    color: var(--sidebar-muted) !important;
    font-size: 0.82rem !important;
    font-weight: 500 !important;
}
[data-testid="stSidebar"] .streamlit-expanderHeader {
    background: rgba(255, 255, 255, 0.1) !important;
    border: 1px solid rgba(255, 255, 255, 0.32) !important;
    color: var(--sidebar-text) !important;
    font-weight: 600 !important;
    font-size: 0.86rem !important;
    border-radius: var(--radius-sm) !important;
    padding: 0.55rem 0.75rem !important;
}
[data-testid="stSidebar"] .streamlit-expanderHeader:hover {
    background: rgba(255, 255, 255, 0.16) !important;
    border-color: rgba(255, 255, 255, 0.42) !important;
}
[data-testid="stSidebar"] [data-testid="stAlert"] {
    background: rgba(255, 255, 255, 0.12) !important;
    border-color: rgba(255, 255, 255, 0.2) !important;
    color: var(--sidebar-text) !important;
}
[data-testid="stSidebar"] .stButton > button {
    background: var(--sidebar-item-bg) !important;
    border: 1px solid var(--sidebar-item-border) !important;
    color: var(--sidebar-text) !important;
    border-radius: var(--radius-pill) !important;
    font-size: 0.84rem !important;
    font-weight: 500 !important;
    line-height: 1.35 !important;
    white-space: normal !important;
    min-height: 2.35rem !important;
    text-align: left !important;
    padding: 0.55rem 0.85rem !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: var(--sidebar-item-active-bg) !important;
    border-color: rgba(255, 255, 255, 0.35) !important;
    color: #fff !important;
    font-weight: 700 !important;
    box-shadow: var(--sidebar-item-active-shadow) !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: var(--sidebar-item-hover) !important;
    border-color: rgba(255, 255, 255, 0.28) !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
    background: var(--coral-400) !important;
    border-color: rgba(255, 255, 255, 0.45) !important;
}
[data-testid="stSidebar"] .stButton {
    margin-bottom: 0.35rem !important;
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

/* ── Sidebar menu (nền hồng phấn đậm) ── */
[data-testid="stSidebar"] [data-testid="stMarkdown"] h1,
[data-testid="stSidebar"] [data-testid="stMarkdown"] h2,
[data-testid="stSidebar"] [data-testid="stMarkdown"] h3,
[data-testid="stSidebar"] [data-testid="stMarkdown"] h4,
[data-testid="stSidebar"] [data-testid="stMarkdown"] h5,
[data-testid="stSidebar"] [data-testid="stMarkdown"] h6 {
    color: var(--sidebar-text) !important;
    font-weight: 700 !important;
    font-size: 1rem !important;
    letter-spacing: -0.01em;
    margin-bottom: 0.15rem !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdown"] strong {
    color: var(--sidebar-text) !important;
}
[data-testid="stSidebar"] hr {
    border: none !important;
    border-top: 1px solid rgba(255, 255, 255, 0.28) !important;
    margin: 0.75rem 0 !important;
}
[data-testid="stSidebar"] .stDownloadButton > button {
    background: var(--sidebar-item-bg) !important;
    border: 1px solid var(--sidebar-item-border) !important;
    color: var(--sidebar-text) !important;
    font-weight: 600 !important;
}
[data-testid="stSidebar"] .stDownloadButton > button:hover {
    background: var(--sidebar-item-hover) !important;
    border-color: rgba(255, 255, 255, 0.28) !important;
}
/* Sidebar workflow menu — chỉ style label trong radiogroup, không đụng widget label */
[data-testid="stSidebar"] .stRadio > [data-testid="stWidgetLabel"] {
    display: none !important;
}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] {
    display: flex !important;
    flex-direction: column !important;
    gap: 0.4rem !important;
    background: transparent !important;
    border: none !important;
    padding: 0 !important;
}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label {
    background: var(--sidebar-item-bg) !important;
    border: 1px solid var(--sidebar-item-border) !important;
    border-radius: var(--radius-pill) !important;
    padding: 0.55rem 0.85rem !important;
    margin: 0 !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
    line-height: 1.4 !important;
    white-space: normal !important;
    color: rgba(255, 255, 255, 0.95) !important;
    transition: background 0.15s, border-color 0.15s, color 0.15s, box-shadow 0.15s !important;
    box-shadow: none !important;
    cursor: pointer !important;
}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label p,
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label span,
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] [data-testid="stMarkdownContainer"],
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] [data-testid="stMarkdownContainer"] p {
    color: inherit !important;
    font-size: inherit !important;
    font-weight: inherit !important;
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label:hover {
    border-color: rgba(255, 255, 255, 0.45) !important;
    background: var(--sidebar-item-hover) !important;
    color: #fff !important;
}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label:has(input:checked) {
    background: var(--sidebar-item-active-bg) !important;
    border-color: rgba(255, 255, 255, 0.35) !important;
    color: var(--sidebar-item-active-text) !important;
    font-weight: 700 !important;
    box-shadow: var(--sidebar-item-active-shadow) !important;
}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] [data-baseweb="radio"] {
    margin-right: 0.45rem !important;
    flex-shrink: 0 !important;
}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] [data-baseweb="radio"] > div {
    background: transparent !important;
    border-color: rgba(255, 255, 255, 0.55) !important;
}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label:has(input:checked) [data-baseweb="radio"] > div {
    background: #fff !important;
    border-color: #fff !important;
}

/* ── Hero (card trắng kiểu Vdesign) ── */
.app-hero {
    background: var(--surface);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius);
    padding: 1.5rem 1.75rem 1.3rem;
    margin-bottom: 1.1rem;
    box-shadow: var(--shadow-sm);
    position: relative;
}
.app-hero-badge {
    display: inline-block;
    background: var(--brand);
    color: #fff;
    border: none;
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 0.28rem 0.75rem;
    border-radius: var(--radius-pill);
    margin-bottom: 0.65rem;
}
.app-title {
    margin: 0 0 0.45rem 0 !important;
    font-size: 2rem !important;
    font-weight: 700 !important;
    font-family: "Inter", sans-serif !important;
    color: var(--ink) !important;
    -webkit-text-fill-color: var(--ink) !important;
    background: none !important;
    line-height: 1.2 !important;
    letter-spacing: -0.025em;
}
.app-subtitle {
    margin: 0;
    color: var(--ink-muted);
    font-size: 0.9rem;
    line-height: 1.65;
    font-weight: 400;
}
.app-subtitle strong {
    color: var(--brand);
    font-weight: 600;
}

/* ── Step headings ── */
.main h3, .main h2 {
    color: var(--ink) !important;
    font-weight: 700 !important;
    font-size: 1.08rem !important;
    letter-spacing: -0.02em;
    border-left: none !important;
    padding-left: 0 !important;
    padding-bottom: 0.5rem !important;
    margin-top: 0.15rem !important;
    border-bottom: 1px solid var(--border-soft) !important;
}
.main h1:not(.app-title) {
    color: var(--ink);
}

/* ── Step cards ── */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--surface) !important;
    border: 1px solid var(--border-soft) !important;
    border-radius: var(--radius) !important;
    box-shadow: var(--shadow-sm) !important;
    padding: 0.35rem 0.15rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: var(--coral-200) !important;
    box-shadow: var(--shadow-md) !important;
}

/* ── Buttons ── */
.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"] {
    background: var(--brand) !important;
    color: #fff !important;
    border: none !important;
    border-radius: var(--radius-pill) !important;
    font-weight: 600 !important;
    letter-spacing: 0;
    padding: 0.55rem 1.4rem !important;
    box-shadow: var(--shadow-btn) !important;
    transition: background 0.15s, transform 0.15s, box-shadow 0.15s !important;
}
.stButton > button[kind="primary"]:hover:not(:disabled),
.stDownloadButton > button[kind="primary"]:hover:not(:disabled) {
    background: var(--coral-600) !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 14px rgba(var(--brand-rgb), 0.38) !important;
}
.stButton > button[kind="primary"]:disabled {
    opacity: 0.5;
    box-shadow: none !important;
}
.stButton > button[kind="secondary"] {
    background: var(--surface) !important;
    color: var(--ink) !important;
    border: 1px solid var(--border-soft) !important;
    border-radius: var(--radius-pill) !important;
    font-weight: 600 !important;
}
.stButton > button[kind="secondary"]:hover {
    background: var(--coral-50) !important;
    border-color: var(--coral-300) !important;
    color: var(--ink) !important;
}

/* ── Alerts ── */
[data-testid="stAlert"] {
    border-radius: var(--radius-sm) !important;
}
[data-testid="stAlertContainer"] div[role="alert"][data-baseweb="notification"] {
    background-color: var(--coral-50);
    border-color: var(--coral-200);
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
    border-color: var(--border-soft) !important;
    background: var(--surface) !important;
    font-family: "Inter", sans-serif !important;
    color: var(--ink) !important;
}
.stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
    border-color: var(--coral-400) !important;
    box-shadow: 0 0 0 3px rgba(var(--brand-rgb), 0.18) !important;
}

/* ── Tabs (pill nav kiểu Vdesign) ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.2rem;
    background: var(--surface);
    border-radius: var(--radius-pill);
    padding: 0.28rem;
    border: 1px solid var(--border-soft);
    box-shadow: var(--shadow-sm);
}
.stTabs [data-baseweb="tab"] {
    border-radius: var(--radius-pill) !important;
    color: var(--ink-muted) !important;
    font-weight: 500 !important;
    font-size: 0.86rem !important;
}
.stTabs [aria-selected="true"] {
    background: var(--brand) !important;
    color: #fff !important;
    box-shadow: none !important;
}

.stRadio > div[role="radiogroup"][aria-label] {
    gap: 0.45rem;
}

[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-soft);
    overflow: hidden;
    background: var(--surface);
}

.streamlit-expanderHeader {
    background: var(--surface) !important;
    border: 1px solid var(--border-soft) !important;
    border-radius: var(--radius-sm) !important;
    font-weight: 600 !important;
    color: var(--ink) !important;
}

.stProgress > div > div {
    background: var(--brand) !important;
    border-radius: var(--radius-pill);
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
    border-radius: var(--radius-pill);
    border: 1px solid var(--border-soft);
    background: var(--surface);
    color: var(--brand);
    font-weight: 600;
    text-align: center;
}

.val-missing { color: #BE123C; font-weight: 700; }
.val-missing-count { color: #E11D48; }

[data-testid="stPills"] {
    background: var(--surface);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-pill);
    padding: 0.2rem;
}
[data-testid="stPills"] button {
    border-radius: var(--radius-pill) !important;
    border-color: transparent !important;
    font-weight: 500 !important;
    color: var(--ink-muted) !important;
}
[data-testid="stPills"] button[aria-pressed="true"] {
    background: var(--brand) !important;
    color: white !important;
    border-color: transparent !important;
    font-weight: 600 !important;
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
    background: var(--surface);
    border: 1px solid var(--border-soft);
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
    background: var(--brand);
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
    border-top: 1px dashed var(--border-soft);
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
    border-top: 1px dashed var(--border-soft);
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
    border: 1px solid var(--border-soft) !important;
    color: var(--ink) !important;
    box-shadow: none !important;
}
.crawl-step1-wrap .crawl-side-tools [data-testid="stButton"] button:hover {
    border-color: var(--coral-300) !important;
    background: var(--coral-50) !important;
}

@media (max-width: 1100px) {
    .gmc-bulk-wrap [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
    .gmc-bulk-wrap [data-testid="column"] {
        flex: 1 1 22% !important;
        min-width: 6.5rem !important;
    }
}
/* ── Mobile / tablet: sidebar trên, main full width ── */
@media (max-width: 900px) {
    [data-testid="stAppViewContainer"] {
        flex-direction: column !important;
        flex-wrap: nowrap !important;
    }
    [data-testid="stAppViewContainer"] > div:has(> section[data-testid="stSidebar"]),
    [data-testid="stAppViewContainer"] > .react-resizable:has([data-testid="stSidebar"]) {
        flex: 0 0 auto !important;
        width: 100% !important;
        min-width: 100% !important;
        max-width: 100% !important;
    }
    [data-testid="stSidebar"],
    [data-testid="stSidebar"][aria-expanded="true"],
    [data-testid="stSidebar"][aria-expanded="false"] {
        flex: 0 0 auto !important;
        width: 100% !important;
        min-width: 100% !important;
        max-width: 100% !important;
        position: relative !important;
        border-right: none !important;
        border-bottom: 1px solid rgba(255, 255, 255, 0.12) !important;
        box-shadow: 0 4px 18px rgba(var(--brand-rgb), 0.18) !important;
    }
    [data-testid="stSidebar"] > div:first-child {
        padding: 1rem 0.85rem 0.85rem !important;
        max-height: none !important;
    }
    [data-testid="stSidebar"] .stButton > button {
        font-size: 0.8rem !important;
        padding: 0.5rem 0.75rem !important;
        min-height: 2.1rem !important;
        word-break: break-word !important;
    }
    [data-testid="stMain"] {
        flex: 1 1 auto !important;
        width: 100% !important;
        min-width: 0 !important;
        max-width: 100% !important;
        padding-top: 0.35rem !important;
    }
    .block-container {
        padding-top: 0.75rem !important;
        padding-left: 0.85rem !important;
        padding-right: 0.85rem !important;
    }
    .app-hero {
        padding: 1rem 1.05rem 0.9rem;
        margin-bottom: 0.75rem;
    }
    .app-title {
        font-size: 1.42rem !important;
        line-height: 1.25 !important;
    }
    .app-subtitle {
        font-size: 0.82rem;
        line-height: 1.55;
    }
    .main h3, .main h2 {
        font-size: 0.98rem !important;
    }
    .crawl-step1-wrap [data-testid="stHorizontalBlock"] {
        flex-direction: column !important;
        gap: 0.75rem !important;
    }
    .crawl-step1-wrap [data-testid="column"] {
        width: 100% !important;
        flex: 1 1 100% !important;
        min-width: 0 !important;
    }
    .crawl-action-center [data-testid="stButton"] button {
        min-width: 100%;
        max-width: 100%;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        padding: 0.25rem 0.1rem !important;
    }
    [data-testid="stTabs"] [data-baseweb="tab-list"] {
        flex-wrap: wrap !important;
        border-radius: var(--radius-sm) !important;
    }
    [data-testid="stPills"] {
        flex-wrap: wrap !important;
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
            const win = window.parent;
            const DESKTOP_MIN = 901;
            const clearSidebarLock = (sidebar) => {
                [sidebar.closest(".react-resizable"), sidebar].filter(Boolean).forEach((el) => {
                    el.style.removeProperty("width");
                    el.style.removeProperty("min-width");
                    el.style.removeProperty("max-width");
                    el.style.removeProperty("flex");
                });
            };
            const lock = () => {
                try { win.localStorage.removeItem("sidebarWidth"); } catch (e) {}
                const sidebar = doc.querySelector('[data-testid="stSidebar"]');
                if (!sidebar) return;
                doc.querySelectorAll(".react-resizable-handle").forEach((handle) => {
                    handle.style.display = "none";
                    handle.style.pointerEvents = "none";
                });
                if (win.innerWidth <= DESKTOP_MIN) {
                    clearSidebarLock(sidebar);
                    return;
                }
                sidebar.setAttribute("aria-expanded", "true");
                [sidebar.closest(".react-resizable"), sidebar].filter(Boolean).forEach((el) => {
                    el.style.setProperty("width", "20vw", "important");
                    el.style.setProperty("min-width", "20vw", "important");
                    el.style.setProperty("max-width", "20vw", "important");
                    el.style.setProperty("flex", "0 0 20vw", "important");
                });
            };
            lock();
            win.addEventListener("resize", lock, { passive: true });
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
        badge = f"v{APP_VERSION} · {app_build_id()}"

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
