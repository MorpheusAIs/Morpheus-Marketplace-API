"""Drop unused tables: sessions, delegations, user_private_keys, user_automation_settings

These tables supported endpoints that have been removed:
- /api/v1/session/* (sessions table)
- /api/v1/auth/private-key (user_private_keys table)
- /api/v1/auth/delegation (delegations table)
- /api/v1/automation/settings (user_automation_settings table)

The routed_sessions table is NOT dropped — it is actively used by the
session routing service for chat, embeddings, and audio endpoints.

Revision ID: d7e8f9a0b1c2
Revises: b2c3d4e5f6a7
Create Date: 2026-02-17 00:01:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'd7e8f9a0b1c2'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop sessions table and its indexes
    op.drop_index('sessions_active_api_key_unique', table_name='sessions', if_exists=True)
    op.drop_index('ix_sessions_is_active', table_name='sessions', if_exists=True)
    op.drop_index('ix_sessions_api_key_id', table_name='sessions', if_exists=True)
    op.drop_table('sessions')

    # Drop delegations table and its indexes
    op.drop_index('ix_delegations_user_id', table_name='delegations', if_exists=True)
    op.drop_index('ix_delegations_is_active', table_name='delegations', if_exists=True)
    op.drop_index('ix_delegations_id', table_name='delegations', if_exists=True)
    op.drop_index('ix_delegations_delegate_address', table_name='delegations', if_exists=True)
    op.drop_table('delegations')

    # Drop user_private_keys table
    op.drop_table('user_private_keys')

    # Drop user_automation_settings table and its indexes
    op.drop_index('ix_user_automation_settings_id', table_name='user_automation_settings', if_exists=True)
    op.drop_table('user_automation_settings')


def downgrade() -> None:
    pass
