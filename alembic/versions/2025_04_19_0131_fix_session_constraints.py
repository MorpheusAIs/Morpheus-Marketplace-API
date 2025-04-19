"""Fix session table constraints

Revision ID: fix_session_constraints
Revises: 7c29c35fc9bc
Create Date: 2025-04-19 01:31:37.250233+00:00

"""
from typing import Sequence, Union
from sqlalchemy.exc import ProgrammingError

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fix_session_constraints'
down_revision: Union[str, None] = '7c29c35fc9bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def drop_constraint_if_exists(constraint_name, table_name):
    try:
        op.drop_constraint(constraint_name, table_name, type_='unique')
    except ProgrammingError:
        pass  # Constraint doesn't exist, which is fine


def upgrade() -> None:
    # Drop existing constraints if they exist
    drop_constraint_if_exists('unique_api_key_session', 'user_sessions')
    drop_constraint_if_exists('user_sessions_api_key_id_key', 'user_sessions')
    
    # Add new constraint that only enforces uniqueness for active sessions
    op.create_index(
        'unique_active_api_key_session',
        'user_sessions',
        ['api_key_id'],
        unique=True,
        postgresql_where=sa.text('is_active = true')
    )


def downgrade() -> None:
    # Remove the new constraint
    op.drop_index('unique_active_api_key_session', table_name='user_sessions')
    
    # Restore original constraints
    op.create_unique_constraint('user_sessions_api_key_id_key', 'user_sessions', ['api_key_id'])
    op.create_unique_constraint('unique_api_key_session', 'user_sessions', ['api_key_id']) 