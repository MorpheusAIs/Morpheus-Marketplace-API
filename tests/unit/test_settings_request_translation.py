import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def test_request_translation_settings_ship_inert():
    from src.core.config import settings

    assert settings.REQUEST_TRANSLATION_ENABLED is False
    assert settings.PROVIDER_API_SPEC_TTL_SECONDS == 600
