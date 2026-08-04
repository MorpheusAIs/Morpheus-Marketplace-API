"""Tombstone for deleted Cognito identities.

Prevents JWT auth from auto-recreating a user row after account deletion
while an access token is still valid.
"""
from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime

from .base import Base


class DeletedUser(Base):
    """Immutable tombstone keyed by cognito_user_id."""

    __tablename__ = "deleted_users"

    cognito_user_id = Column(String, primary_key=True, nullable=False)
    former_user_id = Column(Integer, nullable=True)
    deleted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
