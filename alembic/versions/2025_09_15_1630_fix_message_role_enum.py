"""fix message role enum name

Revision ID: fix_message_role_enum
Revises: add_chat_tables
Create Date: 2025-09-15 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'fix_message_role_enum'
down_revision = 'add_chat_tables'
branch_labels = None
depends_on = None


def upgrade():
    # Check if message_role enum exists and rename it to messagerole
    # This fixes the mismatch between migration and model
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'message_role') THEN
                ALTER TYPE message_role RENAME TO messagerole;
            END IF;
        END $$;
    """)


def downgrade():
    # Rename back to the original name
    op.execute("ALTER TYPE messagerole RENAME TO message_role")
