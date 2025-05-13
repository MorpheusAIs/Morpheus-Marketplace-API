"""add updated_at to users table

Revision ID: 5f7a3e1b8d42
Revises: 881e615d25ac
Create Date: 2025-04-30 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f7a3e1b8d42'
down_revision: Union[str, None] = '881e615d25ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if column exists first to make migration idempotent
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = [column['name'] for column in inspector.get_columns('users')]
    
    if 'updated_at' not in columns:
        op.add_column('users', sa.Column('updated_at', sa.DateTime(), 
                                        nullable=True, 
                                        server_default=sa.func.current_timestamp()))


def downgrade() -> None:
    # Check if column exists first to make downgrade idempotent
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = [column['name'] for column in inspector.get_columns('users')]
    
    if 'updated_at' in columns:
        op.drop_column('users', 'updated_at') 