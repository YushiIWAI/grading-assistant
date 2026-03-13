"""ローカルストレージ: 採点結果のDB保存・読み込み・CSV出力"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa

from db import get_engine, init_db, scoring_sessions
from models import ScoringSession

OUTPUT_DIR = Path(__file__).parent / "output"


def ensure_dirs():
    """必要なディレクトリを作成する"""
    OUTPUT_DIR.mkdir(exist_ok=True)


def _ensure_table():
    """テーブルが存在しなければ作成する"""
    init_db()


def save_session(session: ScoringSession) -> Path:
    """採点セッションをDBに保存する"""
    ensure_dirs()
    _ensure_table()
    session.updated_at = datetime.now().isoformat()
    data = session.to_dict()

    engine = get_engine()
    with engine.begin() as conn:
        # UPSERT: 存在チェック → insert or update
        existing = conn.execute(
            sa.select(scoring_sessions.c.session_id).where(
                scoring_sessions.c.session_id == session.session_id
            )
        ).fetchone()

        if existing:
            conn.execute(
                scoring_sessions.update()
                .where(scoring_sessions.c.session_id == session.session_id)
                .values(
                    created_at=data["created_at"],
                    updated_at=data["updated_at"],
                    rubric_title=data["rubric_title"],
                    pdf_filename=data["pdf_filename"],
                    pages_per_student=data["pages_per_student"],
                    grading_mode=data["grading_mode"],
                    students=data["students"],
                    ocr_results=data["ocr_results"],
                )
            )
        else:
            conn.execute(
                scoring_sessions.insert().values(
                    session_id=data["session_id"],
                    created_at=data["created_at"],
                    updated_at=data["updated_at"],
                    rubric_title=data["rubric_title"],
                    pdf_filename=data["pdf_filename"],
                    pages_per_student=data["pages_per_student"],
                    grading_mode=data["grading_mode"],
                    students=data["students"],
                    ocr_results=data["ocr_results"],
                )
            )

    # 互換のため合成パスを返す（呼び出し側は使っていない）
    return Path(f"db://session_{session.session_id}")


def load_session(session_id: str) -> ScoringSession | None:
    """セッションIDからセッションを読み込む"""
    _ensure_table()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(scoring_sessions).where(
                scoring_sessions.c.session_id == session_id
            )
        ).mappings().fetchone()

    if row is None:
        return None

    data = dict(row)
    # JSON カラムが文字列の場合はパースする（SQLite の場合）
    for col in ("students", "ocr_results"):
        if isinstance(data[col], str):
            data[col] = json.loads(data[col])

    return ScoringSession.from_dict(data)


def list_sessions() -> list[dict]:
    """保存済みセッションの一覧を返す"""
    _ensure_table()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                scoring_sessions.c.session_id,
                scoring_sessions.c.created_at,
                scoring_sessions.c.rubric_title,
                scoring_sessions.c.pdf_filename,
                scoring_sessions.c.students,
            ).order_by(scoring_sessions.c.created_at.desc())
        ).mappings().all()

    sessions = []
    for row in rows:
        students = row["students"]
        if isinstance(students, str):
            students = json.loads(students)
        sessions.append({
            "session_id": row["session_id"],
            "created_at": row["created_at"],
            "rubric_title": row["rubric_title"],
            "pdf_filename": row["pdf_filename"],
            "student_count": len(students),
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
                f"問{qs.question_id}_確認理由",
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
                qs.review_reason if qs.needs_review else "",
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


def migrate_json_to_db(data_dir: Path | None = None) -> list[str]:
    """既存のJSONファイルをDBに移行する。移行済みファイルは .json.migrated にリネーム。

    Returns:
        移行したセッションIDのリスト
    """
    _ensure_table()
    if data_dir is None:
        data_dir = Path(__file__).parent / "data"

    migrated = []
    for path in sorted(data_dir.glob("session_*.json")):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        session = ScoringSession.from_dict(data)
        save_session(session)
        path.rename(path.with_suffix(".json.migrated"))
        migrated.append(session.session_id)

    return migrated


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "migrate-json":
        ids = migrate_json_to_db()
        print(f"移行完了: {len(ids)} セッション")
        for sid in ids:
            print(f"  - {sid}")
    else:
        print("使い方: python -m storage migrate-json")
