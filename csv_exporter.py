"""採点結果のCSVエクスポート処理。

Google Classroomへの返却を想定し、生徒ごとの点数・フィードバックをCSVで出力する。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from models import Rubric, ScoringSession, StudentResult


def _sanitize_csv_cell(value: str) -> str:
    """Excelの数式インジェクションを防止する。"""
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@dataclass
class ExportConfig:
    """CSVエクスポートの設定"""
    include_feedback: bool = True  # フィードバック列を含めるか
    include_comment: bool = False  # 教員向けコメント列を含めるか
    include_needs_review: bool = False  # 要確認フラグ列を含めるか
    combine_feedback: bool = False  # 全問のフィードバックを1列にまとめるか
    feedback_separator: str = "\n---\n"  # まとめる場合の区切り文字


def export_results_csv(
    session: ScoringSession,
    rubric: Rubric,
    config: ExportConfig | None = None,
) -> str:
    """採点結果をCSV文字列として出力する。

    Args:
        session: 採点済みのセッション
        rubric: ルーブリック（設問情報の取得用）
        config: エクスポート設定

    Returns:
        CSV文字列（UTF-8 BOM付き、Excel対応）
    """
    if config is None:
        config = ExportConfig()

    # 設問IDリスト（ルーブリックの順序で）
    question_ids = []
    question_labels = {}
    for q in rubric.questions:
        if q.sub_questions:
            for sq in q.sub_questions:
                qid = str(sq.id)
                question_ids.append(qid)
                question_labels[qid] = f"問{q.id}-{sq.id}"
        else:
            qid = str(q.id)
            question_ids.append(qid)
            question_labels[qid] = f"問{q.id}"

    # ヘッダー構築
    headers = ["組", "番号", "氏名"]
    for qid in question_ids:
        label = question_labels[qid]
        headers.append(f"{label}（点数）")
        if config.include_feedback and not config.combine_feedback:
            headers.append(f"{label}（フィードバック）")
        if config.include_comment:
            headers.append(f"{label}（採点根拠）")
        if config.include_needs_review:
            headers.append(f"{label}（要確認）")
    headers.append("合計")
    if config.include_feedback and config.combine_feedback:
        headers.append("フィードバック（全問）")

    # student_id からクラス・番号を分離
    def _parse_student_id(student: StudentResult) -> tuple[str, str, str]:
        """student_id と student_name からクラス、番号、氏名を抽出する。"""
        sid = student.student_id
        name = student.student_name or sid

        # "1-3" 形式 → クラス=1, 番号=3
        if "-" in sid:
            parts = sid.split("-", 1)
            class_val = parts[0]
            number_val = parts[1]
        else:
            class_val = ""
            number_val = sid

        # student_name が "1 3 佐藤美咲" 形式の場合、氏名部分だけ取り出す
        name_parts = name.split()
        if len(name_parts) >= 3:
            # 先頭2つがクラス・番号なら3つ目以降が氏名
            display_name = " ".join(name_parts[2:])
        elif len(name_parts) == 2 and name_parts[0] == class_val:
            display_name = name_parts[1]
        else:
            display_name = name

        return class_val, number_val, display_name

    # データ行構築
    rows = []
    for student in sorted(session.students, key=lambda s: s.student_id):
        class_val, number_val, display_name = _parse_student_id(student)

        # question_id → QuestionScore のマップ
        score_map = {}
        for qs in student.question_scores:
            score_map[qs.question_id] = qs

        row = [_sanitize_csv_cell(class_val), number_val,
               _sanitize_csv_cell(display_name)]
        total = 0.0
        all_feedbacks = []

        for qid in question_ids:
            qs = score_map.get(qid)
            if qs:
                row.append(str(qs.score))
                total += qs.score
                if config.include_feedback and not config.combine_feedback:
                    row.append(_sanitize_csv_cell(qs.feedback or ""))
                if config.include_comment:
                    # コメントから検証結果の部分を除外（教員向け簡潔版）
                    comment = qs.comment.split("\n\n【検証結果】")[0] if qs.comment else ""
                    row.append(_sanitize_csv_cell(comment))
                if config.include_needs_review:
                    row.append("要確認" if qs.needs_review else "")
                if config.combine_feedback and qs.feedback:
                    label = question_labels[qid]
                    all_feedbacks.append(f"【{label}】{qs.feedback}")
            else:
                row.append("")
                if config.include_feedback and not config.combine_feedback:
                    row.append("")
                if config.include_comment:
                    row.append("")
                if config.include_needs_review:
                    row.append("")

        row.append(str(total))
        if config.include_feedback and config.combine_feedback:
            row.append(_sanitize_csv_cell(
                config.feedback_separator.join(all_feedbacks)))

        rows.append(row)

    # CSV出力（BOM付きUTF-8でExcel対応）
    output = io.StringIO()
    output.write("\ufeff")  # BOM
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    writer.writerows(rows)

    return output.getvalue()


def export_feedback_only_csv(
    session: ScoringSession,
    rubric: Rubric,
) -> str:
    """Google Classroom返却用の簡易CSV。氏名・合計点・全問フィードバックのみ。"""
    return export_results_csv(
        session=session,
        rubric=rubric,
        config=ExportConfig(
            include_feedback=True,
            combine_feedback=True,
            include_comment=False,
            include_needs_review=False,
        ),
    )
