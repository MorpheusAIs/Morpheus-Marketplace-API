"""Add routed_sessions table for Session Routing Service

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2025-12-30 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6g7'
down_revision: str = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create routed_sessions table
    # Note: Using String instead of Enum to avoid PostgreSQL enum type complications.
    # The application layer validates state values via the SessionState Python enum.
    op.create_table(
        'routed_sessions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('model_name', sa.String(), nullable=True),
        sa.Column('model_id', sa.String(), nullable=False),
        sa.Column('state', sa.String(20), nullable=False, server_default='OPEN'),
        sa.Column('active_requests', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('endpoint', sa.String(), nullable=True),
        sa.Column('error_reason', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for efficient queries
    op.create_index('idx_routed_sessions_model_name', 'routed_sessions', ['model_name'], unique=False)
    op.create_index('idx_routed_sessions_model_id', 'routed_sessions', ['model_id'], unique=False)
    op.create_index('idx_routed_sessions_state', 'routed_sessions', ['state'], unique=False)
    op.create_index('idx_routed_sessions_model_state', 'routed_sessions', ['model_id', 'state'], unique=False)
    op.create_index('idx_routed_sessions_state_active', 'routed_sessions', ['state', 'active_requests'], unique=False)
    op.create_index('idx_routed_sessions_expires', 'routed_sessions', ['expires_at'], unique=False)
    op.create_index('idx_routed_sessions_last_used', 'routed_sessions', ['last_used_at'], unique=False)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_routed_sessions_last_used', table_name='routed_sessions')
    op.drop_index('idx_routed_sessions_expires', table_name='routed_sessions')
    op.drop_index('idx_routed_sessions_state_active', table_name='routed_sessions')
    op.drop_index('idx_routed_sessions_model_state', table_name='routed_sessions')
    op.drop_index('idx_routed_sessions_state', table_name='routed_sessions')
    op.drop_index('idx_routed_sessions_model_id', table_name='routed_sessions')
    op.drop_index('idx_routed_sessions_model_name', table_name='routed_sessions')
    
    # Drop table
    op.drop_table('routed_sessions')

