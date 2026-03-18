"""add audit_chain_pointer table for chain serialization

Revision ID: j0e1f2345678
Revises: i9d0e1f23456
Create Date: 2026-03-14 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'j0e1f2345678'
down_revision: Union[str, Sequence[str], None] = 'i9d0e1f23456'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create audit_chain_pointer table and seed from existing audit_logs."""
    op.create_table(
        'audit_chain_pointer',
        sa.Column('id', sa.Integer(), primary_key=True, server_default='1'),
        sa.Column('latest_hash', sa.String(length=64), nullable=False, server_default=''),
    )
    # Seed with latest hash from existing audit_logs
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT integrity_hash FROM audit_logs ORDER BY timestamp DESC LIMIT 1")
    ).fetchone()
    latest = row[0] if row else ""
    conn.execute(
        sa.text("INSERT INTO audit_chain_pointer (id, latest_hash) VALUES (1, :h)"),
        {"h": latest},
    )


def downgrade() -> None:
    """Drop audit_chain_pointer table."""
    op.drop_table('audit_chain_pointer')
