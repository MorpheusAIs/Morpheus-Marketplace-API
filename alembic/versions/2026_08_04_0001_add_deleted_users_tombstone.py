"""Add deleted_users tombstone table for auth revocation

Revision ID: add_deleted_users
Revises: add_provider_address
Create Date: 2026-08-04 00:01:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "add_deleted_users"
down_revision: Union[str, None] = "add_provider_address"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deleted_users",
        sa.Column("cognito_user_id", sa.String(), nullable=False),
        sa.Column("former_user_id", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("cognito_user_id"),
    )
    op.create_index(
        "ix_deleted_users_deleted_at",
        "deleted_users",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_deleted_users_deleted_at", table_name="deleted_users")
    op.drop_table("deleted_users")
