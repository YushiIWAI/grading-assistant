"""タブ1: 採点基準 モジュール"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from api_client import load_rubric_from_yaml
from rubric_io import rubric_from_dict, rubric_to_yaml


def render_rubric_tab(tab):
    """採点基準タブを描画する。"""
    with tab:
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

まずは下の「採点基準の作成」から始めてください。
""")
            st.divider()

        st.header("採点基準の作成")
        st.caption("試験の採点基準を設定します。フォームに入力するか、設定ファイル（YAML）をお持ちの場合はそちらからも読み込めます。")

        method = st.radio(
            "作成方法",
            ["gui", "yaml"],
            format_func=lambda x: {"gui": "フォーム入力で作成", "yaml": "設定ファイル（YAML）で読み込み"}[x],
            horizontal=True,
        )

        if method == "gui":
            _render_gui_builder()
        else:
            _render_yaml_loader()

        # 現在の採点基準表示
        if st.session_state.rubric:
            st.divider()
            r = st.session_state.rubric
            st.success(f"設定済み: 「{r.title}」 {len(r.questions)}問 / {r.total_points}点満点")

            # --- 表記・文法の減点オプション ---
            @st.fragment
            def _grading_options_fragment():
                with st.expander("表記・文法の減点オプション", expanded=False):
                    st.caption("採点時に、内容の評価に加えて表記・文法面の減点を行うかを設定します。")
                    st.checkbox(
                        "誤字・脱字を減点する",
                        key="penalize_typos",
                        help="誤字・脱字1箇所につき減点します。",
                    )
                    st.checkbox(
                        "文法の誤りを減点する",
                        key="penalize_grammar",
                        help="主語と述語のねじれ、助詞の誤用など文法的な誤りを減点します。",
                    )
                    st.checkbox(
                        "人名・用語の表記ミスを減点する",
                        key="penalize_wrong_names",
                        help="登場人物名や専門用語の漢字の書き間違い等を減点します。内容的な間違いは内容の採点で評価されます。",
                    )
                    st.checkbox(
                        "ひらがな表記を減点する",
                        key="penalize_hiragana",
                        help="本文中で漢字表記されている語をひらがなで書いている場合に減点します（例: 枯れ草→かれくさ）。内容点には影響しません。",
                    )
            _grading_options_fragment()

            st.info("**次のステップ →** 「2. 答案の取り込みと仮採点」タブに進んで、答案PDFをアップロードしてください。")


def _render_gui_builder():
    """GUIルーブリックビルダーを描画する。"""
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

        rubric = rubric_from_dict({
            "exam_info": {
                "title": rb_title,
                "total_points": rb_total,
                "pages_per_student": rb_pages,
            },
            "notes": rb_notes,
            "questions": questions,
        })
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
                preview_questions.append(q)
            preview_rubric = rubric_from_dict({
                "exam_info": {
                    "title": rb_title,
                    "total_points": rb_total,
                    "pages_per_student": rb_pages,
                },
                "notes": rb_notes,
                "questions": preview_questions,
            })
            st.code(rubric_to_yaml(preview_rubric), language="yaml")


def _render_yaml_loader():
    """YAMLモードの採点基準読み込みを描画する。"""
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
