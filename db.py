"""データベースエンジンとテーブル定義"""

from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import JSON, MetaData, Table, Column, String, Integer, Text, DateTime
from sqlalchemy.sql import func

_DEFAULT_DB_PATH = Path(__file__).parent / "data" / "grading.db"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{_DEFAULT_DB_PATH}",
)

metadata = MetaData()

scoring_sessions = Table(
    "scoring_sessions",
    metadata,
    Column("session_id", String(8), primary_key=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False, server_default=""),
    Column("rubric_title", Text, nullable=False, server_default=""),
    Column("pdf_filename", Text, nullable=False, server_default=""),
    Column("pages_per_student", Integer, nullable=False, server_default="1"),
    Column("grading_mode", Text, nullable=False, server_default="legacy"),
    Column("students", JSON, nullable=False, server_default="[]"),
    Column("ocr_results", JSON, nullable=False, server_default="[]"),
)

_engine: sa.Engine | None = None


def get_engine(url: str | None = None) -> sa.Engine:
    """エンジンのシングルトンを返す。url を指定するとそちらを使用。"""
    global _engine
    if _engine is None or url is not None:
        target_url = url or DATABASE_URL
        # SQLite の場合は json_serializer 不要（SQLAlchemy が自動処理）
        _engine = sa.create_engine(target_url)
    return _engine


def reset_engine() -> None:
    """エンジンをリセットする（テスト用）"""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def init_db(engine: sa.Engine | None = None) -> None:
    """テーブルを作成する（存在しなければ）"""
    eng = engine or get_engine()
    metadata.create_all(eng)
