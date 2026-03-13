"""Gemini APIでのrefine_rubric品質テスト

test_rubric.yaml + generate_test_data.pyの答案データを使って、
Gemini APIでrefine_rubricを呼び出し、出力品質を確認する。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from generate_test_data import STUDENTS
from rubric_io import load_rubric_from_yaml
from scoring_engine import GeminiProvider, build_rubric_refine_prompt


def build_ocr_answers() -> dict[str, list[tuple[str, str]]]:
    """generate_test_data.pyの答案データからOCR結果を模擬する。"""
    answers_by_q: dict[str, list[tuple[str, str]]] = {}
    for student in STUDENTS:
        sid = f"S{student['number'].zfill(3)}"
        for q_num, answer_text in student["answers"].items():
            qid = str(q_num)
            if qid not in answers_by_q:
                answers_by_q[qid] = []
            answers_by_q[qid].append((sid, str(answer_text)))
    return answers_by_q


def main():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: 環境変数 GOOGLE_API_KEY を設定してください。")
        sys.exit(1)

    # ルーブリック読み込み
    rubric_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "rubrics", "test_rubric.yaml",
    )
    with open(rubric_path, "r", encoding="utf-8") as f:
        rubric = load_rubric_from_yaml(f.read())
    print(f"ルーブリック: {rubric.title}")
    print(f"設問数: {len(rubric.questions)}")
    print()

    # OCR結果を模擬
    ocr_answers = build_ocr_answers()
    print("OCR答案データ:")
    for qid, answers in ocr_answers.items():
        print(f"  問{qid}: {len(answers)}名分")
    print()

    # プロンプト確認
    prompt = build_rubric_refine_prompt(rubric, ocr_answers)
    print("=" * 60)
    print("生成されたプロンプト:")
    print("=" * 60)
    print(prompt)
    print("=" * 60)
    print()

    # Gemini API呼び出し
    models_to_test = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-3.1-pro-preview",
    ]

    for model_name in models_to_test:
        print(f"\n{'=' * 60}")
        print(f"モデル: {model_name}")
        print(f"{'=' * 60}")

        try:
            provider = GeminiProvider(api_key, model_name)
            result = provider.refine_rubric(rubric, ocr_answers)

            print(f"\n結果 (JSON):")
            print(json.dumps(result, ensure_ascii=False, indent=2))

            # 品質チェック
            questions = result.get("questions", [])
            print(f"\n--- 品質チェック ---")
            print(f"生成された質問数: {len(questions)}")

            issues = []
            for i, q in enumerate(questions):
                # 必須フィールドチェック
                for field in ["question_id", "aspect", "student_answer", "student_id", "question", "options"]:
                    if field not in q:
                        issues.append(f"  質問{i+1}: フィールド '{field}' が欠落")

                # question_idが実在するか
                qid = q.get("question_id", "")
                valid_qids = [str(qq.id) for qq in rubric.questions]
                if qid not in valid_qids:
                    issues.append(f"  質問{i+1}: question_id '{qid}' は存在しない設問")

                # 記述式のみ対象か
                for qq in rubric.questions:
                    if str(qq.id) == qid and qq.question_type != "descriptive":
                        issues.append(f"  質問{i+1}: 問{qid}は記述式ではない（{qq.question_type}）")

                # student_idが実在するか
                sid = q.get("student_id", "")
                valid_sids = [f"S{s['number'].zfill(3)}" for s in STUDENTS]
                if sid and sid not in valid_sids:
                    # student_idの形式が違う場合もありうる（AIが独自の形式で返す可能性）
                    issues.append(f"  質問{i+1}: student_id '{sid}' がテストデータと不一致（参考）")

                # optionsが配列か
                opts = q.get("options", [])
                if not isinstance(opts, list) or len(opts) < 2:
                    issues.append(f"  質問{i+1}: options が2つ未満")

                # 実際の学生の解答を引用しているか
                student_answer = q.get("student_answer", "")
                if not student_answer or len(student_answer) < 5:
                    issues.append(f"  質問{i+1}: student_answer が短すぎるか空")

            if issues:
                print("問題点:")
                for issue in issues:
                    print(issue)
            else:
                print("OK: 全質問がフォーマット要件を満たしています")

            # 質問の内容サマリー
            print(f"\n--- 質問サマリー ---")
            for i, q in enumerate(questions):
                print(f"  [{i+1}] 問{q.get('question_id', '?')} / {q.get('aspect', '?')}")
                print(f"      対象学生: {q.get('student_id', '?')}")
                print(f"      質問: {q.get('question', '?')[:80]}...")
                print(f"      選択肢: {q.get('options', [])}")
                print()

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
