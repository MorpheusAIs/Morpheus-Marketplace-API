import pytest
from src.core.model_routing import ModelRouter

@pytest.fixture
def model_router():
    return ModelRouter()

def test_get_target_model_valid_name(model_router):
    # Test getting blockchain ID for valid model name
    result = model_router.get_target_model("llama-3.3-70b")
    assert result == "0xdf474728f624712570170f311a866a6937436c14861568f38593a531b7f45845"

def test_get_target_model_valid_blockchain_id(model_router):
    # Test validating and returning a valid blockchain ID
    valid_id = "0xdf474728f624712570170f311a866a6937436c14861568f38593a531b7f45845"
    assert model_router.get_target_model(valid_id) == valid_id

def test_get_target_model_invalid_name(model_router):
    # Test graceful handling of invalid model name by returning default
    result = model_router.get_target_model("invalid-model")
    # Should return default model blockchain ID instead of raising error
    assert result.startswith("0x")  # Should be a valid blockchain ID

def test_get_target_model_invalid_blockchain_id(model_router):
    # Test graceful handling of invalid blockchain ID by returning default
    result = model_router.get_target_model("0xinvalid")
    # Should return default model blockchain ID instead of raising error
    assert result.startswith("0x")  # Should be a valid blockchain ID

def test_get_target_model_empty_input(model_router):
    # Test graceful handling of empty input by returning default
    result_none = model_router.get_target_model(None)
    result_empty = model_router.get_target_model("")
    # Both should return default model blockchain ID
    assert result_none.startswith("0x")
    assert result_empty.startswith("0x")

def test_is_valid_model(model_router):
    # Test model validation
    assert model_router.is_valid_model("llama-3.3-70b") is True
    assert model_router.is_valid_model("0xdf474728f624712570170f311a866a6937436c14861568f38593a531b7f45845") is True
    assert model_router.is_valid_model("invalid-model") is False
    assert model_router.is_valid_model("0xinvalid") is False
    assert model_router.is_valid_model(None) is False
    assert model_router.is_valid_model("") is False

def test_get_available_models(model_router):
    # Test getting available models
    models = model_router.get_available_models()
    assert isinstance(models, dict)
    assert len(models) > 0  # Should have at least some models
    # Check that all values are valid blockchain IDs
    for name, blockchain_id in models.items():
        assert blockchain_id.startswith("0x")
        assert len(blockchain_id) == 66  # 0x + 64 hex characters

def test_get_available_models_immutable(model_router):
    # Test that returned dict is a copy and doesn't affect internal state
    models1 = model_router.get_available_models()
    models2 = model_router.get_available_models()
    
    # Modify the returned dict
    if models1:
        first_key = next(iter(models1))
        models1[first_key] = "modified"
    
    # Original should be unchanged
    assert models1 != models2 or len(models1) == 0 