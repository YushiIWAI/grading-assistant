"""add token_invalidated_at column to users

Revision ID: g7b8c9d0e123
Revises: f6a7b8c9d012
Create Date: 2026-03-14 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g7b8c9d0e123'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add token_invalidated_at to users for token revocation."""
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(
            sa.Column('token_invalidated_at', sa.Text(), nullable=True)
        )


def downgrade() -> None:
    """Remove token_invalidated_at from users."""
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('token_invalidated_at')
