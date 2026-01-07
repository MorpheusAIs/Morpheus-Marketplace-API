"""Add payment source fields to credits_ledger table.

Revision ID: a1b2c3d4e5f6
Revises: add_credits_billing
Create Date: 2025-12-26 00:01:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'add_credits_billing'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add payment source fields to credits_ledger
    # Uses a flexible schema to support multiple payment providers
    
    # Payment source identifier (stripe, coinbase, manual, etc.)
    op.add_column(
        'credits_ledger',
        sa.Column('payment_source', sa.String(50), nullable=True)
    )
    
    # Primary external transaction ID for fast lookups (indexed)
    # For Stripe: checkout_session_id or invoice_id
    # For Coinbase: charge_id
    # For others: their primary transaction identifier
    op.add_column(
        'credits_ledger',
        sa.Column('external_transaction_id', sa.String(255), nullable=True)
    )
    
    # JSONB column for provider-specific metadata (flexible schema)
    # Example for Stripe:
    # {
    #   "checkout_session_id": "cs_xxx",
    #   "payment_intent_id": "pi_xxx",
    #   "invoice_id": "in_xxx",
    #   "customer_id": "cus_xxx"
    # }
    # Example for Coinbase:
    # {
    #   "charge_id": "xxx",
    #   "charge_code": "xxx",
    #   "payment_id": "xxx"
    # }
    op.add_column(
        'credits_ledger',
        sa.Column('payment_metadata', JSONB, nullable=True)
    )
    
    # Index for external transaction ID lookups
    op.create_index(
        'ix_credits_ledger_external_transaction_id',
        'credits_ledger',
        ['external_transaction_id'],
        unique=False
    )
    
    # GIN index for JSONB queries (optional but improves performance)
    op.create_index(
        'ix_credits_ledger_payment_metadata',
        'credits_ledger',
        ['payment_metadata'],
        unique=False,
        postgresql_using='gin'
    )


def downgrade() -> None:
    # Drop indexes first
    op.drop_index('ix_credits_ledger_payment_metadata', table_name='credits_ledger')
    op.drop_index('ix_credits_ledger_external_transaction_id', table_name='credits_ledger')
    
    # Drop columns
    op.drop_column('credits_ledger', 'payment_metadata')
    op.drop_column('credits_ledger', 'external_transaction_id')
    op.drop_column('credits_ledger', 'payment_source')
