"""Add rate_limit_multiplier to users table

Per-user scaling factor for rate limits (RPM/TPM).
Default 1.0 means no change; 2.0 doubles the limits, 0.5 halves them.
Managed exclusively by admin endpoints — never exposed to end users.

Revision ID: add_rate_limit_mult
Revises: drop_email_name_2026
Create Date: 2026-03-10 00:01:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_rate_limit_mult'
down_revision: Union[str, None] = 'drop_email_name_2026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('rate_limit_multiplier', sa.Float(), nullable=False, server_default='1.0'),
    )


def downgrade() -> None:
    op.drop_column('users', 'rate_limit_multiplier')
