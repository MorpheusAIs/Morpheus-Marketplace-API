"""Add credits billing tables

Revision ID: add_credits_billing
Revises: social_login_prep_2025
Create Date: 2025-12-23 00:01:00.000000

This migration adds the credits_ledger and credit_account_balances tables
for the B1 ledger split billing system.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'add_credits_billing'
down_revision: Union[str, None] = 'social_login_prep_2025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types for credits ledger
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ledger_status') THEN
                CREATE TYPE ledger_status AS ENUM ('pending', 'posted', 'voided');
            END IF;
        END $$;
    """)
    
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ledger_entry_type') THEN
                CREATE TYPE ledger_entry_type AS ENUM (
                    'purchase', 
                    'staking_refresh', 
                    'usage_hold', 
                    'usage_charge', 
                    'refund', 
                    'adjustment'
                );
            END IF;
        END $$;
    """)

    # Create credits_ledger table
    op.create_table('credits_ledger',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='USD'),
        sa.Column('status', postgresql.ENUM('pending', 'posted', 'voided', name='ledger_status', create_type=False), nullable=False),
        sa.Column('entry_type', postgresql.ENUM('purchase', 'staking_refresh', 'usage_hold', 'usage_charge', 'refund', 'adjustment', name='ledger_entry_type', create_type=False), nullable=False),
        sa.Column('amount_paid', sa.Numeric(precision=20, scale=8), nullable=False, server_default='0'),
        sa.Column('amount_staking', sa.Numeric(precision=20, scale=8), nullable=False, server_default='0'),
        sa.Column('idempotency_key', sa.Text(), nullable=True),
        sa.Column('related_entry_id', postgresql.UUID(as_uuid=True), nullable=True),
        # Usage metadata
        sa.Column('request_id', sa.Text(), nullable=True),
        sa.Column('api_key_id', sa.Integer(), nullable=True),
        sa.Column('model_name', sa.Text(), nullable=True),
        sa.Column('model_id', sa.String(66), nullable=True),  # Hex32 blockchain model identifier
        sa.Column('endpoint', sa.Text(), nullable=True),
        sa.Column('tokens_input', sa.Integer(), nullable=True),
        sa.Column('tokens_output', sa.Integer(), nullable=True),
        sa.Column('tokens_total', sa.Integer(), nullable=True),
        sa.Column('input_price_per_million', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('output_price_per_million', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('failure_code', sa.Text(), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        # Constraints
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['related_entry_id'], ['credits_ledger.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('user_id', 'idempotency_key', name='uq_credits_ledger_idempotency_key'),
    )
    
    # Create indexes for credits_ledger
    op.create_index('ix_credits_ledger_user_id_created_at', 'credits_ledger', ['user_id', sa.text('created_at DESC')])
    op.create_index('ix_credits_ledger_user_id_request_id', 'credits_ledger', ['user_id', 'request_id'])
    op.create_index('ix_credits_ledger_user_id', 'credits_ledger', ['user_id'])
    op.create_index('ix_credits_ledger_status', 'credits_ledger', ['status'])
    op.create_index('ix_credits_ledger_entry_type', 'credits_ledger', ['entry_type'])
    
    # Create partial index for posted usage charges (for spending queries)
    op.execute("""
        CREATE INDEX ix_credits_ledger_posted_usage 
        ON credits_ledger (user_id, created_at DESC) 
        WHERE status = 'posted' AND entry_type = 'usage_charge';
    """)
    
    # Create partial index for model_name filtering on usage charges
    op.execute("""
        CREATE INDEX ix_credits_ledger_model_name_usage 
        ON credits_ledger (user_id, model_name, created_at DESC) 
        WHERE entry_type = 'usage_charge';
    """)
    
    # Create index for model_id lookups
    op.create_index('ix_credits_ledger_model_id', 'credits_ledger', ['model_id'])

    # Create credit_account_balances table
    op.create_table('credit_account_balances',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('paid_posted_balance', sa.Numeric(precision=20, scale=8), nullable=False, server_default='0'),
        sa.Column('paid_pending_holds', sa.Numeric(precision=20, scale=8), nullable=False, server_default='0'),
        sa.Column('staking_daily_amount', sa.Numeric(precision=20, scale=8), nullable=False, server_default='0'),
        sa.Column('staking_refresh_date', sa.Date(), nullable=True),
        sa.Column('staking_available', sa.Numeric(precision=20, scale=8), nullable=False, server_default='0'),
        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        # Constraints
        sa.PrimaryKeyConstraint('user_id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )


def downgrade() -> None:
    # Drop tables
    op.drop_table('credit_account_balances')
    
    # Drop indexes first
    op.drop_index('ix_credits_ledger_model_id', table_name='credits_ledger')
    op.execute("DROP INDEX IF EXISTS ix_credits_ledger_model_name_usage;")
    op.execute("DROP INDEX IF EXISTS ix_credits_ledger_posted_usage;")
    op.drop_index('ix_credits_ledger_entry_type', table_name='credits_ledger')
    op.drop_index('ix_credits_ledger_status', table_name='credits_ledger')
    op.drop_index('ix_credits_ledger_user_id', table_name='credits_ledger')
    op.drop_index('ix_credits_ledger_user_id_request_id', table_name='credits_ledger')
    op.drop_index('ix_credits_ledger_user_id_created_at', table_name='credits_ledger')
    
    op.drop_table('credits_ledger')
    
    # Drop enum types
    op.execute("DROP TYPE IF EXISTS ledger_entry_type;")
    op.execute("DROP TYPE IF EXISTS ledger_status;")

