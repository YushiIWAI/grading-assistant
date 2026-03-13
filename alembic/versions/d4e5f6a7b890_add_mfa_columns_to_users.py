"""add MFA columns to users

Revision ID: d4e5f6a7b890
Revises: c3f9a2b7e801
Create Date: 2026-03-13 24:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b890'
down_revision: Union[str, Sequence[str], None] = 'c3f9a2b7e801'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add MFA columns to users table."""
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('mfa_secret', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('mfa_enabled', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        batch_op.add_column(sa.Column('mfa_backup_codes', sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove MFA columns from users table."""
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('mfa_backup_codes')
        batch_op.drop_column('mfa_enabled')
        batch_op.drop_column('mfa_secret')
