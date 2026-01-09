"""Add wallet_links and wallet_nonces tables for Web3 wallet integration

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2025-01-09 12:00:00.000000

This migration adds support for linking Web3 wallets to user accounts.
Key features:
- Multiple wallets per user (user_id NOT unique)
- One user per wallet (wallet_address IS unique globally)
- Database-based nonce management for signature verification
- Cascade delete when user is deleted
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6g7h8'
down_revision: str = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create wallet_links table
    op.create_table(
        'wallet_links',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('wallet_address', sa.String(length=42), nullable=False),
        sa.Column('staked_amount', sa.Numeric(78, 0), nullable=False, server_default='0'),
        sa.Column('linked_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        
        # Primary key
        sa.PrimaryKeyConstraint('id', name='pk_wallet_links'),
        
        # Foreign key to users table with cascade delete
        sa.ForeignKeyConstraint(
            ['user_id'], 
            ['users.id'],
            name='fk_wallet_links_user_id',
            ondelete='CASCADE'
        ),
        
        # CRITICAL: Wallet address must be unique across ALL users
        # This prevents the same wallet from being linked to multiple accounts
        sa.UniqueConstraint('wallet_address', name='uq_wallet_links_wallet_address'),
    )
    
    # Create indexes for wallet_links
    op.create_index('ix_wallet_links_id', 'wallet_links', ['id'], unique=False)
    op.create_index('ix_wallet_links_user_id', 'wallet_links', ['user_id'], unique=False)
    op.create_index('ix_wallet_links_wallet_address', 'wallet_links', ['wallet_address'], unique=True)
    op.create_index('ix_wallet_links_user_wallet', 'wallet_links', ['user_id', 'wallet_address'], unique=False)
    
    # Create wallet_nonces table for signature verification
    op.create_table(
        'wallet_nonces',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('nonce', sa.String(length=64), nullable=False),
        sa.Column('wallet_address', sa.String(length=42), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('consumed', sa.DateTime(), nullable=True),
        
        # Primary key
        sa.PrimaryKeyConstraint('id', name='pk_wallet_nonces'),
        
        # Foreign key to users table with cascade delete
        sa.ForeignKeyConstraint(
            ['user_id'], 
            ['users.id'],
            name='fk_wallet_nonces_user_id',
            ondelete='CASCADE'
        ),
        
        # Nonce must be unique
        sa.UniqueConstraint('nonce', name='uq_wallet_nonces_nonce'),
    )
    
    # Create indexes for wallet_nonces
    op.create_index('ix_wallet_nonces_id', 'wallet_nonces', ['id'], unique=False)
    op.create_index('ix_wallet_nonces_user_id', 'wallet_nonces', ['user_id'], unique=False)
    op.create_index('ix_wallet_nonces_nonce', 'wallet_nonces', ['nonce'], unique=True)
    op.create_index('ix_wallet_nonces_expires', 'wallet_nonces', ['expires_at'], unique=False)
    op.create_index('ix_wallet_nonces_user_consumed', 'wallet_nonces', ['user_id', 'consumed'], unique=False)


def downgrade() -> None:
    # Drop wallet_nonces indexes
    op.drop_index('ix_wallet_nonces_user_consumed', table_name='wallet_nonces')
    op.drop_index('ix_wallet_nonces_expires', table_name='wallet_nonces')
    op.drop_index('ix_wallet_nonces_nonce', table_name='wallet_nonces')
    op.drop_index('ix_wallet_nonces_user_id', table_name='wallet_nonces')
    op.drop_index('ix_wallet_nonces_id', table_name='wallet_nonces')
    
    # Drop wallet_nonces table
    op.drop_table('wallet_nonces')
    
    # Drop wallet_links indexes
    op.drop_index('ix_wallet_links_user_wallet', table_name='wallet_links')
    op.drop_index('ix_wallet_links_wallet_address', table_name='wallet_links')
    op.drop_index('ix_wallet_links_user_id', table_name='wallet_links')
    op.drop_index('ix_wallet_links_id', table_name='wallet_links')
    
    # Drop wallet_links table
    op.drop_table('wallet_links')
