"""Tests for max-bid PPS hard gate (standard lane)."""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services.session_routing_service import (
    SessionPriceGateError,
    SessionRoutingService,
)


@pytest.fixture
def service():
    return SessionRoutingService()


async def test_pps_gate_zero_skips(service):
    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_MAX_BID_PPS_MOR = 0
        mock_settings.SESSION_PREFERRED_MODELS = ""
        mock_settings.SESSION_PREMIUM_MODEL_IDS = ""
        service._get_model_max_price_per_second = AsyncMock(
            side_effect=AssertionError("should not lookup when gate disabled")
        )
        await service._ensure_max_bid_pps_allows_open("0xcold")


async def test_warm_skips_pps_gate(service):
    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_MAX_BID_PPS_MOR = 0.00028764
        mock_settings.SESSION_PREFERRED_MODELS = "0xwarm"
        mock_settings.SESSION_PREMIUM_MODEL_IDS = ""
        service._get_model_max_price_per_second = AsyncMock(
            side_effect=AssertionError("warm skips PPS lookup")
        )
        await service._ensure_max_bid_pps_allows_open("0xwarm")


async def test_premium_skips_pps_gate(service):
    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_MAX_BID_PPS_MOR = 0.00028764
        mock_settings.SESSION_PREFERRED_MODELS = ""
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        service._get_model_max_price_per_second = AsyncMock(
            side_effect=AssertionError("premium skips PPS lookup")
        )
        await service._ensure_max_bid_pps_allows_open("0xprem")


async def test_over_gate_refused(service):
    service._get_model_max_price_per_second = AsyncMock(return_value=0.001)

    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_MAX_BID_PPS_MOR = 0.00028764
        mock_settings.SESSION_PREFERRED_MODELS = ""
        mock_settings.SESSION_PREMIUM_MODEL_IDS = ""
        with pytest.raises(SessionPriceGateError) as exc_info:
            await service._ensure_max_bid_pps_allows_open("0xexpensive")

    err = exc_info.value
    assert err.model_id == "0xexpensive"
    assert err.max_pps == 0.001
    assert err.category == "price_gate"
    assert "too expensive" in err.message
    assert "nodedocs.mor.org" in err.message


async def test_under_gate_allowed(service):
    service._get_model_max_price_per_second = AsyncMock(return_value=0.0001)

    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_MAX_BID_PPS_MOR = 0.00028764
        mock_settings.SESSION_PREFERRED_MODELS = ""
        mock_settings.SESSION_PREMIUM_MODEL_IDS = ""
        await service._ensure_max_bid_pps_allows_open("0xcheap")


async def test_missing_price_fail_open(service):
    service._get_model_max_price_per_second = AsyncMock(return_value=None)

    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_MAX_BID_PPS_MOR = 0.00028764
        mock_settings.SESSION_PREFERRED_MODELS = ""
        mock_settings.SESSION_PREMIUM_MODEL_IDS = ""
        await service._ensure_max_bid_pps_allows_open("0xunknown")
