"""
CRUD operations for Web3 wallet management.
"""
import secrets
from datetime import datetime
from typing import List, Optional
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, delete

from ..db.models import WalletLink, WalletNonce, NONCE_TTL_SECONDS


# =============================================================================
# Wallet Link CRUD
# =============================================================================

async def get_wallet_link_by_id(
    db: AsyncSession,
    wallet_link_id: int
) -> Optional[WalletLink]:
    """Get a wallet link by its ID."""
    return await db.get(WalletLink, wallet_link_id)


async def get_wallet_link_by_address(
    db: AsyncSession,
    wallet_address: str
) -> Optional[WalletLink]:
    """Get a wallet link by wallet address (case-insensitive)."""
    result = await db.execute(
        select(WalletLink)
        .where(WalletLink.wallet_address == wallet_address.lower())
    )
    return result.scalar_one_or_none()


async def get_user_wallet_links(
    db: AsyncSession,
    user_id: int,
    skip: int = 0,
    limit: int = 100
) -> List[WalletLink]:
    """Get all wallet links for a specific user."""
    result = await db.execute(
        select(WalletLink)
        .where(WalletLink.user_id == user_id)
        .order_by(WalletLink.linked_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_user_wallet_count(
    db: AsyncSession,
    user_id: int
) -> int:
    """Get the count of wallets linked to a user."""
    result = await db.execute(
        select(WalletLink)
        .where(WalletLink.user_id == user_id)
    )
    return len(result.scalars().all())


async def check_wallet_availability(
    db: AsyncSession,
    wallet_address: str
) -> bool:
    """Check if a wallet address is available (not linked to any user)."""
    existing = await get_wallet_link_by_address(db, wallet_address)
    return existing is None


async def create_wallet_link(
    db: AsyncSession,
    user_id: int,
    wallet_address: str,
    staked_amount: Decimal = Decimal(0)
) -> WalletLink:
    """
    Create a new wallet link.
    
    Args:
        db: Database session
        user_id: User ID to link the wallet to
        wallet_address: Ethereum wallet address (will be normalized to lowercase)
        staked_amount: Initial staked amount in wei (fetched from Builders API)
        
    Returns:
        Created WalletLink object
        
    Raises:
        IntegrityError if wallet is already linked to another user
    """
    now = datetime.utcnow()
    wallet_link = WalletLink(
        user_id=user_id,
        wallet_address=wallet_address.lower(),
        staked_amount=staked_amount,
        linked_at=now,
        updated_at=now,
    )
    db.add(wallet_link)
    await db.commit()
    await db.refresh(wallet_link)
    return wallet_link


async def update_wallet_staked_amount(
    db: AsyncSession,
    wallet_link: WalletLink,
    staked_amount: Decimal
) -> WalletLink:
    """
    Update the staked amount for a wallet link.
    
    Args:
        db: Database session
        wallet_link: WalletLink object to update
        staked_amount: New staked amount in wei
        
    Returns:
        Updated WalletLink object
    """
    wallet_link.staked_amount = staked_amount
    wallet_link.updated_at = datetime.utcnow()
    db.add(wallet_link)
    await db.commit()
    await db.refresh(wallet_link)
    return wallet_link


async def get_total_staked_for_user(
    db: AsyncSession,
    user_id: int
) -> Decimal:
    """
    Get the total staked amount across all wallets for a user.
    
    Returns:
        Total staked amount in wei
    """
    result = await db.execute(
        select(WalletLink.staked_amount)
        .where(WalletLink.user_id == user_id)
    )
    amounts = result.scalars().all()
    return sum((Decimal(str(a or 0)) for a in amounts), Decimal(0))


async def delete_wallet_link(
    db: AsyncSession,
    wallet_link: WalletLink
) -> None:
    """Delete a wallet link."""
    await db.delete(wallet_link)
    await db.commit()


async def delete_user_wallet_link_by_address(
    db: AsyncSession,
    user_id: int,
    wallet_address: str
) -> Optional[WalletLink]:
    """
    Delete a user's wallet link by address.
    
    Returns the deleted wallet link, or None if not found.
    """
    result = await db.execute(
        select(WalletLink)
        .where(
            and_(
                WalletLink.user_id == user_id,
                WalletLink.wallet_address == wallet_address.lower()
            )
        )
    )
    wallet_link = result.scalar_one_or_none()
    
    if wallet_link:
        await db.delete(wallet_link)
        await db.commit()
    
    return wallet_link


async def delete_all_user_wallet_links(
    db: AsyncSession,
    user_id: int
) -> int:
    """Delete all wallet links for a user. Returns count of deleted links."""
    result = await db.execute(
        delete(WalletLink).where(WalletLink.user_id == user_id)
    )
    await db.commit()
    return result.rowcount


# =============================================================================
# Wallet Nonce CRUD
# =============================================================================

async def generate_nonce(
    db: AsyncSession,
    user_id: int,
    wallet_address: Optional[str] = None
) -> str:
    """
    Generate a cryptographically secure nonce for wallet linking.
    
    Previous nonces for this user are NOT invalidated - they will expire naturally.
    This allows users to initiate multiple wallet linking flows.
    """
    nonce = secrets.token_hex(32)  # 64 character hex string
    
    wallet_nonce = WalletNonce.create_with_expiry(
        user_id=user_id,
        nonce=nonce,
        wallet_address=wallet_address
    )
    
    db.add(wallet_nonce)
    await db.commit()
    await db.refresh(wallet_nonce)
    
    return nonce


async def verify_and_consume_nonce(
    db: AsyncSession,
    user_id: int,
    nonce: str
) -> bool:
    """
    Verify a nonce is valid and consume it (one-time use).
    
    Returns True if the nonce was valid and consumed.
    Returns False if the nonce is invalid, expired, or already consumed.
    """
    result = await db.execute(
        select(WalletNonce)
        .where(
            and_(
                WalletNonce.user_id == user_id,
                WalletNonce.nonce == nonce,
                WalletNonce.consumed.is_(None)
            )
        )
    )
    wallet_nonce = result.scalar_one_or_none()
    
    if not wallet_nonce:
        return False
    
    if wallet_nonce.is_expired:
        return False
    
    # Consume the nonce
    wallet_nonce.consumed = datetime.utcnow()
    db.add(wallet_nonce)
    await db.commit()
    
    return True


async def get_nonce(
    db: AsyncSession,
    nonce: str
) -> Optional[WalletNonce]:
    """Get a nonce record by its value."""
    result = await db.execute(
        select(WalletNonce)
        .where(WalletNonce.nonce == nonce)
    )
    return result.scalar_one_or_none()


async def cleanup_expired_nonces(
    db: AsyncSession
) -> int:
    """
    Clean up expired nonces. Should be called periodically.
    
    Returns the number of deleted nonces.
    """
    now = datetime.utcnow()
    result = await db.execute(
        delete(WalletNonce).where(WalletNonce.expires_at < now)
    )
    await db.commit()
    return result.rowcount
