"""フィードバック品質テスト: 新プロンプトでのfeedback内容を確認

使い方:
    cd grading-assistant
    python3 test_data/run_feedback_quality_test.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from csv_importer import parse_forms_csv, get_question_candidate_cols, convert_to_ocr_results, ColumnMapping
from rubric_io import load_rubric_from_yaml
from models import ScoringSession
from scoring_engine import run_horizontal_grading, GeminiProvider

# 各層から代表を選出
# 優秀: 山田太郎(1-1), 中上位: 渡辺陽菜(1-12), 中位: 松本結衣(1-22),
# 下位: 藤田龍之介(1-30), 誤字多い: 野口大地(1-37)
TARGET_IDS = {"1-1", "1-12", "1-22", "1-30", "1-37"}


def main():
    with open("test_data/kokyou_csv_test_rubric.yaml", "r") as f:
        rubric = load_rubric_from_yaml(f.read())

    with open("test_data/kokyou_csv_test.csv", "r") as f:
        csv_content = f.read()

    data = parse_forms_csv(csv_content)
    candidate_cols = get_question_candidate_cols(data)
    mapping = ColumnMapping(
        class_col=data.auto_mapping.class_col,
        number_col=data.auto_mapping.number_col,
        name_col=data.auto_mapping.name_col,
        question_cols={},
        ignore_cols=data.auto_mapping.ignore_cols,
    )
    for i, col_idx in enumerate(candidate_cols):
        if i < len(rubric.questions):
            mapping.question_cols[str(rubric.questions[i].id)] = col_idx

    ocr_results, _ = convert_to_ocr_results(data, mapping, rubric)
    ocr_results = [r for r in ocr_results if r.student_id in TARGET_IDS]

    print(f"テスト対象: {len(ocr_results)}名")
    for r in ocr_results:
        print(f"  {r.student_id} {r.student_name}")

    session = ScoringSession(
        session_id="feedback_quality_test",
        rubric_title=rubric.title,
        ocr_results=ocr_results,
    )

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("エラー: GOOGLE_API_KEY が設定されていません")
        sys.exit(1)

    provider = GeminiProvider(api_key=api_key, model_name="gemini-2.5-flash")

    print(f"\n採点実行中...")
    t0 = time.time()
    errors = run_horizontal_grading(
        provider=provider,
        rubric=rubric,
        session=session,
        batch_size=10,
        enable_verification=True,
        is_typed=True,
    )
    print(f"完了: {time.time() - t0:.1f}秒")
    if errors:
        print(f"エラー: {errors}")

    # 結果表示
    for student in sorted(session.students, key=lambda s: s.student_id):
        name = student.student_name.split()[-1] if student.student_name else student.student_id
        print(f"\n{'=' * 60}")
        print(f"{student.student_id} {name} (合計: {student.total_score}/{student.total_max_points})")
        print(f"{'=' * 60}")
        for qs in student.question_scores:
            print(f"\n  問{qs.question_id} ({qs.score}/{qs.max_points})")
            print(f"  [comment] {qs.comment.split(chr(10) + chr(10) + '【検証')[0]}")
            print(f"  [feedback] {qs.feedback}")

    # JSON保存
    result = {}
    for s in session.students:
        result[s.student_id] = {
            "name": s.student_name,
            "scores": {
                qs.question_id: {
                    "score": qs.score,
                    "max_points": qs.max_points,
                    "comment": qs.comment,
                    "feedback": qs.feedback,
                }
                for qs in s.question_scores
            },
        }
    path = "test_data/feedback_quality_test_result.json"
    with open(path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n詳細を {path} に保存しました")


if __name__ == "__main__":
    main()
