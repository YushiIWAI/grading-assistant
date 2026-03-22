"""
国語 採点支援アプリ (プロトタイプ)
====================================
教員の採点業務を補助するためのツールです。
AIによる仮採点はあくまで参考であり、最終判断は教員が行ってください。

起動方法:
    python3 -m streamlit run app.py
"""

from __future__ import annotations

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# --- 起動時設定検証 ---
from config import validate_secrets
validate_secrets()

# --- ページ設定 ---
st.set_page_config(
    page_title="国語 採点支援",
    page_icon="📝",
    layout="wide",
)

# --- 認証 ---
from ui_auth import check_auth

# TODO: パイロット前に削除すること
import os as _os
if not _os.environ.get("SKIP_AUTH"):
    if not check_auth():
        st.stop()

# --- カスタムCSS ---
from ui_styles import inject_custom_css
inject_custom_css()

# --- セッション状態の初期化 ---
from ui_helpers import init_session_state
init_session_state()

# --- サイドバー ---
from ui_sidebar import render_sidebar
render_sidebar()

# --- メインコンテンツ ---
tab_rubric, tab_scoring, tab_review, tab_export = st.tabs([
    "1. 採点基準",
    "2. 答案の取り込みと仮採点",
    "3. 確認・修正",
    "4. 成績の書き出し",
])

from tab_rubric import render_rubric_tab
from tab_scoring import render_scoring_tab
from tab_review import render_review_tab
from tab_export import render_export_tab

render_rubric_tab(tab_rubric)
render_scoring_tab(tab_scoring)
render_review_tab(tab_review)
render_export_tab(tab_export)
