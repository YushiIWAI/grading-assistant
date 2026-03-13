"""データベースエンジンとテーブル定義"""

from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, ForeignKey, MetaData, Table, Column, String, Integer, Text, DateTime, Index
from sqlalchemy.sql import func

_DEFAULT_DB_PATH = Path(__file__).parent / "data" / "grading.db"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{_DEFAULT_DB_PATH}",
)

metadata = MetaData()

schools = Table(
    "schools",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", Text, nullable=False),
    Column("slug", String(100), nullable=False, unique=True),
    Column("retention_days", Integer, nullable=False, server_default="365"),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False, server_default=""),
)

users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("school_id", String(36), ForeignKey("schools.id"), nullable=False),
    Column("email", String(255), nullable=False, unique=True),
    Column("hashed_password", Text, nullable=False),
    Column("display_name", Text, nullable=False, server_default=""),
    Column("role", String(20), nullable=False, server_default="teacher"),
    Column("is_active", Boolean, nullable=False, server_default=sa.text("1")),
    Column("mfa_secret", String(32), nullable=True),
    Column("mfa_enabled", Boolean, nullable=False, server_default=sa.text("0")),
    Column("mfa_backup_codes", Text, nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False, server_default=""),
)

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
    Column("students_encrypted", Text, nullable=True),
    Column("ocr_results_encrypted", Text, nullable=True),
    Column("school_id", String(36), ForeignKey("schools.id"), nullable=True),
    Column("created_by", String(36), ForeignKey("users.id"), nullable=True),
)

api_keys = Table(
    "api_keys",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("school_id", String(36), ForeignKey("schools.id"), nullable=False),
    Column("provider", String(20), nullable=False),  # "gemini" | "anthropic"
    Column("encrypted_key", Text, nullable=False),
    Column("key_suffix", String(4), nullable=False, server_default=""),  # 末尾4文字（表示用）
    Column("created_by", String(36), ForeignKey("users.id"), nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False, server_default=""),
)

Index("ix_api_keys_school_provider", api_keys.c.school_id, api_keys.c.provider, unique=True)

audit_logs = Table(
    "audit_logs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("timestamp", Text, nullable=False),
    Column("user_id", String(36), nullable=True),
    Column("school_id", String(36), nullable=True),
    Column("action", String(50), nullable=False),
    Column("resource_type", String(50), nullable=False),
    Column("resource_id", String(100), nullable=True),
    Column("details", JSON, nullable=True),
    Column("ip_address", String(45), nullable=True),
    Column("integrity_hash", String(64), nullable=False),
    Column("prev_hash", String(64), nullable=False, server_default=""),
)

# 監査ログの検索用インデックス
Index("ix_audit_logs_timestamp", audit_logs.c.timestamp)
Index("ix_audit_logs_user_id", audit_logs.c.user_id)
Index("ix_audit_logs_school_id", audit_logs.c.school_id)
Index("ix_audit_logs_action", audit_logs.c.action)
Index("ix_audit_logs_resource", audit_logs.c.resource_type, audit_logs.c.resource_id)

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
