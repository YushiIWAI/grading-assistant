"""カスタムCSS モジュール"""
from __future__ import annotations

import streamlit as st

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;500;700&display=swap');

html, body, .stApp {
    font-family: 'Noto Sans JP', 'Hiragino Kaku Gothic ProN',
                 'Hiragino Sans', 'Yu Gothic UI', 'Meiryo', sans-serif;
}
.material-symbols-rounded,
[class*="material-symbols"] {
    font-family: "Material Symbols Rounded" !important;
    font-style: normal !important;
}

:root {
    --ga-primary: #2563a8;
    --ga-primary-light: #e8f0fe;
    --ga-primary-dark: #1a4a7a;
    --ga-accent: #0d9488;
    --ga-surface: #f8fafb;
    --ga-border: #e2e8f0;
    --ga-text: #1e293b;
    --ga-text-secondary: #64748b;
}

.stApp { background-color: #f0f4f8; }

button[data-testid="stBaseButton-primary"] {
    background-color: var(--ga-primary) !important;
    border-color: var(--ga-primary) !important;
    color: white !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
    transition: all 0.2s ease !important;
}
button[data-testid="stBaseButton-primary"]:hover {
    background-color: var(--ga-primary-dark) !important;
    box-shadow: 0 2px 8px rgba(37, 99, 168, 0.3) !important;
}
button[data-testid="stBaseButton-secondary"] {
    border-radius: 8px !important;
    border-color: var(--ga-border) !important;
    transition: all 0.2s ease !important;
}

section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1a4a7a 0%, #163d66 100%) !important;
}
section[data-testid="stSidebar"] .stMarkdown,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stCaption,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stSelectbox label {
    color: #e2e8f0 !important;
}
section[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.15) !important;
}

.stTabs [data-baseweb="tab-list"] {
    gap: 0px;
    background-color: white;
    border-radius: 12px 12px 0 0;
    padding: 4px 4px 0 4px;
    border-bottom: 2px solid var(--ga-border);
}
.stTabs [data-baseweb="tab"] {
    padding: 12px 24px !important;
    font-weight: 500 !important;
    color: var(--ga-text-secondary) !important;
    border-radius: 8px 8px 0 0 !important;
    border: none !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    color: var(--ga-primary) !important;
    background-color: var(--ga-primary-light) !important;
    border-bottom: 3px solid var(--ga-primary) !important;
}

div[data-testid="stMetric"] {
    background-color: white;
    border: 1px solid var(--ga-border);
    border-radius: 12px;
    padding: 16px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
div[data-testid="stMetric"] label {
    color: var(--ga-text-secondary) !important;
    font-size: 0.8rem !important;
}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: var(--ga-primary) !important;
    font-weight: 700 !important;
}

details[data-testid="stExpander"] {
    border: 1px solid var(--ga-border) !important;
    border-radius: 10px !important;
    margin-bottom: 8px !important;
    background-color: white !important;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04) !important;
}

.stProgress > div > div > div {
    background-color: var(--ga-accent) !important;
    border-radius: 4px !important;
}

div[data-testid="stAlert"] { border-radius: 10px !important; }

.block-container {
    padding-top: 2rem !important;
    max-width: 1200px !important;
}

.stTextInput > div > div,
.stTextArea > div > div,
.stNumberInput > div > div {
    border-radius: 8px !important;
}
.stTextInput > div > div:focus-within,
.stTextArea > div > div:focus-within {
    border-color: var(--ga-primary) !important;
    box-shadow: 0 0 0 2px rgba(37, 99, 168, 0.15) !important;
}
</style>
"""


def inject_custom_css():
    """カスタムCSSをページに注入する。"""
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
