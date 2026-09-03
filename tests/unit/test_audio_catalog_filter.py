"""TTS/STT models must not appear in the public model catalog."""

from src.core.model_types import is_audio_model


def test_whisper_tags_are_audio():
    assert is_audio_model(
        tags=["btbf", "transcribe", "s2t", "speech"],
        model_type="UNKNOWN",
        model_name="Whisper-1",
    )


def test_tts_model_type_is_audio():
    assert is_audio_model(tags=[], model_type="TTS", model_name="tts-kokoro")


def test_stt_model_type_is_audio():
    assert is_audio_model(tags=[], model_type="STT", model_name="whisper-1")


def test_llm_is_not_audio():
    assert not is_audio_model(
        tags=["textgeneration"],
        model_type="LLM",
        model_name="glm-5",
    )


def test_embeddings_is_not_audio():
    assert not is_audio_model(
        tags=["Embeddings"],
        model_type="EMBEDDING",
        model_name="text-embedding-bge-m3",
    )
