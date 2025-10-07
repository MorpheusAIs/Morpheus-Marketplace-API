"""Add is_default column to api_keys table

Revision ID: add_is_default_api_keys
Revises: add_message_role_enum
Create Date: 2025-01-22 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_is_default_api_keys'
down_revision = 'add_message_role_enum'
branch_labels = None
depends_on = None


def upgrade():
    """Add is_default column to api_keys table."""
    # Add the is_default column
    op.add_column('api_keys', sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'))
    
    # Create index for better query performance
    op.create_index('ix_api_keys_is_default', 'api_keys', ['is_default'])


def downgrade():
    """Remove is_default column from api_keys table."""
    # Drop the index
    op.drop_index('ix_api_keys_is_default', table_name='api_keys')
    
    # Drop the column
    op.drop_column('api_keys', 'is_default')
