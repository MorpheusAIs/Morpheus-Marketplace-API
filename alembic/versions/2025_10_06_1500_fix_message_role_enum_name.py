"""fix_message_role_enum_name

Revision ID: fix_enum_name_2025
Revises: add_encrypted_api_keys
Create Date: 2025-10-06 15:00:00.000000

This migration fixes the enum type name mismatch where the database has 'messagerole'
but the code expects 'message_role' (with underscore).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fix_enum_name_2025'
down_revision: Union[str, None] = 'add_encrypted_api_keys'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Fix the message role enum name from 'messagerole' to 'message_role'.
    
    Strategy:
    1. Check if 'messagerole' type exists (the problematic one)
    2. If it does, rename it to 'message_role'
    3. If 'message_role' already exists correctly, do nothing
    """
    
    # Use raw SQL to check and fix the enum type name
    op.execute("""
        DO $$
        BEGIN
            -- Check if the wrong enum name exists
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'messagerole') THEN
                -- Check if the correct name doesn't already exist
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'message_role') THEN
                    -- Rename the enum type
                    ALTER TYPE messagerole RENAME TO message_role;
                    RAISE NOTICE 'Renamed enum type from messagerole to message_role';
                ELSE
                    -- Both exist - need to migrate the column to use the correct one
                    -- First, alter the column to use the correct enum
                    ALTER TABLE messages ALTER COLUMN role TYPE message_role USING role::text::message_role;
                    -- Drop the old enum type
                    DROP TYPE messagerole;
                    RAISE NOTICE 'Migrated column to message_role and dropped messagerole';
                END IF;
            ELSIF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'message_role') THEN
                -- Neither exists - create the correct one
                CREATE TYPE message_role AS ENUM ('user', 'assistant');
                RAISE NOTICE 'Created message_role enum type';
            ELSE
                -- Correct enum already exists
                RAISE NOTICE 'Enum type message_role already exists correctly';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    """
    Downgrade is not straightforward for enum renames.
    We'll leave the correct name in place.
    """
    # Don't rename back as it would break things
    # The correct name is 'message_role' with underscore
    pass

