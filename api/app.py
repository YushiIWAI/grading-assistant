"""grading-assistant の API レイヤー。"""

from __future__ import annotations

import base64
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from models import ScoringSession
from pdf_processor import PrivacyMaskConfig, pdf_to_images, split_pages_by_student
from provider_factory import build_provider
from rubric_io import (
    load_rubric_from_yaml,
    rubric_from_dict,
    rubric_summary,
    rubric_to_yaml,
)
from scoring_engine import ocr_all_students, run_horizontal_grading
from storage import export_csv, list_sessions, load_session, save_session


class RubricParseRequest(BaseModel):
    yaml_text: str = Field(..., min_length=1)


class RubricRenderRequest(BaseModel):
    rubric: dict[str, Any]


class SessionCreateRequest(BaseModel):
    rubric_title: str = Field(..., min_length=1)
    pdf_filename: str = "uploaded.pdf"
    pages_per_student: int = Field(default=1, ge=1)


class PrivacyMaskRequest(BaseModel):
    enabled: bool = True
    strategy: str = "top_right"
    width_ratio: float = 0.36
    height_ratio: float = 0.14
    margin_x_ratio: float = 0.03
    margin_y_ratio: float = 0.02
    first_page_only: bool = True


class ProviderConfigRequest(BaseModel):
    provider: str = "demo"
    api_key: str = ""
    model_name: str = ""
    privacy_mask: PrivacyMaskRequest | None = None


class RubricRefineRequest(BaseModel):
    session_id: str
    rubric: dict[str, Any]
    provider: ProviderConfigRequest


class OcrRunRequest(BaseModel):
    session_id: str
    rubric: dict[str, Any]
    pdf_base64: str = Field(..., min_length=1)
    provider: ProviderConfigRequest
    enable_two_stage: bool = True


class HorizontalGradingRunRequest(BaseModel):
    session_id: str
    rubric: dict[str, Any]
    provider: ProviderConfigRequest
    batch_size: int = Field(default=15, ge=1, le=100)
    enable_verification: bool = False
    student_ids_to_grade: list[str] | None = None


app = FastAPI(
    title="grading-assistant API",
    version="0.1.0",
    description=(
        "Streamlit UI から採点ワークフローを段階的に切り離すための初期 API 層。"
        "フェーズ1では、ルーブリック変換とセッション永続化を担当する。"
    ),
)


def _build_provider_from_request(config: ProviderConfigRequest):
    privacy_mask = PrivacyMaskConfig(
        **(config.privacy_mask.model_dump() if config.privacy_mask else {})
    )
    try:
        return build_provider(
            provider_name=config.provider,
            api_key=config.api_key,
            model_name=config.model_name,
            privacy_mask=privacy_mask,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "grading-assistant-api",
        "storage_mode": "local-json-bridge",
    }


@app.post("/api/v1/rubrics/parse")
def parse_rubric(request: RubricParseRequest) -> dict[str, Any]:
    try:
        rubric = load_rubric_from_yaml(request.yaml_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "rubric": asdict(rubric),
        "summary": rubric_summary(rubric),
    }


@app.post("/api/v1/rubrics/render", response_class=PlainTextResponse)
def render_rubric(request: RubricRenderRequest) -> str:
    try:
        rubric = rubric_from_dict(request.rubric)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return rubric_to_yaml(rubric)


@app.post("/api/v1/rubrics/refine")
def refine_rubric(request: RubricRefineRequest) -> dict[str, Any]:
    """OCR結果を基に、採点基準の精緻化質問を生成する。"""
    session = load_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    if not session.ocr_results:
        raise HTTPException(status_code=400, detail="OCR結果がありません。先にOCRを実行してください。")

    try:
        rubric = rubric_from_dict(request.rubric)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # OCR結果を question_id → [(student_id, text)] に変換
    ocr_answers_by_question: dict[str, list[tuple[str, str]]] = {}
    for ocr in session.ocr_results:
        if ocr.status not in ("ocr_done", "reviewed"):
            continue
        for ans in ocr.answers:
            ocr_answers_by_question.setdefault(ans.question_id, []).append(
                (ocr.student_id, ans.transcribed_text)
            )

    provider = _build_provider_from_request(request.provider)
    result = provider.refine_rubric(rubric, ocr_answers_by_question)

    return {"questions": result.get("questions", [])}


@app.get("/api/v1/sessions")
def get_sessions() -> dict[str, list[dict[str, Any]]]:
    return {"sessions": list_sessions()}


@app.post("/api/v1/sessions", status_code=201)
def create_session(request: SessionCreateRequest) -> dict[str, Any]:
    session = ScoringSession(
        rubric_title=request.rubric_title,
        pdf_filename=request.pdf_filename,
        pages_per_student=request.pages_per_student,
    )
    save_session(session)
    return {"session": session.to_dict()}


@app.get("/api/v1/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    session = load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session": session.to_dict()}


@app.put("/api/v1/sessions/{session_id}")
def put_session(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    body_session_id = payload.get("session_id")
    if body_session_id and body_session_id != session_id:
        raise HTTPException(
            status_code=400,
            detail="path の session_id と payload の session_id が一致しません",
        )

    payload["session_id"] = session_id
    try:
        session = ScoringSession.from_dict(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    save_session(session)
    return {"session": session.to_dict()}


@app.get("/api/v1/sessions/{session_id}/exports/csv", response_class=PlainTextResponse)
def export_session_csv(session_id: str) -> str:
    session = load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return export_csv(session)


@app.post("/api/v1/runs/ocr")
def run_ocr(request: OcrRunRequest) -> dict[str, Any]:
    session = load_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    try:
        rubric = rubric_from_dict(request.rubric)
        pdf_bytes = base64.b64decode(request.pdf_base64)
        images = pdf_to_images(pdf_bytes)
        student_groups = split_pages_by_student(images, rubric.pages_per_student)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    provider = _build_provider_from_request(request.provider)

    ocr_results, errors = ocr_all_students(
        provider=provider,
        student_groups=student_groups,
        rubric=rubric,
        enable_two_stage=request.enable_two_stage,
    )

    session.rubric_title = rubric.title
    session.pages_per_student = rubric.pages_per_student
    session.ocr_results = ocr_results
    save_session(session)

    return {
        "session": session.to_dict(),
        "errors": errors,
        "student_count": len(student_groups),
    }


@app.post("/api/v1/runs/horizontal-grading")
def run_horizontal(request: HorizontalGradingRunRequest) -> dict[str, Any]:
    session = load_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    try:
        rubric = rubric_from_dict(request.rubric)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    provider = _build_provider_from_request(request.provider)
    errors = run_horizontal_grading(
        provider=provider,
        rubric=rubric,
        session=session,
        reference_students=session.get_reference_students() or None,
        batch_size=request.batch_size,
        student_ids_to_grade=request.student_ids_to_grade,
        enable_verification=request.enable_verification,
    )
    save_session(session)

    return {
        "session": session.to_dict(),
        "errors": errors,
        "summary": session.summary(),
    }
