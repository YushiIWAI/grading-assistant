"""タブ4: 成績の書き出し モジュール"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from api_client import export_csv, save_session
from ui_helpers import get_status_emoji


def render_export_tab(tab):
    """成績書き出しタブを描画する。"""
    with tab:
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

            # フィードバック付きCSV
            if st.session_state.get("rubric"):
                from csv_exporter import export_results_csv, export_feedback_only_csv
                _rubric = st.session_state.rubric
                st.write("**フィードバック付き成績表**（生徒への返却用）")
                col_fb1, col_fb2 = st.columns(2)
                with col_fb1:
                    fb_detail = export_results_csv(session, _rubric)
                    st.download_button(
                        "詳細版（設問ごとのフィードバック）",
                        data=fb_detail.encode("utf-8"),
                        file_name=f"feedback_detail_{session.session_id}.csv",
                        mime="text/csv",
                        key="dl_fb_detail",
                    )
                with col_fb2:
                    fb_simple = export_feedback_only_csv(session, _rubric)
                    st.download_button(
                        "簡易版（フィードバックまとめ）",
                        data=fb_simple.encode("utf-8"),
                        file_name=f"feedback_{session.session_id}.csv",
                        mime="text/csv",
                        key="dl_fb_simple",
                    )

            with st.expander("その他の形式"):
                st.write("**JSON形式**（バックアップ・復元用の詳細データ）")
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
