"""Create user_automation_settings table

Revision ID: 2025_04_20_0432
Revises: fix_session_constraints
Create Date: 2025-04-20 04:32:00.000000+00:00

"""
from typing import Sequence, Union
from sqlalchemy.exc import ProgrammingError

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2025_04_20_0432'
down_revision: Union[str, None] = 'fix_session_constraints'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create user_automation_settings table
    try:
        op.create_table(
            'user_automation_settings',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('is_enabled', sa.Boolean(), nullable=True, server_default='false'),
            sa.Column('session_duration', sa.Integer(), nullable=True, server_default='3600'),
            sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id')
        )
        op.create_index(op.f('ix_user_automation_settings_id'), 'user_automation_settings', ['id'], unique=False)
    except Exception as e:
        # Log the error but don't raise it - this allows the migration to continue
        print(f"Error creating user_automation_settings table: {e}")


def downgrade() -> None:
    # Drop table if it exists
    try:
        op.drop_index(op.f('ix_user_automation_settings_id'), table_name='user_automation_settings')
        op.drop_table('user_automation_settings')
    except Exception as e:
        # Log the error but don't raise it
        print(f"Error dropping user_automation_settings table: {e}") 