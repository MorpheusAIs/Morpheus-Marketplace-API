"""Add age verification fields to users table

Stores evidence of 18+ age verification consent:
- age_verified: boolean indicating the user confirmed they are 18+
- age_verified_at: timestamp of when the verification was submitted

Revision ID: e1f2a3b4c5d6
Revises: d7e8f9a0b1c2
Create Date: 2026-03-03 00:01:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1f2a3b4c5d6'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('age_verified', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('users', sa.Column('age_verified_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'age_verified_at')
    op.drop_column('users', 'age_verified')
