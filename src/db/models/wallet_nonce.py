"""
WalletNonce model for secure nonce management.

Uses database storage instead of Redis for nonce management.
Nonces are one-time use and expire after 5 minutes.
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta

from .base import Base


# Nonce expiration time in seconds (5 minutes)
NONCE_TTL_SECONDS = 300


class WalletNonce(Base):
    """
    Stores one-time nonces for wallet signature verification.
    
    Nonces are used to prevent replay attacks during wallet linking.
    Each nonce can only be used once and expires after 5 minutes.
    """
    __tablename__ = "wallet_nonces"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Cryptographically secure nonce (64 hex characters)
    nonce = Column(String(64), unique=True, nullable=False, index=True)
    
    # Wallet address this nonce is for (optional - for additional validation)
    wallet_address = Column(String(42), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    
    # Whether the nonce has been consumed
    consumed = Column(DateTime, nullable=True)
    
    # Relationship to User model
    user = relationship("User", back_populates="wallet_nonces")
    
    # Index for cleanup queries
    __table_args__ = (
        Index('ix_wallet_nonces_expires', 'expires_at'),
        Index('ix_wallet_nonces_user_consumed', 'user_id', 'consumed'),
    )
    
    @property
    def is_expired(self) -> bool:
        """Check if the nonce has expired."""
        return datetime.utcnow() > self.expires_at
    
    @property
    def is_valid(self) -> bool:
        """Check if the nonce is valid (not expired and not consumed)."""
        return not self.is_expired and self.consumed is None
    
    @classmethod
    def create_with_expiry(cls, user_id: int, nonce: str, wallet_address: str = None) -> "WalletNonce":
        """Create a new nonce with automatic expiry time."""
        now = datetime.utcnow()
        return cls(
            user_id=user_id,
            nonce=nonce,
            wallet_address=wallet_address.lower() if wallet_address else None,
            created_at=now,
            expires_at=now + timedelta(seconds=NONCE_TTL_SECONDS),
        )
    
    def __repr__(self) -> str:
        return f"<WalletNonce(id={self.id}, user_id={self.user_id}, valid={self.is_valid})>"

