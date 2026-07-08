import pytest
from unittest.mock import AsyncMock, patch

from src.core.model_routing import ModelRouter

@pytest.fixture
def model_router():
    return ModelRouter()


EMBED_ID = "0x" + "aa" * 32
LLM_ID = "0x" + "bb" * 32
DEFAULT_LLM_ID = "0x" + "cc" * 32
DEFAULT_EMBED_ID = EMBED_ID


def _patched_model_service():
    """Patch direct_model_service with a small fixed catalog."""
    mapping = {
        "text-embedding-bge-m3": EMBED_ID,
        "some-llm": LLM_ID,
        "mistral-31-24b": DEFAULT_LLM_ID,
    }
    id_to_name = {v: k for k, v in mapping.items()}
    mapping_type = {
        "text-embedding-bge-m3": "EMBEDDING",
        "some-llm": "LLM",
        "mistral-31-24b": "LLM",
    }
    service = patch.multiple(
        "src.core.model_routing.direct_model_service",
        resolve_model_id=AsyncMock(side_effect=lambda m: mapping.get(m.lower())),
        get_model_name_from_id=AsyncMock(side_effect=lambda i: id_to_name.get(i)),
        get_model_mapping_type=AsyncMock(return_value=mapping_type),
        get_model_mapping=AsyncMock(return_value=mapping),
        get_blockchain_ids=AsyncMock(return_value=set(mapping.values())),
    )
    return service


@pytest.mark.asyncio
async def test_chat_request_for_embedding_model_falls_back_to_default_llm(model_router):
    # A chat completion (type="LLM") naming an EMBEDDING model must NOT route
    # to the embedding provider (its backend rejects chat payloads with
    # "Router.aembedding() missing 1 required positional argument: 'input'").
    with _patched_model_service():
        result = await model_router.get_target_model("text-embedding-bge-m3", type="LLM")
    assert result == DEFAULT_LLM_ID


@pytest.mark.asyncio
async def test_embeddings_request_for_llm_model_falls_back_to_default_embeddings(model_router):
    with _patched_model_service():
        result = await model_router.get_target_model("some-llm", type="EMBEDDINGS")
    assert result == DEFAULT_EMBED_ID


@pytest.mark.asyncio
async def test_matching_types_resolve_normally(model_router):
    with _patched_model_service():
        assert await model_router.get_target_model("some-llm", type="LLM") == LLM_ID
        assert await model_router.get_target_model(
            "text-embedding-bge-m3", type="EMBEDDINGS"
        ) == EMBED_ID


@pytest.mark.asyncio
async def test_unlisted_request_type_skips_compatibility_check(model_router):
    # TTS/STT are not typed distinctly in active_models.json - no rule, no block.
    with _patched_model_service():
        assert await model_router.get_target_model("some-llm", type="TTS") == LLM_ID

@pytest.mark.asyncio
async def test_get_target_model_valid_name(model_router):
    # Test getting blockchain ID for valid model name
    result = await model_router.get_target_model("LMR-Hermes-3-Llama-3.1-8B")
    # The model doesn't exist in the live API, so it should return the default fallback
    assert result.startswith("0xdb98b4e067ead72daf1001591e0abd775c4ed9a6d6d207517533e0ead80163c1")  # Should be a valid blockchain ID
    assert len(result) == 66  # 0x + 64 hex characters

@pytest.mark.asyncio
async def test_get_target_model_valid_blockchain_id(model_router):
    # Test validating and returning a valid blockchain ID
    # Use one of the actual blockchain IDs from the live API
    valid_id = "0x34cd811e3e4710103080f363bb698a933a4cf13c5ab834e2c7652cfdd537bd96"
    result = await model_router.get_target_model(valid_id)
    assert result == valid_id

@pytest.mark.asyncio
async def test_get_target_model_invalid_name(model_router):
    # Test graceful handling of invalid model name by returning default
    result = await model_router.get_target_model("invalid-model")
    # Should return default model blockchain ID instead of raising error
    assert result.startswith("0x")  # Should be a valid blockchain ID

@pytest.mark.asyncio
async def test_get_target_model_invalid_blockchain_id(model_router):
    # Test graceful handling of invalid blockchain ID by returning default
    result = await model_router.get_target_model("0xinvalid")
    # Should return default model blockchain ID instead of raising error
    assert result.startswith("0x")  # Should be a valid blockchain ID

@pytest.mark.asyncio
async def test_get_target_model_empty_input(model_router):
    # Test graceful handling of empty input by returning default
    result_none = await model_router.get_target_model(None)
    result_empty = await model_router.get_target_model("")
    # Both should return default model blockchain ID
    assert result_none.startswith("0x")
    assert result_empty.startswith("0x")

@pytest.mark.asyncio
async def test_is_valid_model(model_router):
    # Test model validation
    # Test with actual models from the live API
    assert await model_router.is_valid_model("text-embedding-bge-m3") is True
    assert await model_router.is_valid_model("0x34cd811e3e4710103080f363bb698a933a4cf13c5ab834e2c7652cfdd537bd96") is True
    assert await model_router.is_valid_model("invalid-model") is False
    assert await model_router.is_valid_model("0xinvalid") is False
    assert await model_router.is_valid_model(None) is False
    assert await model_router.is_valid_model("") is False

@pytest.mark.asyncio
async def test_get_available_models(model_router):
    # Test getting available models
    models = await model_router.get_available_models()
    assert isinstance(models, dict)
    assert len(models) > 0  # Should have at least some models
    # Check that all values are valid blockchain IDs
    for name, blockchain_id in models.items():
        assert blockchain_id.startswith("0x")
        assert len(blockchain_id) == 66  # 0x + 64 hex characters

@pytest.mark.asyncio
async def test_get_available_models_immutable(model_router):
    # Test that returned dict is a copy and doesn't affect internal state
    models1 = await model_router.get_available_models()
    models2 = await model_router.get_available_models()
    
    # Modify the returned dict
    if models1:
        first_key = next(iter(models1))
        models1[first_key] = "modified"
    
    # Original should be unchanged
    assert models1 != models2 or len(models1) == 0 