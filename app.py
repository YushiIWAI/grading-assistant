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


def build_provider():
    """現在の設定からプロバイダーを構築する"""
    provider_name = st.session_state.get("provider_choice", "demo")
    if provider_name == "gemini" and st.session_state.gemini_key:
        model = st.session_state.get("gemini_model", "gemini-2.5-flash")
        return GeminiProvider(st.session_state.gemini_key, model)
    elif provider_name == "anthropic" and st.session_state.anthropic_key:
        model = st.session_state.get("anthropic_model", "claude-sonnet-4-20250514")
        return AnthropicProvider(st.session_state.anthropic_key, model)
    else:
        return DemoProvider()


# ============================================================
# サイドバー
# ============================================================

with st.sidebar:
    st.title("採点支援アプリ")
    st.caption("プロトタイプ v0.2")

    st.divider()

    # --- API設定 ---
    st.subheader("APIプロバイダー")
    provider = st.radio(
        "使用するAI",
        ["gemini", "anthropic", "demo"],
        format_func=lambda x: {
            "gemini": "Google Gemini",
            "anthropic": "Anthropic Claude",
            "demo": "デモモード（APIなし）",
        }[x],
        index=0,
        key="provider_choice",
    )

    if provider == "gemini":
        st.text_input(
            "Google API キー",
            value=st.session_state.gemini_key,
            type="password",
            key="gemini_key",
            help="Google AI Studio (aistudio.google.com) で取得できます",
        )
        st.selectbox(
            "モデル",
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
            "Anthropic API キー",
            value=st.session_state.anthropic_key,
            type="password",
            key="anthropic_key",
            help="console.anthropic.com で取得（最低$5の課金が必要）",
        )
        st.selectbox(
            "モデル",
            list(AnthropicProvider.MODELS.keys()),
            format_func=lambda x: AnthropicProvider.MODELS[x],
            key="anthropic_model",
        )
        if st.session_state.anthropic_key:
            st.success("APIキー設定済み")
        else:
            st.warning("APIキーを入力してください")

    else:
        st.info("デモモード: ランダムな仮採点結果で動作確認できます")

    st.divider()

    # --- 過去のセッション ---
    st.subheader("保存済みセッション")
    sessions = list_sessions()
    if sessions:
        options = {s["session_id"]: f'{s["rubric_title"]} ({s["student_count"]}名)' for s in sessions}
        selected = st.selectbox(
            "読み込むセッション", ["（新規）"] + list(options.keys()),
            format_func=lambda x: options.get(x, x),
        )
        if selected != "（新規）" and st.button("読み込む"):
            loaded = load_session(selected)
            if loaded:
                st.session_state.session = loaded
                st.success(f"セッション {selected} を読み込みました")
                st.rerun()
    else:
        st.caption("保存済みセッションはありません")

    st.divider()
    st.caption(
        "⚠️ このツールのAI判定は仮採点です。\n"
        "最終成績は必ず教員が確認してください。"
    )


# ============================================================
# メインコンテンツ
# ============================================================

tab_rubric, tab_scoring, tab_review, tab_export = st.tabs([
    "1. 採点基準",
    "2. 答案読み込み・仮採点",
    "3. 確認・修正",
    "4. 結果出力",
])


# ============================================================
# タブ1: 採点基準（YAMLアップロード or GUIビルダー）
# ============================================================

with tab_rubric:
    st.header("採点基準の作成")

    method = st.radio(
        "作成方法",
        ["gui", "yaml"],
        format_func=lambda x: {"gui": "フォーム入力で作成", "yaml": "YAMLファイルで読み込み"}[x],
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

        # 設問追加ボタン
        add_col1, add_col2 = st.columns(2)
        with add_col1:
            if st.button("短答問題を追加"):
                questions.append({
                    "id": len(questions) + 1,
                    "description": "",
                    "type": "short_answer",
                    "max_points": 10,
                    "scoring_criteria": "",
                    "model_answer": "",
                    "sub_questions": [],
                })
                st.rerun()
        with add_col2:
            if st.button("記述問題を追加"):
                questions.append({
                    "id": len(questions) + 1,
                    "description": "",
                    "type": "descriptive",
                    "max_points": 15,
                    "scoring_criteria": "",
                    "model_answer": "",
                    "sub_questions": [],
                })
                st.rerun()

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
                    if st.button("この問題を削除", key=f"q_del_{qi}"):
                        questions.pop(qi)
                        # IDを振り直す
                        for i, qq in enumerate(questions):
                            qq["id"] = i + 1
                        st.rerun()

                if q["type"] == "short_answer":
                    st.caption("小問（漢字の読み、語句の穴埋めなど）")
                    subs = q["sub_questions"]

                    if st.button("小問を追加", key=f"add_sub_{qi}"):
                        subs.append({"id": f"{q['id']}-{len(subs)+1}", "text": "", "answer": "", "points": 2})
                        st.rerun()

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

        # 読み込みボタン
        st.divider()
        if questions and st.button("この採点基準を読み込む", type="primary", key="load_gui_rubric"):
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
            with st.expander("生成されるYAMLのプレビュー"):
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
        st.subheader("YAMLファイルで採点基準を読み込み")

        rubric_file = st.file_uploader("採点基準YAMLファイル", type=["yaml", "yml"])
        sample_path = Path(__file__).parent / "rubrics" / "sample_rubric.yaml"
        default_yaml = ""
        if sample_path.exists():
            default_yaml = sample_path.read_text(encoding="utf-8")
        if rubric_file:
            default_yaml = rubric_file.read().decode("utf-8")

        rubric_text = st.text_area("採点基準YAML", value=default_yaml, height=400)

        if st.button("採点基準を読み込む", type="primary", key="load_yaml_rubric"):
            try:
                rubric = load_rubric_from_yaml(rubric_text)
                st.session_state.rubric = rubric
                st.success(f"「{rubric.title}」を読み込みました（{len(rubric.questions)}問, {rubric.total_points}点満点）")
            except Exception as e:
                st.error(f"YAML読み込みエラー: {e}")

    # 現在の採点基準表示
    if st.session_state.rubric:
        st.divider()
        r = st.session_state.rubric
        st.success(f"読み込み済み: 「{r.title}」 {len(r.questions)}問 / {r.total_points}点満点")


# ============================================================
# タブ2: 答案読み込み・仮採点
# ============================================================

with tab_scoring:
    st.header("答案の読み込みと仮採点")

    # --- PDF読み込み ---
    st.subheader("答案PDFのアップロード")

    col_pdf1, col_pdf2 = st.columns([2, 1])
    with col_pdf1:
        pdf_file = st.file_uploader("答案PDFファイル", type=["pdf"])
    with col_pdf2:
        if st.session_state.rubric:
            pages_per = st.number_input(
                "1人あたりのページ数",
                min_value=1, max_value=10,
                value=st.session_state.rubric.pages_per_student,
            )
        else:
            pages_per = st.number_input("1人あたりのページ数", min_value=1, max_value=10, value=1)

    if pdf_file and st.button("PDFを読み込む", type="primary"):
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
            preview_idx = st.slider("学生番号", 1, len(st.session_state.student_groups), 1)
            group = st.session_state.student_groups[preview_idx - 1]
            for page_num, img in group:
                st.image(image_to_bytes(img), caption=f"ページ {page_num}", use_container_width=True)

    st.divider()

    # --- 共通チェック ---
    if not st.session_state.rubric:
        st.warning("先に「採点基準」タブで採点基準を読み込んでください。")
    elif not st.session_state.student_groups:
        st.warning("先に上のセクションで答案PDFを読み込んでください。")
    else:
        rubric = st.session_state.rubric
        prov = build_provider()

        st.write(f"**試験**: {rubric.title} / **学生数**: {len(st.session_state.student_groups)}名 / **AI**: {prov.name}")

        # --- プライバシー通知 ---
        is_api = not isinstance(prov, DemoProvider)
        if is_api and not st.session_state.privacy_accepted:
            st.warning(
                "**個人情報に関する確認**\n\n"
                "読み取り・採点を実行すると、答案の画像やテキストが"
                "外部APIサーバーに送信されます。\n\n"
                "- 送信先: Google / Anthropic のAPIサーバー\n"
                "- 有料APIのため、送信データはAIモデルの学習には**使用されません**\n"
                "- データはAPIの処理後、一定期間で自動削除されます\n\n"
                "学校の情報管理規程に基づき、適切な許可を得た上でご利用ください。"
            )
            if st.checkbox("上記を確認し、外部API送信に同意します", key="privacy_check"):
                st.session_state.privacy_accepted = True
                st.rerun()
        elif is_api:
            st.caption("✓ 外部API送信に同意済み")

        can_run = isinstance(prov, DemoProvider) or st.session_state.privacy_accepted
        session = st.session_state.session

        # ==========================================================
        # Step 1: OCR（Phase 1）
        # ==========================================================
        st.subheader("Step 1: 答案テキスト読み取り (OCR)")

        if session and session.ocr_results:
            ocr_ok = sum(1 for o in session.ocr_results if o.status in ("ocr_done", "reviewed"))
            ocr_err = sum(1 for o in session.ocr_results if o.status == "pending" and o.ocr_error)
            st.success(f"読み取り済み: {ocr_ok}名" + (f"（{ocr_err}名エラー）" if ocr_err else ""))
        elif can_run:
            if st.button("読み取り開始", type="primary", key="start_ocr"):
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
            st.subheader("Step 2: 読み取り結果の確認・修正")
            st.caption("AIが読み取ったテキストを確認し、必要に応じて修正してください。修正後は「保存」を押してください。")

            # 一括確認ボタン
            unreviewed = [o for o in session.ocr_results if o.status == "ocr_done" and not o.ocr_error]
            if unreviewed:
                if st.button(f"全て確認済みにする（{len(unreviewed)}名）", key="ocr_bulk_review"):
                    for o in unreviewed:
                        o.status = "reviewed"
                    save_session(session)
                    st.rerun()

            for ocr in session.ocr_results:
                if ocr.status == "pending" and ocr.ocr_error:
                    label = f"{ocr.student_id} (OCRエラー)"
                else:
                    status_label = "確認済み" if ocr.status == "reviewed" else "未確認"
                    label = f"{ocr.student_id} {ocr.student_name or '(氏名不明)'} ({status_label})"

                with st.expander(label):
                    if ocr.ocr_error:
                        st.error(f"OCRエラー: {ocr.ocr_error}")
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
                                    st.markdown(f"確信度: :{conf_color}[{ans.confidence}]")
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
                                st.markdown(f"確信度: :{conf_color}[{ans.confidence}]")
                                if ans.manually_corrected:
                                    st.caption("(手動修正済み)")

                    if ocr.status != "reviewed":
                        if st.button("確認済みにする", key=f"ocr_review_{ocr.student_id}"):
                            ocr.status = "reviewed"
                            save_session(session)
                            st.rerun()

            if st.button("読み取り結果を保存", key="save_ocr"):
                save_session(session)
                st.success("保存しました")

        # ==========================================================
        # Step 3: 横断採点（Phase 2）
        # ==========================================================
        if session and session.ocr_complete():
            st.subheader("Step 3: 横断採点")
            st.info(
                "各設問ごとに全学生の解答をまとめて採点します。"
                "テキストのみで採点するため、画像の再送信は不要です。"
            )

            rec_size, rec_reason = recommend_batch_size(rubric)
            batch_size = st.number_input(
                "バッチサイズ（1回のAPI呼び出しに含める学生数）",
                min_value=3, max_value=30, value=rec_size,
                help="解答が長い記述問題は小さめ(10-12)、漢字問題は大きめ(20)がおすすめ",
                key="batch_size_input",
            )
            st.caption(f"💡 推奨: {rec_size}名 — {rec_reason}")

            already_graded = session.students and any(
                s.status != "pending" for s in session.students
            )
            if already_graded:
                st.success("横断採点は完了しています。「確認・修正」タブで結果を確認してください。")

            rescore_confirmed = True
            if already_graded:
                rescore_confirmed = st.checkbox(
                    "既存の採点結果を上書きして再採点する",
                    value=False,
                    key="rescore_confirm_check",
                    help="チェックすると再採点ボタンが有効になります。既存のスコアは上書きされます。",
                )

            if can_run and st.button(
                "再採点する" if already_graded else "横断採点を開始する",
                type="primary", key="start_horizontal",
                disabled=(already_graded and not rescore_confirmed),
            ):
                rubric = st.session_state.rubric
                refs = session.get_reference_students() or None
                progress = st.progress(0)
                status_text = st.empty()
                total_q = len(rubric.questions)

                def on_q_progress(q_idx, total, question, batch_idx, total_batches):
                    batch_info = f" (バッチ {batch_idx + 1}/{total_batches})" if total_batches > 1 else ""
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
        st.success("横断採点が完了しました。「確認・修正」タブで結果を確認してください。")
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
                st.subheader("参考例を使った再採点（横断モード）")
                st.info(
                    f"**{len(refs)}件の参考例**を使って{len(unconfirmed)}名を再採点します。\n"
                    "OCR結果を再利用するため、画像の再送信は不要です（高速・低コスト）。"
                )

                can_rerun = isinstance(build_provider(), DemoProvider) or st.session_state.privacy_accepted
                if can_rerun and st.button("再採点を開始する", type="primary", key="re_grade_horizontal"):
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
                    )

                    for s in session.students:
                        if s.student_id in target_ids:
                            s.ai_overall_comment = (
                                (s.ai_overall_comment or "") + "\n[参考例をもとに再採点（横断モード）]"
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
            "| 🤖 | AI採点済み | AIが仮採点しました。教員の確認が必要です |\n"
            "| ✅ | 確定 | 教員が確認・確定済みです |"
        )

    if not st.session_state.session or not st.session_state.session.students:
        st.warning("先に「答案読み込み・仮採点」タブで採点を実行してください。")
    else:
        session = st.session_state.session

        # フィルター
        fcol1, fcol2 = st.columns(2)
        with fcol1:
            status_filter = st.multiselect(
                "状態でフィルタ",
                ["pending", "ai_scored", "confirmed"],
                default=["ai_scored", "pending"],
                format_func=lambda x: {
                    "pending": "⏳ 未採点", "ai_scored": "🤖 AI採点済み",
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

        if not filtered:
            st.info("該当する学生がいません。フィルタ条件を変更してください。")

        for student in filtered:
            emoji = get_status_emoji(student.status)
            review_badge = f" ⚠️{student.review_needed_count()}件" if student.review_needed_count() > 0 else ""
            student_idx = session.students.index(student)

            with st.expander(
                f"{emoji} {student.student_id} {student.student_name or '(氏名不明)'}"
                f" — {student.total_score}/{student.total_max_points}点{review_badge}",
                expanded=(student.review_needed_count() > 0),
            ):
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

                    st.markdown(f"**{review_mark}問{qs.question_id}** (確信度: :{conf_color}[{qs.confidence}])")

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
                        st.caption(f"/ {qs.max_points}点")

                        if qs.needs_review and not qs.reviewed:
                            if st.button("確認済み", key=f"rev_{student.student_id}_{qs.question_id}"):
                                qs.reviewed = True
                                st.rerun()

                st.divider()
                notes = st.text_area(
                    "教員メモ", value=student.reviewer_notes,
                    key=f"notes_{student.student_id}", height=68,
                )
                student.reviewer_notes = notes

                bcol1, bcol2, bcol3 = st.columns(3)
                with bcol1:
                    if student.status not in ("confirmed", "reviewed") and st.button("確定する", key=f"mk_conf_{student.student_id}"):
                        student.status = "confirmed"
                        save_session(session)
                        st.rerun()
                with bcol2:
                    ref_label = "参考例を解除" if student.is_reference else "参考例にする"
                    if student.status in ("reviewed", "confirmed"):
                        if st.button(ref_label, key=f"ref_{student.student_id}"):
                            student.is_reference = not student.is_reference
                            save_session(session)
                            st.rerun()
                with bcol3:
                    if st.button("保存", key=f"save_{student.student_id}"):
                        save_session(session)
                        st.success("保存しました")

                if student.is_reference:
                    st.caption("📌 この答案はAI再採点の参考例として使用されます")


# ============================================================
# タブ4: 結果出力
# ============================================================

with tab_export:
    st.header("結果の出力")

    if not st.session_state.session or not st.session_state.session.students:
        st.warning("採点結果がありません。")
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
        ecol1, ecol2 = st.columns(2)
        with ecol1:
            st.write("**CSV出力**（Excel等で開けます）")
            csv_content = export_csv(session)
            st.download_button(
                "CSVをダウンロード",
                data=csv_content.encode("utf-8-sig"),
                file_name=f"results_{session.session_id}.csv",
                mime="text/csv",
            )
        with ecol2:
            st.write("**JSON出力**（データ保存用）")
            import json
            json_content = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
            st.download_button(
                "JSONをダウンロード",
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
                    "状態": get_status_emoji(s.status) + " " + s.status,
                    "要確認": s.review_needed_count(),
                    "メモ": s.reviewer_notes,
                })
            st.dataframe(table_data, use_container_width=True)
