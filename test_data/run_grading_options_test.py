"""GradingOptions（表記・文法減点）の実動作テスト

APIキーが必要。故郷CSVテストデータのうち、誤字・文法崩壊がある生徒6名だけを
抽出して、GradingOptionsあり/なしの2回採点し、減点の差を確認する。

使い方:
    cd grading-assistant
    python3 test_data/run_grading_options_test.py
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
from models import GradingOptions, ScoringSession
from scoring_engine import run_horizontal_grading, GeminiProvider


# テスト対象:
# - 野口大地 (1-37): 誤字多い
# - 原田結菜 (2-37): 文法崩壊
# - 井上美月 (1-25): 内容はまとも（減点の対照群）
# - 林あかり (1-28): 内容はまとも（対照群）
TARGET_IDS = {"1-37", "2-37", "1-25", "1-28"}


def load_test_data():
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
            q = rubric.questions[i]
            mapping.question_cols[str(q.id)] = col_idx

    ocr_results, _ = convert_to_ocr_results(data, mapping, rubric)

    # テスト対象のみ抽出
    ocr_results = [r for r in ocr_results if r.student_id in TARGET_IDS]
    print(f"テスト対象: {len(ocr_results)}名")
    for r in ocr_results:
        print(f"  {r.student_id} {r.student_name}")

    return rubric, ocr_results


def run_grading(rubric, ocr_results, grading_options, label):
    """採点を実行し、結果を返す"""
    rubric.grading_options = grading_options

    session = ScoringSession(
        session_id=f"options_test_{label}",
        rubric_title=rubric.title,
        ocr_results=list(ocr_results),  # コピー（状態をリセット）
    )

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("エラー: GOOGLE_API_KEY が設定されていません")
        sys.exit(1)

    provider = GeminiProvider(api_key=api_key, model_name="gemini-2.5-flash")

    print(f"\n{'=' * 60}")
    print(f"採点実行: {label}")
    print(f"  GradingOptions: {grading_options}")
    print(f"{'=' * 60}")

    t0 = time.time()
    errors = run_horizontal_grading(
        provider=provider,
        rubric=rubric,
        session=session,
        batch_size=10,
        enable_verification=False,  # 検証なし（減点の差を純粋に比較するため）
        is_typed=True,
    )
    elapsed = time.time() - t0

    print(f"  完了: {elapsed:.1f}秒")
    if errors:
        print(f"  エラー: {errors}")

    return session


def compare_results(session_without, session_with):
    """2つの採点結果を比較"""
    print(f"\n{'=' * 60}")
    print("比較結果")
    print(f"{'=' * 60}")
    print(f"{'生徒ID':<10} {'氏名':<8} {'問':>3} {'減点なし':>8} {'減点あり':>8} {'差':>6} {'コメント'}")
    print("-" * 80)

    total_diff = 0
    penalty_applied_count = 0

    for s_without in sorted(session_without.students, key=lambda s: s.student_id):
        s_with = next(
            (s for s in session_with.students if s.student_id == s_without.student_id),
            None,
        )
        if not s_with:
            continue

        for qs_w, qs_wo in zip(s_with.question_scores, s_without.question_scores):
            diff = qs_w.score - qs_wo.score
            total_diff += diff

            # コメントに減点情報があるか
            has_penalty = "減点" in qs_w.comment if qs_w.comment else False
            if has_penalty:
                penalty_applied_count += 1

            marker = ""
            if diff < 0:
                marker = "← 減点あり"
            elif diff > 0:
                marker = "← 加点？"

            name_short = s_without.student_name.split()[-1] if s_without.student_name else s_without.student_id
            print(
                f"{s_without.student_id:<10} {name_short:<8} "
                f"問{qs_wo.question_id:>1} "
                f"{qs_wo.score:>7.1f} {qs_w.score:>7.1f} {diff:>+5.1f}  {marker}"
            )

    print("-" * 80)
    print(f"合計点差: {total_diff:+.1f}  減点コメント数: {penalty_applied_count}")

    # 詳細: 減点ありの採点コメントを表示
    print(f"\n{'=' * 60}")
    print("減点あり版の採点コメント（減点関連部分）")
    print(f"{'=' * 60}")
    for s in sorted(session_with.students, key=lambda s: s.student_id):
        for qs in s.question_scores:
            if qs.comment and "減点" in qs.comment:
                name_short = s.student_name.split()[-1] if s.student_name else s.student_id
                print(f"\n{s.student_id} {name_short} 問{qs.question_id} ({qs.score}/{qs.max_points}):")
                # コメントの減点関連行だけ抽出
                for line in qs.comment.split("\n"):
                    if "減点" in line or "誤字" in line or "文法" in line or "表記" in line:
                        print(f"  {line.strip()}")


def main():
    rubric, ocr_results = load_test_data()

    # Run 1: 減点オプションなし
    opts_off = GradingOptions()
    session_without = run_grading(rubric, ocr_results, opts_off, "減点なし")

    # Run 2: 減点オプションあり（全ON）
    opts_on = GradingOptions(
        penalize_typos=True,
        penalize_grammar=True,
        penalize_wrong_names=True,
        penalty_per_error=1.0,
        penalty_cap_ratio=0.5,
    )
    session_with = run_grading(rubric, ocr_results, opts_on, "減点あり")

    # 比較
    compare_results(session_without, session_with)

    # JSON保存
    output = {
        "without_options": {},
        "with_options": {},
    }
    for session, key in [(session_without, "without_options"), (session_with, "with_options")]:
        for s in session.students:
            output[key][s.student_id] = {
                "name": s.student_name,
                "scores": {
                    qs.question_id: {
                        "score": qs.score,
                        "comment": qs.comment,
                        "feedback": qs.feedback,
                    }
                    for qs in s.question_scores
                },
            }

    path = "test_data/grading_options_test_result.json"
    with open(path, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n詳細結果を {path} に保存しました")


if __name__ == "__main__":
    main()
