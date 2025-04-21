"""Fix session table constraints

Revision ID: fix_session_constraints
Revises: 7c29c35fc9bc
Create Date: 2025-04-19 01:31:37.250233+00:00

"""
from typing import Sequence, Union # Ensure Union is imported
from sqlalchemy.exc import ProgrammingError
# revision identifiers, used by Alembic.
revision: str = 'fix_session_constraints'
down_revision: Union[str, None] = '7c29c35fc9bc' # Restore original down_revision
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None 