"""ローカルストレージ: 採点結果のJSON保存・読み込み・CSV出力"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

from models import ScoringSession

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"


def ensure_dirs():
    """必要なディレクトリを作成する"""
    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)


def save_session(session: ScoringSession) -> Path:
    """採点セッションをJSONファイルに保存する"""
    ensure_dirs()
    session.updated_at = datetime.now().isoformat()
    filename = f"session_{session.session_id}.json"
    path = DATA_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def load_session(session_id: str) -> ScoringSession | None:
    """セッションIDからセッションを読み込む"""
    path = DATA_DIR / f"session_{session_id}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return ScoringSession.from_dict(data)


def list_sessions() -> list[dict]:
    """保存済みセッションの一覧を返す"""
    ensure_dirs()
    sessions = []
    for path in sorted(DATA_DIR.glob("session_*.json"), reverse=True):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        sessions.append({
            "session_id": data.get("session_id", ""),
            "created_at": data.get("created_at", ""),
            "rubric_title": data.get("rubric_title", ""),
            "pdf_filename": data.get("pdf_filename", ""),
            "student_count": len(data.get("students", [])),
        })
    return sessions


def export_csv(session: ScoringSession) -> str:
    """採点結果をCSV文字列にエクスポートする"""
    output = io.StringIO()
    writer = csv.writer(output)

    # ヘッダー行
    headers = ["学生番号", "氏名", "状態"]
    # 設問ごとの列を動的に作成
    if session.students:
        first = session.students[0]
        for qs in first.question_scores:
            headers.extend([
                f"問{qs.question_id}_得点",
                f"問{qs.question_id}_配点",
                f"問{qs.question_id}_読取",
                f"問{qs.question_id}_コメント",
                f"問{qs.question_id}_確信度",
                f"問{qs.question_id}_要確認",
            ])
    headers.extend(["合計点", "満点", "教員メモ"])
    writer.writerow(headers)

    # データ行
    for student in session.students:
        row = [student.student_id, student.student_name, student.status]
        for qs in student.question_scores:
            row.extend([
                qs.score,
                qs.max_points,
                qs.transcribed_text,
                qs.comment,
                qs.confidence,
                "要確認" if qs.needs_review else "",
            ])
        row.extend([student.total_score, student.total_max_points, student.reviewer_notes])
        writer.writerow(row)

    return output.getvalue()


def export_csv_file(session: ScoringSession) -> Path:
    """採点結果をCSVファイルにエクスポートする"""
    ensure_dirs()
    csv_content = export_csv(session)
    filename = f"results_{session.session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8-sig") as f:  # BOM付きUTF-8（Excel対応）
        f.write(csv_content)
    return path
