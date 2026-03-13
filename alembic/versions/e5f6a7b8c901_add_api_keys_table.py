"""add api_keys table

Revision ID: e5f6a7b8c901
Revises: d4e5f6a7b890
Create Date: 2026-03-13 25:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c901'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create api_keys table."""
    op.create_table('api_keys',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('school_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('encrypted_key', sa.Text(), nullable=False),
        sa.Column('key_suffix', sa.String(length=4), server_default='', nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), server_default='', nullable=False),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_api_keys_school_provider', 'api_keys', ['school_id', 'provider'], unique=True)


def downgrade() -> None:
    """Drop api_keys table."""
    op.drop_index('ix_api_keys_school_provider', 'api_keys')
    op.drop_table('api_keys')
