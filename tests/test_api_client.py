"""api_client.py のユニットテスト"""

import fitz
import httpx
import pytest

import api_client
import storage


def _build_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "API client OCR test")
    return doc.tobytes()


def test_rubric_round_trip_via_local_api(sample_rubric, monkeypatch):
    monkeypatch.delenv("GRADING_API_BASE_URL", raising=False)

    yaml_text = api_client.rubric_to_yaml(sample_rubric)
    loaded = api_client.load_rubric_from_yaml(yaml_text)

    assert loaded.title == sample_rubric.title
    assert len(loaded.questions) == len(sample_rubric.questions)


def test_session_round_trip_via_local_api(sample_session, test_db, monkeypatch):
    monkeypatch.delenv("GRADING_API_BASE_URL", raising=False)

    created = api_client.create_session_record("API Client Test", "answers.pdf", 1)
    assert created.rubric_title == "API Client Test"

    sample_session.session_id = created.session_id
    saved = api_client.save_session(sample_session)
    assert saved.updated_at

    loaded = api_client.load_session(sample_session.session_id)
    assert loaded is not None
    assert loaded.session_id == sample_session.session_id

    sessions = api_client.list_sessions()
    assert len(sessions) == 1

    csv_text = api_client.export_csv(sample_session)
    assert "学生番号,氏名,状態" in csv_text


def test_run_ocr_and_horizontal_grading_via_local_api(sample_rubric, test_db, monkeypatch):
    monkeypatch.delenv("GRADING_API_BASE_URL", raising=False)

    session = api_client.create_session_record(
        sample_rubric.title,
        "answers.pdf",
        sample_rubric.pages_per_student,
    )

    session, ocr_errors = api_client.run_ocr(
        session_id=session.session_id,
        rubric=sample_rubric,
        pdf_bytes=_build_pdf_bytes(),
        provider_config={"provider": "demo", "privacy_mask": {"enabled": True}},
        enable_two_stage=True,
    )
    assert not ocr_errors
    assert len(session.ocr_results) == 1

    session, grading_errors = api_client.run_horizontal_grading(
        session=session,
        rubric=sample_rubric,
        provider_config={"provider": "demo", "privacy_mask": {"enabled": True}},
        batch_size=10,
        enable_verification=False,
    )
    assert not grading_errors
    assert len(session.students) == 1
    assert session.students[0].status == "ai_scored"


def test_refine_rubric_via_local_api(sample_session, sample_rubric, test_db, monkeypatch):
    monkeypatch.delenv("GRADING_API_BASE_URL", raising=False)

    # sample_session にはOCR結果がある
    api_client.save_session(sample_session)

    questions = api_client.refine_rubric(
        session_id=sample_session.session_id,
        rubric=sample_rubric,
        provider_config={"provider": "demo", "privacy_mask": {"enabled": True}},
    )

    assert isinstance(questions, list)
    assert len(questions) >= 1
    assert questions[0]["question_id"] == "2"
    assert "student_answer" in questions[0]


def test_request_wraps_httpx_errors(monkeypatch, caplog):
    monkeypatch.setenv("GRADING_API_BASE_URL", "https://example.test")

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, path, json=None):
            request = httpx.Request(method, f"https://example.test{path}")
            raise httpx.ConnectError("network down", request=request)

    monkeypatch.setattr(api_client.httpx, "Client", FailingClient)

    with pytest.raises(api_client.ApiClientError, match="network down"):
        api_client.list_sessions()

    assert "HTTP API呼び出しに失敗しました" in caplog.text


def test_rubric_to_yaml_rejects_empty_response(sample_rubric, monkeypatch):
    class EmptyResponse:
        is_error = False
        text = "   "

    monkeypatch.setattr(api_client, "_request", lambda *args, **kwargs: EmptyResponse())

    with pytest.raises(api_client.ApiClientError, match="生成結果が空"):
        api_client.rubric_to_yaml(sample_rubric)
