"""
Staking service for fetching MOR staker data from the Builders API.

Provides functionality to:
- Fetch all stakers with pagination
- Look up staked amount for a specific wallet
- Sync staking data with linked wallets
- Update user credit balances based on staked amounts

Daily Credits Formula:
1. stake_share = user_staked / total_staked
2. mor_earned_today = stake_share * today_emission
3. daily_credits_usd = mor_earned_today * mor_price_usd * X

Where X is the adjustment factor (env: STAKING_CREDITS_ADJUSTMENT_FACTOR, default: 1.0)
"""
import httpx
from typing import Dict, Optional, List, Tuple
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..core.config import settings
from ..core.logging_config import get_api_logger
from ..db.models import WalletLink, LedgerStatus, LedgerEntryType
from ..crud import credits as credits_crud
from .mor_pricing_service import get_mor_pricing_service
from .mor_emission_service import get_mor_emission_service

logger = get_api_logger()


class StakingService:
    """
    Service for interacting with the Builders API to fetch MOR staking data.
    """
    
    def __init__(self):
        self._http_client: Optional[httpx.AsyncClient] = None
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with connection pooling."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            )
        return self._http_client
    
    async def close(self):
        """Close the HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
    
    async def fetch_total_staked(self) -> Decimal:
        """
        Fetch total staked MOR from the subnets API.
        
        Returns total staked in wei from data.totals.totalstaked.
        """
        staking_logger = logger.bind(component="staking_service", action="fetch_total_staked")
        
        url = "https://dashboard.mor.org/api/builders/subnets"
        client = await self._get_http_client()
        
        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Extract total staked from data.totals.totalstaked
            totals = data.get("data", {}).get("totals", {})
            total_staked_raw = totals.get("totalStaked", 0)
            total_staked_wei = Decimal(str(total_staked_raw))
            
            staking_logger.info(
                "Fetched total staked from subnets API",
                total_staked_wei=str(total_staked_wei),
                url=url
            )
            
            return total_staked_wei
            
        except httpx.HTTPStatusError as e:
            staking_logger.error(
                "HTTP error fetching total staked",
                status_code=e.response.status_code,
                url=url,
                error=str(e)
            )
            raise
        except Exception as e:
            staking_logger.error(
                "Error fetching total staked",
                url=url,
                error=str(e)
            )
            raise
    
    async def fetch_all_stakers(self) -> Dict[str, Decimal]:
        """
        Fetch all stakers from Builders API with pagination.
        
        Returns a dict mapping lowercase wallet addresses to staked amounts (in wei).
        """
        staking_logger = logger.bind(component="staking_service", action="fetch_all_stakers")
        staking_logger.info(
            "Starting to fetch all stakers",
            builders_api_url=settings.BUILDERS_API_URL,
            subnet_id=settings.BUILDERS_SUBNET_ID
        )
        
        stakers: Dict[str, Decimal] = {}
        offset = 0
        limit = 1000
        total_fetched = 0
        
        client = await self._get_http_client()
        
        while True:
            url = (
                f"{settings.BUILDERS_API_URL}/builders/stakers"
                f"?subnet_id={settings.BUILDERS_SUBNET_ID}"
                f"&limit={limit}&offset={offset}"
            )
            
            try:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                
                # Handle response format - expecting array of staker objects
                
                staker_list = data.get("data", {}).get("stakers", [])
                if not staker_list:
                    staking_logger.info(
                        "No more stakers found",
                        offset=offset,
                        total_fetched=total_fetched
                    )
                    break
                
                # Process stakers
                for staker in staker_list:
                    # Handle various API response formats
                    address = staker.get("address")
                    stake = staker.get("staked")
                    
                    if address:
                        # Normalize address to lowercase
                        address_lower = address.lower()
                        # Convert stake to Decimal (handle string or int)
                        stake_decimal = Decimal(str(stake))
                        stakers[address_lower] = stake_decimal
                
                batch_size = len(staker_list)
                total_fetched += batch_size
                
                staking_logger.debug(
                    "Fetched staker batch",
                    offset=offset,
                    batch_size=batch_size,
                    total_fetched=total_fetched
                )
                
                # If we got fewer than limit, we've reached the end
                if batch_size < limit:
                    break
                
                offset += limit
                
            except httpx.HTTPStatusError as e:
                staking_logger.error(
                    "HTTP error fetching stakers",
                    status_code=e.response.status_code,
                    url=url,
                    error=str(e)
                )
                raise
            except Exception as e:
                staking_logger.error(
                    "Error fetching stakers",
                    url=url,
                    error=str(e)
                )
                raise
        
        staking_logger.info(
            "Finished fetching all stakers",
            total_stakers=len(stakers),
            event_type="stakers_fetched"
        )
        
        return stakers
    
    async def get_wallet_stake(self, wallet_address: str) -> Decimal:
        """
        Get the staked amount for a specific wallet address.
        
        This fetches fresh data from the API for a single wallet.
        For bulk operations, use fetch_all_stakers() instead.
        
        Returns stake amount in wei, or 0 if not found.
        """
        staking_logger = logger.bind(
            component="staking_service",
            action="get_wallet_stake",
            wallet=wallet_address[:10] + "..."
        )
        
        # Fetch all stakers and find this wallet
        # TODO: If API supports single wallet lookup, use that instead
        all_stakers = await self.fetch_all_stakers()
        
        stake = all_stakers.get(wallet_address.lower(), Decimal(0))
        
        staking_logger.info(
            "Retrieved wallet stake",
            staked_amount=str(stake),
            found=stake > 0
        )
        
        return stake
    
    async def _get_pricing_data(self) -> Tuple[Decimal, Decimal]:
        """
        Fetch MOR price and today's emission for daily credits calculation.
        
        Returns:
            Tuple of (mor_price_usd, today_emission)
        """
        pricing_service = get_mor_pricing_service()
        emission_service = get_mor_emission_service()
        
        mor_price = await pricing_service.get_price_usd()
        today_emission = emission_service.get_compute_emission()
        
        return mor_price, today_emission
    
    def _calculate_daily_credits(
        self,
        user_stake_wei: Decimal,
        total_staked_wei: Decimal,
        today_emission: Decimal,
        mor_price_usd: Decimal,
    ) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        """
        Calculate daily credits using the stake-share formula.
        
        Formula:
        1. stake_share = user_staked / total_staked
        2. mor_earned_today = stake_share * today_emission
        3. daily_credits_usd = mor_earned_today * mor_price_usd * X (adjustment factor)
        
        Args:
            user_stake_wei: User's total stake in wei
            total_staked_wei: Total staked by all users in wei
            today_emission: Today's MOR emission (total for all categories)
            mor_price_usd: Current MOR price in USD
            
        Returns:
            Tuple of (stake_share, mor_earned, daily_credits_usd, adjustment_factor)
        """
        wei_divisor = Decimal("1000000000000000000")  # 10^18
        
        # Get adjustment factor from config (env var: STAKING_CREDITS_ADJUSTMENT_FACTOR)
        adjustment_factor = Decimal(str(settings.STAKING_CREDITS_ADJUSTMENT_FACTOR))
        
        # Convert from wei to MOR
        user_stake_mor = user_stake_wei / wei_divisor
        total_staked_mor = total_staked_wei / wei_divisor
        
        # Avoid division by zero
        if total_staked_mor <= 0:
            return Decimal("0"), Decimal("0"), Decimal("0"), adjustment_factor
        
        # 1. Calculate stake share
        stake_share = user_stake_mor / total_staked_mor
        
        # 2. Calculate MOR earned today (using full emission)
        mor_earned = stake_share * today_emission
        
        # 3. Calculate USD value with adjustment factor (X)
        daily_credits_usd = mor_earned * mor_price_usd * adjustment_factor
        
        # Round to reasonable precision
        stake_share = stake_share.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
        mor_earned = mor_earned.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        daily_credits_usd = daily_credits_usd.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        
        return stake_share, mor_earned, daily_credits_usd, adjustment_factor
    
    async def run_daily_sync(self, db: AsyncSession) -> Dict:
        """
        Run the full daily sync process:
        1. Fetch all stakers from Builders API
        2. Get MOR price and today's emission
        3. Get all linked wallets from DB
        4. Group wallets by user_id
        5. For each user: calculate daily credits and update balance
        
        Daily Credits Formula:
        - stake_share = user_staked / total_staked
        - mor_earned = stake_share * today_emission
        - daily_credits = mor_earned * mor_price_usd
        
        Returns summary of the sync operation.
        """
        sync_logger = logger.bind(component="staking_service", action="run_daily_sync")
        sync_logger.info("Starting daily staking sync", event_type="daily_sync_start")
        
        start_time = datetime.utcnow()
        today = date.today()
        wei_divisor = Decimal("1000000000000000000")  # 10^18
        
        try:
            # Step 1: Fetch all stakers from Builders API (for individual wallet stakes)
            all_stakers = await self.fetch_all_stakers()
            
            # Step 2: Fetch total staked from subnets API (for global total)
            total_staked_wei = await self.fetch_total_staked()
            total_staked_mor = total_staked_wei / wei_divisor
            
            # Step 3: Get MOR price and today's emission
            mor_price, today_emission = await self._get_pricing_data()
            
            # Get adjustment factor for logging
            adjustment_factor = Decimal(str(settings.STAKING_CREDITS_ADJUSTMENT_FACTOR))
            
            sync_logger.info(
                "Fetched pricing data",
                mor_price_usd=str(mor_price),
                today_emission=str(today_emission),
                total_staked_mor=str(total_staked_mor),
                adjustment_factor=str(adjustment_factor)
            )
            
            # Step 4: Get all linked wallets from DB
            result = await db.execute(select(WalletLink))
            wallet_links = list(result.scalars().all())
            
            sync_logger.info(
                "Loaded data for sync",
                staker_count=len(all_stakers),
                wallet_count=len(wallet_links)
            )
            
            # Step 5: Group wallets by user_id
            user_wallets: Dict[int, List[WalletLink]] = {}
            for wallet_link in wallet_links:
                if wallet_link.user_id not in user_wallets:
                    user_wallets[wallet_link.user_id] = []
                user_wallets[wallet_link.user_id].append(wallet_link)
            
            sync_logger.info(
                "Grouped wallets by user",
                user_count=len(user_wallets)
            )
            
            # Step 6: Process each user in a single transaction
            users_processed = 0
            users_skipped = 0  # Already refreshed today
            users_failed = 0
            wallets_updated = 0
            total_wallets = len(wallet_links)
            
            for user_id, user_wallet_links in user_wallets.items():
                user_logger = sync_logger.bind(user_id=user_id)
                
                try:
                    # Update staked amounts for all user's wallets
                    user_total_stake = Decimal(0)
                    user_wallets_updated = 0
                    
                    for wallet_link in user_wallet_links:
                        address_lower = wallet_link.wallet_address.lower()
                        new_stake = all_stakers.get(address_lower, Decimal(0))
                        old_stake = Decimal(str(wallet_link.staked_amount or 0))
                        
                        # Update stake if changed
                        wallet_link.staked_amount = new_stake
                        wallet_link.updated_at = datetime.utcnow()
                        user_wallets_updated += 1
                        
                        user_logger.debug(
                            "Updated wallet stake",
                            wallet_id=wallet_link.id,
                            wallet_address=address_lower[:10] + "...",
                            old_stake=str(old_stake),
                            new_stake=str(new_stake)
                        )
                        
                        # Accumulate total stake for this user
                        user_total_stake += new_stake
                    
                    # Calculate daily credits using stake-share formula
                    stake_in_mor = user_total_stake / wei_divisor
                    stake_share, mor_earned, daily_amount, adj_factor = self._calculate_daily_credits(
                        user_stake_wei=user_total_stake,
                        total_staked_wei=total_staked_wei,
                        today_emission=today_emission,
                        mor_price_usd=mor_price,
                    )
                    
                    user_logger.debug(
                        "Calculated daily credits",
                        stake_in_mor=str(stake_in_mor),
                        stake_share=str(stake_share),
                        mor_earned=str(mor_earned),
                        daily_credits_usd=str(daily_amount),
                        adjustment_factor=str(adj_factor)
                    )
                    
                    # Get current balance to check if already refreshed today
                    balance = await credits_crud.get_or_create_balance(db, user_id)
                    
                    # Check if already refreshed today - skip balance update but still update wallet stakes
                    if balance.staking_refresh_date == today:
                        user_logger.debug(
                            "User already refreshed today, skipping balance update",
                            refresh_date=str(balance.staking_refresh_date)
                        )
                        # Still commit wallet stake updates
                        await db.commit()
                        users_skipped += 1
                        wallets_updated += user_wallets_updated
                        continue
                    
                    idempotency_key = f"staking_sync:{user_id}:{today.isoformat()}"
                    
                    # Create ledger entry for staking refresh (transaction record)
                    if daily_amount > 0:
                        await credits_crud.create_ledger_entry(
                            db=db,
                            user_id=user_id,
                            entry_type=LedgerEntryType.staking_refresh,
                            status=LedgerStatus.posted,
                            idempotency_key=idempotency_key,
                            amount_paid=Decimal("0"),
                            amount_staking=daily_amount,  # Positive for credit (in USD)
                            description=f"Daily staking rewards: {stake_in_mor:.4f} MOR staked ({stake_share*100:.4f}% share), earned {mor_earned:.6f} MOR @ ${mor_price:.2f}",
                        )
                    
                    # Update user's staking balance
                    # Reset staking_available to daily_amount (doesn't accumulate)
                    balance.staking_daily_amount = daily_amount
                    balance.staking_available = daily_amount
                    balance.staking_refresh_date = today
                    balance.updated_at = datetime.utcnow()
                    
                    # Commit all changes for this user atomically
                    await db.commit()
                    
                    users_processed += 1
                    wallets_updated += user_wallets_updated
                    
                    user_logger.debug(
                        "User sync completed",
                        wallets_updated=user_wallets_updated,
                        total_wallets=len(user_wallet_links),
                        total_stake_mor=str(stake_in_mor),
                        daily_amount=str(daily_amount)
                    )
                    
                except Exception as e:
                    # Rollback this user's changes and continue with next user
                    await db.rollback()
                    users_failed += 1
                    
                    user_logger.error(
                        "Failed to sync user",
                        error=str(e),
                        event_type="user_sync_failed"
                    )
            
            duration = (datetime.utcnow() - start_time).total_seconds()
            
            summary = {
                "success": True,
                "timestamp": start_time.isoformat(),
                "duration_seconds": duration,
                "stakers_fetched": len(all_stakers),
                "total_staked_mor": str(total_staked_mor),
                "mor_price_usd": str(mor_price),
                "today_emission": str(today_emission),
                "adjustment_factor": str(adjustment_factor),
                "total_wallets": total_wallets,
                "wallets_updated": wallets_updated,
                "users_processed": users_processed,
                "users_skipped": users_skipped,  # Already refreshed today
                "users_failed": users_failed,
            }
            
            sync_logger.info(
                "Daily staking sync completed",
                **summary,
                event_type="daily_sync_complete"
            )
            
            return summary
            
        except Exception as e:
            duration = (datetime.utcnow() - start_time).total_seconds()
            
            summary = {
                "success": False,
                "timestamp": start_time.isoformat(),
                "duration_seconds": duration,
                "error": str(e),
            }
            
            sync_logger.error(
                "Daily staking sync failed",
                **summary,
                event_type="daily_sync_failed"
            )
            
            raise


# Global service instance
staking_service = StakingService()

