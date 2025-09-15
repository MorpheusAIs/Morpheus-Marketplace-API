"""add message_role enum

Revision ID: add_message_role_enum
Revises: add_chat_tables
Create Date: 2025-09-15 17:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_message_role_enum'
down_revision = 'add_chat_tables'
branch_labels = None
depends_on = None


def upgrade():
    # Create message_role enum if it doesn't exist
    op.execute("DO $$ BEGIN CREATE TYPE message_role AS ENUM ('user', 'assistant'); EXCEPTION WHEN duplicate_object THEN null; END $$;")


def downgrade():
    # Drop the enum type if no tables are using it
    op.execute("DROP TYPE IF EXISTS message_role")
