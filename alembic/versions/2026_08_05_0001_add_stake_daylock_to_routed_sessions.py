"""Add stake_mor and daylock_mor to routed_sessions

Persists on-chain escrow at open and receipt-true (or pro-rata fallback)
daylock at close so ops can sum burn by model / lane. Premium gate uses
stake_mor on OPEN/CLOSING premium rows as in-flight holds.

Revision ID: add_stake_daylock
Revises: add_deleted_users
Create Date: 2026-08-05 00:01:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "add_stake_daylock"
down_revision: Union[str, None] = "add_deleted_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "routed_sessions",
        sa.Column("stake_mor", sa.Numeric(36, 18), nullable=True),
    )
    op.add_column(
        "routed_sessions",
        sa.Column("daylock_mor", sa.Numeric(36, 18), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("routed_sessions", "daylock_mor")
    op.drop_column("routed_sessions", "stake_mor")
