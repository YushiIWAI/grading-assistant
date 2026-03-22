"""タブ2: 答案読み込み・仮採点 モジュール"""
from __future__ import annotations

import streamlit as st

import api_client
from api_client import (
    ApiClientError,
    create_session_record,
    import_csv as import_csv_via_api,
    refine_rubric as refine_rubric_via_api,
    run_horizontal_grading as run_horizontal_grading_via_api,
    run_ocr as run_ocr_via_api,
    save_session,
)
from models import GradingOptions
from pdf_processor import image_to_bytes, pdf_to_images, split_pages_by_student
from scoring_engine import (
    DEFAULT_BATCH_SIZE,
    DemoProvider,
    recommend_batch_size,
    run_horizontal_grading as _run_grading,
)
from ui_helpers import (
    build_provider,
    format_confidence,
    get_confidence_color,
    get_provider_config,
    get_status_emoji,
)


def render_scoring_tab(tab):
    """答案読み込み・仮採点タブを描画する。"""
    with tab:
        st.header("答案の読み込みと仮採点")

        # --- ステッパーUI ---
        _session = st.session_state.session
        _is_csv = st.session_state.get("data_source") == "csv"
        _has_data = len(st.session_state.student_groups) > 0 or _is_csv
        _ocr_done = _session and _session.ocr_results and len(_session.ocr_results) > 0
        _ocr_reviewed = _session and _session.ocr_complete() if _session else False
        _graded = _session and _session.students and any(s.status != "pending" for s in _session.students) if _session else False

        _steps = [
            ("データ取り込み", _has_data),
            ("CSV取り込み済み" if _is_csv else "文字読み取り", _ocr_done),
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

        # --- データ取り込み ---
        st.subheader("答案データの取り込み")

        from ui_helpers import ENABLE_PDF_INPUT

        if ENABLE_PDF_INPUT:
            # PDF + CSV の2タブ構成
            input_tab_pdf, input_tab_csv = st.tabs(["答案PDF", "Google Forms 回答CSV"])

            with input_tab_pdf:
                @st.fragment
                def _pdf_upload_fragment():
                    from pdf_processor import pdf_to_images, split_pages_by_student, image_to_bytes
                    st.caption("スキャンした答案のPDFファイルを取り込みます。")

                    col_pdf1, col_pdf2 = st.columns([2, 1])
                    with col_pdf1:
                        pdf_file = st.file_uploader("答案PDFファイル", type=["pdf"], key="pdf_uploader")
                    with col_pdf2:
                        if st.session_state.rubric:
                            pages_per = st.number_input(
                                "1人あたりのページ数",
                                min_value=1, max_value=10,
                                value=max(1, st.session_state.rubric.pages_per_student),
                            )
                        else:
                            pages_per = st.number_input("1人あたりのページ数", min_value=1, max_value=10, value=1)

                    if pdf_file and st.button("答案を取り込む", type="primary", key="import_pdf_btn"):
                        with st.spinner("PDFを画像に変換中..."):
                            pdf_bytes = pdf_file.read()
                            st.session_state.pdf_bytes = pdf_bytes
                            st.session_state.pdf_filename = pdf_file.name
                            images = pdf_to_images(pdf_bytes)
                            st.session_state.images = images
                            if len(images) % pages_per != 0:
                                st.warning(
                                    f"総ページ数 {len(images)} は「1人あたり{pages_per}ページ」で割り切れません。"
                                    f"最後の学生のページが不完全になる可能性があります。"
                                )
                            groups = split_pages_by_student(images, pages_per)
                            st.session_state.student_groups = groups
                            st.session_state.data_source = "pdf"
                            st.session_state.csv_data = None
                            st.session_state._csv_content = None
                            st.session_state.session = None
                            st.success(f"{len(images)}ページ → {len(groups)}名分に分割しました")
                            st.rerun()

                    if st.session_state.student_groups and st.session_state.get("data_source") == "pdf":
                        with st.expander(f"答案プレビュー（{len(st.session_state.student_groups)}名分）"):
                            n_groups = len(st.session_state.student_groups)
                            preview_idx = st.slider("学生番号", 1, max(n_groups, 2), 1, key="preview_slider") if n_groups > 1 else 1
                            group = st.session_state.student_groups[preview_idx - 1]
                            for page_num, img in group:
                                st.image(image_to_bytes(img), caption=f"ページ {page_num}", use_container_width=True)

                _pdf_upload_fragment()

            with input_tab_csv:
                pass  # CSV フラグメントが下で定義・呼び出しされる

        # CSV取り込みフラグメント（PDF有効時はinput_tab_csv内、無効時は直接表示）
        @st.fragment
        def _csv_upload_fragment():
            from csv_importer import parse_forms_csv, get_question_candidate_cols, convert_to_ocr_results, ColumnMapping

            st.caption("Google Forms の回答スプレッドシートからダウンロードした CSV を取り込みます。")

            if not st.session_state.rubric:
                st.info("先に「1. 採点基準」タブで採点基準を設定してください。設問と列の対応付けに必要です。")

            csv_file = st.file_uploader("回答CSVファイル", type=["csv"], key="csv_uploader")

            if csv_file and not st.session_state.rubric:
                st.warning("CSVをアップロードしましたが、採点基準が未設定のため列マッピングができません。")
            elif csv_file:
                try:
                    csv_content = csv_file.read().decode("utf-8-sig")
                except UnicodeDecodeError:
                    csv_file.seek(0)
                    csv_content = csv_file.read().decode("shift_jis", errors="replace")

                try:
                    csv_data = parse_forms_csv(csv_content)
                    st.session_state.csv_data = csv_data
                    st.session_state._csv_content = csv_content
                except ValueError as e:
                    st.error(str(e))
                    return

                st.success(f"{len(csv_data.rows)}名分のデータを検出しました")

                # --- 列マッピングUI ---
                st.markdown("**列の役割を設定してください**")

                rubric = st.session_state.rubric
                question_options = []
                if rubric:
                    for q in rubric.questions:
                        if q.sub_questions:
                            for sq in q.sub_questions:
                                question_options.append(f"問{q.id}-{sq.id}")
                        else:
                            question_options.append(f"問{q.id}")

                role_options = ["無視", "組", "番号", "氏名"] + question_options
                auto = csv_data.auto_mapping
                candidate_cols = get_question_candidate_cols(csv_data)

                col_roles = {}
                for i, header in enumerate(csv_data.headers):
                    if i in auto.ignore_cols:
                        default_idx = 0
                    elif i == auto.class_col:
                        default_idx = 1
                    elif i == auto.number_col:
                        default_idx = 2
                    elif i == auto.name_col:
                        default_idx = 3
                    elif i in candidate_cols and question_options:
                        q_idx = candidate_cols.index(i)
                        if q_idx < len(question_options):
                            default_idx = 4 + q_idx
                        else:
                            default_idx = 0
                    else:
                        default_idx = 0

                    truncated = header[:40] + "..." if len(header) > 40 else header
                    col_roles[i] = st.selectbox(
                        f"列{i+1}: {truncated}",
                        options=role_options,
                        index=min(default_idx, len(role_options) - 1),
                        key=f"csv_col_role_{i}",
                    )

                # プレビュー（先頭5行）
                with st.expander("データプレビュー（先頭5行）"):
                    import pandas as pd
                    preview_df = pd.DataFrame(
                        csv_data.rows[:5],
                        columns=csv_data.headers,
                    )
                    st.dataframe(preview_df, use_container_width=True)

                # 取り込みボタン
                if rubric and st.button("回答データを取り込む", type="primary", key="import_csv_btn"):
                    mapping = ColumnMapping()
                    for i, role in col_roles.items():
                        if role == "無視":
                            mapping.ignore_cols.append(i)
                        elif role == "組":
                            mapping.class_col = i
                        elif role == "番号":
                            mapping.number_col = i
                        elif role == "氏名":
                            mapping.name_col = i
                        elif role.startswith("問"):
                            qid = role[1:]
                            mapping.question_cols[qid] = i

                    if not mapping.question_cols:
                        st.error("設問に対応する列を1つ以上指定してください。")
                        return

                    with st.spinner("回答データを取り込み中..."):
                        session = create_session_record(
                            rubric_title=rubric.title,
                            pdf_filename=csv_file.name,
                            pages_per_student=max(1, rubric.pages_per_student),
                        )
                        from csv_importer import convert_to_ocr_results, parse_forms_csv
                        try:
                            csv_data = parse_forms_csv(st.session_state._csv_content)
                            # score_cols が自動検出された場合は mapping に反映
                            if csv_data.detected_score_cols:
                                mapping.score_cols = csv_data.detected_score_cols
                            ocr_results, errors, teacher_scores = convert_to_ocr_results(
                                csv_data, mapping, rubric,
                            )
                            session.rubric_title = rubric.title
                            session.pages_per_student = rubric.pages_per_student
                            session.ocr_results = ocr_results
                            save_session(session)
                        except (ValueError, Exception) as e:
                            st.error(f"CSV取り込みに失敗しました: {e}")
                            return

                    st.session_state.session = session
                    st.session_state.data_source = "csv"
                    st.session_state.submission_type = "typed"
                    st.session_state.student_groups = []
                    st.session_state.images = []
                    st.session_state.pdf_bytes = b""
                    st.session_state.teacher_scores = teacher_scores if teacher_scores else {}

                    if errors:
                        for err in errors:
                            st.warning(err)

                    st.rerun()

            # CSV取り込み済みの表示
            if st.session_state.session and st.session_state.get("data_source") == "csv":
                session = st.session_state.session
                if session.ocr_results:
                    _ts = st.session_state.get("teacher_scores", {})
                    if _ts:
                        rubric = st.session_state.rubric
                        n_students = len(session.ocr_results)
                        n_questions = len(rubric.questions) if rubric else 0
                        total_cells = n_students * n_questions
                        scored_cells = sum(
                            1 for ts in _ts.values()
                            for v in ts.values() if v is not None
                        )
                        st.info(
                            f"**教員の採点データを検出しました** — "
                            f"採点済み: **{scored_cells} / {total_cells} セル**\n\n"
                            f"- 採点済みセル → AIが一貫性チェック\n"
                            f"- 未採点セル → AIが仮採点"
                        )
                    else:
                        st.success(f"CSV取り込み済み: {len(session.ocr_results)}名分")

        _csv_upload_fragment()

        st.divider()

        # --- 共通チェック ---
        if not st.session_state.rubric:
            st.info(
                "このステップでは、採点基準をもとにAIが仮採点を行います。\n\n"
                "**次のアクション:** 「1. 採点基準」タブで採点基準を設定してください。"
            )
        elif not st.session_state.student_groups and st.session_state.get("data_source") != "csv":
            st.info("上の「答案データの取り込み」からPDFまたはCSVファイルを取り込んでください。")
        else:
            rubric = st.session_state.rubric
            prov = build_provider()
            is_csv_source = st.session_state.get("data_source") == "csv"

            if prov is None:
                st.error(
                    "選択中のAIプロバイダのAPIキーが設定されていません。\n\n"
                    "サイドバーの「AIプロバイダ設定」からAPIキーを入力するか、「デモモード」に切り替えてください。"
                )
                st.stop()

            if is_csv_source:
                n_students = len(st.session_state.session.ocr_results) if st.session_state.session else 0
                st.write(f"**試験**: {rubric.title} / **学生数**: {n_students}名（CSV取り込み） / **AI**: {prov.name}")
            else:
                st.write(f"**試験**: {rubric.title} / **学生数**: {len(st.session_state.student_groups)}名 / **AI**: {prov.name}")
            from ui_helpers import ENABLE_PDF_INPUT
            if ENABLE_PDF_INPUT and st.session_state.get("mask_student_name", True) and not isinstance(prov, DemoProvider):
                st.caption("外部AI送信時は先頭ページ上部の氏名欄を自動マスキングします。氏名はステップ2で必要に応じて補ってください。")

            # --- プライバシー通知 ---
            is_api = not isinstance(prov, DemoProvider)
            if is_csv_source and is_api:
                # CSV入力は匿名化済み — 同意チェック不要、通知のみ
                if not st.session_state.privacy_accepted:
                    st.info(
                        "**データ送信について**\n\n"
                        "採点実行時、回答テキストが外部AIサービス（Google Gemini API）に送信されます。\n\n"
                        "- **生徒の氏名・IDは送信されません**（自動的に匿名化されます）\n"
                        "- 有料APIのため、送信データがAIの学習に使われることは**ありません**"
                    )
                    def _accept_privacy():
                        st.session_state.privacy_accepted = True

                    st.checkbox("上記を確認しました", key="privacy_check",
                                on_change=_accept_privacy)
                else:
                    st.caption("✓ 匿名化済みデータのみが外部AIに送信されます")
            elif is_api and not st.session_state.privacy_accepted:
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
            # Step 1: OCR（Phase 1）— CSV取り込みの場合はスキップ
            # ==========================================================
            if is_csv_source:
                st.subheader("ステップ1: 回答データ（CSV取り込み済み）")
                if session and session.ocr_results:
                    st.success(f"CSV取り込み完了: {len(session.ocr_results)}名分（文字読み取り不要）")
            else:
                st.subheader("ステップ1: 答案の文字読み取り（OCR）")

            if not is_csv_source and session and session.ocr_results:
                ocr_ok = sum(1 for o in session.ocr_results if o.status in ("ocr_done", "reviewed"))
                ocr_err = sum(1 for o in session.ocr_results if o.status == "pending" and o.ocr_error)
                st.success(f"読み取り完了: {ocr_ok}名分" + (f"（{ocr_err}名分は読み取れませんでした）" if ocr_err else ""))
            elif not is_csv_source and can_run:
                if st.button("文字の読み取りを開始", type="primary", key="start_ocr"):
                    pdf_bytes = st.session_state.get("pdf_bytes", b"")
                    if not pdf_bytes:
                        st.error("OCR実行用のPDFデータが見つかりません。もう一度「答案を取り込む」を押してください。")
                        st.stop()

                    session = create_session_record(
                        rubric_title=rubric.title,
                        pdf_filename=st.session_state.get("pdf_filename", "uploaded.pdf"),
                        pages_per_student=rubric.pages_per_student,
                    )
                    sub_type = st.session_state.get("submission_type", "handwritten")
                    two_stage = st.session_state.get("enable_two_stage_ocr", True)
                    n_students = len(st.session_state.student_groups)
                    with st.status(
                        f"文字読み取り中... （{n_students}名分）",
                        expanded=True,
                    ) as ocr_status:
                        st.write(f"**{n_students}名**の答案を読み取っています。")
                        if sub_type == "typed":
                            st.write("電子データモード: 軽量・高速処理で読み取ります。")
                        elif two_stage:
                            st.write("レイアウト分析 → 文字読み取り の2段階で処理します。")
                        st.write(f"AI: **{prov.name}** / 1名あたり数秒〜十数秒かかります。")
                        try:
                            session, errors = run_ocr_via_api(
                                session_id=session.session_id,
                                rubric=rubric,
                                pdf_bytes=pdf_bytes,
                                provider_config=get_provider_config(),
                                enable_two_stage=two_stage,
                                submission_type=sub_type,
                            )
                        except ApiClientError as e:
                            ocr_status.update(label="文字読み取りに失敗しました", state="error")
                            st.error(f"OCRのAPI実行に失敗しました。\n（詳細: {e}）")
                            st.stop()
                        ocr_status.update(
                            label=f"文字読み取り完了（{len(session.ocr_results)}名分）",
                            state="complete",
                        )
                    st.session_state.session = session

                    if errors:
                        for err in errors:
                            st.warning(err)
                    st.success(f"読み取り完了: {len(session.ocr_results)}名分")
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
                    if st.button(f"全て確認済みにする（{len(unreviewed)}名）", key="ocr_bulk_review"):
                        for o in unreviewed:
                            o.status = "reviewed"
                        save_session(session)
                        st.rerun()

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
                            if st.button("確認済みにする", key=f"ocr_review_{ocr.student_id}"):
                                ocr.status = "reviewed"
                                save_session(session)
                                st.rerun()

                if st.button("読み取り結果を保存", key="save_ocr"):
                    save_session(session)
                    st.success("保存しました")

            # ==========================================================
            # Step 2.5: 答案駆動型の採点基準精緻化
            # ==========================================================
            if session and session.ocr_complete() and st.session_state.rubric:
                _has_descriptive = any(
                    q.question_type == "descriptive"
                    for q in st.session_state.rubric.questions
                )
                if _has_descriptive:
                    with st.expander("採点基準を答案から精緻化する（推奨）", expanded=False):
                        st.caption(
                            "AIが実際の学生の解答を読み、判断が分かれそうなケースを具体的に指摘します。"
                            "事前に回答しておくと、採点のブレが減ります。"
                        )

                        if can_run and st.button(
                            "答案を分析して確認ポイントを抽出",
                            key="refine_rubric_btn",
                        ):
                            try:
                                with st.status(
                                    "AIが答案を分析中...",
                                    expanded=True,
                                ) as refine_status:
                                    st.write("全学生の解答を通読し、ボーダーラインケースを抽出しています。")
                                    refine_qs = refine_rubric_via_api(
                                        session_id=session.session_id,
                                        rubric=st.session_state.rubric,
                                        provider_config=get_provider_config(),
                                    )
                                    refine_status.update(
                                        label=f"分析完了（{len(refine_qs)}件の確認ポイント）",
                                        state="complete",
                                    )
                                st.session_state.rubric_refine_questions = refine_qs
                            except (ApiClientError, Exception) as e:
                                st.error(f"答案分析に失敗しました: {e}")

                        if st.session_state.get("rubric_refine_questions"):
                            refine_qs = st.session_state.rubric_refine_questions
                            st.markdown(f"**{len(refine_qs)}件の確認パターンが見つかりました。**")

                            for i, rq in enumerate(refine_qs):
                                pattern_label = rq.get("pattern_label", "")
                                count = rq.get("count", 0)
                                header = f"問{rq.get('question_id', '?')} — {rq.get('aspect', '')}"
                                if pattern_label:
                                    header += f"：{pattern_label}"
                                if count:
                                    header += f"（{count}名）"
                                st.markdown(f"---\n**{header}**")

                                # パターンベース: 代表的な解答例を複数表示
                                example_answers = rq.get("example_answers", [])
                                if example_answers:
                                    for ex in example_answers:
                                        sid = ex.get("student_id", "")
                                        txt = ex.get("text", "")
                                        citation = f"[{sid}] " if sid else ""
                                        st.markdown(f"> {citation}「{txt}」")
                                else:
                                    # 旧形式との後方互換
                                    student_answer = rq.get("student_answer") or rq.get("sample_answer", "")
                                    student_id = rq.get("student_id", "")
                                    if student_answer:
                                        citation = f"[{student_id}] " if student_id else ""
                                        st.markdown(f"> {citation}「{student_answer}」")

                                st.markdown(rq.get("question", ""))

                                options = rq.get("options", [])
                                if options:
                                    choice = st.radio(
                                        "回答を選択",
                                        options=options,
                                        key=f"rubric_refine_{i}",
                                        index=None,
                                    )
                                    st.session_state[f"rubric_refine_answer_{i}"] = choice
                                else:
                                    answer = st.text_input(
                                        "回答を入力",
                                        key=f"rubric_refine_{i}",
                                    )
                                    st.session_state[f"rubric_refine_answer_{i}"] = answer

                            if st.button(
                                "回答を採点基準に反映する",
                                type="primary",
                                key="apply_rubric_refine",
                            ):
                                clarifications = []
                                for i, rq in enumerate(refine_qs):
                                    answer = st.session_state.get(f"rubric_refine_answer_{i}", "")
                                    if answer:
                                        clarifications.append({
                                            "question_id": rq.get("question_id", ""),
                                            "question": rq.get("question", ""),
                                            "answer": answer,
                                        })

                                for cl in clarifications:
                                    for q in st.session_state.rubric.questions:
                                        if str(q.id) == cl["question_id"]:
                                            addition = f"\n\n【教員補足】Q: {cl['question']} → A: {cl['answer']}"
                                            q.scoring_criteria += addition
                                            break

                                if clarifications:
                                    st.session_state.rubric_refine_questions = []
                                    st.success(f"{len(clarifications)}件の補足を採点基準に反映しました。")
                                    st.rerun()
                                else:
                                    st.warning("回答が入力されていません。")

            # ==========================================================
            # Step 3: まとめ採点（Phase 2）
            # ==========================================================
            if session and session.ocr_complete():
                st.subheader("ステップ3: 設問ごとのまとめ採点")
                st.info(
                    "同じ設問について全員分の解答をまとめてAIが採点します。"
                    "読み取り済みの文字データを使うため、追加の通信は最小限です。"
                )

                _is_typed_for_batch = (st.session_state.get("submission_type") == "typed"
                                       or st.session_state.get("data_source") == "csv")
                rec_size, rec_reason = recommend_batch_size(rubric, is_typed=_is_typed_for_batch)
                batch_size = st.number_input(
                    "1回あたりの処理人数（バッチサイズ）",
                    min_value=3, max_value=60, value=rec_size,
                    help="テキスト入力の場合は30〜40名の一括処理も可能です。手書きOCRの場合は10〜15名が目安です。",
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
                    # 表記・文法オプションをルーブリックに反映
                    rubric.grading_options = GradingOptions(
                        penalize_typos=st.session_state.get("penalize_typos", False),
                        penalize_grammar=st.session_state.get("penalize_grammar", False),
                        penalize_wrong_names=st.session_state.get("penalize_wrong_names", False),
                        penalize_hiragana=st.session_state.get("penalize_hiragana", False),
                    )
                    save_session(session)
                    n_questions = len(rubric.questions)
                    n_students = len(session.ocr_results)
                    verification = st.session_state.get("enable_verification", False)
                    _is_typed = st.session_state.get("submission_type") == "typed"

                    # ローカル直接呼び出し（進捗表示のため）
                    progress_placeholder = st.empty()
                    with st.status(
                        f"まとめ採点中... （{n_questions}問 × {n_students}名）",
                        expanded=True,
                    ) as grading_status:
                        _has_teacher = bool(st.session_state.get("teacher_scores"))
                        if _has_teacher:
                            st.write(f"**{n_questions}問**×**{n_students}名**を一貫性チェック + 仮採点します。")
                        else:
                            st.write(f"**{n_questions}問**を**{n_students}名**分まとめて採点します。")
                        st.write(f"AI: **{prov.name}** / バッチサイズ: {int(batch_size)}名")
                        if verification:
                            st.write("ダブルチェック方式が有効です（記述式問題は2パスで検証）。")
                        progress_text = st.empty()

                        def _on_progress(q_idx, total_q, question, batch_idx, total_batches):
                            phase = "検証中" if batch_idx > total_batches else "採点中"
                            progress_text.write(
                                f"問{question.id}（{q_idx+1}/{total_q}）: "
                                f"バッチ {min(batch_idx, total_batches)}/{total_batches} {phase}"
                            )

                        _teacher_scores = st.session_state.get("teacher_scores", {})
                        try:
                            errors = _run_grading(
                                provider=prov,
                                rubric=rubric,
                                session=session,
                                batch_size=int(batch_size),
                                enable_verification=verification,
                                is_typed=_is_typed,
                                on_question_progress=_on_progress,
                                teacher_scores=_teacher_scores or None,
                            )
                        except Exception as e:
                            grading_status.update(label="まとめ採点に失敗しました", state="error")
                            st.error(f"まとめ採点に失敗しました。\n（詳細: {e}）")
                            st.stop()
                        grading_status.update(
                            label=f"まとめ採点完了（{n_questions}問 × {n_students}名）",
                            state="complete",
                        )
                    st.session_state.session = session

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

                    _re_prov = build_provider()
                    can_rerun = _re_prov is not None and (isinstance(_re_prov, DemoProvider) or st.session_state.privacy_accepted)
                    if _re_prov is None:
                        st.warning("APIキーが設定されていません。サイドバーで設定してください。")
                    elif can_rerun and st.button("お手本を使って再採点する", type="primary", key="re_grade_horizontal"):
                        rubric = st.session_state.rubric
                        target_ids = [s.student_id for s in unconfirmed]
                        save_session(session)
                        re_prov = _re_prov
                        with st.status(
                            f"お手本再採点中... （{len(target_ids)}名）",
                            expanded=True,
                        ) as re_status:
                            st.write(f"**{len(refs)}件のお手本**を参考に**{len(target_ids)}名**を再採点します。")
                            st.write(f"AI: **{re_prov.name}**")
                            try:
                                session, errors = run_horizontal_grading_via_api(
                                    session=session,
                                    rubric=rubric,
                                    provider_config=get_provider_config(),
                                    batch_size=DEFAULT_BATCH_SIZE,
                                    enable_verification=st.session_state.get("enable_verification", False),
                                    student_ids_to_grade=target_ids,
                                    is_typed=st.session_state.get("submission_type") == "typed",
                                )
                            except ApiClientError as e:
                                re_status.update(label="再採点に失敗しました", state="error")
                                st.error(f"再採点のAPI実行に失敗しました。\n（詳細: {e}）")
                                st.stop()
                            re_status.update(
                                label=f"再採点完了（{len(target_ids)}名）",
                                state="complete",
                            )

                        for s in session.students:
                            if s.student_id in target_ids:
                                s.ai_overall_comment = (
                                    (s.ai_overall_comment or "") + "\n[お手本をもとに再採点しました]"
                                )

                        save_session(session)
                        st.session_state.session = session
                        if errors:
                            for err in errors:
                                st.warning(err)
                        st.success("再採点完了。「確認・修正」タブで確認してください。")
                        st.rerun()
