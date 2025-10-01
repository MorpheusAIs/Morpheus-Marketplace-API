import pytest
from src.core.model_routing import ModelRouter

@pytest.fixture
def model_router():
    return ModelRouter()

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