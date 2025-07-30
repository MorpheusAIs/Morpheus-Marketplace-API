"""replace local auth with cognito

Revision ID: 6f8a4e1b9d43
Revises: 5f7a3e1b8d42
Create Date: 2025-01-20 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f8a4e1b9d43'
down_revision: Union[str, None] = '5f7a3e1b8d42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add cognito_user_id column
    op.add_column('users', sa.Column('cognito_user_id', sa.String(), nullable=True))
    
    # Make email non-nullable
    op.alter_column('users', 'email', nullable=False)
    
    # Create unique index on cognito_user_id
    op.create_index('ix_users_cognito_user_id', 'users', ['cognito_user_id'], unique=True)
    
    # Remove hashed_password column (after making cognito_user_id non-nullable)
    # First, we need to make cognito_user_id non-nullable after data migration if any
    # For fresh install, we can make it non-nullable immediately
    op.alter_column('users', 'cognito_user_id', nullable=False)
    
    # Drop hashed_password column as it's no longer needed
    op.drop_column('users', 'hashed_password')


def downgrade() -> None:
    # Add back hashed_password column
    op.add_column('users', sa.Column('hashed_password', sa.String(), nullable=True))
    
    # Drop cognito_user_id index and column
    op.drop_index('ix_users_cognito_user_id', table_name='users')
    op.drop_column('users', 'cognito_user_id')
    
    # Make email nullable again
    op.alter_column('users', 'email', nullable=True) 