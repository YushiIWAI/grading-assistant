"""widen mfa_secret column to Text for encrypted values

Revision ID: f6a7b8c9d012
Revises: e5f6a7b8c901
Create Date: 2026-03-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d012'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen mfa_secret from String(32) to Text.

    Fernet-encrypted MFA secrets are ~188 chars and do not fit in VARCHAR(32).
    """
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column(
            'mfa_secret',
            existing_type=sa.String(length=32),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    """Revert mfa_secret to String(32).

    暗号化済みの値（~188文字）が存在する場合、VARCHAR(32) に収まらず
    データ破損するため、事前にチェックして中止する。
    """
    conn = op.get_bind()
    long_secrets = conn.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE LENGTH(mfa_secret) > 32")
    ).scalar()
    if long_secrets:
        raise RuntimeError(
            f"downgrade 中止: {long_secrets} 件の mfa_secret が 32 文字を超えています。"
            " 暗号化済みデータを先に復号するか、該当行の mfa_secret を NULL にしてください。"
        )
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column(
            'mfa_secret',
            existing_type=sa.Text(),
            type_=sa.String(length=32),
            existing_nullable=True,
        )
