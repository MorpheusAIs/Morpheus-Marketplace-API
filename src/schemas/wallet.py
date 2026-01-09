"""
Pydantic schemas for Web3 wallet integration endpoints.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
from decimal import Decimal
import re


# Ethereum address pattern: 0x followed by 40 hex characters
ETH_ADDRESS_PATTERN = re.compile(r'^0x[a-fA-F0-9]{40}$')


class WalletAddressValidator:
    """Mixin for wallet address validation."""
    
    @staticmethod
    def validate_eth_address(address: str) -> str:
        """Validate Ethereum address format and return checksummed version."""
        if not address:
            raise ValueError("Wallet address cannot be empty")
        
        if not ETH_ADDRESS_PATTERN.match(address):
            raise ValueError("Invalid wallet address format. Must be 0x followed by 40 hex characters")
        
        return address


# =============================================================================
# Nonce Schemas
# =============================================================================

class NonceResponse(BaseModel):
    """Response containing a nonce for wallet signature verification."""
    nonce: str = Field(
        ...,
        description="Cryptographic nonce (64 hex characters)",
        min_length=64,
        max_length=64
    )
    message_template: str = Field(
        ...,
        description="Template for constructing the message to sign. Replace {wallet_address}, {nonce}, and {timestamp} with actual values."
    )
    expires_in: int = Field(
        default=300,
        description="Number of seconds until the nonce expires"
    )


# =============================================================================
# Wallet Link Schemas
# =============================================================================

class WalletLinkRequest(BaseModel):
    """Request to link a Web3 wallet to the user's account."""
    wallet_address: str = Field(
        ...,
        description="Ethereum wallet address (0x prefixed, 40 hex characters)",
        min_length=42,
        max_length=42
    )
    signature: str = Field(
        ...,
        description="EIP-191 signature of the message (0x prefixed hex string)"
    )
    message: str = Field(
        ...,
        description="The exact message that was signed"
    )
    nonce: str = Field(
        ...,
        description="The nonce received from POST /wallet/nonce",
        min_length=64,
        max_length=64
    )
    timestamp: str = Field(
        ...,
        description="ISO 8601 timestamp when the message was constructed"
    )
    
    @field_validator("wallet_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        return WalletAddressValidator.validate_eth_address(v)
    
    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        if not v.startswith("0x"):
            raise ValueError("Signature must start with 0x")
        # Signature should be 65 bytes (130 hex chars) + 0x prefix = 132 chars
        # But some wallets may produce different formats, so we just check it's hex
        try:
            int(v[2:], 16)
        except ValueError:
            raise ValueError("Signature must be a valid hex string")
        return v


class WalletLinkResponse(BaseModel):
    """Response after successfully linking a wallet."""
    id: int = Field(..., description="Wallet link ID")
    wallet_address: str = Field(..., description="The linked wallet address (checksummed)")
    staked_amount: str = Field(
        default="0",
        description="Staked MOR amount in wei (as string for precision)"
    )
    linked_at: datetime = Field(..., description="Timestamp when the wallet was linked")
    updated_at: datetime = Field(..., description="Timestamp when the wallet was last updated")
    
    class Config:
        from_attributes = True


class WalletStatusResponse(BaseModel):
    """Response containing the user's wallet linking status."""
    has_wallets: bool = Field(..., description="Whether the user has any wallets linked")
    wallet_count: int = Field(..., description="Number of linked wallets")
    total_staked: str = Field(
        default="0",
        description="Total staked MOR amount across all wallets in wei (as string)"
    )
    wallets: List["WalletLinkResponse"] = Field(
        default_factory=list,
        description="List of linked wallets"
    )


class WalletAvailabilityResponse(BaseModel):
    """Response indicating whether a wallet address is available."""
    wallet_address: str = Field(..., description="The wallet address that was checked (checksummed)")
    is_available: bool = Field(..., description="Whether the wallet is available to be linked")


class WalletUnlinkResponse(BaseModel):
    """Response after unlinking a wallet."""
    message: str = Field(..., description="Success message")
    wallet_address: str = Field(..., description="The unlinked wallet address")


class WalletErrorResponse(BaseModel):
    """Error response for wallet operations."""
    detail: str = Field(..., description="Human-readable error message")


# =============================================================================
# Message Template
# =============================================================================

WALLET_SIGN_MESSAGE_TEMPLATE = """Sign this message to link your wallet to your Morpheus API Gateway account.

Wallet: {wallet_address}
Nonce: {nonce}
Timestamp: {timestamp}

This signature does not trigger any blockchain transaction or cost any gas."""


def create_sign_message(wallet_address: str, nonce: str, timestamp: str) -> str:
    """Create the standardized message for signing."""
    return WALLET_SIGN_MESSAGE_TEMPLATE.format(
        wallet_address=wallet_address,
        nonce=nonce,
        timestamp=timestamp
    )
