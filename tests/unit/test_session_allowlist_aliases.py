"""Tests for curated allowlist + family aliases."""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.core.model_errors import ModelNotAllowlistedError
from src.core.model_routing import (
    ModelRouter,
    apply_model_alias,
    parse_model_aliases,
    parse_model_id_csv,
)
from src.services.session_routing_service import (
    SessionAllowlistError,
    SessionRoutingService,
)


def test_parse_model_aliases_supports_separators():
    raw = "claude-opus-4.8=Claude Opus 4.7,glm-5->glm-5.2,kimi-k2.5→Kimi K3"
    aliases = parse_model_aliases(raw)
    assert aliases["claude-opus-4.8"] == "Claude Opus 4.7"
    assert aliases["glm-5"] == "glm-5.2"
    assert aliases["kimi-k2.5"] == "Kimi K3"


def test_apply_model_alias_case_insensitive():
    aliases = {"claude opus 4.8": "Claude Opus 4.7", "glm-5:web": "glm-5.2:web"}
    rewritten, key = apply_model_alias("Claude Opus 4.8", aliases)
    assert rewritten == "Claude Opus 4.7"
    assert key == "claude opus 4.8"
    rewritten, key = apply_model_alias("GLM-5:web", aliases)
    assert rewritten == "glm-5.2:web"
    assert key == "glm-5:web"


def test_parse_model_id_csv():
    assert parse_model_id_csv(" 0xa ,0xb, ") == {"0xa", "0xb"}
    assert parse_model_id_csv("") == set()
    assert parse_model_id_csv(None) == set()


@pytest.fixture
def service():
    return SessionRoutingService()


async def test_allowlist_empty_skips(service):
    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_ALLOWED_MODEL_IDS = ""
        await service._ensure_allowlist_allows_open("0xanything")


async def test_allowlist_refuses_unknown(service):
    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_ALLOWED_MODEL_IDS = "0xallowed"
        with pytest.raises(SessionAllowlistError) as exc_info:
            await service._ensure_allowlist_allows_open("0xother")
    assert exc_info.value.model_id == "0xother"
    assert exc_info.value.category == "allowlist"
    assert "nodedocs.mor.org" in exc_info.value.message


async def test_allowlist_allows_listed(service):
    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_ALLOWED_MODEL_IDS = "0xallowed,0xother"
        await service._ensure_allowlist_allows_open("0xallowed")


async def test_router_allowlist_and_alias():
    router = ModelRouter()
    with patch("src.core.model_routing.settings") as mock_settings, patch(
        "src.core.model_routing.direct_model_service"
    ) as mock_dms, patch(
        "src.core.model_routing.apply_model_alias",
        wraps=apply_model_alias,
    ):
        mock_settings.SESSION_ALLOWED_MODEL_IDS = "0xopus47"
        mock_settings.SESSION_MODEL_ALIASES = "claude-opus-4.8=Claude Opus 4.7"
        mock_dms.resolve_model_id = AsyncMock(return_value="0xopus47")
        mock_dms.get_model_name_from_id = AsyncMock(return_value="Claude Opus 4.7")
        mock_dms.get_model_mapping_type = AsyncMock(
            return_value={"claude opus 4.7": "LLM"}
        )

        resolved = await router.get_target_model("claude-opus-4.8", type="LLM")
        assert resolved == "0xopus47"
        mock_dms.resolve_model_id.assert_awaited_once_with("Claude Opus 4.7")


async def test_router_refuses_non_allowlisted():
    router = ModelRouter()
    with patch("src.core.model_routing.settings") as mock_settings, patch(
        "src.core.model_routing.direct_model_service"
    ) as mock_dms:
        mock_settings.SESSION_ALLOWED_MODEL_IDS = "0xallowed"
        mock_settings.SESSION_MODEL_ALIASES = ""
        mock_dms.resolve_model_id = AsyncMock(return_value="0xother")
        mock_dms.get_model_name_from_id = AsyncMock(return_value="other")
        mock_dms.get_model_mapping_type = AsyncMock(return_value={"other": "LLM"})

        with pytest.raises(ModelNotAllowlistedError) as exc_info:
            await router.get_target_model("other", type="LLM")
        assert exc_info.value.resolved_id == "0xother"
        assert exc_info.value.status_code == 503
