"""
国語 採点支援アプリ (プロトタイプ)
====================================
教員の採点業務を補助するためのツールです。
AIによる仮採点はあくまで参考であり、最終判断は教員が行ってください。

起動方法:
    python3 -m streamlit run app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
import yaml
from dotenv import load_dotenv

from models import (
    Rubric, Question, SubQuestion,
    StudentResult, QuestionScore, ScoringSession,
    StudentOcr, OcrAnswer,
)
from pdf_processor import (
    pdf_to_images, split_pages_by_student, image_to_bytes,
)
from scoring_engine import (
    GeminiProvider, AnthropicProvider, DemoProvider,
    parse_scoring_result,
    score_student_by_question,
    ocr_all_students,
    run_horizontal_grading,
    analyze_batch_calibration,
    DEFAULT_BATCH_SIZE,
    recommend_batch_size,
)
from storage import (
    save_session, load_session, list_sessions,
    export_csv,
)

load_dotenv()

# --- ページ設定 ---
st.set_page_config(
    page_title="国語 採点支援",
    page_icon="📝",
    layout="wide",
)


# --- パスワード認証 ---
def check_password() -> bool:
    """共通パスワードによる簡易認証。正しいパスワードなら True を返す。"""
    if st.session_state.get("authenticated"):
        return True

    password = st.secrets.get("password", "")
    if not password:
        # secrets にパスワード未設定 → 認証なしで通す（ローカル開発用）
        return True

    st.markdown(
        "<h2 style='text-align:center; margin-top:2rem;'>📝 国語 採点支援</h2>"
        "<p style='text-align:center; color:#64748b;'>ログインしてください</p>",
        unsafe_allow_html=True,
    )
    with st.form("login_form"):
        entered = st.text_input("パスワード", type="password")
        submitted = st.form_submit_button("ログイン", use_container_width=True)
        if submitted:
            if entered == password:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("パスワードが正しくありません")
    return False


if not check_password():
    st.stop()

# --- カスタムCSS ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;500;700&display=swap');

html, body, [class*="st-"]:not(.material-symbols-rounded) {
    font-family: 'Noto Sans JP', 'Hiragino Kaku Gothic ProN',
                 'Hiragino Sans', 'Yu Gothic UI', 'Meiryo', sans-serif;
}
.material-symbols-rounded,
[class*="material-symbols"] {
    font-family: "Material Symbols Rounded" !important;
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
""", unsafe_allow_html=True)

# --- セッション状態の初期化 ---
DEFAULTS = {
    "session": None,
    "images": [],
    "student_groups": [],
    "rubric": None,
    "gemini_key": os.getenv("GOOGLE_API_KEY", ""),
    "anthropic_key": os.getenv("ANTHROPIC_API_KEY", ""),
    "privacy_accepted": False,
    # ルーブリックビルダー用
    "rb_title": "国語テスト",
    "rb_total": 100,
    "rb_pages": 1,
    "rb_notes": "",
    "rb_questions": [],
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ============================================================
# ユーティリティ
# ============================================================

def load_rubric_from_yaml(yaml_text: str) -> Rubric:
    """YAML文字列から採点基準を読み込む"""
    data = yaml.safe_load(yaml_text)
    exam = data.get("exam_info", data)
    questions = []
    for qdata in data.get("questions", []):
        subs = []
        for sq in qdata.get("sub_questions", []):
            subs.append(SubQuestion(
                id=str(sq["id"]), text=sq.get("text", ""),
                answer=sq.get("answer", ""), points=sq.get("points", 0),
            ))
        questions.append(Question(
            id=qdata["id"], description=qdata.get("description", ""),
            question_type=qdata.get("type", "short_answer"),
            max_points=qdata.get("max_points", 0),
            scoring_criteria=qdata.get("scoring_criteria", ""),
            model_answer=qdata.get("model_answer", ""),
            sub_questions=subs,
        ))
    return Rubric(
        title=exam.get("title", "無題の試験"),
        total_points=exam.get("total_points", 100),
        pages_per_student=exam.get("pages_per_student", 1),
        questions=questions,
        notes=data.get("notes", ""),
    )


def rubric_to_yaml(rubric: Rubric) -> str:
    """RubricオブジェクトをYAML文字列に変換する"""
    data = {
        "exam_info": {
            "title": rubric.title,
            "total_points": rubric.total_points,
            "pages_per_student": rubric.pages_per_student,
        },
        "notes": rubric.notes,
        "questions": [],
    }
    for q in rubric.questions:
        qd = {
            "id": q.id, "description": q.description,
            "type": q.question_type, "max_points": q.max_points,
        }
        if q.scoring_criteria:
            qd["scoring_criteria"] = q.scoring_criteria
        if q.model_answer:
            qd["model_answer"] = q.model_answer
        if q.sub_questions:
            qd["sub_questions"] = [
                {"id": sq.id, "text": sq.text, "answer": sq.answer, "points": sq.points}
                for sq in q.sub_questions
            ]
        data["questions"].append(qd)
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


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
    """現在の設定からプロバイダーを構築する"""
    provider_name = st.session_state.get("provider_choice", "demo")
    if provider_name == "gemini" and st.session_state.gemini_key:
        model = st.session_state.get("gemini_model", "gemini-3.1-pro-preview")
        return GeminiProvider(st.session_state.gemini_key, model)
    elif provider_name == "anthropic" and st.session_state.anthropic_key:
        model = st.session_state.get("anthropic_model", "claude-sonnet-4-20250514")
        return AnthropicProvider(st.session_state.anthropic_key, model)
    else:
        return DemoProvider()


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


# ============================================================
# サイドバー
# ============================================================

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

    # --- 採点オプション ---
    st.subheader("採点オプション")
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
    sessions = list_sessions()
    if sessions:
        options = {s["session_id"]: f'{s["rubric_title"]} ({s["student_count"]}名)' for s in sessions}
        selected = st.selectbox(
            "読み込む採点データ", ["（新規）"] + list(options.keys()),
            format_func=lambda x: options.get(x, x),
        )
        if selected != "（新規）" and st.button("読み込む"):
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


# ============================================================
# メインコンテンツ
# ============================================================

# --- ウェルカム画面（初回利用時） ---
if not st.session_state.rubric and not st.session_state.session:
    st.markdown("""
## はじめに

このアプリは、**4つのステップ**で採点作業を進めます。

| ステップ | 内容 | 所要時間の目安 |
|:---:|:---|:---|
| **1** | 採点基準を入力する | 5〜10分 |
| **2** | 答案PDFを取り込み、AIが文字起こしと仮採点を行う | 数分（待ち時間） |
| **3** | AIの採点結果を確認・修正する | 10〜30分 |
| **4** | 成績をファイルに書き出す | 1分 |

まずは **「1. 採点基準」** タブから始めてください。
""")
    st.divider()

tab_rubric, tab_scoring, tab_review, tab_export = st.tabs([
    "1. 採点基準",
    "2. 答案の取り込みと仮採点",
    "3. 確認・修正",
    "4. 成績の書き出し",
])


# ============================================================
# タブ1: 採点基準（YAMLアップロード or GUIビルダー）
# ============================================================

with tab_rubric:
    st.header("採点基準の作成")
    st.caption("試験の採点基準を設定します。フォームに入力するか、設定ファイル（YAML）をお持ちの場合はそちらからも読み込めます。")

    method = st.radio(
        "作成方法",
        ["gui", "yaml"],
        format_func=lambda x: {"gui": "フォーム入力で作成", "yaml": "設定ファイル（YAML）で読み込み"}[x],
        horizontal=True,
    )

    if method == "gui":
        # --- GUIルーブリックビルダー ---
        st.subheader("試験情報")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            rb_title = st.text_input("試験名", value=st.session_state.rb_title, key="_rb_title")
        with col_b:
            rb_total = st.number_input("満点", value=st.session_state.rb_total, min_value=1, key="_rb_total")
        with col_c:
            rb_pages = st.number_input("1人あたりのページ数", value=st.session_state.rb_pages, min_value=1, max_value=10, key="_rb_pages")

        rb_notes = st.text_area("採点上の注意（任意）", value=st.session_state.rb_notes, height=80, key="_rb_notes")

        st.divider()
        st.subheader("設問")

        # セッション状態から設問リストを管理
        questions = st.session_state.rb_questions

        # 設問追加コールバック
        def _add_question(qtype):
            qs = st.session_state.rb_questions
            defaults = {
                "short_answer": {"max_points": 10},
                "descriptive": {"max_points": 15},
            }
            qs.append({
                "id": len(qs) + 1,
                "description": "",
                "type": qtype,
                "max_points": defaults.get(qtype, {}).get("max_points", 10),
                "scoring_criteria": "",
                "model_answer": "",
                "sub_questions": [],
            })

        # 設問追加ボタン
        add_col1, add_col2 = st.columns(2)
        with add_col1:
            st.button("短答問題を追加", on_click=_add_question, args=("short_answer",))
        with add_col2:
            st.button("記述問題を追加", on_click=_add_question, args=("descriptive",))

        # 各設問の編集フォーム
        for qi, q in enumerate(questions):
            type_label = "短答" if q["type"] == "short_answer" else "記述"
            with st.expander(f"問{q['id']}（{type_label}）: {q['description'] or '未入力'}", expanded=True):
                qcol1, qcol2, qcol3 = st.columns([3, 1, 1])
                with qcol1:
                    q["description"] = st.text_input(
                        "問題の説明", value=q["description"], key=f"q_desc_{qi}",
                    )
                with qcol2:
                    q["max_points"] = st.number_input(
                        "配点", value=q["max_points"], min_value=1, key=f"q_pts_{qi}",
                    )
                with qcol3:
                    def _delete_question(idx):
                        qs = st.session_state.rb_questions
                        qs.pop(idx)
                        for i, qq in enumerate(qs):
                            qq["id"] = i + 1

                    st.button("この問題を削除", key=f"q_del_{qi}",
                              on_click=_delete_question, args=(qi,))

                if q["type"] == "short_answer":
                    st.caption("小問（漢字の読み、語句の穴埋めなど）")
                    subs = q["sub_questions"]

                    sub_input_mode = st.radio(
                        "入力方式", ["individual", "bulk"],
                        format_func=lambda x: {"individual": "1つずつ入力", "bulk": "まとめて入力（貼り付け）"}[x],
                        horizontal=True, key=f"sub_mode_{qi}",
                        label_visibility="collapsed",
                    )

                    if sub_input_mode == "bulk":
                        st.markdown(
                            "1行に1小問。タブ区切りで **問題文**・**正答**・**配点** を指定してください。\n\n"
                            "例: `矛盾\tむじゅん\t2`"
                        )
                        default_lines = "\n".join(
                            f"{sq['text']}\t{sq['answer']}\t{sq['points']}" for sq in subs
                        ) if subs else ""
                        bulk_text = st.text_area(
                            "小問データ（タブ区切り: 問題文 / 正答 / 配点）",
                            value=default_lines, height=150, key=f"bulk_sub_{qi}",
                            placeholder="矛盾\tむじゅん\t2\n慈悲\tじひ\t2",
                        )

                        def _parse_bulk_subs(text, question_dict):
                            new_subs = []
                            for line in text.strip().split("\n"):
                                line = line.strip()
                                if not line:
                                    continue
                                parts = line.split("\t")
                                if len(parts) < 2:
                                    parts = line.split()
                                if len(parts) >= 3:
                                    sub_text, answer = parts[0].strip(), parts[1].strip()
                                    try:
                                        pts = int(parts[2])
                                    except ValueError:
                                        pts = 2
                                elif len(parts) == 2:
                                    sub_text, answer, pts = parts[0].strip(), parts[1].strip(), 2
                                else:
                                    sub_text, answer, pts = parts[0].strip(), "", 2
                                sub_id = f"{question_dict['id']}-{len(new_subs) + 1}"
                                new_subs.append({"id": sub_id, "text": sub_text, "answer": answer, "points": pts})
                            if new_subs:
                                question_dict["sub_questions"] = new_subs

                        st.button("取り込む", key=f"parse_bulk_{qi}",
                                  on_click=_parse_bulk_subs, args=(bulk_text, q))
                        if subs:
                            st.caption(f"現在 {len(subs)} 小問が登録されています")

                    else:
                        def _add_sub(question_dict):
                            s = question_dict["sub_questions"]
                            s.append({"id": f"{question_dict['id']}-{len(s)+1}",
                                      "text": "", "answer": "", "points": 2})

                        st.button("小問を追加", key=f"add_sub_{qi}",
                                  on_click=_add_sub, args=(q,))

                        for si, sq in enumerate(subs):
                            scol1, scol2, scol3, scol4 = st.columns([1, 3, 3, 1])
                            with scol1:
                                sq["id"] = st.text_input("ID", value=sq["id"], key=f"sq_id_{qi}_{si}", disabled=True)
                            with scol2:
                                sq["text"] = st.text_input("問題文/対象語句", value=sq["text"], key=f"sq_text_{qi}_{si}")
                            with scol3:
                                sq["answer"] = st.text_input("正答", value=sq["answer"], key=f"sq_ans_{qi}_{si}")
                            with scol4:
                                sq["points"] = st.number_input("点", value=sq["points"], min_value=1, key=f"sq_pts_{qi}_{si}")

                    q["scoring_criteria"] = st.text_area(
                        "採点基準（任意）", value=q["scoring_criteria"],
                        height=60, key=f"q_crit_{qi}",
                        placeholder="例: 正確な読みのみ正解とする",
                    )

                else:  # descriptive
                    q["model_answer"] = st.text_area(
                        "模範解答（任意）", value=q["model_answer"],
                        height=80, key=f"q_model_{qi}",
                    )
                    q["scoring_criteria"] = st.text_area(
                        "採点基準", value=q["scoring_criteria"],
                        height=120, key=f"q_crit_{qi}",
                        placeholder="例:\n- キーワード「〇〇」に言及: 5点\n- 論理的な説明: 5点\n- 自分の言葉で表現: 5点",
                    )

        # 小問配点チェック（リアルタイム）
        for q in questions:
            if q["type"] == "short_answer" and q.get("sub_questions"):
                sub_total = sum(sq["points"] for sq in q["sub_questions"])
                if sub_total != q["max_points"]:
                    color = "red" if sub_total > q["max_points"] else "orange"
                    st.markdown(
                        f":{color}[問{q['id']}: 小問合計 {sub_total}点 ≠ 配点 {q['max_points']}点]"
                    )

        # 確定ボタン
        st.divider()
        if questions and st.button("この採点基準を確定する", type="primary", key="load_gui_rubric"):
            # バリデーション: 小問の合計が配点を超えていないか
            validation_errors = []
            for q in questions:
                if q["type"] == "short_answer" and q.get("sub_questions"):
                    sub_total = sum(sq["points"] for sq in q["sub_questions"])
                    if sub_total > q["max_points"]:
                        validation_errors.append(
                            f"問{q['id']}: 小問の合計({sub_total}点)が配点({q['max_points']}点)を超えています"
                        )
            if validation_errors:
                for err in validation_errors:
                    st.error(err)
                st.stop()

            built_questions = []
            for q in questions:
                subs = [SubQuestion(id=s["id"], text=s["text"], answer=s["answer"], points=s["points"])
                        for s in q.get("sub_questions", [])]
                built_questions.append(Question(
                    id=q["id"], description=q["description"],
                    question_type=q["type"], max_points=q["max_points"],
                    scoring_criteria=q.get("scoring_criteria", ""),
                    model_answer=q.get("model_answer", ""),
                    sub_questions=subs,
                ))
            rubric = Rubric(
                title=rb_title, total_points=rb_total,
                pages_per_student=rb_pages, questions=built_questions,
                notes=rb_notes,
            )
            st.session_state.rubric = rubric
            # セッション状態を保持
            st.session_state.rb_title = rb_title
            st.session_state.rb_total = rb_total
            st.session_state.rb_pages = rb_pages
            st.session_state.rb_notes = rb_notes
            st.success(f"「{rubric.title}」を読み込みました（{len(rubric.questions)}問, {rubric.total_points}点満点）")

        # YAMLプレビュー
        if questions:
            with st.expander("作成した採点基準のプレビュー"):
                preview_questions = []
                for q in questions:
                    subs = [SubQuestion(id=s["id"], text=s["text"], answer=s["answer"], points=s["points"])
                            for s in q.get("sub_questions", [])]
                    preview_questions.append(Question(
                        id=q["id"], description=q["description"],
                        question_type=q["type"], max_points=q["max_points"],
                        scoring_criteria=q.get("scoring_criteria", ""),
                        model_answer=q.get("model_answer", ""),
                        sub_questions=subs,
                    ))
                preview_rubric = Rubric(
                    title=rb_title, total_points=rb_total,
                    pages_per_student=rb_pages, questions=preview_questions,
                    notes=rb_notes,
                )
                st.code(rubric_to_yaml(preview_rubric), language="yaml")

    else:
        # --- YAMLモード ---
        st.subheader("設定ファイル（YAML）で採点基準を読み込み")

        rubric_file = st.file_uploader("採点基準ファイル（.yaml形式）", type=["yaml", "yml"])
        sample_path = Path(__file__).parent / "rubrics" / "sample_rubric.yaml"
        default_yaml = ""
        if sample_path.exists():
            default_yaml = sample_path.read_text(encoding="utf-8")
        if rubric_file:
            default_yaml = rubric_file.read().decode("utf-8")

        rubric_text = st.text_area("採点基準（設定ファイル内容）", value=default_yaml, height=400)

        if st.button("採点基準を読み込む", type="primary", key="load_yaml_rubric"):
            try:
                rubric = load_rubric_from_yaml(rubric_text)
                st.session_state.rubric = rubric
                st.success(f"「{rubric.title}」を読み込みました（{len(rubric.questions)}問, {rubric.total_points}点満点）")
            except Exception as e:
                st.error(f"ファイルの読み込みに失敗しました。形式を確認してください。\n（詳細: {e}）")

    # 現在の採点基準表示
    if st.session_state.rubric:
        st.divider()
        r = st.session_state.rubric
        st.success(f"設定済み: 「{r.title}」 {len(r.questions)}問 / {r.total_points}点満点")
        st.info("**次のステップ →** 「2. 答案の取り込みと仮採点」タブに進んで、答案PDFをアップロードしてください。")


# ============================================================
# タブ2: 答案読み込み・仮採点
# ============================================================

with tab_scoring:
    st.header("答案の読み込みと仮採点")

    # --- ステッパーUI ---
    _session = st.session_state.session
    _has_pdf = len(st.session_state.student_groups) > 0
    _ocr_done = _session and _session.ocr_results and len(_session.ocr_results) > 0
    _ocr_reviewed = _session and _session.ocr_complete() if _session else False
    _graded = _session and _session.students and any(s.status != "pending" for s in _session.students) if _session else False

    _steps = [
        ("PDF取り込み", _has_pdf),
        ("文字読み取り", _ocr_done),
        ("読み取り確認", _ocr_reviewed),
        ("まとめ採点", _graded),
    ]
    _step_html = ""
    for _i, (_label, _done) in enumerate(_steps):
        if _done:
            _circle = f'<div style="width:28px;height:28px;border-radius:50%;background:#059669;color:white;display:flex;align-items:center;justify-content:center;font-size:0.7rem;font-weight:600;">✓</div>'
            _lbl_style = "color:#059669;font-weight:500;"
        elif _i == 0 or _steps[_i-1][1]:
            _circle = f'<div style="width:28px;height:28px;border-radius:50%;background:#2563a8;color:white;display:flex;align-items:center;justify-content:center;font-size:0.75rem;font-weight:600;box-shadow:0 0 0 3px rgba(37,99,168,0.2);">{_i+1}</div>'
            _lbl_style = "color:#2563a8;font-weight:600;"
        else:
            _circle = f'<div style="width:28px;height:28px;border-radius:50%;background:#e2e8f0;color:#94a3b8;display:flex;align-items:center;justify-content:center;font-size:0.75rem;font-weight:600;">{_i+1}</div>'
            _lbl_style = "color:#94a3b8;"
        _connector = f'<div style="flex:1;height:2px;background:{"#059669" if _done else "#e2e8f0"};margin:0 6px;align-self:center;"></div>' if _i < len(_steps) - 1 else ""
        _step_html += f'<div style="display:flex;flex-direction:column;align-items:center;min-width:70px;">{_circle}<div style="margin-top:4px;font-size:0.72rem;{_lbl_style}">{_label}</div></div>{_connector}'

    with st.container():
        st.markdown(f'<div style="display:flex;align-items:flex-start;justify-content:center;padding:12px 16px;margin-bottom:16px;background:white;border-radius:12px;border:1px solid #e2e8f0;">{_step_html}</div>', unsafe_allow_html=True)

    # --- PDF読み込み ---
    st.subheader("答案PDFのアップロード")
    st.caption("スキャンした答案のPDFファイルを取り込みます。")

    col_pdf1, col_pdf2 = st.columns([2, 1])
    with col_pdf1:
        pdf_file = st.file_uploader("答案PDFファイル", type=["pdf"], key="pdf_uploader")
    with col_pdf2:
        if st.session_state.rubric:
            pages_per = st.number_input(
                "1人あたりのページ数",
                min_value=1, max_value=10,
                value=st.session_state.rubric.pages_per_student,
            )
        else:
            pages_per = st.number_input("1人あたりのページ数", min_value=1, max_value=10, value=1)

    if pdf_file and st.button("答案を取り込む", type="primary"):
        with st.spinner("PDFを画像に変換中..."):
            pdf_bytes = pdf_file.read()
            images = pdf_to_images(pdf_bytes)
            st.session_state.images = images
            if len(images) % pages_per != 0:
                st.warning(
                    f"総ページ数 {len(images)} は「1人あたり{pages_per}ページ」で割り切れません。"
                    f"最後の学生のページが不完全になる可能性があります。"
                )
            groups = split_pages_by_student(images, pages_per)
            st.session_state.student_groups = groups
            st.success(f"{len(images)}ページ → {len(groups)}名分に分割しました")

    # プレビュー
    if st.session_state.student_groups:
        with st.expander(f"答案プレビュー（{len(st.session_state.student_groups)}名分）"):
            preview_idx = st.slider("学生番号", 1, len(st.session_state.student_groups), 1, key="preview_slider")
            group = st.session_state.student_groups[preview_idx - 1]
            for page_num, img in group:
                st.image(image_to_bytes(img), caption=f"ページ {page_num}", use_container_width=True)

    st.divider()

    # --- 共通チェック ---
    if not st.session_state.rubric:
        st.info(
            "このステップでは、採点基準をもとにAIが仮採点を行います。\n\n"
            "**次のアクション:** 「1. 採点基準」タブで採点基準を設定してください。"
        )
    elif not st.session_state.student_groups:
        st.info("上の「答案PDFのアップロード」から答案ファイルを取り込んでください。")
    else:
        rubric = st.session_state.rubric
        prov = build_provider()

        st.write(f"**試験**: {rubric.title} / **学生数**: {len(st.session_state.student_groups)}名 / **AI**: {prov.name}")

        # --- プライバシー通知 ---
        is_api = not isinstance(prov, DemoProvider)
        if is_api and not st.session_state.privacy_accepted:
            st.warning(
                "**個人情報に関する確認**\n\n"
                "読み取り・採点を実行すると、答案の画像や文字データが"
                "外部のAIサービスに送信されます。\n\n"
                "- 送信先: Google / Anthropic のAIサービス\n"
                "- 有料サービスのため、送信されたデータがAIの学習に使われることは**ありません**\n"
                "- データは処理後、一定期間で自動削除されます\n\n"
                "学校の情報管理規程に基づき、適切な許可を得た上でご利用ください。"
            )
            def _accept_privacy():
                st.session_state.privacy_accepted = True

            st.checkbox("上記を確認し、外部AIサービスへのデータ送信に同意します", key="privacy_check",
                        on_change=_accept_privacy)
        elif is_api:
            st.caption("✓ 外部AIサービスへのデータ送信に同意済み")

        can_run = isinstance(prov, DemoProvider) or st.session_state.privacy_accepted
        session = st.session_state.session

        # ==========================================================
        # Step 1: OCR（Phase 1）
        # ==========================================================
        st.subheader("ステップ1: 答案の文字読み取り（OCR）")

        if session and session.ocr_results:
            ocr_ok = sum(1 for o in session.ocr_results if o.status in ("ocr_done", "reviewed"))
            ocr_err = sum(1 for o in session.ocr_results if o.status == "pending" and o.ocr_error)
            st.success(f"読み取り完了: {ocr_ok}名分" + (f"（{ocr_err}名分は読み取れませんでした）" if ocr_err else ""))
        elif can_run:
            if st.button("文字の読み取りを開始", type="primary", key="start_ocr"):
                session = ScoringSession(
                    rubric_title=rubric.title,
                    pdf_filename=pdf_file.name if pdf_file else "uploaded.pdf",
                    pages_per_student=rubric.pages_per_student,
                )

                total = len(st.session_state.student_groups)
                progress = st.progress(0)
                status_text = st.empty()

                def on_ocr_progress(i, total_s):
                    status_text.text(f"読み取り中: 学生 {i + 1}/{total_s}...")
                    progress.progress((i + 1) / total_s)

                ocr_results, errors = ocr_all_students(
                    provider=prov,
                    student_groups=st.session_state.student_groups,
                    rubric=rubric,
                    on_student_ocr=on_ocr_progress,
                )

                session.ocr_results = ocr_results
                st.session_state.session = session
                save_session(session)
                status_text.empty()

                if errors:
                    for err in errors:
                        st.warning(err)
                st.success(f"読み取り完了: {len(ocr_results)}名分")
                st.rerun()

        # ==========================================================
        # Step 2: OCR確認・修正
        # ==========================================================
        if session and session.ocr_results:
            st.subheader("ステップ2: 読み取り結果の確認・修正")
            st.caption("AIが答案から読み取った文字を確認してください。読み間違いがあれば直接修正できます。修正したら「読み取り結果を保存」を押してください。")

            # 一括確認ボタン
            unreviewed = [o for o in session.ocr_results if o.status == "ocr_done" and not o.ocr_error]
            if unreviewed:
                def _bulk_review_ocr(ocr_list, sess):
                    for o in ocr_list:
                        o.status = "reviewed"
                    save_session(sess)

                st.button(f"全て確認済みにする（{len(unreviewed)}名）", key="ocr_bulk_review",
                          on_click=_bulk_review_ocr, args=(unreviewed, session))

            for ocr in session.ocr_results:
                if ocr.status == "pending" and ocr.ocr_error:
                    label = f"{ocr.student_id}（文字の読み取りに失敗）"
                else:
                    status_label = "確認済み" if ocr.status == "reviewed" else "未確認"
                    label = f"{ocr.student_id} {ocr.student_name or '(氏名不明)'}（{status_label}）"

                with st.expander(label):
                    if ocr.ocr_error:
                        st.error(f"文字の読み取りに失敗しました。答案画像が鮮明か確認してください。\n（詳細: {ocr.ocr_error}）")
                        continue

                    new_name = st.text_input(
                        "氏名", value=ocr.student_name,
                        key=f"ocr_name_{ocr.student_id}",
                    )
                    if new_name != ocr.student_name:
                        ocr.student_name = new_name

                    student_idx = int(ocr.student_id[1:]) - 1
                    has_images = (
                        st.session_state.student_groups
                        and student_idx < len(st.session_state.student_groups)
                    )

                    if has_images:
                        # 左: 答案画像、右: OCRテキスト
                        img_col, text_col = st.columns([1, 1])
                        with img_col:
                            st.caption("答案画像")
                            for pn, img in st.session_state.student_groups[student_idx]:
                                st.image(image_to_bytes(img), caption=f"ページ {pn}", use_container_width=True)
                        with text_col:
                            st.caption("読み取り結果")
                            for ans in ocr.answers:
                                acol1, acol2 = st.columns([4, 1])
                                with acol1:
                                    new_text = st.text_area(
                                        f"問{ans.question_id}",
                                        value=ans.transcribed_text,
                                        key=f"ocr_text_{ocr.student_id}_{ans.question_id}",
                                        height=68,
                                    )
                                    if new_text != ans.transcribed_text:
                                        ans.transcribed_text = new_text
                                        ans.manually_corrected = True
                                with acol2:
                                    conf_color = get_confidence_color(ans.confidence)
                                    st.markdown(f"読み取り精度: :{conf_color}[{format_confidence(ans.confidence)}]")
                                    if ans.manually_corrected:
                                        st.caption("(手動修正済み)")
                    else:
                        for ans in ocr.answers:
                            col1, col2 = st.columns([4, 1])
                            with col1:
                                new_text = st.text_area(
                                    f"問{ans.question_id}",
                                    value=ans.transcribed_text,
                                    key=f"ocr_text_{ocr.student_id}_{ans.question_id}",
                                    height=68,
                                )
                                if new_text != ans.transcribed_text:
                                    ans.transcribed_text = new_text
                                    ans.manually_corrected = True
                            with col2:
                                conf_color = get_confidence_color(ans.confidence)
                                st.markdown(f"読み取り精度: :{conf_color}[{format_confidence(ans.confidence)}]")
                                if ans.manually_corrected:
                                    st.caption("(手動修正済み)")

                    if ocr.status != "reviewed":
                        def _review_ocr(ocr_obj, sess):
                            ocr_obj.status = "reviewed"
                            save_session(sess)

                        st.button("確認済みにする", key=f"ocr_review_{ocr.student_id}",
                                  on_click=_review_ocr, args=(ocr, session))

            if st.button("読み取り結果を保存", key="save_ocr"):
                save_session(session)
                st.success("保存しました")

        # ==========================================================
        # Step 3: まとめ採点（Phase 2）
        # ==========================================================
        if session and session.ocr_complete():
            st.subheader("ステップ3: 設問ごとのまとめ採点")
            st.info(
                "同じ設問について全員分の解答をまとめてAIが採点します。"
                "読み取り済みの文字データを使うため、追加の通信は最小限です。"
            )

            rec_size, rec_reason = recommend_batch_size(rubric)
            batch_size = st.number_input(
                "1回あたりの処理人数（バッチサイズ）",
                min_value=3, max_value=30, value=rec_size,
                help="記述問題が多い場合は少なめ（10〜12人）、漢字の読み書きが中心の場合は多め（20人）が目安です",
                key="batch_size_input",
            )
            st.caption(f"推奨: {rec_size}名 — {rec_reason}")

            already_graded = bool(session.students and any(
                s.status != "pending" for s in session.students
            ))
            if already_graded:
                st.success("まとめ採点は完了しています。")
                st.info("**次のステップ →** 「3. 確認・修正」タブで、AIの採点結果を確認してください。特に⚠️マークの項目はAIの自信度が低いため、重点的に確認してください。")

            rescore_confirmed = True
            if already_graded:
                rescore_confirmed = st.checkbox(
                    "今の採点結果を消して、もう一度採点をやり直す",
                    value=False,
                    key="rescore_confirm_check",
                    help="チェックすると再採点ボタンが有効になります。現在の採点結果は上書きされます。",
                )

            if can_run and st.button(
                "もう一度採点する" if already_graded else "まとめ採点を開始する",
                type="primary", key="start_horizontal",
                disabled=(already_graded and not rescore_confirmed),
            ):
                rubric = st.session_state.rubric
                refs = session.get_reference_students() or None
                progress = st.progress(0)
                status_text = st.empty()
                total_q = len(rubric.questions)

                def on_q_progress(q_idx, total, question, batch_idx, total_batches):
                    batch_info = f"（{batch_idx + 1}/{total_batches}回目）" if total_batches > 1 else ""
                    status_text.text(
                        f"問{question.id} を採点中{batch_info}... ({q_idx + 1}/{total}問)"
                    )
                    progress.progress(min((q_idx + 1) / total, 1.0))

                errors = run_horizontal_grading(
                    provider=prov,
                    rubric=rubric,
                    session=session,
                    reference_students=refs,
                    batch_size=int(batch_size),
                    on_question_progress=on_q_progress,
                    enable_verification=st.session_state.get("enable_verification", False),
                )

                save_session(session)
                status_text.empty()

                if errors:
                    st.session_state["grading_errors"] = errors
                else:
                    st.session_state["grading_errors"] = []
                    st.session_state["grading_success"] = True
                st.rerun()

    # 採点結果のメッセージ表示（rerun後も残る）
    if st.session_state.get("grading_errors"):
        for err in st.session_state["grading_errors"]:
            st.warning(err)
        st.warning(f"採点完了（{len(st.session_state['grading_errors'])}件のエラーあり）")
        st.session_state["grading_errors"] = []
    elif st.session_state.get("grading_success"):
        st.success("まとめ採点が完了しました。「3. 確認・修正」タブで結果を確認してください。")
        st.session_state["grading_success"] = False

    # セッション概要
    if st.session_state.session:
        summary = st.session_state.session.summary()
        st.divider()
        cols = st.columns(4)
        cols[0].metric("学生数", summary["total_students"])
        cols[1].metric("採点済み", summary["scored"])
        cols[2].metric("確定済み", summary["reviewed"])
        cols[3].metric("要確認項目", summary["needs_review_items"])

        # --- 参考例を使った再採点（横断モード）---
        session = st.session_state.session
        refs = session.get_reference_students()
        if refs and session.ocr_results:
            unconfirmed = [
                s for s in session.students
                if not s.is_reference and s.status in ("ai_scored", "pending")
            ]
            if unconfirmed:
                st.divider()
                st.subheader("お手本を使った再採点")
                st.info(
                    f"**{len(refs)}件のお手本**を使って{len(unconfirmed)}名を再採点します。\n"
                    "読み取り済みのデータを使うため、短時間で完了します。"
                )

                can_rerun = isinstance(build_provider(), DemoProvider) or st.session_state.privacy_accepted
                if can_rerun and st.button("お手本を使って再採点する", type="primary", key="re_grade_horizontal"):
                    prov = build_provider()
                    rubric = st.session_state.rubric
                    target_ids = [s.student_id for s in unconfirmed]
                    progress = st.progress(0)
                    status_text = st.empty()

                    def on_q_progress_re(q_idx, total, question, batch_idx, total_batches):
                        status_text.text(f"再採点: 問{question.id} ({q_idx + 1}/{total})")
                        progress.progress(min((q_idx + 1) / total, 1.0))

                    errors = run_horizontal_grading(
                        provider=prov,
                        rubric=rubric,
                        session=session,
                        reference_students=refs,
                        batch_size=DEFAULT_BATCH_SIZE,
                        on_question_progress=on_q_progress_re,
                        student_ids_to_grade=target_ids,
                        enable_verification=st.session_state.get("enable_verification", False),
                    )

                    for s in session.students:
                        if s.student_id in target_ids:
                            s.ai_overall_comment = (
                                (s.ai_overall_comment or "") + "\n[お手本をもとに再採点しました]"
                            )

                    save_session(session)
                    status_text.empty()
                    if errors:
                        for err in errors:
                            st.warning(err)
                    st.success("再採点完了。「確認・修正」タブで確認してください。")
                    st.rerun()


# ============================================================
# タブ3: 確認・修正
# ============================================================

with tab_review:
    st.header("採点結果の確認・修正")

    with st.expander("ステータスの説明", expanded=False):
        st.markdown(
            "| アイコン | 状態 | 説明 |\n"
            "|:---:|:---|:---|\n"
            "| ⏳ | 未採点 | まだAIが採点していません |\n"
            "| 🤖 | AI仮採点済み | AIが仮採点しました。教員の確認が必要です |\n"
            "| ✅ | 確定 | 教員が確認・確定済みです |"
        )

    if not st.session_state.session or not st.session_state.session.students:
        st.info("まだ採点結果がありません。「2. 答案の取り込みと仮採点」タブで仮採点を行ってください。")
    else:
        session = st.session_state.session

        if session.updated_at:
            st.caption(f"最終保存: {session.updated_at[:19].replace('T', ' ')}")

        # バッチ間キャリブレーション分析
        if (
            session.grading_mode == "horizontal"
            and st.session_state.rubric
            and len(session.students) > DEFAULT_BATCH_SIZE
        ):
            cal_warnings = analyze_batch_calibration(
                session, st.session_state.rubric, DEFAULT_BATCH_SIZE,
            )
            if cal_warnings:
                with st.expander("採点のばらつきチェック", expanded=False):
                    for w in cal_warnings:
                        icon = "⚠️" if w["severity"] == "warning" else "ℹ️"
                        st.markdown(
                            f"{icon} **問{w['question_id']}** ({w['description'][:30]}): "
                            f"グループ間の点数のばらつき 最大 **{w['max_deviation']}点** "
                            f"(全体平均: {w['overall_mean']}点)"
                        )
                        if w["severity"] == "warning":
                            st.caption(
                                "AIの採点基準にばらつきがある可能性があります。"
                                "この設問の得点を特に注意して確認してください。"
                            )

        # 表示モード切替
        review_mode = st.radio(
            "表示モード",
            ["学生別", "一覧テーブル"],
            horizontal=True,
            key="review_view_mode",
            help="「一覧テーブル」では全学生の得点を一覧で確認・編集できます",
        )

        if review_mode == "一覧テーブル":
            # --- サマリーテーブルモード ---
            import pandas as pd
            rubric = st.session_state.rubric
            pivot_data = []
            for s in session.students:
                row = {
                    "学生番号": s.student_id,
                    "氏名": s.student_name or "(不明)",
                }
                for qs in s.question_scores:
                    row[f"問{qs.question_id}"] = qs.score
                row["合計"] = s.total_score
                row["状態"] = {"pending": "未採点", "ai_scored": "AI仮採点済み", "confirmed": "確定", "reviewed": "確定"}.get(s.status, s.status)
                pivot_data.append(row)

            if pivot_data:
                df = pd.DataFrame(pivot_data)
                col_config = {}
                if rubric:
                    for q in rubric.questions:
                        col_config[f"問{q.id}"] = st.column_config.NumberColumn(
                            min_value=0, max_value=float(q.max_points), step=0.5,
                            help=f"配点: {q.max_points}点",
                        )
                col_config["合計"] = st.column_config.NumberColumn(format="%.1f")

                edited = st.data_editor(
                    df,
                    column_config=col_config,
                    disabled=["学生番号", "氏名", "合計", "状態"],
                    use_container_width=True,
                    key="score_table_editor",
                )

                if st.button("変更を保存", key="save_table_scores", type="primary"):
                    for i, row in edited.iterrows():
                        sid = row["学生番号"]
                        for s in session.students:
                            if s.student_id == sid:
                                for qs in s.question_scores:
                                    col_name = f"問{qs.question_id}"
                                    if col_name in row and row[col_name] != qs.score:
                                        qs.score = float(row[col_name])
                                s.recalculate_total()
                    save_session(session)
                    st.success("保存しました")
                    st.rerun()

        # --- 学生別モード: フィルターと個別表示 ---
        if review_mode != "学生別":
            status_filter = ["ai_scored", "pending"]
            show_review_only = False
        else:
            fcol1, fcol2 = st.columns(2)
            with fcol1:
                status_filter = st.multiselect(
                    "状態でフィルタ",
                    ["pending", "ai_scored", "confirmed"],
                    default=["ai_scored", "pending"],
                    format_func=lambda x: {
                        "pending": "⏳ 未採点", "ai_scored": "🤖 AI仮採点済み",
                        "confirmed": "✅ 確定",
                    }.get(x, x),
                )
            with fcol2:
                show_review_only = st.checkbox("要確認のみ表示", value=False)

        # 旧データの "reviewed" は "confirmed" と同等に扱う
        effective_filter = set(status_filter)
        if "confirmed" in effective_filter:
            effective_filter.add("reviewed")

        filtered = [
            s for s in session.students
            if s.status in effective_filter
            and (not show_review_only or s.review_needed_count() > 0)
        ]

        if review_mode == "学生別" and not filtered:
            st.info("該当する学生がいません。フィルタ条件を変更してください。")

        # --- 一括操作バー（学生別モードのみ） ---
        if review_mode == "学生別" and filtered:
            _unconfirmed = [s for s in filtered if s.status not in ("confirmed", "reviewed")]
            _safe_to_confirm = [s for s in _unconfirmed if s.review_needed_count() == 0]
            _needs_review_students = [s for s in filtered if s.review_needed_count() > 0]

            bulk_col1, bulk_col2, bulk_col3 = st.columns([1, 1, 2])
            with bulk_col1:
                if _safe_to_confirm:
                    def _bulk_confirm(students, sess):
                        for s in students:
                            s.status = "confirmed"
                        save_session(sess)

                    st.button(
                        f"要確認なしの{len(_safe_to_confirm)}名を一括確定",
                        on_click=_bulk_confirm,
                        args=(_safe_to_confirm, session),
                        type="primary",
                        key="bulk_confirm_btn",
                    )
            with bulk_col2:
                if _needs_review_students:
                    _total_review = sum(s.review_needed_count() for s in _needs_review_students)
                    def _bulk_mark_reviewed(students):
                        for s in students:
                            for qs in s.question_scores:
                                if qs.needs_review:
                                    qs.reviewed = True

                    st.button(
                        f"要確認{_total_review}件を確認済みに",
                        on_click=_bulk_mark_reviewed,
                        args=(_needs_review_students,),
                        key="bulk_review_btn",
                    )
            with bulk_col3:
                st.caption(f"表示中: {len(filtered)}名 / 全{len(session.students)}名")

        for student in (filtered if review_mode == "学生別" else []):
            emoji = get_status_emoji(student.status)
            review_badge = f" ⚠️{student.review_needed_count()}件" if student.review_needed_count() > 0 else ""
            student_idx = session.students.index(student)

            with st.expander(
                f"{emoji} {student.student_id} {student.student_name or '(氏名不明)'}"
                f" — {student.total_score}/{student.total_max_points}点{review_badge}",
                expanded=(student.review_needed_count() > 0),
            ):
                # ステータスバッジ行
                with st.container():
                    badges = f"{status_badge_html(student.status)} {review_needed_badge_html(student.review_needed_count())}"
                    st.markdown(badges, unsafe_allow_html=True)

                # 答案画像
                if st.session_state.student_groups and student_idx < len(st.session_state.student_groups):
                    with st.expander("答案画像を表示"):
                        for page_num, img in st.session_state.student_groups[student_idx]:
                            st.image(image_to_bytes(img), caption=f"ページ {page_num}", use_container_width=True)

                if student.ai_overall_comment:
                    st.markdown(f"**AI総合コメント:** {student.ai_overall_comment}")

                # 各設問
                for qs in student.question_scores:
                    conf_color = get_confidence_color(qs.confidence)
                    review_mark = "⚠️ " if qs.needs_review and not qs.reviewed else ""

                    verified_mark = " ✓検証済" if "【検証結果】" in qs.comment else ""
                    st.markdown(f"**{review_mark}問{qs.question_id}** (AIの自信度: :{conf_color}[{format_confidence(qs.confidence)}]){verified_mark}")

                    qc1, qc2 = st.columns([3, 1])
                    with qc1:
                        st.text_area(
                            "読み取りテキスト", value=qs.transcribed_text,
                            key=f"trans_{student.student_id}_{qs.question_id}",
                            height=68, disabled=True,
                        )
                        if qs.comment:
                            st.info(f"💬 {qs.comment}")

                    with qc2:
                        new_score = st.number_input(
                            "得点", min_value=0.0, max_value=float(qs.max_points),
                            value=float(qs.score), step=0.5,
                            key=f"score_{student.student_id}_{qs.question_id}",
                        )
                        if new_score != qs.score:
                            qs.score = new_score
                            student.recalculate_total()
                            save_session(session)
                        st.caption(f"/ {qs.max_points}点")

                        if qs.ai_score is not None and abs(qs.score - qs.ai_score) > 0.01:
                            def _restore_ai_score(q_score, s, sess):
                                q_score.score = q_score.ai_score
                                s.recalculate_total()
                                save_session(sess)

                            st.button(
                                f"AIスコアに戻す ({qs.ai_score:.1f}点)",
                                key=f"restore_ai_{student.student_id}_{qs.question_id}",
                                on_click=_restore_ai_score,
                                args=(qs, student, session),
                            )

                        if qs.needs_review and not qs.reviewed:
                            def _mark_reviewed(q_score, sess):
                                q_score.reviewed = True
                                save_session(sess)

                            st.button("確認済み", key=f"rev_{student.student_id}_{qs.question_id}",
                                      on_click=_mark_reviewed, args=(qs, session))

                st.divider()
                notes = st.text_area(
                    "教員メモ", value=student.reviewer_notes,
                    key=f"notes_{student.student_id}", height=68,
                )
                student.reviewer_notes = notes

                def _confirm_student(s, sess):
                    s.status = "confirmed"
                    save_session(sess)

                def _toggle_reference(s, sess):
                    s.is_reference = not s.is_reference
                    save_session(sess)

                bcol1, bcol2, bcol3 = st.columns(3)
                with bcol1:
                    if student.status not in ("confirmed", "reviewed"):
                        st.button("確定する", key=f"mk_conf_{student.student_id}",
                                  on_click=_confirm_student, args=(student, session))
                with bcol2:
                    ref_label = "お手本の指定を解除" if student.is_reference else "お手本に指定する"
                    if student.status in ("reviewed", "confirmed"):
                        st.button(ref_label, key=f"ref_{student.student_id}",
                                  on_click=_toggle_reference, args=(student, session))
                with bcol3:
                    if st.button("保存", key=f"save_{student.student_id}"):
                        save_session(session)
                        st.success("保存しました")

                if student.is_reference:
                    st.caption("📌 この答案はAI再採点のお手本として使用されます")


# ============================================================
# タブ4: 結果出力
# ============================================================

with tab_export:
    st.header("成績の書き出し")

    if not st.session_state.session or not st.session_state.session.students:
        st.info("採点結果がありません。「2. 答案の取り込みと仮採点」タブで採点を行ってください。")
    else:
        session = st.session_state.session
        summary = session.summary()

        cols = st.columns(5)
        cols[0].metric("学生数", summary["total_students"])
        cols[1].metric("採点済み", summary["scored"])
        cols[2].metric("確定済み", summary["reviewed"])
        cols[3].metric("要確認", summary["needs_review_items"])
        cols[4].metric("平均点", summary["average_score"])

        unconfirmed = summary["total_students"] - summary["reviewed"]
        if unconfirmed > 0:
            st.warning(f"⚠️ {unconfirmed}名の採点がまだ確定されていません。")

        # 得点分布
        st.divider()
        st.subheader("得点分布")
        scored_students = [s for s in session.students if s.status != "pending"]
        if scored_students:
            import pandas as pd
            scores_df = pd.DataFrame({
                "学生": [s.student_id for s in scored_students],
                "得点": [s.total_score for s in scored_students],
            })
            st.bar_chart(scores_df.set_index("学生"))

        # エクスポート
        st.divider()
        st.subheader("ファイル出力")
        st.write("**成績表のダウンロード**（Excelで開けます）")
        csv_content = export_csv(session)
        st.download_button(
            "成績表をダウンロード（CSV形式）",
            data=csv_content.encode("utf-8-sig"),
            file_name=f"results_{session.session_id}.csv",
            mime="text/csv",
        )
        with st.expander("その他の形式"):
            st.write("**JSON形式**（バックアップ・復元用の詳細データ）")
            import json
            json_content = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
            st.download_button(
                "詳細データをダウンロード（JSON形式）",
                data=json_content.encode("utf-8"),
                file_name=f"session_{session.session_id}.json",
                mime="application/json",
            )

        # 一覧テーブル
        st.divider()
        st.subheader("採点結果一覧")
        if scored_students:
            table_data = []
            for s in session.students:
                table_data.append({
                    "学生番号": s.student_id,
                    "氏名": s.student_name or "(不明)",
                    "合計点": s.total_score,
                    "満点": s.total_max_points,
                    "状態": get_status_emoji(s.status) + " " + {"pending": "未採点", "ai_scored": "AI仮採点済み", "confirmed": "確定", "reviewed": "確定"}.get(s.status, s.status),
                    "要確認": s.review_needed_count(),
                    "メモ": s.reviewer_notes,
                })
            st.dataframe(table_data, use_container_width=True)
