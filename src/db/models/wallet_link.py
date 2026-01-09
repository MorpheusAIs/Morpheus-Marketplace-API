"""
WalletLink model for Web3 wallet integration.

Supports multiple wallets per user, allowing users to aggregate
daily allocations from multiple MOR staking positions onto a single account.
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index, Numeric
from sqlalchemy.orm import relationship, validates
from datetime import datetime
from decimal import Decimal

from .base import Base


class WalletLink(Base):
    """
    Links Web3 wallets to user accounts.
    
    Supports multiple wallets per user (1:many relationship).
    Each wallet address can only be linked to ONE user account across the system.
    Also tracks the staked MOR amount for credit allocation calculations.
    """
    __tablename__ = "wallet_links"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Wallet address - stored lowercase for consistent lookups
    # 42 chars = "0x" + 40 hex characters
    # UNIQUE across all users - one wallet can only belong to one account
    wallet_address = Column(
        String(42),
        unique=True,
        nullable=False,
        index=True,
    )
    
    # Staked MOR amount - fetched from Builders API
    # Stored in wei (18 decimals)
    staked_amount = Column(
        Numeric(78, 0),  # Large enough for wei values
        nullable=False,
        default=0,
    )
    
    # Timestamps
    linked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationship to User model
    user = relationship("User", back_populates="wallet_links")
    
    # Composite index for user wallet lookups
    __table_args__ = (
        Index('ix_wallet_links_user_wallet', 'user_id', 'wallet_address'),
    )
    
    @validates("wallet_address")
    def validate_wallet_address(self, key: str, address: str) -> str:
        """
        Validate and normalize wallet address before storing.
        
        - Validates format (0x prefix, 40 hex chars)
        - Normalizes to lowercase for consistent storage
        """
        if not address:
            raise ValueError("Wallet address cannot be empty")
        
        if not address.startswith("0x"):
            raise ValueError("Wallet address must start with 0x")
        
        if len(address) != 42:
            raise ValueError("Wallet address must be 42 characters (0x + 40 hex chars)")
        
        # Validate it's a valid hex string
        try:
            int(address[2:], 16)
        except ValueError:
            raise ValueError("Wallet address must contain only hexadecimal characters")
        
        # Store lowercase for consistent lookups
        return address.lower()
    
    def __repr__(self) -> str:
        return f"<WalletLink(id={self.id}, user_id={self.user_id}, wallet={self.wallet_address[:10]}...)>"
