"""サイドバー UI モジュール"""
from __future__ import annotations

import streamlit as st

import api_client
from api_client import list_sessions, load_session
from scoring_engine import AnthropicProvider, GeminiProvider
from ui_helpers import progress_ring_html


def render_sidebar():
    """サイドバーを描画する。"""
    with st.sidebar:
        st.markdown(
            '<div style="text-align:center;padding:12px 0 8px;">'
            '<div style="font-size:1.5rem;font-weight:700;color:white;letter-spacing:0.5px;">'
            '採点支援</div>'
            '<div style="font-size:0.7rem;color:rgba(255,255,255,0.55);margin-top:4px;">'
            'AI Grading Assistant v0.3</div></div>',
            unsafe_allow_html=True,
        )

        st.divider()

        # --- API設定 ---
        st.subheader("AI設定")
        provider = st.radio(
            "使用するAI",
            ["gemini", "anthropic", "demo"],
            format_func=lambda x: {
                "gemini": "Google Gemini（推奨）",
                "anthropic": "Anthropic Claude",
                "demo": "デモモード（AI接続なし）",
            }[x],
            index=0,
            key="provider_choice",
        )

        if provider == "gemini":
            st.text_input(
                "Google APIキー（認証情報）",
                value=st.session_state.gemini_key,
                type="password",
                key="gemini_key",
                help="Google AI Studio (aistudio.google.com) にログインして取得できます",
            )
            st.selectbox(
                "AIの種類",
                list(GeminiProvider.MODELS.keys()),
                format_func=lambda x: GeminiProvider.MODELS[x],
                key="gemini_model",
            )
            if st.session_state.gemini_key:
                st.success("APIキー設定済み")
            else:
                st.warning("APIキーを入力してください")

        elif provider == "anthropic":
            st.text_input(
                "Anthropic APIキー（認証情報）",
                value=st.session_state.anthropic_key,
                type="password",
                key="anthropic_key",
                help="console.anthropic.com で取得できます（利用には最低$5の課金が必要です）",
            )
            st.selectbox(
                "AIの種類",
                list(AnthropicProvider.MODELS.keys()),
                format_func=lambda x: AnthropicProvider.MODELS[x],
                key="anthropic_model",
            )
            if st.session_state.anthropic_key:
                st.success("APIキー設定済み")
            else:
                st.warning("APIキーを入力してください")

        else:
            st.info("デモモード: AIを使わず、サンプルの採点結果で操作を試すことができます")

        st.divider()

        # --- 個人情報保護 & 採点オプション ---
        from ui_helpers import ENABLE_PDF_INPUT
        if ENABLE_PDF_INPUT:
            st.subheader("送信前の個人情報保護")
            st.checkbox(
                "外部AIに送る前に氏名欄をマスクする",
                value=True,
                key="mask_student_name",
                help="Gemini / Claude に送る画像の先頭ページ上部を黒塗りしたコピーに差し替えます。",
            )
            if st.session_state.mask_student_name:
                strategy_labels = {
                    "top_right": "右上を隠す（氏名欄が右寄りの用紙向け）",
                    "top_left": "左上を隠す（氏名欄が左寄りの用紙向け）",
                    "top_band": "上端を広く隠す（位置が一定しない場合）",
                }
                st.selectbox(
                    "氏名欄の位置",
                    list(strategy_labels.keys()),
                    format_func=lambda x: strategy_labels[x],
                    key="mask_strategy",
                )
                st.slider("マスク幅（横方向）", min_value=20, max_value=80,
                          value=st.session_state.mask_width_percent, step=2, key="mask_width_percent")
                st.slider("マスク高さ（縦方向）", min_value=8, max_value=25,
                          value=st.session_state.mask_height_percent, step=1, key="mask_height_percent")
            st.divider()

        st.subheader("採点オプション")
        if not ENABLE_PDF_INPUT:
            st.caption("生徒の個人情報（氏名・ID）はAIに送信されません。回答テキストのみが匿名化された状態で処理されます。")
        if ENABLE_PDF_INPUT and st.session_state.get("data_source") != "csv":
            st.radio(
                "答案の形式",
                options=["typed", "handwritten"],
                format_func=lambda x: "電子データ（タイプ入力・Classroom等）" if x == "typed" else "手書き答案（スキャンPDF）",
                index=0, key="submission_type",
            )
            st.checkbox(
                "2段構えOCR（レイアウト分析＋読み取り）",
                value=True, key="enable_two_stage_ocr",
                disabled=st.session_state.get("submission_type") == "typed",
            )
        st.checkbox(
            "ダブルチェック方式（記述式）",
            value=True,
            key="enable_verification",
            help="記述式問題の採点後にAIが自動で検証を行い、得点とコメントの整合性を確認します。"
                 "得点が変更された場合は「要確認」フラグが付きます。",
        )

        st.divider()

        # --- 進捗リング ---
        if st.session_state.session and st.session_state.session.students:
            summary = st.session_state.session.summary()
            total = summary["total_students"]
            confirmed = summary["reviewed"]
            pct = (confirmed / total * 100) if total > 0 else 0
            st.markdown(
                progress_ring_html(pct, f"{confirmed}/{total}名 確定済み"),
                unsafe_allow_html=True,
            )
            st.divider()

        # --- 過去のセッション ---
        st.subheader("過去の採点データ")
        try:
            sessions = list_sessions()
        except api_client.ApiClientError:
            sessions = []
        if sessions:
            options = {s["session_id"]: f'{s["rubric_title"]} ({s["student_count"]}名)' for s in sessions}
            selected = st.selectbox(
                "読み込む採点データ", ["（新規）"] + list(options.keys()),
                format_func=lambda x: options.get(x, x),
            )
            if selected != "（新規）" and st.button("読み込む", type="primary"):
                loaded = load_session(selected)
                if loaded:
                    st.session_state.session = loaded
                    st.success("採点データを読み込みました")
                    st.rerun()
        else:
            st.caption("保存済みの採点データはありません")

        st.divider()
        st.markdown(
            '<div style="padding:10px;border-radius:8px;background:rgba(255,255,255,0.08);'
            'font-size:0.7rem;color:rgba(255,255,255,0.7);line-height:1.5;">'
            'このツールのAI判定はあくまで仮採点です。'
            '最終成績は必ず教員ご自身で確認してください。</div>',
            unsafe_allow_html=True,
        )
