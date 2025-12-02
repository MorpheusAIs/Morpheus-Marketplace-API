"""make email nullable and non-unique for social login support

Revision ID: social_login_prep_2025
Revises: fix_enum_name_2025
Create Date: 2025-12-02 14:00:00.000000

This migration prepares the users table for social login and alternative authentication methods:
- Makes email column nullable (some auth methods don't provide email)
- Removes UNIQUE constraint on email (same email can exist across different identity providers)
- Keeps index on email for query performance
- Makes name column nullable for consistency
- cognito_user_id remains the ONLY unique identifier

Rationale:
- Social logins (Google, Facebook, GitHub) may not share email or use same email with different accounts
- Magic link/passwordless auth may not provide email initially
- Phone number authentication has no email
- Frontend displays user info from JWT token, not database
- Backend keyed entirely by cognito_user_id (sub claim from Cognito)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'social_login_prep_2025'
down_revision: Union[str, None] = 'fix_enum_name_2025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Prepare users table for social login and alternative authentication methods.
    
    Changes:
    1. Drop UNIQUE constraint on email (via index)
    2. Make email nullable
    3. Create non-unique index on email for performance
    4. Make name nullable for consistency
    """
    
    # Step 1: Drop the unique index on email (ix_users_email)
    # This removes the UNIQUE constraint
    op.drop_index('ix_users_email', table_name='users')
    
    # Step 2: Make email nullable
    # Users authenticated via social login or magic link may not have email
    op.alter_column('users', 'email',
                   existing_type=sa.String(),
                   nullable=True)
    
    # Step 3: Create a new non-unique index on email for query performance
    # This maintains fast lookups without enforcing uniqueness
    op.create_index('ix_users_email_nonunique', 'users', ['email'], unique=False)
    
    # Step 4: Make name nullable (it may already be, but ensure consistency)
    op.alter_column('users', 'name',
                   existing_type=sa.String(),
                   nullable=True)
    
    # Note: cognito_user_id remains UNIQUE and NOT NULL (the only unique identifier)


def downgrade() -> None:
    """
    Revert changes - restore unique constraint on email.
    
    WARNING: This downgrade will FAIL if duplicate emails exist in the database.
    You must clean up duplicate emails before running this downgrade.
    """
    
    # Step 1: Drop the non-unique index
    op.drop_index('ix_users_email_nonunique', table_name='users')
    
    # Step 2: Make email non-nullable
    # This will FAIL if any users have NULL email
    op.alter_column('users', 'email',
                   existing_type=sa.String(),
                   nullable=False)
    
    # Step 3: Recreate the unique index on email
    # This will FAIL if duplicate emails exist
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    
    # Note: name nullable state is preserved (already nullable in most cases)

