"""add chat tables

Revision ID: add_chat_tables
Revises: 6f8a4e1b9d43
Create Date: 2025-01-22 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_chat_tables'
down_revision = '6f8a4e1b9d43'
branch_labels = None
depends_on = None


def upgrade():
    # Create chat table
    op.create_table('chats',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('is_archived', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chats_user_id_updated_at', 'chats', ['user_id', 'updated_at'])

    # Create messages table (enum will be created automatically)
    op.create_table('messages',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('chat_id', sa.String(), nullable=False),
        sa.Column('role', postgresql.ENUM('user', 'assistant', name='messagerole'), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('tokens', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_messages_chat_id_sequence', 'messages', ['chat_id', 'sequence'])


def downgrade():
    # Drop tables and indexes (enum will be dropped automatically)
    op.drop_index('ix_messages_chat_id_sequence', table_name='messages')
    op.drop_table('messages')
    
    op.drop_index('ix_chats_user_id_updated_at', table_name='chats')
    op.drop_table('chats')
