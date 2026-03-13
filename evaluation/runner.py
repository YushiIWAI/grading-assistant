"""採点エンジン評価ランナー

ゴールドデータ（期待スコア付きテストケース）を使って
採点エンジンの精度を自動評価する。

使い方:
    # 環境変数 GOOGLE_API_KEY を設定済みの状態で:
    python evaluation/runner.py

    # 特定のゴールドデータのみ:
    python evaluation/runner.py --gold evaluation/gold/todai_gendaibun.json

    # ドライラン（API呼び出しなし、ゴールドデータの検証のみ）:
    python evaluation/runner.py --dry-run

    # モデル指定:
    python evaluation/runner.py --model gemini-2.5-flash
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import yaml

from models import Question, Rubric, SubQuestion
from scoring_engine import (
    GeminiProvider,
    build_horizontal_grading_prompt,
    parse_horizontal_grading_result,
    HORIZONTAL_GRADING_SYSTEM_PROMPT,
)


# ============================================================
# データ構造
# ============================================================

@dataclass
class TestCase:
    case_id: str
    question_id: str
    student_answer: str
    expected_score: float
    score_tolerance: float
    score_level: str
    expected_elements: list[str]
    tags: list[str]
    rationale: str
    expected_needs_review: bool = False


@dataclass
class GoldData:
    exam_id: str
    exam_title: str
    source_rubric: str
    questions: list[dict]
    test_cases: list[TestCase]


@dataclass
class CaseResult:
    case_id: str
    question_id: str
    expected_score: float
    actual_score: float
    tolerance: float
    passed: bool
    score_level: str
    tags: list[str]
    ai_comment: str
    rationale: str
    expected_needs_review: bool = False
    actual_needs_review: bool = False
    review_reason: str = ""


@dataclass
class EvalReport:
    exam_id: str
    exam_title: str
    provider_name: str
    total_cases: int = 0
    passed_cases: int = 0
    results: list[CaseResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def pass_rate(self) -> float:
        return self.passed_cases / self.total_cases * 100 if self.total_cases else 0

    def results_by_question_type(self) -> dict[str, dict]:
        by_type: dict[str, dict] = {}
        for r in self.results:
            qtype = r.question_id.split("-")[0] if "-" in r.question_id else r.question_id
            if qtype not in by_type:
                by_type[qtype] = {"total": 0, "passed": 0}
            by_type[qtype]["total"] += 1
            if r.passed:
                by_type[qtype]["passed"] += 1
        return by_type

    def results_by_score_level(self) -> dict[str, dict]:
        by_level: dict[str, dict] = {}
        for r in self.results:
            level = r.score_level
            if level not in by_level:
                by_level[level] = {"total": 0, "passed": 0}
            by_level[level]["total"] += 1
            if r.passed:
                by_level[level]["passed"] += 1
        return by_level

    def needs_review_metrics(self) -> dict:
        """needs_review フラグの Precision / Recall / F1 を計算"""
        tp = fp = fn = tn = 0
        for r in self.results:
            if r.expected_needs_review and r.actual_needs_review:
                tp += 1
            elif not r.expected_needs_review and r.actual_needs_review:
                fp += 1
            elif r.expected_needs_review and not r.actual_needs_review:
                fn += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        return {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }


# ============================================================
# ゴールドデータの読み込み
# ============================================================

def load_gold_data(path: Path) -> GoldData:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    test_cases = [
        TestCase(
            case_id=tc["case_id"],
            question_id=tc["question_id"],
            student_answer=tc["student_answer"],
            expected_score=tc["expected_score"],
            score_tolerance=tc["score_tolerance"],
            score_level=tc["score_level"],
            expected_elements=tc.get("expected_elements", []),
            tags=tc.get("tags", []),
            rationale=tc.get("rationale", ""),
            expected_needs_review=tc.get("expected_needs_review", False),
        )
        for tc in raw["test_cases"]
    ]

    return GoldData(
        exam_id=raw["exam_id"],
        exam_title=raw["exam_title"],
        source_rubric=raw["source_rubric"],
        questions=raw["questions"],
        test_cases=test_cases,
    )


def load_rubric_from_yaml(rubric_path: Path) -> Rubric:
    with open(rubric_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    questions = []
    for q in data.get("questions", []):
        sub_questions = [
            SubQuestion(id=sq["id"], text=sq["text"], answer=sq["answer"], points=sq["points"])
            for sq in q.get("sub_questions", [])
        ]
        questions.append(Question(
            id=q["id"],
            description=q["description"],
            question_type=q["type"],
            max_points=q["max_points"],
            scoring_criteria=q.get("scoring_criteria", ""),
            model_answer=q.get("model_answer", ""),
            sub_questions=sub_questions,
        ))

    exam_info = data.get("exam_info", {})
    return Rubric(
        title=exam_info.get("title", ""),
        total_points=exam_info.get("total_points", 0),
        pages_per_student=exam_info.get("pages_per_student", 1),
        questions=questions,
        notes=data.get("notes", ""),
    )


# ============================================================
# 評価実行
# ============================================================

def run_evaluation(
    gold: GoldData,
    rubric: Rubric,
    provider: GeminiProvider,
) -> EvalReport:
    """ゴールドデータを使って採点エンジンを評価する。

    横断採点（grade_question_batch）を使い、設問ごとにテストケースをバッチで送る。
    """
    report = EvalReport(
        exam_id=gold.exam_id,
        exam_title=gold.exam_title,
        provider_name=provider.name,
    )
    start_time = time.time()

    # テストケースを question_id でグループ化
    cases_by_question: dict[str, list[TestCase]] = {}
    for tc in gold.test_cases:
        cases_by_question.setdefault(tc.question_id, []).append(tc)

    for question in rubric.questions:
        # この設問に対応するテストケースを収集
        q_ids: list[str] = []
        if question.sub_questions:
            q_ids = [sq.id for sq in question.sub_questions]
        else:
            q_ids = [str(question.id)]

        for q_id in q_ids:
            cases = cases_by_question.get(q_id, [])
            if not cases:
                continue

            print(f"  問{q_id}: {len(cases)}件のテストケースを評価中...", end="", flush=True)

            # 各テストケースを「学生」として横断採点に送る
            students_answers: list[tuple[str, str, str]] = [
                (tc.case_id, tc.case_id, tc.student_answer)
                for tc in cases
            ]

            try:
                result = provider.grade_question_batch(
                    question=question,
                    rubric_title=rubric.title,
                    students_answers=students_answers,
                    notes=rubric.notes,
                )

                expected_ids = [tc.case_id for tc in cases]
                scores_by_student = parse_horizontal_grading_result(
                    result, question, expected_ids,
                )

                # 結果を照合
                for tc in cases:
                    q_scores = scores_by_student.get(tc.case_id, [])

                    if question.sub_questions:
                        # 小問の場合: q_id に一致するスコアを探す
                        actual_score = 0.0
                        ai_comment = ""
                        actual_needs_review = False
                        review_reason = ""
                        for qs in q_scores:
                            if qs.question_id == q_id:
                                actual_score = qs.score
                                ai_comment = qs.comment
                                actual_needs_review = qs.needs_review
                                review_reason = getattr(qs, "review_reason", "")
                                break
                    else:
                        actual_score = q_scores[0].score if q_scores else 0.0
                        ai_comment = q_scores[0].comment if q_scores else ""
                        actual_needs_review = q_scores[0].needs_review if q_scores else False
                        review_reason = getattr(q_scores[0], "review_reason", "") if q_scores else ""

                    passed = abs(actual_score - tc.expected_score) <= tc.score_tolerance

                    case_result = CaseResult(
                        case_id=tc.case_id,
                        question_id=q_id,
                        expected_score=tc.expected_score,
                        actual_score=actual_score,
                        tolerance=tc.score_tolerance,
                        passed=passed,
                        score_level=tc.score_level,
                        tags=tc.tags,
                        ai_comment=ai_comment,
                        rationale=tc.rationale,
                        expected_needs_review=tc.expected_needs_review,
                        actual_needs_review=actual_needs_review,
                        review_reason=review_reason,
                    )
                    report.results.append(case_result)
                    report.total_cases += 1
                    if passed:
                        report.passed_cases += 1

                print(f" 完了")

            except Exception as e:
                error_msg = f"問{q_id}: {e}"
                report.errors.append(error_msg)
                print(f" エラー: {e}")

                # エラー時はすべて不合格として記録
                for tc in cases:
                    report.results.append(CaseResult(
                        case_id=tc.case_id,
                        question_id=q_id,
                        expected_score=tc.expected_score,
                        actual_score=-1,
                        tolerance=tc.score_tolerance,
                        passed=False,
                        score_level=tc.score_level,
                        tags=tc.tags,
                        ai_comment=f"APIエラー: {e}",
                        rationale=tc.rationale,
                        expected_needs_review=tc.expected_needs_review,
                        actual_needs_review=False,
                    ))
                    report.total_cases += 1

    report.elapsed_seconds = time.time() - start_time
    return report


# ============================================================
# レポート出力
# ============================================================

def print_report(report: EvalReport):
    print()
    print("=" * 60)
    print(f"採点エンジン評価レポート")
    print(f"試験: {report.exam_title}")
    print(f"プロバイダー: {report.provider_name}")
    print(f"実行時間: {report.elapsed_seconds:.1f}秒")
    print("=" * 60)

    print(f"\n総テストケース: {report.total_cases}")
    print(f"合格: {report.passed_cases}/{report.total_cases} ({report.pass_rate:.1f}%)")

    if report.errors:
        print(f"\nAPIエラー: {len(report.errors)}件")
        for err in report.errors:
            print(f"  - {err}")

    # 不合格ケースの詳細
    failed = [r for r in report.results if not r.passed]
    if failed:
        print(f"\n--- 不合格ケース ({len(failed)}件) ---")
        for r in failed:
            diff = r.actual_score - r.expected_score
            sign = "+" if diff > 0 else ""
            print(f"\n{r.case_id} (問{r.question_id}):")
            print(f"  期待: {r.expected_score}±{r.tolerance}, 実際: {r.actual_score} (乖離: {sign}{diff:.0f})")
            print(f"  レベル: {r.score_level}")
            print(f"  理由: {r.rationale}")
            if r.ai_comment:
                comment_short = r.ai_comment[:100] + "..." if len(r.ai_comment) > 100 else r.ai_comment
                print(f"  AIコメント: {comment_short}")

    # 設問別サマリー
    by_q = report.results_by_question_type()
    if by_q:
        print(f"\n--- 設問別 ---")
        for q_id, stats in sorted(by_q.items()):
            rate = stats["passed"] / stats["total"] * 100 if stats["total"] else 0
            marker = " <-- 要改善" if rate < 70 else ""
            print(f"  問{q_id}: {stats['passed']}/{stats['total']} ({rate:.1f}%){marker}")

    # 得点レベル別
    by_level = report.results_by_score_level()
    if by_level:
        print(f"\n--- 得点レベル別 ---")
        level_order = ["full", "high_partial", "low_partial", "zero", "off_topic"]
        for level in level_order:
            if level in by_level:
                stats = by_level[level]
                rate = stats["passed"] / stats["total"] * 100 if stats["total"] else 0
                print(f"  {level}: {stats['passed']}/{stats['total']} ({rate:.1f}%)")

    # needs_review 精度
    nr = report.needs_review_metrics()
    print(f"\n--- needs_review 精度 ---")
    print(f"  Precision: {nr['precision']:.3f}  (AIがreview判定したもののうち正しかった割合)")
    print(f"  Recall:    {nr['recall']:.3f}  (本来reviewすべきもののうちAIが検出した割合)")
    print(f"  F1:        {nr['f1']:.3f}")
    print(f"  TP={nr['tp']} FP={nr['fp']} FN={nr['fn']} TN={nr['tn']}")

    # FN詳細（見逃し: reviewすべきなのにAIがスルー）
    fn_cases = [r for r in report.results if r.expected_needs_review and not r.actual_needs_review]
    if fn_cases:
        print(f"\n  見逃し（FN）: {len(fn_cases)}件")
        for r in fn_cases:
            print(f"    {r.case_id} (問{r.question_id}, {r.score_level}): "
                  f"期待{r.expected_score}→実際{r.actual_score}")

    # FP詳細（過剰フラグ: reviewしなくてよいのにAIがフラグ）
    fp_cases = [r for r in report.results if not r.expected_needs_review and r.actual_needs_review]
    if fp_cases:
        print(f"\n  過剰フラグ（FP）: {len(fp_cases)}件")
        for r in fp_cases:
            reason_short = r.review_reason[:60] + "..." if len(r.review_reason) > 60 else r.review_reason
            print(f"    {r.case_id} (問{r.question_id}, {r.score_level}): "
                  f"reason={reason_short}")

    # 重大な誤採点チェック
    critical = [
        r for r in report.results
        if (r.score_level == "full" and r.actual_score <= r.expected_score * 0.3)
        or (r.score_level in ("zero", "off_topic") and r.actual_score >= r.expected_score + 5)
    ]
    if critical:
        print(f"\n!!! 重大な誤採点: {len(critical)}件 !!!")
        for r in critical:
            print(f"  {r.case_id}: 期待{r.expected_score} → 実際{r.actual_score} ({r.score_level})")


def save_report_json(report: EvalReport, output_path: Path):
    data = {
        "exam_id": report.exam_id,
        "exam_title": report.exam_title,
        "provider_name": report.provider_name,
        "total_cases": report.total_cases,
        "passed_cases": report.passed_cases,
        "pass_rate": round(report.pass_rate, 1),
        "elapsed_seconds": round(report.elapsed_seconds, 1),
        "errors": report.errors,
        "results": [
            {
                "case_id": r.case_id,
                "question_id": r.question_id,
                "expected_score": r.expected_score,
                "actual_score": r.actual_score,
                "tolerance": r.tolerance,
                "passed": r.passed,
                "score_level": r.score_level,
                "tags": r.tags,
                "ai_comment": r.ai_comment,
                "rationale": r.rationale,
                "expected_needs_review": r.expected_needs_review,
                "actual_needs_review": r.actual_needs_review,
                "review_reason": r.review_reason,
            }
            for r in report.results
        ],
        "by_question": report.results_by_question_type(),
        "by_score_level": report.results_by_score_level(),
        "needs_review_metrics": report.needs_review_metrics(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nレポートJSON: {output_path}")


# ============================================================
# ドライラン（ゴールドデータの検証のみ）
# ============================================================

def dry_run(gold: GoldData, rubric: Rubric):
    print(f"\n=== ドライラン: {gold.exam_title} ===")
    print(f"ルーブリック設問数: {len(rubric.questions)}")
    print(f"テストケース数: {len(gold.test_cases)}")

    # テストケースのquestion_idがルーブリックに存在するか検証
    valid_ids: set[str] = set()
    for q in rubric.questions:
        if q.sub_questions:
            for sq in q.sub_questions:
                valid_ids.add(str(sq.id))
        else:
            valid_ids.add(str(q.id))

    invalid = [tc for tc in gold.test_cases if tc.question_id not in valid_ids]
    if invalid:
        print(f"\n警告: ルーブリックに存在しないquestion_idのケース:")
        for tc in invalid:
            print(f"  {tc.case_id} -> question_id={tc.question_id}")
    else:
        print("question_idの整合性: OK")

    # 得点レベル別の分布
    by_level: dict[str, int] = {}
    by_question: dict[str, int] = {}
    for tc in gold.test_cases:
        by_level[tc.score_level] = by_level.get(tc.score_level, 0) + 1
        by_question[tc.question_id] = by_question.get(tc.question_id, 0) + 1

    print(f"\n得点レベル別:")
    for level, count in sorted(by_level.items()):
        print(f"  {level}: {count}件")

    print(f"\n設問別:")
    for q_id, count in sorted(by_question.items()):
        print(f"  問{q_id}: {count}件")

    # expected_needs_review の分布
    nr_true = sum(1 for tc in gold.test_cases if tc.expected_needs_review)
    nr_false = len(gold.test_cases) - nr_true
    print(f"\nexpected_needs_review: True={nr_true}, False={nr_false}")

    # score_tolerance の分布
    tolerances = [tc.score_tolerance for tc in gold.test_cases]
    print(f"\nscore_tolerance: min={min(tolerances)}, max={max(tolerances)}, avg={sum(tolerances)/len(tolerances):.1f}")


# ============================================================
# エントリーポイント
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="採点エンジン評価ランナー")
    parser.add_argument(
        "--gold",
        type=str,
        default=None,
        help="ゴールドデータJSONのパス（未指定で evaluation/gold/ 内の全ファイル）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gemini-2.5-flash",
        help="Geminiモデル名（デフォルト: gemini-2.5-flash）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API呼び出しなしでゴールドデータの検証のみ実行",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="レポートJSON出力先（未指定で evaluation/reports/ に自動保存）",
    )
    args = parser.parse_args()

    # ゴールドデータの収集
    eval_dir = PROJECT_ROOT / "evaluation"
    gold_dir = eval_dir / "gold"

    if args.gold:
        gold_paths = [Path(args.gold)]
    else:
        gold_paths = sorted(gold_dir.glob("*.json"))

    if not gold_paths:
        print("ゴールドデータが見つかりません。evaluation/gold/ にJSONファイルを配置してください。")
        sys.exit(1)

    for gold_path in gold_paths:
        print(f"\n{'=' * 60}")
        print(f"ゴールドデータ: {gold_path.name}")

        gold = load_gold_data(gold_path)
        rubric_path = PROJECT_ROOT / gold.source_rubric
        if not rubric_path.exists():
            print(f"ルーブリックが見つかりません: {rubric_path}")
            continue

        rubric = load_rubric_from_yaml(rubric_path)

        if args.dry_run:
            dry_run(gold, rubric)
            continue

        # APIキーの確認
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print("環境変数 GOOGLE_API_KEY を設定してください。")
            sys.exit(1)

        provider = GeminiProvider(api_key=api_key, model_name=args.model)
        print(f"プロバイダー: {provider.name}")

        report = run_evaluation(gold, rubric, provider)
        print_report(report)

        # レポート保存
        if args.output:
            output_path = Path(args.output)
        else:
            reports_dir = eval_dir / "reports"
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            output_path = reports_dir / f"{gold.exam_id}_{timestamp}.json"

        save_report_json(report, output_path)


if __name__ == "__main__":
    main()
