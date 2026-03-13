"""api/app.py のユニットテスト"""

import base64
from dataclasses import asdict

import fitz
from fastapi.testclient import TestClient
import pytest

from api.app import app
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

    def test_create_and_get_session(self, client):
        create_response = client.post(
            "/api/v1/sessions",
            json={
                "rubric_title": "APIテスト",
                "pdf_filename": "answers.pdf",
                "pages_per_student": 2,
            },
        )

        assert create_response.status_code == 201
        session = create_response.json()["session"]

        get_response = client.get(f"/api/v1/sessions/{session['session_id']}")
        assert get_response.status_code == 200
        assert get_response.json()["session"]["rubric_title"] == "APIテスト"

    def test_list_sessions(self, client, sample_session, test_db):
        storage.save_session(sample_session)

        response = client.get("/api/v1/sessions")

        assert response.status_code == 200
        sessions = response.json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == sample_session.session_id

    def test_put_session_and_export_csv(self, client, sample_session, test_db):
        storage.save_session(sample_session)
        payload = sample_session.to_dict()
        payload["rubric_title"] = "更新後タイトル"

        put_response = client.put(
            f"/api/v1/sessions/{sample_session.session_id}",
            json=payload,
        )
        assert put_response.status_code == 200
        assert put_response.json()["session"]["rubric_title"] == "更新後タイトル"

        csv_response = client.get(
            f"/api/v1/sessions/{sample_session.session_id}/exports/csv",
        )
        assert csv_response.status_code == 200
        assert "学生番号,氏名,状態" in csv_response.text

    def test_run_ocr(self, client, sample_rubric):
        create_response = client.post(
            "/api/v1/sessions",
            json={
                "rubric_title": sample_rubric.title,
                "pdf_filename": "answers.pdf",
                "pages_per_student": sample_rubric.pages_per_student,
            },
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
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["session"]["ocr_results"]) == 1
        assert body["session"]["ocr_results"][0]["status"] == "ocr_done"

    def test_run_horizontal_grading(self, client, sample_session, sample_rubric, test_db):
        storage.save_session(sample_session)

        response = client.post(
            "/api/v1/runs/horizontal-grading",
            json={
                "session_id": sample_session.session_id,
                "rubric": asdict(sample_rubric),
                "provider": {"provider": "demo", "privacy_mask": {"enabled": True}},
                "batch_size": 10,
                "enable_verification": False,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["scored"] == 2
        assert len(body["session"]["students"]) == 2

    def test_refine_rubric(self, client, sample_session, sample_rubric, test_db):
        storage.save_session(sample_session)

        response = client.post(
            "/api/v1/rubrics/refine",
            json={
                "session_id": sample_session.session_id,
                "rubric": asdict(sample_rubric),
                "provider": {"provider": "demo", "privacy_mask": {"enabled": True}},
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert "questions" in body
        # sample_session には記述式問題(id=2)のOCR結果がある
        assert len(body["questions"]) >= 1
        q = body["questions"][0]
        assert q["question_id"] == "2"
        assert "student_answer" in q
        assert "options" in q

    def test_refine_rubric_no_ocr(self, client, test_db, sample_rubric):
        # OCR結果なしのセッションで400エラー
        create_resp = client.post(
            "/api/v1/sessions",
            json={"rubric_title": "テスト", "pdf_filename": "test.pdf", "pages_per_student": 1},
        )
        session_id = create_resp.json()["session"]["session_id"]

        response = client.post(
            "/api/v1/rubrics/refine",
            json={
                "session_id": session_id,
                "rubric": asdict(sample_rubric),
                "provider": {"provider": "demo"},
            },
        )
        assert response.status_code == 400
