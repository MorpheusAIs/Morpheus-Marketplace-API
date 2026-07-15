"""Add provider_address to routed_sessions

Stores the on-chain provider address serving each session so that failover
can exclude the failed provider (proxy-router omitProvider) when opening
the retry session instead of potentially landing on the same impaired one.

Revision ID: add_provider_address
Revises: add_rate_limit_mult
Create Date: 2026-07-14 00:01:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_provider_address'
down_revision: Union[str, None] = 'add_rate_limit_mult'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'routed_sessions',
        sa.Column('provider_address', sa.String(length=42), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('routed_sessions', 'provider_address')
