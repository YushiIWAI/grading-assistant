"""共有フィクスチャ"""

import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    OcrAnswer, Question, QuestionScore, Rubric, ScoringSession,
    StudentOcr, StudentResult, SubQuestion,
)
import db as db_module
import storage


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """テスト用 SQLite DB を tmp_path 内に作成し、storage/db がそれを使うようにする。

    テスト終了後にエンジンを破棄する。
    """
    db_path = tmp_path / "test_grading.db"
    test_url = f"sqlite:///{db_path}"

    # db モジュールのエンジンをリセットしてテスト用 URL で再作成
    db_module.reset_engine()
    engine = db_module.get_engine(test_url)
    db_module.init_db(engine)

    # OUTPUT_DIR も tmp_path に向ける
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(storage, "OUTPUT_DIR", output_dir)

    yield engine

    # teardown
    db_module.reset_engine()


@pytest.fixture
def sample_rubric():
    """短答(小問付き) + 記述の2問構成ルーブリック"""
    return Rubric(
        title="テスト用ルーブリック",
        total_points=25,
        pages_per_student=1,
        questions=[
            Question(
                id=1,
                description="漢字の読み",
                question_type="short_answer",
                max_points=10,
                sub_questions=[
                    SubQuestion(id="1-1", text="矛盾", answer="むじゅん", points=5),
                    SubQuestion(id="1-2", text="慈悲", answer="じひ", points=5),
                ],
            ),
            Question(
                id=2,
                description="要約問題",
                question_type="descriptive",
                max_points=15,
                model_answer="模範解答テキスト",
                scoring_criteria="要素A: 5点、要素B: 5点、表現: 5点",
            ),
        ],
    )


@pytest.fixture
def sample_session(sample_rubric):
    """2名の学生がいるセッション（OCR完了・AI採点済み）"""
    session = ScoringSession(
        session_id="test_session",
        rubric_title=sample_rubric.title,
        grading_mode="horizontal",
    )

    # OCR結果
    for i, (name, status) in enumerate(
        [("山田太郎", "reviewed"), ("佐藤花子", "ocr_done")], start=1
    ):
        sid = f"S{i:03d}"
        session.ocr_results.append(StudentOcr(
            student_id=sid,
            student_name=name,
            page_numbers=[i],
            answers=[
                OcrAnswer(question_id="1-1", transcribed_text="むじゅん", confidence="high"),
                OcrAnswer(question_id="1-2", transcribed_text="じひ", confidence="high"),
                OcrAnswer(question_id="2", transcribed_text="テスト解答", confidence="medium"),
            ],
            status=status,
        ))

    # 採点結果
    for i, (name, scores_data) in enumerate(
        [
            ("山田太郎", [("1-1", 5, 5), ("1-2", 5, 5), ("2", 12, 15)]),
            ("佐藤花子", [("1-1", 5, 5), ("1-2", 3, 5), ("2", 8, 15)]),
        ],
        start=1,
    ):
        sid = f"S{i:03d}"
        q_scores = [
            QuestionScore(
                question_id=qid, score=s, max_points=mp,
                comment="テスト", confidence="high",
            )
            for qid, s, mp in scores_data
        ]
        student = StudentResult(
            student_id=sid,
            student_name=name,
            page_numbers=[i],
            question_scores=q_scores,
            status="ai_scored",
        )
        student.recalculate_total()
        session.students.append(student)

    return session
