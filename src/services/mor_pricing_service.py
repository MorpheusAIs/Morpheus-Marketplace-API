"""
MOR Pricing Service - Fetches current MOR token price from external APIs.

This service is designed with a provider-based architecture to allow
easy switching between different price APIs in the future.
"""
import httpx
from abc import ABC, abstractmethod
from typing import Optional
from decimal import Decimal
from datetime import datetime, timedelta

from ..core.config import settings
from ..core.logging_config import get_core_logger

logger = get_core_logger()


class MORPriceProvider(ABC):
    """Abstract base class for MOR price providers."""
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """Return the name of this pricing source."""
        pass
    
    @abstractmethod
    async def get_mor_price_usd(self) -> Optional[Decimal]:
        """
        Get the current MOR price in USD.
        
        Returns:
            Decimal price in USD, or None if unavailable
        """
        pass


class CoinCapPriceProvider(MORPriceProvider):
    """
    CoinCap API provider for MOR price data.
    
    Uses the CoinCap Pro API: https://pro.coincap.io/api-docs/
    """
    
    BASE_URL = "https://rest.coincap.io/v3"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the CoinCap provider.
        
        Args:
            api_key: Optional API key for CoinCap Pro (higher rate limits)
        """
        self._api_key = api_key
        self._http_client: Optional[httpx.AsyncClient] = None
        self._cache_price: Optional[Decimal] = None
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=5)  # Cache price for 5 minutes
    
    @property
    def source_name(self) -> str:
        return "coincap"
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers=headers,
                follow_redirects=True,
            )
        return self._http_client
    
    async def close(self):
        """Close the HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
    
    async def get_mor_price_usd(self) -> Optional[Decimal]:
        """
        Get current MOR price from CoinCap API.
        
        CoinCap uses asset IDs - we need to find the MOR asset ID.
        The Morpheus token is listed as "morpheus" on CoinCap.
        
        Returns:
            Decimal price in USD, or None if unavailable
        """
        pricing_logger = logger.bind(
            component="mor_pricing_service",
            action="get_mor_price",
            provider=self.source_name
        )
        
        # Check cache first
        if self._cache_price is not None and self._cache_timestamp is not None:
            if datetime.utcnow() - self._cache_timestamp < self._cache_ttl:
                pricing_logger.debug(
                    "Returning cached MOR price",
                    price=str(self._cache_price),
                    cached_at=self._cache_timestamp.isoformat()
                )
                return self._cache_price
        
        try:
            client = await self._get_http_client()
            
            url = f"{self.BASE_URL}/price/bysymbol/MOR"
            logger.info(f"Fetching MOR price from CoinCap: {url}")
            
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            
            # CoinCap response format: {
            #   "timestamp": 1769689259601,
            #   "data": [
            #     "0.741886878824233653"
            #   ]
            # }
            price_str = data.get("data", [])[0]
            
            if price_str:
                price = Decimal(str(price_str))
                
                # Update cache
                self._cache_price = price
                self._cache_timestamp = datetime.utcnow()
                
                pricing_logger.info(
                    "Fetched MOR price from CoinCap",
                    price_usd=str(price),
                    event_type="mor_price_fetched"
                )
                
                return price
            else:
                pricing_logger.warning(
                    "No price data in CoinCap response",
                    response_data=data
                )
                return None
                
        except httpx.HTTPStatusError as e:
            pricing_logger.error(
                "HTTP error fetching MOR price",
                status_code=e.response.status_code,
                error=str(e)
            )
            # Return cached price if available
            if self._cache_price is not None:
                pricing_logger.info(
                    "Returning stale cached price due to API error",
                    price=str(self._cache_price)
                )
                return self._cache_price
            return None
            
        except Exception as e:
            pricing_logger.error(
                "Error fetching MOR price",
                error=str(e)
            )
            # Return cached price if available
            if self._cache_price is not None:
                return self._cache_price
            return None


class MORPricingService:
    """
    Main service for fetching MOR token prices.
    
    Uses a provider-based architecture to allow easy switching between
    different price sources (CoinCap, CoinGecko, etc.)
    
    Usage:
        service = MORPricingService()
        price = await service.get_price_usd()
    """
    
    def __init__(self, provider: Optional[MORPriceProvider] = None):
        """
        Initialize the pricing service.
        
        Args:
            provider: Price provider to use. Defaults to CoinCapPriceProvider.
        """
        api_key = getattr(settings, 'COINCAP_API_KEY', None)
        self._provider = provider or CoinCapPriceProvider(api_key=api_key)
    
    @property
    def provider(self) -> MORPriceProvider:
        """Get the current price provider."""
        return self._provider
    
    def set_provider(self, provider: MORPriceProvider) -> None:
        """
        Set a new price provider.
        
        Args:
            provider: New price provider to use
        """
        logger.info(
            "Switching MOR price provider",
            old_provider=self._provider.source_name,
            new_provider=provider.source_name,
        )
        self._provider = provider
    
    async def get_price_usd(self) -> Decimal:
        """
        Get the current MOR price in USD from CoinCap.
        
        Returns:
            Decimal price in USD
            
        Raises:
            RuntimeError: If price cannot be fetched from CoinCap
        """
        price = await self._provider.get_mor_price_usd()
        
        if price is None:
            raise RuntimeError("Failed to fetch MOR price from CoinCap API")
        
        return price
    
    async def close(self):
        """Close any open connections."""
        if hasattr(self._provider, 'close'):
            await self._provider.close()


# Global service instance
_mor_pricing_service: Optional[MORPricingService] = None


def get_mor_pricing_service() -> MORPricingService:
    """
    Get the global MOR pricing service instance.
    
    Returns:
        MORPricingService singleton
    """
    global _mor_pricing_service
    if _mor_pricing_service is None:
        _mor_pricing_service = MORPricingService()
    return _mor_pricing_service


def set_mor_pricing_service(service: MORPricingService) -> None:
    """
    Set the global MOR pricing service instance.
    
    Args:
        service: MORPricingService instance to use globally
    """
    global _mor_pricing_service
    _mor_pricing_service = service

