"""add hash_version to audit_logs and re-sign chain with v2

Revision ID: h8c9d0e1f234
Revises: g7b8c9d0e123
Create Date: 2026-03-14 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import hashlib
import hmac
import json
import os


# revision identifiers, used by Alembic.
revision: str = 'h8c9d0e1f234'
down_revision: Union[str, Sequence[str], None] = 'g7b8c9d0e123'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _compute_audit_hash_v2(
    log_id: str,
    timestamp: str,
    action: str,
    resource_type: str,
    resource_id: str | None,
    school_id: str | None,
    prev_hash: str,
    hmac_key: bytes,
) -> str:
    """v2 ハッシュ: PII を署名対象から除外。"""
    message = (
        f"{log_id}|{timestamp}|{action}|{resource_type}|{resource_id or ''}"
        f"|{school_id or ''}|{prev_hash}"
    )
    return hmac.new(hmac_key, message.encode(), hashlib.sha256).hexdigest()


def upgrade() -> None:
    """hash_version カラムを追加し、既存チェーン全体を v2 で再署名する。"""
    with op.batch_alter_table('audit_logs') as batch_op:
        batch_op.add_column(
            sa.Column('hash_version', sa.Integer(), nullable=False, server_default='2')
        )

    # 既存ログを v2 で再署名（チェーン全体を再構築）
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, timestamp, action, resource_type, resource_id, school_id FROM audit_logs ORDER BY timestamp ASC")
    ).fetchall()

    if not rows:
        return

    # config._get_audit_hmac_key() と同じ解決ロジック:
    # AUDIT_HMAC_KEY → JWT_SECRET_KEY → _JWT_SECRET_DEFAULT
    _audit_key = os.environ.get("AUDIT_HMAC_KEY", "")
    if _audit_key:
        hmac_key = _audit_key.encode()
    else:
        hmac_key = os.environ.get("JWT_SECRET_KEY", "dev-secret-do-not-use-in-production").encode()
    prev_hash = ""

    for row in rows:
        log_id, timestamp, action, resource_type, resource_id, school_id = row
        new_hash = _compute_audit_hash_v2(
            log_id, timestamp, action, resource_type, resource_id, school_id,
            prev_hash, hmac_key,
        )
        conn.execute(
            sa.text(
                "UPDATE audit_logs SET integrity_hash = :hash, prev_hash = :prev, hash_version = 2 WHERE id = :id"
            ),
            {"hash": new_hash, "prev": prev_hash, "id": log_id},
        )
        prev_hash = new_hash


def downgrade() -> None:
    """hash_version カラムを削除する。

    注意: ダウングレード後はハッシュが v2 形式のまま残るため、
    v1 の verify_audit_chain では検証不能になる。
    """
    with op.batch_alter_table('audit_logs') as batch_op:
        batch_op.drop_column('hash_version')
