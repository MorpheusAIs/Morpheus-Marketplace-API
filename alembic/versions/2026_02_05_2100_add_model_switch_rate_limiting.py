"""add model switch rate limiting

Revision ID: 2026_02_05_2100
Revises: 2025_12_02_1400_make_email_nullable_and_non_unique
Create Date: 2026-02-05 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2026_02_05_2100'
down_revision: Union[str, None] = '2025_12_02_1400_make_email_nullable_and_non_unique'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create model switch tracking table
    op.create_table('api_key_model_switches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('api_key_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('from_model', sa.String(), nullable=True),
        sa.Column('to_model', sa.String(), nullable=False),
        sa.Column('switched_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for efficient rate limit queries
    op.create_index('ix_api_key_switches_lookup', 'api_key_model_switches', 
                    ['api_key_id', 'switched_at'], unique=False)
    op.create_index('ix_user_switches_lookup', 'api_key_model_switches', 
                    ['user_id', 'switched_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_user_switches_lookup', table_name='api_key_model_switches')
    op.drop_index('ix_api_key_switches_lookup', table_name='api_key_model_switches')
    op.drop_table('api_key_model_switches')
