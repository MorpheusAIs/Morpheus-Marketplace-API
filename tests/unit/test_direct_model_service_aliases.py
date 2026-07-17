"""Unit tests for catalog / veniceId / slug model-name aliases."""

import pytest

from src.core.direct_model_service import (
    DirectModelService,
    _alias_candidates,
    catalog_name_slug,
    suggest_near_miss_models,
)


SONNET_ID = "0x" + "11" * 32
OPUS_ID = "0x" + "22" * 32
LUNA_ID = "0x" + "33" * 32
DEEPSEEK_SPACED_ID = "0x" + "44" * 32
DEEPSEEK_KEBAB_ID = "0x" + "55" * 32
GLM_ID = "0x" + "66" * 32


def _svc_with_models(models):
    svc = DirectModelService(cache_duration_seconds=300)
    svc._update_cache(models, content_hash="testhash", etag=None)
    return svc


class TestCatalogNameSlug:
    def test_spaced_title_case(self):
        assert catalog_name_slug("Claude Sonnet 5") == "claude-sonnet-5"
        assert catalog_name_slug("Claude Opus 4.5") == "claude-opus-4.5"
        assert catalog_name_slug("GPT-5.6 Luna") == "gpt-5.6-luna"
        assert catalog_name_slug("Llama 3.2 3B") == "llama-3.2-3b"

    def test_already_kebab(self):
        assert catalog_name_slug("glm-5.1") == "glm-5.1"

    def test_web_suffix_preserved(self):
        assert catalog_name_slug("glm-5.1:web") == "glm-5.1:web"


class TestAliasCandidates:
    def test_venice_and_slug(self):
        aliases = _alias_candidates(
            "Claude Sonnet 5",
            {"veniceId": "claude-sonnet-5", "capability": "code"},
        )
        assert aliases == {"claude-sonnet-5"}

    def test_distinct_venice_and_slug(self):
        aliases = _alias_candidates(
            "Claude Opus 4.5",
            {"veniceId": "claude-opus-4-5"},
        )
        assert aliases == {"claude-opus-4.5", "claude-opus-4-5"}

    def test_no_alias_when_name_already_kebab(self):
        aliases = _alias_candidates("llama-3.2-3b", {"veniceId": "llama-3.2-3b"})
        assert aliases == set()


@pytest.mark.asyncio
async def test_resolves_catalog_venice_and_slug():
    svc = _svc_with_models(
        [
            {
                "Name": "Claude Sonnet 5",
                "Id": SONNET_ID,
                "ModelType": "LLM",
                "enrichment": {"veniceId": "claude-sonnet-5", "capability": "code"},
            },
            {
                "Name": "Claude Opus 4.5",
                "Id": OPUS_ID,
                "ModelType": "LLM",
                "enrichment": {"veniceId": "claude-opus-4-5", "capability": "code"},
            },
            {
                "Name": "GPT-5.6 Luna",
                "Id": LUNA_ID,
                "ModelType": "LLM",
                "enrichment": {"veniceId": "openai-gpt-56-luna", "capability": "chat"},
            },
            {
                "Name": "glm-5.1",
                "Id": GLM_ID,
                "ModelType": "LLM",
                "enrichment": {"veniceId": "zai-org-glm-5-1"},
            },
        ]
    )

    # Catalog names
    assert await svc.resolve_model_id("Claude Sonnet 5") == SONNET_ID
    assert await svc.resolve_model_id("Claude Opus 4.5") == OPUS_ID
    assert await svc.resolve_model_id("GPT-5.6 Luna") == LUNA_ID

    # Catalog slug (client kebab guess)
    assert await svc.resolve_model_id("claude-sonnet-5") == SONNET_ID
    assert await svc.resolve_model_id("claude-opus-4.5") == OPUS_ID
    assert await svc.resolve_model_id("gpt-5.6-luna") == LUNA_ID

    # Venice ids that differ from slug
    assert await svc.resolve_model_id("claude-opus-4-5") == OPUS_ID
    assert await svc.resolve_model_id("openai-gpt-56-luna") == LUNA_ID
    assert await svc.resolve_model_id("zai-org-glm-5-1") == GLM_ID

    # Reverse lookup always returns catalog Name
    assert await svc.get_model_name_from_id(SONNET_ID) == "Claude Sonnet 5"
    assert await svc.get_model_name_from_id(OPUS_ID) == "Claude Opus 4.5"


@pytest.mark.asyncio
async def test_ambiguous_slug_not_registered_when_catalog_twin_exists():
    """Spaced + kebab twins: slug of spaced name collides with kebab catalog name."""
    svc = _svc_with_models(
        [
            {
                "Name": "DeepSeek V4 Flash",
                "Id": DEEPSEEK_SPACED_ID,
                "ModelType": "UNKNOWN",
                "enrichment": {"veniceId": "deepseek-v4-flash", "capability": "chat"},
            },
            {
                "Name": "deepseek-v4-flash",
                "Id": DEEPSEEK_KEBAB_ID,
                "ModelType": "LLM",
                "enrichment": {"veniceId": "deepseek-v4-flash", "capability": "chat"},
            },
        ]
    )

    # Catalog names always win
    assert await svc.resolve_model_id("DeepSeek V4 Flash") == DEEPSEEK_SPACED_ID
    assert await svc.resolve_model_id("deepseek-v4-flash") == DEEPSEEK_KEBAB_ID

    # Ambiguous veniceId claimed by both → not used as override (kebab catalog kept)
    assert await svc.resolve_model_id("deepseek-v4-flash") == DEEPSEEK_KEBAB_ID


LLAMA_KEBAB_ID = "0x" + "77" * 32
QWEN_ID = "0x" + "88" * 32


@pytest.mark.asyncio
async def test_request_side_slug_resolves_spaced_title_case_to_kebab_catalog():
    """Clients send 'Llama 3.2 3B'; catalog Name is already kebab."""
    svc = _svc_with_models(
        [
            {
                "Name": "llama-3.2-3b",
                "Id": LLAMA_KEBAB_ID,
                "ModelType": "LLM",
                "enrichment": {"veniceId": "llama-3.2-3b"},
            },
        ]
    )
    assert await svc.resolve_model_id("Llama 3.2 3B") == LLAMA_KEBAB_ID
    assert await svc.resolve_model_id("llama 3.2 3b") == LLAMA_KEBAB_ID
    assert await svc.resolve_model_id("llama-3.2-3b") == LLAMA_KEBAB_ID


def test_suggest_near_miss_strips_turbo_suffix():
    mapping = {"qwen3-coder-480b-a35b-instruct": QWEN_ID}
    id_to_name = {QWEN_ID: "qwen3-coder-480b-a35b-instruct"}
    suggestions = suggest_near_miss_models(
        "qwen3-coder-480b-a35b-instruct-turbo",
        set(mapping.keys()),
        id_to_name,
        mapping,
    )
    assert suggestions == ["qwen3-coder-480b-a35b-instruct"]
