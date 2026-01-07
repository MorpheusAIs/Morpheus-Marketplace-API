"""
Delegation model for EIP-712 signed delegations.
"""
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, TEXT
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class Delegation(Base):
    """Delegation model for storing EIP-712 signed delegations."""
    __tablename__ = "delegations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    delegate_address = Column(String, nullable=False, index=True)
    # Store the signed delegation object (EIP-712 structure + signature) as JSON or Text
    # Using TEXT might be simpler initially if the structure isn't fixed
    signed_delegation_data = Column(TEXT, nullable=False)
    expiry = Column(DateTime, nullable=True)  # Optional expiry from delegation
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, default=True, index=True)

    user = relationship("User", back_populates="delegations")

