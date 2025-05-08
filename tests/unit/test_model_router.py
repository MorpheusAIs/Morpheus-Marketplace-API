import pytest
from src.core.model_routing import ModelRouter

@pytest.fixture
def model_router():
    return ModelRouter()

def test_get_target_model_valid_name(model_router):
    # Test getting blockchain ID for valid model name
    assert model_router.get_target_model("claude-3-opus-20240229") == "0x8f9f631f647b318e720ec00e6aaeeaa60ca2c52db9362a292d44f217e66aa04f"
    assert model_router.get_target_model("claude-3-sonnet-20240229") == "0xfe4cc20404f223f336f241fa16748b91e8ff1d54141203b0882b637ead9fef79"
    assert model_router.get_target_model("claude-3-haiku-20240229") == "0x7d9a12c4df8cae8890fa43f2ac2986a472ce5b1c3e49198ed44235e23f333abc"

def test_get_target_model_valid_blockchain_id(model_router):
    # Test validating and returning a valid blockchain ID
    valid_id = "0x8f9f631f647b318e720ec00e6aaeeaa60ca2c52db9362a292d44f217e66aa04f"
    assert model_router.get_target_model(valid_id) == valid_id

def test_get_target_model_invalid_name(model_router):
    # Test error handling for invalid model name
    with pytest.raises(ValueError, match="Unknown model name: invalid-model"):
        model_router.get_target_model("invalid-model")

def test_get_target_model_invalid_blockchain_id(model_router):
    # Test error handling for invalid blockchain ID
    with pytest.raises(ValueError, match="Invalid blockchain ID: 0xinvalid"):
        model_router.get_target_model("0xinvalid")

def test_get_target_model_empty_input(model_router):
    # Test error handling for empty input
    with pytest.raises(ValueError, match="No model specified"):
        model_router.get_target_model(None)
    with pytest.raises(ValueError, match="No model specified"):
        model_router.get_target_model("")

def test_is_valid_model(model_router):
    # Test model validation
    assert model_router.is_valid_model("claude-3-opus-20240229") is True
    assert model_router.is_valid_model("0x8f9f631f647b318e720ec00e6aaeeaa60ca2c52db9362a292d44f217e66aa04f") is True
    assert model_router.is_valid_model("invalid-model") is False
    assert model_router.is_valid_model("0xinvalid") is False
    assert model_router.is_valid_model("") is False
    assert model_router.is_valid_model(None) is False

def test_get_available_models(model_router):
    # Test getting available models
    models = model_router.get_available_models()
    assert isinstance(models, dict)
    assert len(models) == 3
    assert "claude-3-opus-20240229" in models
    assert "claude-3-sonnet-20240229" in models
    assert "claude-3-haiku-20240229" in models
    assert models["claude-3-opus-20240229"] == "0x8f9f631f647b318e720ec00e6aaeeaa60ca2c52db9362a292d44f217e66aa04f"

def test_get_available_models_immutable(model_router):
    # Test that get_available_models returns a copy
    models = model_router.get_available_models()
    models["new-model"] = "0xnew"
    assert "new-model" not in model_router.get_available_models() 