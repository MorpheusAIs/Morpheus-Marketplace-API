"""VIDEO model routing never falls back to a default model.

A clip is quoted, priced and held for the model the caller named, so an
empty, unknown or unresolvable VIDEO model is a 400, never the LLM default.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.core.model_errors import ModelNotFoundError, ModelTypeMismatchError
from src.core.model_routing import ModelRouter

VIDEO_ID = "0x" + "5e" * 32
LLM_ID = "0x" + "bb" * 32
DEFAULT_LLM_ID = "0x" + "cc" * 32

MAPPING = {"seedance-2.5": VIDEO_ID, "some-llm": LLM_ID, "mistral-31-24b": DEFAULT_LLM_ID}
MAPPING_TYPE = {"seedance-2.5": "VIDEO", "some-llm": "LLM", "mistral-31-24b": "LLM"}


def _resolve(name):
    if name == "explodes":
        raise RuntimeError("catalog unavailable")
    return MAPPING.get(name.lower())


@pytest.mark.parametrize(
    "requested, allowlist, expected",
    [
        (None, "", ModelNotFoundError),
        ("", "", ModelNotFoundError),
        ("zz-not-a-video-model", "", ModelNotFoundError),
        ("zz-not-a-video-model", VIDEO_ID, ModelNotFoundError),
        ("explodes", "", ModelNotFoundError),
        ("some-llm", "", ModelTypeMismatchError),
        ("seedance-2.5", "", VIDEO_ID),
    ],
)
async def test_video_model_has_no_default(requested, allowlist, expected):
    router = ModelRouter()
    id_to_name = {v: k for k, v in MAPPING.items()}
    with patch.multiple(
        "src.core.model_routing.direct_model_service",
        resolve_model_id=AsyncMock(side_effect=_resolve),
        get_model_name_from_id=AsyncMock(side_effect=lambda i: id_to_name.get(i)),
        get_model_mapping_type=AsyncMock(return_value=MAPPING_TYPE),
        get_model_mapping=AsyncMock(return_value=MAPPING),
        get_blockchain_ids=AsyncMock(return_value=set(MAPPING.values())),
        suggest_models=AsyncMock(return_value=[]),
    ), patch("src.core.model_routing.settings.SESSION_ALLOWED_MODEL_IDS", allowlist), \
            patch.object(router, "_get_default_model_id", AsyncMock(return_value=DEFAULT_LLM_ID)) as default:
        if isinstance(expected, str):
            assert await router.get_target_model(requested, type="VIDEO") == expected
        else:
            with pytest.raises(expected) as exc:
                await router.get_target_model(requested, type="VIDEO")
            assert exc.value.status_code == 400
        default.assert_not_called()
