"""Add is_staker and allow_overage flags to credit_account_balances table.

- is_staker: cached flag set instantly when a wallet with stake > 0 is linked.
- allow_overage: user-managed toggle (only relevant for stakers). When enabled,
  the system automatically deducts from the paid Credit Balance after the Daily
  Staking Allowance is exhausted, preventing service interruption.

Revision ID: b2c3d4e5f6a7
Revises: c3d4e5f6g7h8
Create Date: 2026-02-06 00:01:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'c3d4e5f6g7h8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'credit_account_balances',
        sa.Column('is_staker', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.add_column(
        'credit_account_balances',
        sa.Column('allow_overage', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade() -> None:
    op.drop_column('credit_account_balances', 'allow_overage')
    op.drop_column('credit_account_balances', 'is_staker')
