"""Drop email and name columns from users table

PII (email, name) is managed exclusively in Cognito.  The API resolves email
on-demand via the user's access token when needed (e.g. GET /me).  The database
only stores cognito_user_id as the identity key.

Revision ID: drop_email_name_2026
Revises: e1f2a3b4c5d6
Create Date: 2026-03-05 00:01:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'drop_email_name_2026'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_users_email_nonunique', table_name='users')
    op.drop_column('users', 'email')
    op.drop_column('users', 'name')


def downgrade() -> None:
    op.add_column('users', sa.Column('name', sa.String(), nullable=True))
    op.add_column('users', sa.Column('email', sa.String(), nullable=True))
    op.create_index('ix_users_email_nonunique', 'users', ['email'], unique=False)
