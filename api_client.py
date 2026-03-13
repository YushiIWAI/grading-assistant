"""Streamlit から grading-assistant API を利用するためのクライアント。"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import asdict
from typing import Any

import httpx

from models import Rubric, ScoringSession
from rubric_io import rubric_from_dict


class ApiClientError(RuntimeError):
    """API呼び出しの失敗を表す例外。"""


logger = logging.getLogger(__name__)


def _api_base_url() -> str:
    return os.getenv("GRADING_API_BASE_URL", "").strip()


def _api_timeout() -> float:
    raw = os.getenv("GRADING_API_TIMEOUT", "30")
    try:
        return float(raw)
    except ValueError:
        return 30.0


def _extract_error_message(response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text

    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
    return str(body)


def _request(method: str, path: str, json: dict[str, Any] | None = None):
    base_url = _api_base_url()
    timeout = _api_timeout()

    try:
        if base_url:
            with httpx.Client(base_url=base_url, timeout=timeout) as client:
                response = client.request(method, path, json=json)
        else:
            from fastapi.testclient import TestClient
            from api.app import app

            with TestClient(app) as client:
                response = client.request(method, path, json=json)
    except httpx.HTTPError as exc:
        logger.exception("HTTP API呼び出しに失敗しました: %s %s", method, path)
        raise ApiClientError(f"API呼び出しに失敗しました: {exc}") from exc
    except Exception as exc:
        logger.exception("ローカル API 呼び出しに失敗しました: %s %s", method, path)
        raise ApiClientError(f"API呼び出しに失敗しました: {exc}") from exc

    return response


def load_rubric_from_yaml(yaml_text: str) -> Rubric:
    response = _request("POST", "/api/v1/rubrics/parse", json={"yaml_text": yaml_text})
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return rubric_from_dict(response.json()["rubric"])


def rubric_to_yaml(rubric: Rubric) -> str:
    response = _request(
        "POST",
        "/api/v1/rubrics/render",
        json={"rubric": asdict(rubric)},
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    yaml_text = response.text
    if not yaml_text.strip():
        raise ApiClientError("ルーブリック YAML の生成結果が空です")
    return yaml_text


def list_sessions() -> list[dict[str, Any]]:
    response = _request("GET", "/api/v1/sessions")
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.json()["sessions"]


def load_session(session_id: str) -> ScoringSession | None:
    response = _request("GET", f"/api/v1/sessions/{session_id}")
    if response.status_code == 404:
        return None
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return ScoringSession.from_dict(response.json()["session"])


def create_session_record(
    rubric_title: str,
    pdf_filename: str,
    pages_per_student: int,
) -> ScoringSession:
    response = _request(
        "POST",
        "/api/v1/sessions",
        json={
            "rubric_title": rubric_title,
            "pdf_filename": pdf_filename,
            "pages_per_student": pages_per_student,
        },
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return ScoringSession.from_dict(response.json()["session"])


def save_session(session: ScoringSession) -> ScoringSession:
    response = _request(
        "PUT",
        f"/api/v1/sessions/{session.session_id}",
        json=session.to_dict(),
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    saved = ScoringSession.from_dict(response.json()["session"])
    session.__dict__.update(saved.__dict__)
    return session


def export_csv(session: ScoringSession) -> str:
    saved = save_session(session)
    response = _request(
        "GET",
        f"/api/v1/sessions/{saved.session_id}/exports/csv",
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.text


def refine_rubric(
    session_id: str,
    rubric: Rubric,
    provider_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """OCR結果を基に、採点基準の精緻化質問を生成する。"""
    response = _request(
        "POST",
        "/api/v1/rubrics/refine",
        json={
            "session_id": session_id,
            "rubric": asdict(rubric),
            "provider": provider_config,
        },
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.json()["questions"]


def run_ocr(
    session_id: str,
    rubric: Rubric,
    pdf_bytes: bytes,
    provider_config: dict[str, Any],
    enable_two_stage: bool = True,
) -> tuple[ScoringSession, list[str]]:
    response = _request(
        "POST",
        "/api/v1/runs/ocr",
        json={
            "session_id": session_id,
            "rubric": asdict(rubric),
            "pdf_base64": base64.b64encode(pdf_bytes).decode("utf-8"),
            "provider": provider_config,
            "enable_two_stage": enable_two_stage,
        },
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    body = response.json()
    return ScoringSession.from_dict(body["session"]), body["errors"]


def run_horizontal_grading(
    session: ScoringSession,
    rubric: Rubric,
    provider_config: dict[str, Any],
    batch_size: int,
    enable_verification: bool = False,
    student_ids_to_grade: list[str] | None = None,
) -> tuple[ScoringSession, list[str]]:
    save_session(session)
    response = _request(
        "POST",
        "/api/v1/runs/horizontal-grading",
        json={
            "session_id": session.session_id,
            "rubric": asdict(rubric),
            "provider": provider_config,
            "batch_size": batch_size,
            "enable_verification": enable_verification,
            "student_ids_to_grade": student_ids_to_grade,
        },
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    body = response.json()
    return ScoringSession.from_dict(body["session"]), body["errors"]
