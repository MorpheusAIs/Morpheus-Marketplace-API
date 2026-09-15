import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def test_request_translation_settings_ship_inert():
    from src.core.config import settings

    assert settings.REQUEST_TRANSLATION_MODE == "off"
    assert settings.REQUEST_TRANSLATION_HEADER == "X-Morpheus-Translate"
    assert settings.PROVIDER_API_SPEC_TTL_SECONDS == 600


def test_request_translation_mode_parses_header_and_always():
    from src.core.config import Settings

    with patch.dict(os.environ, {"REQUEST_TRANSLATION_MODE": "header"}):
        assert Settings().REQUEST_TRANSLATION_MODE == "header"
    with patch.dict(os.environ, {"REQUEST_TRANSLATION_MODE": "always"}):
        assert Settings().REQUEST_TRANSLATION_MODE == "always"
    # case-insensitive, trimmed
    with patch.dict(os.environ, {"REQUEST_TRANSLATION_MODE": " Header "}):
        assert Settings().REQUEST_TRANSLATION_MODE == "header"


def test_request_translation_mode_unknown_value_falls_back_to_off_with_warning():
    from src.core.config import Settings

    with patch("src.core.config._config_logger") as mock_logger, \
         patch.dict(os.environ, {"REQUEST_TRANSLATION_MODE": "garbage"}):
        settings_obj = Settings()
    assert settings_obj.REQUEST_TRANSLATION_MODE == "off"
    mock_logger.warning.assert_called_once()


def test_request_translation_header_name_configurable():
    from src.core.config import Settings

    with patch.dict(os.environ, {"REQUEST_TRANSLATION_HEADER": "X-Custom-Translate"}):
        assert Settings().REQUEST_TRANSLATION_HEADER == "X-Custom-Translate"
