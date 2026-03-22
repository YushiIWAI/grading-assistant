"""UI ヘルパー関数・定数モジュール"""
from __future__ import annotations

import os

import streamlit as st

from pdf_processor import PrivacyMaskConfig
from provider_factory import build_provider as build_provider_from_config

# 機能フラグ: PDF手書き答案対応を有効にするか
# 将来 Gemini の画像認識が実用レベルになったら True に戻す
ENABLE_PDF_INPUT = False


DEFAULTS = {
    "session": None,
    "images": [],
    "student_groups": [],
    "rubric": None,
    "pdf_bytes": b"",
    "data_source": None,  # "pdf" | "csv" — どちらで取り込んだか
    "csv_data": None,      # FormsCSVData（CSV取り込み時に使用）
    "_csv_content": None,  # CSV生テキスト（API送信用）
    "gemini_key": os.getenv("GOOGLE_API_KEY", ""),
    "anthropic_key": os.getenv("ANTHROPIC_API_KEY", ""),
    "privacy_accepted": False,
    "mask_student_name": True,
    "mask_strategy": "top_right",
    "mask_width_percent": 36,
    "mask_height_percent": 14,
    # ルーブリックビルダー用
    "rb_title": "国語テスト",
    "rb_total": 100,
    "rb_pages": 1,
    "rb_notes": "",
    "rb_questions": [],
}


def init_session_state():
    """セッション状態の初期化。"""
    for k, v in DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v


def get_status_emoji(status: str) -> str:
    return {"pending": "⏳", "ai_scored": "🤖", "reviewed": "✅", "confirmed": "✅"}.get(status, "❓")


def get_confidence_color(confidence: str) -> str:
    return {"high": "green", "medium": "orange", "low": "red"}.get(confidence, "gray")


def format_confidence(confidence: str) -> str:
    """確信度ラベルを日本語に変換する"""
    return {"high": "高", "medium": "中", "low": "低"}.get(confidence, "不明")


def status_badge_html(status: str) -> str:
    """ステータスに応じたHTMLバッジを生成する"""
    configs = {
        "pending":    {"label": "未採点",      "bg": "#f1f5f9", "color": "#64748b", "border": "#cbd5e1"},
        "ai_scored":  {"label": "AI仮採点済み", "bg": "#e8f0fe", "color": "#2563a8", "border": "#93b4e0"},
        "reviewed":   {"label": "確定",        "bg": "#e6f7f5", "color": "#059669", "border": "#6ee7b7"},
        "confirmed":  {"label": "確定",        "bg": "#e6f7f5", "color": "#059669", "border": "#6ee7b7"},
    }
    c = configs.get(status, configs["pending"])
    return (
        f'<span style="display:inline-flex;align-items:center;gap:4px;'
        f'padding:3px 10px;border-radius:20px;font-size:0.78rem;'
        f'font-weight:500;white-space:nowrap;'
        f'background:{c["bg"]};color:{c["color"]};'
        f'border:1px solid {c["border"]};">'
        f'{c["label"]}</span>'
    )


def confidence_badge_html(confidence: str) -> str:
    """確信度に応じたHTMLバッジを生成する"""
    configs = {
        "high":   {"label": "自信度: 高", "bg": "#e6f7f5", "color": "#059669", "border": "#6ee7b7"},
        "medium": {"label": "自信度: 中", "bg": "#fef3c7", "color": "#d97706", "border": "#fcd34d"},
        "low":    {"label": "自信度: 低", "bg": "#fee2e2", "color": "#dc2626", "border": "#fca5a5"},
    }
    c = configs.get(confidence, {"label": f"自信度: {confidence}", "bg": "#f1f5f9", "color": "#64748b", "border": "#cbd5e1"})
    return (
        f'<span style="display:inline-flex;align-items:center;'
        f'padding:2px 8px;border-radius:12px;font-size:0.72rem;'
        f'font-weight:500;'
        f'background:{c["bg"]};color:{c["color"]};'
        f'border:1px solid {c["border"]};">'
        f'{c["label"]}</span>'
    )


def review_needed_badge_html(count: int) -> str:
    """要確認バッジを生成する"""
    if count == 0:
        return ""
    return (
        f'<span style="display:inline-flex;align-items:center;'
        f'padding:2px 8px;border-radius:12px;font-size:0.72rem;'
        f'font-weight:500;'
        f'background:#fee2e2;color:#dc2626;border:1px solid #fca5a5;">'
        f'要確認 {count}件</span>'
    )


def build_provider():
    """現在の設定からプロバイダーを構築する。

    APIキー未設定時は None を返し、呼び出し元で設定案内を表示する。
    """
    config = get_provider_config()
    try:
        provider, _ = build_provider_from_config(
            provider_name=config["provider"],
            api_key=config["api_key"],
            model_name=config["model_name"],
            privacy_mask=PrivacyMaskConfig(**config["privacy_mask"]),
        )
        return provider
    except ValueError:
        return None


def get_provider_config() -> dict:
    """現在の UI 設定を API 送信用の dict に整形する。"""
    provider_name = st.session_state.get("provider_choice", "demo")
    if provider_name == "gemini":
        api_key = st.session_state.get("gemini_key", "")
        model_name = st.session_state.get("gemini_model", "gemini-3.1-pro-preview")
    elif provider_name == "anthropic":
        api_key = st.session_state.get("anthropic_key", "")
        model_name = st.session_state.get("anthropic_model", "claude-sonnet-4-20250514")
    else:
        api_key = ""
        model_name = ""

    return {
        "provider": provider_name,
        "api_key": api_key,
        "model_name": model_name,
        "privacy_mask": {
            "enabled": st.session_state.get("mask_student_name", True),
            "strategy": st.session_state.get("mask_strategy", "top_right"),
            "width_ratio": st.session_state.get("mask_width_percent", 36) / 100,
            "height_ratio": st.session_state.get("mask_height_percent", 14) / 100,
            "margin_x_ratio": 0.03,
            "margin_y_ratio": 0.02,
            "first_page_only": True,
        },
    }


def progress_ring_html(percent: float, label: str = "", size: int = 90) -> str:
    """SVGベースの円形進捗リング"""
    r = (size - 8) / 2
    circ = 2 * 3.14159 * r
    offset = circ * (1 - percent / 100)
    color = "#059669" if percent >= 100 else "#2563a8"
    return (
        f'<div style="display:flex;flex-direction:column;align-items:center;margin:8px 0;">'
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none"'
        f' stroke="rgba(255,255,255,0.15)" stroke-width="6"/>'
        f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none"'
        f' stroke="{color}" stroke-width="6"'
        f' stroke-dasharray="{circ}" stroke-dashoffset="{offset}"'
        f' stroke-linecap="round"'
        f' transform="rotate(-90 {size/2} {size/2})"'
        f' style="transition:stroke-dashoffset 0.5s ease;"/>'
        f'<text x="{size/2}" y="{size/2}" text-anchor="middle"'
        f' dominant-baseline="central" font-size="{size*0.22}px"'
        f' font-weight="600" fill="white">{percent:.0f}%</text>'
        f'</svg>'
        f'<span style="font-size:0.72rem;color:#e2e8f0;margin-top:4px;">{label}</span>'
        f'</div>'
    )
