"""Unit tests for active.mor.org healthy bidId extraction."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.core.direct_model_service import DirectModelService


@pytest.mark.asyncio
async def test_get_healthy_bid_ids_filters_status():
    svc = DirectModelService()
    svc._cache_expiry = __import__("datetime").datetime.now() + __import__(
        "datetime"
    ).timedelta(hours=1)
    svc._raw_models_data = [
        {
            "Id": "0xMODEL",
            "Name": "demo",
            "bidDetail": [
                {"bidId": "0xAAA", "status": "healthy"},
                {"bidId": "0xBBB", "status": "unhealthy"},
                {"bidId": "0xCCC", "status": "Healthy"},
                {"bidId": "0xDDD", "status": "degraded"},
            ],
        }
    ]
    got = await svc.get_healthy_bid_ids_for_model("0xmodel")
    assert got == {"0xaaa", "0xccc"}


@pytest.mark.asyncio
async def test_get_healthy_bid_ids_missing_model():
    svc = DirectModelService()
    svc._cache_expiry = __import__("datetime").datetime.now() + __import__(
        "datetime"
    ).timedelta(hours=1)
    svc._raw_models_data = [{"Id": "0xother", "bidDetail": [{"bidId": "0xaaa", "status": "healthy"}]}]
    assert await svc.get_healthy_bid_ids_for_model("0xmissing") == set()
