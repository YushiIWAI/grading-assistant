"""storage.py のユニットテスト"""

import csv
import io

import pytest

from auth import hash_password
from models import School, ScoringSession, User
import storage


class TestSaveAndLoad:
    def test_round_trip(self, sample_session, test_db):
        storage.save_session(sample_session)
        loaded = storage.load_session(sample_session.session_id)

        assert loaded is not None
        assert loaded.session_id == sample_session.session_id
        assert len(loaded.students) == 2
        assert loaded.students[0].total_score == sample_session.students[0].total_score

    def test_load_nonexistent(self, test_db):
        result = storage.load_session("nonexistent_id")
        assert result is None


class TestListSessions:
    def test_list_multiple(self, sample_session, test_db):
        storage.save_session(sample_session)

        session2 = ScoringSession(session_id="second", rubric_title="Test 2")
        storage.save_session(session2)

        sessions = storage.list_sessions()
        assert len(sessions) == 2


class TestExportCsv:
    def test_csv_headers(self, sample_session):
        csv_str = storage.export_csv(sample_session)
        reader = csv.reader(io.StringIO(csv_str))
        headers = next(reader)
        assert "学生番号" in headers
        assert "合計点" in headers

    def test_csv_data_rows(self, sample_session):
        csv_str = storage.export_csv(sample_session)
        reader = csv.reader(io.StringIO(csv_str))
        next(reader)  # skip headers
        rows = list(reader)
        assert len(rows) == 2
        # 最初の学生のID
        assert rows[0][0] == "S001"


class TestSchoolCrud:
    def test_create_and_get(self, test_db):
        school = School(name="テスト学校", slug="test-school")
        storage.create_school(school)
        loaded = storage.get_school(school.id)
        assert loaded is not None
        assert loaded.name == "テスト学校"
        assert loaded.slug == "test-school"

    def test_get_by_slug(self, test_db):
        school = School(name="別の学校", slug="another")
        storage.create_school(school)
        loaded = storage.get_school_by_slug("another")
        assert loaded is not None
        assert loaded.id == school.id

    def test_get_nonexistent(self, test_db):
        assert storage.get_school("nonexistent") is None
        assert storage.get_school_by_slug("nonexistent") is None


class TestUserCrud:
    def test_create_and_get(self, test_db):
        school = School(name="学校A", slug="school-a")
        storage.create_school(school)
        user = User(
            school_id=school.id,
            email="teacher@test.com",
            hashed_password=hash_password("pass123"),
            display_name="テスト教員",
            role="teacher",
        )
        storage.create_user(user)
        loaded = storage.get_user(user.id)
        assert loaded is not None
        assert loaded.email == "teacher@test.com"
        assert loaded.school_id == school.id

    def test_get_by_email(self, test_db):
        school = School(name="学校B", slug="school-b")
        storage.create_school(school)
        user = User(
            school_id=school.id,
            email="find@test.com",
            hashed_password=hash_password("pass"),
            display_name="検索テスト",
        )
        storage.create_user(user)
        loaded = storage.get_user_by_email("find@test.com")
        assert loaded is not None
        assert loaded.id == user.id

    def test_get_nonexistent(self, test_db):
        assert storage.get_user("nonexistent") is None
        assert storage.get_user_by_email("nonexistent@test.com") is None


class TestTenantIsolation:
    def test_session_with_school_id(self, sample_session, test_db):
        school = School(name="学校X", slug="school-x")
        storage.create_school(school)
        storage.save_session(sample_session, school_id=school.id)
        loaded = storage.load_session(sample_session.session_id, school_id=school.id)
        assert loaded is not None
        assert loaded.school_id == school.id

    def test_cross_tenant_blocked(self, sample_session, test_db):
        school_a = School(name="学校A", slug="tenant-a")
        school_b = School(name="学校B", slug="tenant-b")
        storage.create_school(school_a)
        storage.create_school(school_b)

        storage.save_session(sample_session, school_id=school_a.id)

        # 学校Aのセッションは学校Bからは見えない
        loaded = storage.load_session(sample_session.session_id, school_id=school_b.id)
        assert loaded is None

    def test_list_sessions_filtered(self, test_db):
        school_a = School(name="学校A", slug="list-a")
        school_b = School(name="学校B", slug="list-b")
        storage.create_school(school_a)
        storage.create_school(school_b)

        s1 = ScoringSession(session_id="s1", rubric_title="A's session")
        s2 = ScoringSession(session_id="s2", rubric_title="B's session")
        storage.save_session(s1, school_id=school_a.id)
        storage.save_session(s2, school_id=school_b.id)

        sessions_a = storage.list_sessions(school_id=school_a.id)
        assert len(sessions_a) == 1
        assert sessions_a[0]["session_id"] == "s1"

        sessions_b = storage.list_sessions(school_id=school_b.id)
        assert len(sessions_b) == 1
        assert sessions_b[0]["session_id"] == "s2"

        # school_id=None は全件
        all_sessions = storage.list_sessions()
        assert len(all_sessions) == 2


class TestDeleteSession:
    def test_delete_existing(self, sample_session, test_db):
        storage.save_session(sample_session)
        deleted = storage.delete_session(sample_session.session_id)
        assert deleted
        assert storage.load_session(sample_session.session_id) is None

    def test_delete_nonexistent(self, test_db):
        deleted = storage.delete_session("nonexistent")
        assert not deleted

    def test_delete_with_tenant(self, sample_session, test_db):
        school_a = School(name="学校A", slug="del-a")
        school_b = School(name="学校B", slug="del-b")
        storage.create_school(school_a)
        storage.create_school(school_b)
        storage.save_session(sample_session, school_id=school_a.id)

        # 学校Bからは削除不可
        deleted = storage.delete_session(sample_session.session_id, school_id=school_b.id)
        assert not deleted
        assert storage.load_session(sample_session.session_id) is not None

        # 学校Aからは削除可能
        deleted = storage.delete_session(sample_session.session_id, school_id=school_a.id)
        assert deleted


class TestPurgeExpired:
    def test_purge_old_sessions(self, test_db):
        import sqlalchemy as sa
        from datetime import datetime, timedelta
        from db import scoring_sessions, get_engine

        school = School(name="パージ学校", slug="purge-school", retention_days=30)
        storage.create_school(school)

        # セッションを保存後、DBのupdated_atを直接書き換えて古くする
        old_session = ScoringSession(session_id="old1", rubric_title="Old")
        storage.save_session(old_session, school_id=school.id)
        old_ts = (datetime.now() - timedelta(days=31)).isoformat()
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                scoring_sessions.update()
                .where(scoring_sessions.c.session_id == "old1")
                .values(updated_at=old_ts)
            )

        # 新しいセッション（1日前）
        new_session = ScoringSession(session_id="new1", rubric_title="New")
        new_session.updated_at = (datetime.now() - timedelta(days=1)).isoformat()
        storage.save_session(new_session, school_id=school.id)

        purged = storage.purge_expired_sessions()
        assert "old1" in purged
        assert "new1" not in purged
        assert storage.load_session("old1") is None
        assert storage.load_session("new1") is not None


class TestExportSchoolData:
    def test_export(self, test_db, test_school, test_user, sample_session):
        storage.save_session(sample_session, school_id=test_school.id)
        data = storage.export_school_data(test_school.id)
        assert data["school"]["id"] == test_school.id
        assert len(data["users"]) == 1
        assert data["users"][0]["email"] == test_user.email
        assert len(data["sessions"]) == 1
        assert "exported_at" in data

    def test_export_nonexistent(self, test_db):
        data = storage.export_school_data("nonexistent")
        assert "error" in data


class TestDeleteSchoolData:
    def test_full_deletion(self, test_db, test_school, test_user, sample_session):
        storage.save_session(sample_session, school_id=test_school.id)
        summary = storage.delete_school_data(test_school.id)
        assert summary["sessions_deleted"] == 1
        assert summary["users_deleted"] == 1
        assert summary["school_deleted"] == 1
        assert storage.get_school(test_school.id) is None


class TestRetentionDays:
    def test_school_retention_days(self, test_db):
        school = School(name="保持テスト", slug="retention-test", retention_days=90)
        storage.create_school(school)
        loaded = storage.get_school(school.id)
        assert loaded.retention_days == 90

    def test_default_retention_days(self, test_db):
        school = School(name="デフォルト", slug="default-ret")
        storage.create_school(school)
        loaded = storage.get_school(school.id)
        assert loaded.retention_days == 365


class TestSeedAdmin:
    def test_seed_idempotent(self, test_db, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAIL", "test-admin@example.com")
        monkeypatch.setenv("ADMIN_PASSWORD", "test-password-123")
        school1, user1 = storage.seed_admin_user()
        school2, user2 = storage.seed_admin_user()
        assert school1.id == school2.id
        assert user1.id == user2.id

    def test_seed_without_env_raises(self, test_db, monkeypatch):
        monkeypatch.delenv("ADMIN_EMAIL", raising=False)
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        import pytest
        with pytest.raises(ValueError, match="ADMIN_EMAIL"):
            storage.seed_admin_user()
