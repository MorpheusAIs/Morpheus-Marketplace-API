"""Add encrypted API key storage

Revision ID: add_encrypted_api_keys
Revises: add_is_default_api_keys
Create Date: 2025-01-22 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_encrypted_api_keys'
down_revision = 'add_is_default_api_keys'
branch_labels = None
depends_on = None


def upgrade():
    """Add encrypted_key and encryption_version columns to api_keys table."""
    # Add new columns for encrypted storage
    op.add_column('api_keys', sa.Column('encrypted_key', sa.Text(), nullable=True))
    op.add_column('api_keys', sa.Column('encryption_version', sa.Integer(), nullable=False, server_default=sa.text('1')))
    
    # Create index on encryption_version for future algorithm updates
    op.create_index(op.f('ix_api_keys_encryption_version'), 'api_keys', ['encryption_version'], unique=False)


def downgrade():
    """Remove encrypted API key columns."""
    op.drop_index(op.f('ix_api_keys_encryption_version'), table_name='api_keys')
    op.drop_column('api_keys', 'encryption_version')
    op.drop_column('api_keys', 'encrypted_key')