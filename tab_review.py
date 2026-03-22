"""タブ3: 確認・修正 モジュール"""
from __future__ import annotations

import streamlit as st

from api_client import save_session
from pdf_processor import image_to_bytes
from scoring_engine import DEFAULT_BATCH_SIZE, analyze_batch_calibration
from ui_helpers import (
    confidence_badge_html,
    format_confidence,
    get_confidence_color,
    get_status_emoji,
    review_needed_badge_html,
    status_badge_html,
)


def render_review_tab(tab):
    """確認・修正タブを描画する。"""
    with tab:
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
                ["学生別", "問い別", "一覧テーブル"],
                horizontal=True,
                key="review_view_mode",
                help="「問い別」では同じ設問に対する全学生の回答を横並びで比較できます。「一覧テーブル」では全学生の得点を一覧で確認・編集できます",
            )

            if review_mode == "一覧テーブル":
                _render_table_mode(session)

            if review_mode == "問い別":
                _render_question_mode(session)

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
                        if st.button(
                            f"要確認なしの{len(_safe_to_confirm)}名を一括確定",
                            type="primary",
                            key="bulk_confirm_btn",
                        ):
                            for s in _safe_to_confirm:
                                s.status = "confirmed"
                            save_session(session)
                            st.rerun()
                with bulk_col2:
                    if _needs_review_students:
                        _total_review = sum(s.review_needed_count() for s in _needs_review_students)
                        if st.button(
                            f"要確認{_total_review}件を確認済みに",
                            key="bulk_review_btn",
                        ):
                            for s in _needs_review_students:
                                for qs in s.question_scores:
                                    if qs.needs_review:
                                        qs.reviewed = True
                            save_session(session)
                            st.rerun()
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
                            new_fb = st.text_area(
                                "📝 生徒へのフィードバック",
                                value=qs.feedback or "",
                                key=f"fb_{student.student_id}_{qs.question_id}",
                                height=100,
                            )
                            if new_fb != (qs.feedback or ""):
                                qs.feedback = new_fb
                                save_session(session)

                        with qc2:
                            # 「AIスコアに変更」ボタンを先に処理（number_input の値と競合しないよう）
                            _restore_clicked = False
                            if qs.teacher_score is not None and qs.ai_score is not None:
                                diff = qs.teacher_score - qs.ai_score
                                diff_str = f"{diff:+.1f}" if diff != 0 else "±0"
                                if abs(diff) >= 2:
                                    st.caption(f"教員 {qs.teacher_score:.1f} / AI {qs.ai_score:.1f} (差{diff_str})")
                                else:
                                    st.caption(f"教員 {qs.teacher_score:.1f} / AI {qs.ai_score:.1f}")
                                if st.button(
                                    f"AIスコアに変更 ({qs.ai_score:.1f}点)",
                                    key=f"restore_ai_{student.student_id}_{qs.question_id}",
                                ):
                                    qs.score = qs.ai_score
                                    student.recalculate_total()
                                    save_session(session)
                                    _restore_clicked = True

                            new_score = st.number_input(
                                "得点", min_value=0.0, max_value=float(qs.max_points),
                                value=float(qs.score), step=0.5,
                                key=f"score_{student.student_id}_{qs.question_id}",
                            )
                            if not _restore_clicked and new_score != qs.score:
                                qs.score = new_score
                                student.recalculate_total()
                                save_session(session)
                            st.caption(f"/ {qs.max_points}点")

                            if not (qs.teacher_score is not None and qs.ai_score is not None) and qs.ai_score is not None and abs(qs.score - qs.ai_score) > 0.01:
                                if st.button(
                                    f"AIスコアに戻す ({qs.ai_score:.1f}点)",
                                    key=f"restore_ai_{student.student_id}_{qs.question_id}",
                                ):
                                    qs.score = qs.ai_score
                                    student.recalculate_total()
                                    save_session(session)
                                    st.rerun()

                            if _restore_clicked:
                                st.rerun()

                            if qs.needs_review and not qs.reviewed:
                                if qs.review_reason:
                                    st.warning(f"🔍 **教員確認ポイント:** {qs.review_reason}")

                                if st.button("確認済み", key=f"rev_{student.student_id}_{qs.question_id}"):
                                    qs.reviewed = True
                                    save_session(session)
                                    st.rerun()

                    st.divider()
                    notes = st.text_area(
                        "教員メモ", value=student.reviewer_notes,
                        key=f"notes_{student.student_id}", height=68,
                    )
                    student.reviewer_notes = notes

                    bcol1, bcol2, bcol3 = st.columns(3)
                    with bcol1:
                        if student.status not in ("confirmed", "reviewed"):
                            if st.button("確定する", key=f"mk_conf_{student.student_id}"):
                                student.status = "confirmed"
                                save_session(session)
                                st.rerun()
                    with bcol2:
                        ref_label = "お手本の指定を解除" if student.is_reference else "お手本に指定する"
                        if student.status in ("reviewed", "confirmed"):
                            if st.button(ref_label, key=f"ref_{student.student_id}"):
                                student.is_reference = not student.is_reference
                                save_session(session)
                                st.rerun()
                    with bcol3:
                        if st.button("保存", key=f"save_{student.student_id}"):
                            save_session(session)
                            st.rerun()

                    if student.is_reference:
                        st.caption("📌 この答案はAI再採点のお手本として使用されます")


def _render_table_mode(session):
    """一覧テーブルモードを描画する。"""
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


def _render_question_mode(session):
    """問い別モードを描画する。"""
    rubric = st.session_state.rubric
    if not rubric or not rubric.questions:
        st.info("採点基準（ルーブリック）が設定されていません。「1. 準備」タブで設定してください。")
        return

    scored_students = [s for s in session.students if s.status != "pending"]
    if not scored_students:
        st.info("まだ採点済みの学生がいません。")
        return

    # 問い選択
    all_question_ids = []
    q_label_map = {}
    for q in rubric.questions:
        if q.sub_questions:
            for sq in q.sub_questions:
                qid = f"{q.id}-{sq.id}"
                all_question_ids.append(qid)
                q_label_map[qid] = f"問{qid}: {sq.text[:30]}" if sq.text else f"問{qid}"
        else:
            qid = str(q.id)
            all_question_ids.append(qid)
            q_label_map[qid] = f"問{qid}: {q.description[:30]}" if q.description else f"問{qid}"

    selected_qid = st.selectbox(
        "設問を選択",
        all_question_ids,
        format_func=lambda x: q_label_map.get(x, f"問{x}"),
        key="qview_question_select",
    )

    # 採点基準の表示
    for q in rubric.questions:
        if str(q.id) == selected_qid:
            with st.expander("採点基準", expanded=False):
                st.markdown(f"**配点:** {q.max_points}点")
                if q.model_answer:
                    st.markdown(f"**模範解答:** {q.model_answer}")
                if q.scoring_criteria:
                    st.markdown(f"**基準:** {q.scoring_criteria}")
            break
        if q.sub_questions:
            for sq in q.sub_questions:
                if f"{q.id}-{sq.id}" == selected_qid:
                    with st.expander("採点基準", expanded=False):
                        st.markdown(f"**配点:** {sq.points}点")
                        st.markdown(f"**模範解答:** {sq.answer}")
                    break

    # フィルタ
    qview_filter_col1, qview_filter_col2 = st.columns(2)
    with qview_filter_col1:
        qview_sort = st.selectbox(
            "並び順",
            ["得点（低い順）", "得点（高い順）", "学生番号順"],
            key="qview_sort",
        )
    with qview_filter_col2:
        qview_review_only = st.checkbox(
            "要確認のみ表示", value=False, key="qview_review_only",
        )

    # データ収集
    q_entries = []
    for s in scored_students:
        for qs in s.question_scores:
            if qs.question_id == selected_qid:
                if qview_review_only and not (qs.needs_review and not qs.reviewed):
                    continue
                q_entries.append((s, qs))
                break

    # ソート
    if qview_sort == "得点（低い順）":
        q_entries.sort(key=lambda x: x[1].score)
    elif qview_sort == "得点（高い順）":
        q_entries.sort(key=lambda x: x[1].score, reverse=True)
    else:
        q_entries.sort(key=lambda x: x[0].student_id)

    # スコア分布サマリー
    if q_entries:
        scores = [qs.score for _, qs in q_entries]
        max_pts = q_entries[0][1].max_points
        avg = sum(scores) / len(scores)
        full_marks = sum(1 for sc in scores if sc >= max_pts)
        zero_marks = sum(1 for sc in scores if sc <= 0)
        review_count = sum(1 for _, qs in q_entries if qs.needs_review and not qs.reviewed)

        mcol1, mcol2, mcol3, mcol4, mcol5 = st.columns(5)
        mcol1.metric("対象人数", len(q_entries))
        mcol2.metric("平均点", f"{avg:.1f}/{max_pts}")
        mcol3.metric("満点", full_marks)
        mcol4.metric("0点", zero_marks)
        mcol5.metric("要確認", review_count)

    st.divider()

    # 各学生の回答を表示
    for student, qs in q_entries:
        conf_color = get_confidence_color(qs.confidence)
        review_mark = "⚠️ " if qs.needs_review and not qs.reviewed else ""
        verified_mark = " ✓検証済" if "【検証結果】" in qs.comment else ""

        with st.expander(
            f"{review_mark}{student.student_id} {student.student_name or '(氏名不明)'}"
            f" — {qs.score}/{qs.max_points}点"
            f" (自信度: {format_confidence(qs.confidence)}){verified_mark}",
            expanded=(qs.needs_review and not qs.reviewed),
        ):
            qc1, qc2 = st.columns([3, 1])
            with qc1:
                st.text_area(
                    "読み取りテキスト", value=qs.transcribed_text,
                    key=f"qview_trans_{student.student_id}_{qs.question_id}",
                    height=68, disabled=True,
                )
                if qs.comment:
                    st.info(f"💬 {qs.comment}")
                new_fb = st.text_area(
                    "📝 生徒へのフィードバック",
                    value=qs.feedback or "",
                    key=f"qview_fb_{student.student_id}_{qs.question_id}",
                    height=100,
                )
                if new_fb != (qs.feedback or ""):
                    qs.feedback = new_fb
                    save_session(session)
            with qc2:
                # 「AIスコアに変更」ボタンを先に処理
                _qview_restore_clicked = False
                if qs.teacher_score is not None and qs.ai_score is not None:
                    diff = qs.teacher_score - qs.ai_score
                    diff_str = f"{diff:+.1f}" if diff != 0 else "±0"
                    if abs(diff) >= 2:
                        st.caption(f"教員 {qs.teacher_score:.1f} / AI {qs.ai_score:.1f} (差{diff_str})")
                    else:
                        st.caption(f"教員 {qs.teacher_score:.1f} / AI {qs.ai_score:.1f}")
                    if st.button(
                        f"AIスコアに変更 ({qs.ai_score:.1f}点)",
                        key=f"qview_restore_{student.student_id}_{qs.question_id}",
                    ):
                        qs.score = qs.ai_score
                        student.recalculate_total()
                        save_session(session)
                        _qview_restore_clicked = True

                new_score = st.number_input(
                    "得点", min_value=0.0, max_value=float(qs.max_points),
                    value=float(qs.score), step=0.5,
                    key=f"qview_score_{student.student_id}_{qs.question_id}",
                )
                if not _qview_restore_clicked and new_score != qs.score:
                    qs.score = new_score
                    student.recalculate_total()
                    save_session(session)
                st.caption(f"/ {qs.max_points}点")

                if not (qs.teacher_score is not None and qs.ai_score is not None) and qs.ai_score is not None and abs(qs.score - qs.ai_score) > 0.01:
                    if st.button(
                        f"AIスコアに戻す ({qs.ai_score:.1f}点)",
                        key=f"qview_restore_{student.student_id}_{qs.question_id}",
                    ):
                        qs.score = qs.ai_score
                        student.recalculate_total()
                        save_session(session)
                        _qview_restore_clicked = True

                if _qview_restore_clicked:
                    st.rerun()

                if qs.needs_review and not qs.reviewed:
                    if qs.review_reason:
                        st.warning(f"🔍 {qs.review_reason}")
                    if st.button("確認済み", key=f"qview_rev_{student.student_id}_{qs.question_id}"):
                        qs.reviewed = True
                        save_session(session)
                        st.rerun()
