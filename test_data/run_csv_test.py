"""CSV入力テスト: 故郷3問 × 30人の採点を実行する"""
import json
import os
import sys
import time

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from csv_importer import parse_forms_csv, get_question_candidate_cols, convert_to_ocr_results, ColumnMapping
from rubric_io import load_rubric_from_yaml
from models import ScoringSession
from scoring_engine import (
    run_horizontal_grading,
    GeminiProvider,
)


def main():
    # --- 1. ルーブリック読み込み ---
    with open("test_data/kokyou_csv_test_rubric.yaml", "r") as f:
        rubric = load_rubric_from_yaml(f.read())
    print(f"ルーブリック: {rubric.title}")
    print(f"  問題数: {len(rubric.questions)}問, 合計: {rubric.total_points}点")

    # --- 2. CSV読み込み ---
    with open("test_data/kokyou_csv_test.csv", "r") as f:
        csv_content = f.read()

    data = parse_forms_csv(csv_content)
    print(f"\nCSV: {len(data.rows)}名分のデータ")

    # 列マッピング: 設問候補列をルーブリックの問題IDに対応付け
    candidate_cols = get_question_candidate_cols(data)
    mapping = ColumnMapping(
        class_col=data.auto_mapping.class_col,
        number_col=data.auto_mapping.number_col,
        name_col=data.auto_mapping.name_col,
        question_cols={},
        ignore_cols=data.auto_mapping.ignore_cols,
    )
    # 設問候補列を順番にルーブリックの問題IDに対応付け
    for i, col_idx in enumerate(candidate_cols):
        if i < len(rubric.questions):
            q = rubric.questions[i]
            mapping.question_cols[str(q.id)] = col_idx
            print(f"  列{col_idx} ({data.headers[col_idx][:30]}...) → 問{q.id}")

    # --- 3. OCR結果に変換 ---
    ocr_results, import_errors = convert_to_ocr_results(data, mapping, rubric)
    if import_errors:
        print(f"\nインポートエラー: {import_errors}")
    print(f"変換完了: {len(ocr_results)}名分のStudentOcr")

    # --- 4. セッション作成 ---
    session = ScoringSession(
        session_id="csv_test_kokyou",
        rubric_title=rubric.title,
        ocr_results=ocr_results,
    )

    # --- 5. プロバイダー作成 ---
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("エラー: GOOGLE_API_KEY が設定されていません")
        sys.exit(1)

    # Flash model + is_typed=True でテスト
    model_name = "gemini-2.5-flash"
    provider = GeminiProvider(api_key=api_key, model_name=model_name)
    print(f"\nプロバイダー: {provider.name} (model={model_name})")

    # --- 6. ルーブリック精緻化テスト: スキップ（動作確認済み）---
    print("\nルーブリック精緻化: スキップ（前回のテストで動作確認済み）")

    # --- 7. 横断採点テスト ---
    print("\n" + "=" * 60)
    print("横断採点テスト (is_typed=True)")
    print("=" * 60)
    t0 = time.time()

    def on_progress(q_idx, total_q, question, batch_idx, total_batches):
        print(f"  問{question.id}: バッチ {batch_idx}/{total_batches}")

    errors = run_horizontal_grading(
        provider=provider,
        rubric=rubric,
        session=session,
        batch_size=10,
        enable_verification=True,
        is_typed=True,
        on_question_progress=on_progress,
    )
    grading_time = time.time() - t0

    print(f"\n採点完了: {grading_time:.1f}秒")
    if errors:
        print(f"エラー: {errors}")

    # --- 8. 結果表示 ---
    print("\n" + "=" * 60)
    print("採点結果")
    print("=" * 60)
    print(f"{'生徒ID':<12} {'氏名':<10} {'問1':>4} {'問2':>4} {'問3':>4} {'合計':>5} {'要確認':>6}")
    print("-" * 52)

    for student in sorted(session.students, key=lambda s: s.student_id):
        scores = {}
        needs_review = False
        for qs in student.question_scores:
            scores[qs.question_id] = qs.score
            if qs.needs_review:
                needs_review = True

        q1 = scores.get("1", scores.get(1, "-"))
        q2 = scores.get("2", scores.get(2, "-"))
        q3 = scores.get("3", scores.get(3, "-"))

        total = sum(v for v in [q1, q2, q3] if isinstance(v, (int, float)))
        review_mark = "★" if needs_review else ""
        print(f"{student.student_id:<12} {student.student_name:<10} {q1:>4} {q2:>4} {q3:>4} {total:>5.0f} {review_mark:>6}")

    # --- 8.5 フィードバックサンプル表示 ---
    print("\n" + "=" * 60)
    print("フィードバックサンプル（5名分）")
    print("=" * 60)
    # 各層から1名ずつ選んで表示
    sample_ids = ["1-1", "1-15", "1-22", "1-30", "1-38"]  # 優秀, 中上位, 中位, 下位, ふざけ
    for student in session.students:
        if student.student_id in sample_ids:
            print(f"\n--- {student.student_name} ({student.student_id}) ---")
            for qs in student.question_scores:
                print(f"  問{qs.question_id} ({qs.score}/{qs.max_points}点)")
                if qs.feedback:
                    print(f"    FB: {qs.feedback}")
                else:
                    print(f"    FB: (なし)")

    # --- 9. 詳細をJSON保存 ---
    output_path = "test_data/kokyou_csv_test_result.json"
    result_data = {
        "model": model_name,
        "grading_time_sec": grading_time,
        "students": [],
    }
    for student in session.students:
        s = {
            "student_id": student.student_id,
            "student_name": student.student_name,
            "question_scores": [],
        }
        for qs in student.question_scores:
            s["question_scores"].append({
                "question_id": qs.question_id,
                "score": qs.score,
                "max_points": qs.max_points,
                "comment": qs.comment,
                "feedback": qs.feedback,
                "needs_review": qs.needs_review,
            })
        result_data["students"].append(s)

    with open(output_path, "w") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)
    print(f"\n詳細結果を {output_path} に保存しました")


if __name__ == "__main__":
    main()
