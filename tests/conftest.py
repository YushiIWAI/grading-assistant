"""共有フィクスチャ"""

import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import create_access_token, hash_password
from models import (
    OcrAnswer, Question, QuestionScore, Rubric, School, ScoringSession,
    StudentOcr, StudentResult, SubQuestion, User,
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


@pytest.fixture
def test_school(test_db):
    """テスト用学校"""
    school = School(name="テスト学校", slug="test-school")
    storage.create_school(school)
    return school


@pytest.fixture
def test_user(test_db, test_school):
    """テスト用教員ユーザー"""
    user = User(
        school_id=test_school.id,
        email="teacher@test.example.com",
        hashed_password=hash_password("testpassword"),
        display_name="テスト教員",
        role="teacher",
    )
    storage.create_user(user)
    return user


@pytest.fixture
def auth_headers(test_user):
    """認証済みリクエスト用のAuthorizationヘッダー"""
    token = create_access_token(test_user.id, test_user.school_id, test_user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_user(test_db, test_school):
    """テスト用管理者ユーザー"""
    user = User(
        school_id=test_school.id,
        email="admin@test.example.com",
        hashed_password=hash_password("adminpassword"),
        display_name="テスト管理者",
        role="admin",
    )
    storage.create_user(user)
    return user


@pytest.fixture
def admin_headers(admin_user):
    """管理者用のAuthorizationヘッダー"""
    token = create_access_token(admin_user.id, admin_user.school_id, admin_user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def superadmin_user(test_db, test_school):
    """テスト用superadminユーザー"""
    user = User(
        school_id=test_school.id,
        email="superadmin@test.example.com",
        hashed_password=hash_password("superadminpassword"),
        display_name="テストスーパー管理者",
        role="superadmin",
    )
    storage.create_user(user)
    return user


@pytest.fixture
def superadmin_headers(superadmin_user):
    """superadmin用のAuthorizationヘッダー"""
    token = create_access_token(superadmin_user.id, superadmin_user.school_id, superadmin_user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """各テスト実行前にレート制限をリセットする。"""
    from api.app import _login_limiter, _mfa_verify_limiter
    _login_limiter.reset()
    _mfa_verify_limiter.reset()
    yield
    _login_limiter.reset()
    _mfa_verify_limiter.reset()
