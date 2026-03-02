"""
Web3 Wallet API endpoints.

Provides endpoints for:
- Generating nonces for wallet signature verification
- Linking wallets to user accounts
- Managing linked wallets
- Checking wallet availability
"""
from typing import List
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from ....db.database import get_db_session
from ....db.models import User, WalletLink, NONCE_TTL_SECONDS
from ....dependencies import get_current_user
from ....crud import wallet as wallet_crud
from ....crud import credits as credits_crud
from ....schemas.wallet import (
    NonceResponse,
    WalletLinkRequest,
    WalletLinkResponse,
    WalletStatusResponse,
    WalletAvailabilityResponse,
    WalletUnlinkResponse,
    get_siwe_template,
)
from ....services.wallet_service import wallet_linking_service, wallet_verification_service
from ....services.staking_service import staking_service
from ....core.logging_config import get_auth_logger

logger = get_auth_logger()

router = APIRouter(tags=["Auth - Wallet"])


# =============================================================================
# Nonce Generation
# =============================================================================

@router.post("/nonce/{wallet_address}", response_model=NonceResponse)
async def generate_wallet_nonce(
    wallet_address: str = Path(
        ...,
        description="Ethereum wallet address to generate nonce for (0x prefixed, 40 hex characters)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Generate a nonce for wallet signature verification.
    
    The frontend should:
    1. Call this endpoint with the wallet address to get a nonce and message template
    2. Construct the message by filling in the template with wallet_address, nonce, and timestamp
    3. Request the user to sign the message with their Web3 wallet
    4. Submit the signature to POST /wallet/link
    
    Nonces expire after 5 minutes and can only be used once.
    """
    # Validate address format
    is_valid, result = wallet_verification_service.validate_wallet_address(wallet_address)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid wallet address format: {result}"
        )
    
    checksummed_address = result
    
    nonce_logger = logger.bind(
        endpoint="generate_wallet_nonce",
        user_id=current_user.id,
        wallet_address=checksummed_address
    )
    nonce_logger.info("Generating wallet nonce", event_type="nonce_generation_start")
    
    # Generate a new nonce associated with this wallet address
    nonce = await wallet_crud.generate_nonce(db, current_user.id, checksummed_address)
    
    nonce_logger.info("Wallet nonce generated successfully", event_type="nonce_generated")
    
    return NonceResponse(
        nonce=nonce,
        message_template=get_siwe_template(),
        expires_in=NONCE_TTL_SECONDS
    )


# =============================================================================
# Wallet Linking
# =============================================================================

@router.post("/link", response_model=WalletLinkResponse, status_code=status.HTTP_201_CREATED)
async def link_wallet(
    request: WalletLinkRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Link a Web3 wallet to the authenticated user's account.
    
    **Requirements:**
    - Valid signature proving wallet ownership (EIP-191 personal_sign)
    - Wallet not already linked to any other account
    - Valid, unexpired nonce from POST /wallet/nonce
    
    **Note:** Users can link multiple wallets to their account.
    Each wallet can only be linked to ONE account in the entire system.
    """
    link_logger = logger.bind(
        endpoint="link_wallet",
        user_id=current_user.id,
        wallet_address=request.wallet_address[:10] + "..."
    )
    link_logger.info("Wallet link request received", event_type="wallet_link_start")
    
    # 1. Validate wallet address format
    is_valid, result = wallet_verification_service.validate_wallet_address(request.wallet_address)
    if not is_valid:
        link_logger.warning("Invalid wallet address format", error=result, event_type="wallet_address_invalid")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid wallet address: {result}"
        )
    checksummed_address = result
    
    # 2. Verify nonce is valid and consume it
    nonce_valid = await wallet_crud.verify_and_consume_nonce(db, current_user.id, request.nonce)
    if not nonce_valid:
        link_logger.warning("Invalid or expired nonce", event_type="nonce_invalid")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired nonce. Please request a new one."
        )
    
    # 3. Validate message format matches expected template
    is_valid, error = wallet_linking_service.validate_message_format(
        message=request.message,
        wallet_address=request.wallet_address,
        nonce=request.nonce,
        timestamp=request.timestamp
    )
    if not is_valid:
        link_logger.warning("Message format mismatch", error=error, event_type="message_format_invalid")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )
    
    # 4. Verify signature
    is_valid, result = wallet_linking_service.verify_wallet_ownership(
        wallet_address=request.wallet_address,
        message=request.message,
        signature=request.signature
    )
    if not is_valid:
        link_logger.warning("Signature verification failed", error=result, event_type="signature_invalid")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signature verification failed. The signature does not match the wallet address."
        )
    
    # 5. Check if wallet is already linked to any account
    existing_link = await wallet_crud.get_wallet_link_by_address(db, request.wallet_address)
    if existing_link:
        if existing_link.user_id == current_user.id:
            link_logger.info("Wallet already linked to this user", event_type="wallet_already_linked_same_user")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This wallet is already linked to your account."
            )
        else:
            link_logger.warning("Wallet already linked to another user", event_type="wallet_already_linked_other_user")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This wallet is already linked to another account."
            )
    
    # 6. Fetch current staked amount from Builders API (no balance update, just store)
    staked_amount = Decimal(0)
    try:
        staked_amount = await staking_service.get_wallet_stake(request.wallet_address)
        link_logger.info(
            "Fetched staked amount for wallet",
            staked_amount=str(staked_amount),
            event_type="staked_amount_fetched"
        )
    except Exception as e:
        # Log but don't fail - we'll update it later via cron
        link_logger.warning(
            "Failed to fetch staked amount, will retry in daily sync",
            error=str(e),
            event_type="staked_amount_fetch_failed"
        )
    
    # 7. Create the wallet link with staked amount
    try:
        wallet_link = await wallet_crud.create_wallet_link(
            db=db,
            user_id=current_user.id,
            wallet_address=request.wallet_address,
            staked_amount=staked_amount
        )
    except IntegrityError:
        # Race condition: another request linked the wallet between our check and create
        await db.rollback()
        link_logger.warning("Wallet link failed due to race condition", event_type="wallet_link_race_condition")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This wallet is already linked to another account."
        )
    
    # 8. Mark user as staker if this wallet has stake
    if staked_amount > 0:
        balance = await credits_crud.get_or_create_balance(db, current_user.id)
        if not balance.is_staker:
            balance.is_staker = True
            balance.updated_at = datetime.utcnow()
            await db.commit()
            link_logger.info("User marked as staker", event_type="user_marked_staker")
    
    link_logger.info(
        "Wallet linked successfully",
        wallet_link_id=wallet_link.id,
        staked_amount=str(staked_amount),
        event_type="wallet_linked"
    )
    
    return WalletLinkResponse(
        id=wallet_link.id,
        wallet_address=checksummed_address,
        staked_amount=str(wallet_link.staked_amount or 0),
        linked_at=wallet_link.linked_at,
        updated_at=wallet_link.updated_at
    )


# =============================================================================
# Wallet Status
# =============================================================================

@router.get("/", response_model=WalletStatusResponse)
async def get_wallet_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Get the wallet linking status for the current user.
    
    Returns a list of all linked wallets with their addresses and staked amounts.
    """
    wallets = await wallet_crud.get_user_wallet_links(db, current_user.id)
    
    # Calculate total staked across all wallets
    total_staked = sum(
        (Decimal(str(w.staked_amount or 0)) for w in wallets),
        Decimal(0)
    )
    
    wallet_responses = [
        WalletLinkResponse(
            id=w.id,
            wallet_address=wallet_verification_service.to_checksum_address(w.wallet_address),
            staked_amount=str(w.staked_amount or 0),
            linked_at=w.linked_at,
            updated_at=w.updated_at
        )
        for w in wallets
    ]
    
    return WalletStatusResponse(
        has_wallets=len(wallets) > 0,
        wallet_count=len(wallets),
        total_staked=str(total_staked),
        wallets=wallet_responses
    )


# =============================================================================
# Wallet Unlinking
# =============================================================================

@router.delete("/{wallet_address}", response_model=WalletUnlinkResponse)
async def unlink_wallet(
    wallet_address: str = Path(
        ...,
        description="Ethereum wallet address to unlink (0x prefixed, 40 hex characters)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Unlink a wallet from the current user's account.
    
    This allows the wallet to be linked to a different account.
    """
    # Validate address format
    is_valid, result = wallet_verification_service.validate_wallet_address(wallet_address)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid wallet address format: {result}"
        )
    
    checksummed_address = result
    
    delete_logger = logger.bind(
        endpoint="unlink_wallet",
        user_id=current_user.id,
        wallet_address=checksummed_address
    )
    delete_logger.info("Wallet unlink request received", event_type="wallet_unlink_start")
    
    wallet_link = await wallet_crud.get_wallet_link_by_address(db, checksummed_address)
    
    if not wallet_link:
        delete_logger.warning("Wallet link not found", event_type="wallet_not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found"
        )
    
    if wallet_link.user_id != current_user.id:
        delete_logger.warning("Wallet belongs to different user", event_type="wallet_ownership_mismatch")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found"
        )
    
    await wallet_crud.delete_wallet_link(db, wallet_link)
    
    # Re-check if user still has any staked wallets
    remaining = await wallet_crud.get_user_wallet_links(db, current_user.id)
    has_stake = any(Decimal(str(w.staked_amount or 0)) > 0 for w in remaining)
    if not has_stake:
        balance = await credits_crud.get_or_create_balance(db, current_user.id)
        if balance.is_staker:
            balance.is_staker = False
            balance.updated_at = datetime.utcnow()
            await db.commit()
            delete_logger.info("User no longer a staker", event_type="user_unmarked_staker")
    
    delete_logger.info("Wallet unlinked successfully", event_type="wallet_unlinked")
    
    return WalletUnlinkResponse(
        message="Wallet unlinked successfully",
        wallet_address=checksummed_address
    )


# =============================================================================
# Wallet Availability Check (Public)
# =============================================================================

@router.get("/check/{wallet_address}", response_model=WalletAvailabilityResponse)
async def check_wallet_availability(
    wallet_address: str = Path(
        ...,
        description="Ethereum wallet address to check (0x prefixed, 40 hex characters)"
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Check if a wallet address is available to be linked.
    
    This is a public endpoint that can be called before authentication
    to provide UX feedback to users attempting to link a specific wallet.
    
    **Note:** This endpoint does not require authentication.
    """
    # Validate address format
    is_valid, result = wallet_verification_service.validate_wallet_address(wallet_address)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid wallet address format: {result}"
        )
    
    checksummed_address = result
    
    # Check availability
    is_available = await wallet_crud.check_wallet_availability(db, wallet_address)
    
    return WalletAvailabilityResponse(
        wallet_address=checksummed_address,
        is_available=is_available
    )


# Export router
wallet_router = router
