"""add audit_logs, encryption columns, retention_days

Revision ID: c3f9a2b7e801
Revises: b82ead1cd54a
Create Date: 2026-03-13 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f9a2b7e801'
down_revision: Union[str, Sequence[str], None] = 'b82ead1cd54a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # audit_logs テーブル
    op.create_table('audit_logs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('timestamp', sa.Text(), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.Column('school_id', sa.String(length=36), nullable=True),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('resource_type', sa.String(length=50), nullable=False),
        sa.Column('resource_id', sa.String(length=100), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('integrity_hash', sa.String(length=64), nullable=False),
        sa.Column('prev_hash', sa.String(length=64), server_default='', nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_logs_timestamp', 'audit_logs', ['timestamp'])
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'])
    op.create_index('ix_audit_logs_school_id', 'audit_logs', ['school_id'])
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'])
    op.create_index('ix_audit_logs_resource', 'audit_logs', ['resource_type', 'resource_id'])

    # scoring_sessions に暗号化カラム追加
    with op.batch_alter_table('scoring_sessions') as batch_op:
        batch_op.add_column(sa.Column('students_encrypted', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('ocr_results_encrypted', sa.Text(), nullable=True))

    # schools に retention_days 追加
    with op.batch_alter_table('schools') as batch_op:
        batch_op.add_column(sa.Column('retention_days', sa.Integer(), server_default='365', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('schools') as batch_op:
        batch_op.drop_column('retention_days')

    with op.batch_alter_table('scoring_sessions') as batch_op:
        batch_op.drop_column('ocr_results_encrypted')
        batch_op.drop_column('students_encrypted')

    op.drop_index('ix_audit_logs_resource', 'audit_logs')
    op.drop_index('ix_audit_logs_action', 'audit_logs')
    op.drop_index('ix_audit_logs_school_id', 'audit_logs')
    op.drop_index('ix_audit_logs_user_id', 'audit_logs')
    op.drop_index('ix_audit_logs_timestamp', 'audit_logs')
    op.drop_table('audit_logs')
