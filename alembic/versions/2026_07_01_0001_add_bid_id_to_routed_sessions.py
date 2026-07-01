"""Add bid_id to routed_sessions

Per-bid attribution for session routing: records the on-chain bid the c-node
selected for a session (read back via getSessionStatus after open). Nullable /
best-effort - capture must never block a session open. Feeds per-bid RUM health
(see docs/active-models-rum-canary.md).

Revision ID: add_bid_id_routed_sess
Revises: add_rate_limit_mult
Create Date: 2026-07-01 00:01:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_bid_id_routed_sess'
down_revision: Union[str, None] = 'add_rate_limit_mult'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'routed_sessions',
        sa.Column('bid_id', sa.String(), nullable=True),
    )
    op.create_index(
        op.f('ix_routed_sessions_bid_id'),
        'routed_sessions',
        ['bid_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_routed_sessions_bid_id'), table_name='routed_sessions')
    op.drop_column('routed_sessions', 'bid_id')
