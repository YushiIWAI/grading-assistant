"""api/app.py のユニットテスト"""

import base64
from dataclasses import asdict

import fitz
from fastapi.testclient import TestClient
import pytest

from api.app import app
from models import School, ScoringSession
from rubric_io import rubric_to_yaml
import storage


def _build_pdf_base64() -> str:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "API OCR test")
    return base64.b64encode(doc.tobytes()).decode("utf-8")


@pytest.fixture
def client(test_db):
    return TestClient(app)


class TestApi:
    def test_healthz(self, client):
        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_parse_rubric(self, client, sample_rubric):
        response = client.post(
            "/api/v1/rubrics/parse",
            json={"yaml_text": rubric_to_yaml(sample_rubric)},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["title"] == sample_rubric.title
        assert body["summary"]["question_count"] == 2

    def test_render_rubric(self, client, sample_rubric):
        response = client.post(
            "/api/v1/rubrics/render",
            json={"rubric": sample_rubric.__dict__ | {
                "questions": [
                    {
                        "id": q.id,
                        "description": q.description,
                        "type": q.question_type,
                        "max_points": q.max_points,
                        "scoring_criteria": q.scoring_criteria,
                        "model_answer": q.model_answer,
                        "sub_questions": [
                            {
                                "id": sq.id,
                                "text": sq.text,
                                "answer": sq.answer,
                                "points": sq.points,
                            }
                            for sq in q.sub_questions
                        ],
                    }
                    for q in sample_rubric.questions
                ],
            }},
        )

        assert response.status_code == 200
        assert "exam_info:" in response.text
        assert sample_rubric.title in response.text

    def test_create_and_get_session(self, client, auth_headers):
        create_response = client.post(
            "/api/v1/sessions",
            json={
                "rubric_title": "APIテスト",
                "pdf_filename": "answers.pdf",
                "pages_per_student": 2,
            },
            headers=auth_headers,
        )

        assert create_response.status_code == 201
        session = create_response.json()["session"]

        get_response = client.get(
            f"/api/v1/sessions/{session['session_id']}",
            headers=auth_headers,
        )
        assert get_response.status_code == 200
        assert get_response.json()["session"]["rubric_title"] == "APIテスト"

    def test_list_sessions(self, client, sample_session, test_school, auth_headers):
        storage.save_session(sample_session, school_id=test_school.id)

        response = client.get("/api/v1/sessions", headers=auth_headers)

        assert response.status_code == 200
        sessions = response.json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == sample_session.session_id

    def test_put_session_and_export_csv(self, client, sample_session, test_school, auth_headers):
        storage.save_session(sample_session, school_id=test_school.id)
        payload = sample_session.to_dict()
        payload["rubric_title"] = "更新後タイトル"

        put_response = client.put(
            f"/api/v1/sessions/{sample_session.session_id}",
            json=payload,
            headers=auth_headers,
        )
        assert put_response.status_code == 200
        assert put_response.json()["session"]["rubric_title"] == "更新後タイトル"

        csv_response = client.get(
            f"/api/v1/sessions/{sample_session.session_id}/exports/csv",
            headers=auth_headers,
        )
        assert csv_response.status_code == 200
        assert "学生番号,氏名,状態" in csv_response.text

    def test_run_ocr(self, client, sample_rubric, auth_headers):
        create_response = client.post(
            "/api/v1/sessions",
            json={
                "rubric_title": sample_rubric.title,
                "pdf_filename": "answers.pdf",
                "pages_per_student": sample_rubric.pages_per_student,
            },
            headers=auth_headers,
        )
        session_id = create_response.json()["session"]["session_id"]

        response = client.post(
            "/api/v1/runs/ocr",
            json={
                "session_id": session_id,
                "rubric": asdict(sample_rubric),
                "pdf_base64": _build_pdf_base64(),
                "provider": {"provider": "demo", "privacy_mask": {"enabled": True}},
                "enable_two_stage": True,
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["session"]["ocr_results"]) == 1
        assert body["session"]["ocr_results"][0]["status"] == "ocr_done"

    def test_run_horizontal_grading(self, client, sample_session, sample_rubric, test_school, auth_headers):
        storage.save_session(sample_session, school_id=test_school.id)

        response = client.post(
            "/api/v1/runs/horizontal-grading",
            json={
                "session_id": sample_session.session_id,
                "rubric": asdict(sample_rubric),
                "provider": {"provider": "demo", "privacy_mask": {"enabled": True}},
                "batch_size": 10,
                "enable_verification": False,
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["scored"] == 2
        assert len(body["session"]["students"]) == 2

    def test_refine_rubric(self, client, sample_session, sample_rubric, test_school, auth_headers):
        storage.save_session(sample_session, school_id=test_school.id)

        response = client.post(
            "/api/v1/rubrics/refine",
            json={
                "session_id": sample_session.session_id,
                "rubric": asdict(sample_rubric),
                "provider": {"provider": "demo", "privacy_mask": {"enabled": True}},
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert "questions" in body
        assert len(body["questions"]) >= 1
        q = body["questions"][0]
        assert q["question_id"] == "2"
        assert "student_answer" in q
        assert "options" in q

    def test_refine_rubric_no_ocr(self, client, sample_rubric, auth_headers):
        create_resp = client.post(
            "/api/v1/sessions",
            json={"rubric_title": "テスト", "pdf_filename": "test.pdf", "pages_per_student": 1},
            headers=auth_headers,
        )
        session_id = create_resp.json()["session"]["session_id"]

        response = client.post(
            "/api/v1/rubrics/refine",
            json={
                "session_id": session_id,
                "rubric": asdict(sample_rubric),
                "provider": {"provider": "demo"},
            },
            headers=auth_headers,
        )
        assert response.status_code == 400


class TestUnauthenticatedAccess:
    """未認証アクセスが401を返すことを検証するテスト"""

    def test_sessions_requires_auth(self, client):
        assert client.get("/api/v1/sessions").status_code == 401

    def test_create_session_requires_auth(self, client):
        resp = client.post("/api/v1/sessions", json={"rubric_title": "x"})
        assert resp.status_code == 401

    def test_audit_logs_requires_auth(self, client):
        assert client.get("/api/v1/audit-logs").status_code == 401

    def test_admin_purge_requires_auth(self, client):
        assert client.post("/api/v1/admin/purge-expired").status_code == 401

    def test_admin_delete_school_requires_auth(self, client):
        assert client.delete("/api/v1/admin/schools/xxx").status_code == 401

    def test_admin_api_keys_requires_auth(self, client):
        assert client.get("/api/v1/admin/api-keys").status_code == 401

    def test_run_ocr_requires_auth(self, client):
        resp = client.post("/api/v1/runs/ocr", json={"session_id": "x", "rubric": {}, "pdf_base64": "x", "provider": {"provider": "demo"}})
        assert resp.status_code == 401


class TestAuthEndpoints:
    """認証API のテスト"""

    def test_login_success(self, client, test_user):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "teacher@test.example.com", "password": "testpassword"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"
        assert body["user"]["email"] == "teacher@test.example.com"
        assert body["user"]["role"] == "teacher"

    def test_login_wrong_password(self, client, test_user):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "teacher@test.example.com", "password": "wrong"},
        )
        assert response.status_code == 401

    def test_login_nonexistent_email(self, client, test_db):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@test.com", "password": "pass"},
        )
        assert response.status_code == 401

    def test_refresh_token(self, client, test_user):
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": "teacher@test.example.com", "password": "testpassword"},
        )
        refresh_token = login_resp.json()["refresh_token"]

        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

    def test_refresh_with_access_token_fails(self, client, test_user):
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": "teacher@test.example.com", "password": "testpassword"},
        )
        access_token = login_resp.json()["access_token"]

        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": access_token},
        )
        assert response.status_code == 401

    def test_me_authenticated(self, client, auth_headers):
        response = client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["authenticated"] is True
        assert body["user"]["email"] == "teacher@test.example.com"

    def test_me_unauthenticated(self, client):
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 200
        assert response.json()["authenticated"] is False


class TestTenantIsolationApi:
    """APIレベルのテナント分離テスト"""

    def test_session_visible_only_to_own_school(self, client, test_user, test_school, auth_headers):
        create_resp = client.post(
            "/api/v1/sessions",
            json={"rubric_title": "テスト", "pdf_filename": "test.pdf"},
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["session"]["session_id"]

        get_resp = client.get(f"/api/v1/sessions/{session_id}", headers=auth_headers)
        assert get_resp.status_code == 200

        from auth import create_access_token, hash_password
        from models import User
        other_school = School(name="別学校", slug="other-school")
        storage.create_school(other_school)
        other_user = User(
            school_id=other_school.id,
            email="other@test.com",
            hashed_password=hash_password("pass"),
            display_name="別の教員",
        )
        storage.create_user(other_user)
        other_token = create_access_token(other_user.id, other_school.id, "teacher")
        other_headers = {"Authorization": f"Bearer {other_token}"}

        get_resp2 = client.get(f"/api/v1/sessions/{session_id}", headers=other_headers)
        assert get_resp2.status_code == 404

    def test_list_sessions_filtered_by_school(self, client, test_user, test_school, auth_headers):
        client.post(
            "/api/v1/sessions",
            json={"rubric_title": "My Session"},
            headers=auth_headers,
        )

        other_school = School(name="別学校2", slug="other-2")
        storage.create_school(other_school)
        other_session = ScoringSession(session_id="other-s", rubric_title="Other")
        storage.save_session(other_session, school_id=other_school.id)

        list_resp = client.get("/api/v1/sessions", headers=auth_headers)
        sessions = list_resp.json()["sessions"]
        session_ids = [s["session_id"] for s in sessions]
        assert "other-s" not in session_ids


class TestDeleteSessionApi:
    """セッション削除APIのテスト"""

    def test_delete_session(self, client, sample_session, test_school, auth_headers):
        storage.save_session(sample_session, school_id=test_school.id)
        response = client.delete(
            f"/api/v1/sessions/{sample_session.session_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["deleted"] is True

        get_resp = client.get(
            f"/api/v1/sessions/{sample_session.session_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 404

    def test_delete_nonexistent(self, client, auth_headers):
        response = client.delete("/api/v1/sessions/nonexistent", headers=auth_headers)
        assert response.status_code == 404


class TestAuditLogApi:
    """監査ログAPIのテスト"""

    def test_get_audit_logs(self, client, auth_headers):
        client.post(
            "/api/v1/sessions",
            json={"rubric_title": "監査テスト"},
            headers=auth_headers,
        )
        response = client.get("/api/v1/audit-logs", headers=auth_headers)
        assert response.status_code == 200
        logs = response.json()["audit_logs"]
        assert len(logs) >= 1

    def test_verify_chain(self, client, auth_headers):
        client.post("/api/v1/sessions", json={"rubric_title": "chain1"}, headers=auth_headers)
        client.post("/api/v1/sessions", json={"rubric_title": "chain2"}, headers=auth_headers)
        response = client.get("/api/v1/audit-logs/verify", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["is_valid"] is True

    def test_login_creates_audit_log(self, client, test_user, auth_headers):
        client.post(
            "/api/v1/auth/login",
            json={"email": "teacher@test.example.com", "password": "testpassword"},
        )
        response = client.get("/api/v1/audit-logs", headers=auth_headers)
        logs = response.json()["audit_logs"]
        login_logs = [l for l in logs if l["action"] == "login"]
        assert len(login_logs) >= 1


class TestAdminApi:
    """管理者APIのテスト"""

    def test_purge_expired(self, client, admin_headers):
        response = client.post("/api/v1/admin/purge-expired", headers=admin_headers)
        assert response.status_code == 200
        assert "purged_count" in response.json()

    def test_purge_forbidden_for_teacher(self, client, auth_headers):
        response = client.post("/api/v1/admin/purge-expired", headers=auth_headers)
        assert response.status_code == 403

    def test_export_school(self, client, test_school, admin_headers, sample_session):
        storage.save_session(sample_session, school_id=test_school.id)
        response = client.get(
            f"/api/v1/admin/schools/{test_school.id}/export",
            headers=admin_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["school"]["id"] == test_school.id
        assert len(body["sessions"]) == 1

    def test_export_nonexistent_school(self, client, admin_headers):
        response = client.get(
            "/api/v1/admin/schools/nonexistent/export",
            headers=admin_headers,
        )
        assert response.status_code == 404

    def test_delete_school(self, client, admin_headers):
        school = School(name="削除学校", slug="delete-me")
        storage.create_school(school)
        response = client.delete(
            f"/api/v1/admin/schools/{school.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["school_deleted"] == 1
