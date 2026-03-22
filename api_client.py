"""Streamlit から grading-assistant API を利用するためのクライアント。"""

from __future__ import annotations

import base64
import contextvars
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

# --- 認証トークン管理 ---
# contextvars を使い、Streamlit の複数ユーザーセッション間でトークンが混線しないようにする。
_auth_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_auth_token_var", default=None
)


def set_auth_token(token: str | None) -> None:
    """APIリクエストに付与するアクセストークンを設定する。"""
    _auth_token_var.set(token)


def get_auth_token() -> str | None:
    """現在設定されているアクセストークンを返す。"""
    return _auth_token_var.get()


# --- Auth API ---


def login(email: str, password: str) -> dict[str, Any]:
    """メール+パスワードでログインし、トークン情報を返す。"""
    response = _request(
        "POST",
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """リフレッシュトークンから新しいアクセストークンを取得する。"""
    response = _request(
        "POST",
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.json()


def mfa_verify(mfa_token: str, code: str) -> dict[str, Any]:
    """MFA検証（ログイン第2段階）。TOTPコードまたはバックアップコードで認証を完了する。"""
    response = _request(
        "POST",
        "/api/v1/auth/mfa/verify",
        json={"mfa_token": mfa_token, "code": code},
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.json()


def mfa_setup() -> dict[str, Any]:
    """MFAセットアップ: シークレット生成 + QRコード用URI返却。"""
    response = _request("POST", "/api/v1/auth/mfa/setup")
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.json()


def mfa_enable(code: str) -> dict[str, Any]:
    """MFA有効化: 最初のTOTPコードを検証してMFAを有効化する。"""
    response = _request(
        "POST",
        "/api/v1/auth/mfa/enable",
        json={"code": code},
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.json()


def mfa_disable(password: str) -> dict[str, Any]:
    """MFA無効化: パスワード再確認の上、MFAを無効化する。"""
    response = _request(
        "POST",
        "/api/v1/auth/mfa/disable",
        json={"password": password},
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.json()


def change_password(current_password: str, new_password: str) -> dict[str, Any]:
    """パスワード変更。変更後は全トークンが無効化される。"""
    response = _request(
        "POST",
        "/api/v1/auth/change-password",
        json={"current_password": current_password, "new_password": new_password},
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.json()


def get_me() -> dict[str, Any]:
    """現在の認証ユーザー情報を取得する。"""
    response = _request("GET", "/api/v1/auth/me")
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    return response.json()


# --- Internal ---


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
    headers: dict[str, str] = {}
    token = _auth_token_var.get()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        if base_url:
            with httpx.Client(base_url=base_url, timeout=timeout) as client:
                response = client.request(method, path, json=json, headers=headers)
        else:
            from fastapi.testclient import TestClient
            from api.app import app

            with TestClient(app) as client:
                response = client.request(method, path, json=json, headers=headers)
    except httpx.HTTPError as exc:
        logger.exception("HTTP API呼び出しに失敗しました: %s %s", method, path)
        raise ApiClientError(f"API呼び出しに失敗しました: {exc}") from exc
    except Exception as exc:
        logger.exception("ローカル API 呼び出しに失敗しました: %s %s", method, path)
        raise ApiClientError(f"API呼び出しに失敗しました: {exc}") from exc

    return response


# --- Rubric API ---


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


# --- Session API ---


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
    # サーバー側で付与される updated_at のみ同期する。
    saved_data = response.json().get("session", {})
    if "updated_at" in saved_data:
        session.updated_at = saved_data["updated_at"]
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


# --- Run API ---


def run_ocr(
    session_id: str,
    rubric: Rubric,
    pdf_bytes: bytes,
    provider_config: dict[str, Any],
    enable_two_stage: bool = True,
    submission_type: str = "handwritten",
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
            "submission_type": submission_type,
        },
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    body = response.json()
    return ScoringSession.from_dict(body["session"]), body["errors"]


def import_csv(
    session_id: str,
    rubric: Rubric,
    csv_content: str,
    column_mapping: dict[str, Any],
) -> tuple[ScoringSession, list[str]]:
    response = _request(
        "POST",
        "/api/v1/runs/import-csv",
        json={
            "session_id": session_id,
            "rubric": asdict(rubric),
            "csv_content": csv_content,
            "column_mapping": column_mapping,
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
    is_typed: bool = False,
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
            "is_typed": is_typed,
        },
    )
    if response.is_error:
        raise ApiClientError(_extract_error_message(response))
    body = response.json()
    return ScoringSession.from_dict(body["session"]), body["errors"]
