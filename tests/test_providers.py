from reachy_kids.tools import TOOL_SPECS
from reachy_kids.config import Settings
from reachy_kids.providers import XAI, OPENAI, connect_url, build_session, resolve_voice


def test_openai_kids_session_listens_patiently():
    settings = Settings(provider="openai", child_name="Mia")
    session = build_session(OPENAI, settings, TOOL_SPECS)

    audio_in = session["audio"]["input"]
    assert session["type"] == "realtime"
    assert audio_in["format"] == {"type": "audio/pcm", "rate": 24000}
    assert audio_in["turn_detection"]["type"] == "semantic_vad"
    assert audio_in["turn_detection"]["eagerness"] == "low"
    assert audio_in["turn_detection"]["interrupt_response"] is True
    assert audio_in["noise_reduction"] == {"type": "far_field"}
    assert "young child" in audio_in["transcription"]["prompt"]
    assert "Mia" in audio_in["transcription"]["prompt"]
    assert session["audio"]["output"]["speed"] < 1.0
    assert session["audio"]["output"]["voice"] == OPENAI.kids_voice
    assert "young child named Mia" in session["instructions"]
    assert [t["name"] for t in session["tools"]] == ["look", "express", "dance"]


def test_xai_kids_session_uses_top_level_vad_and_native_rate():
    settings = Settings(provider="xai", child_name="Leo", keyterms=["dinosaur"])
    session = build_session(XAI, settings, TOOL_SPECS)

    assert "type" not in session
    assert session["turn_detection"] == {
        "type": "server_vad",
        "threshold": 0.5,
        "silence_duration_ms": 1200,
        "prefix_padding_ms": 500,
    }
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 16000}
    assert session["audio"]["input"]["transcription"]["keyterms"] == ["Reachy", "dinosaur", "Leo"]
    assert session["voice"] == XAI.kids_voice


def test_adult_mode_is_snappier_and_not_kid_prompted():
    settings = Settings(kids_mode=False)
    openai = build_session(OPENAI, settings, [])
    xai = build_session(XAI, settings, [])

    assert openai["audio"]["input"]["turn_detection"]["eagerness"] == "auto"
    assert "prompt" not in openai["audio"]["input"]["transcription"]
    assert "young child" not in openai["instructions"]
    assert xai["turn_detection"]["silence_duration_ms"] < 1200
    assert openai["audio"]["output"]["voice"] == OPENAI.default_voice


def test_voice_choice_falls_back_when_unsupported():
    assert resolve_voice(XAI, Settings(voice="Rex")) == "rex"
    assert resolve_voice(XAI, Settings(voice="marin")) == XAI.kids_voice


def test_connect_url_carries_model_and_honors_override():
    assert connect_url(OPENAI, Settings()) == "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1"
    assert connect_url(XAI, Settings(provider="xai")) == "wss://api.x.ai/v1/realtime?model=grok-voice-latest"
    local = Settings(realtime_url="ws://127.0.0.1:9/v1/realtime")
    assert connect_url(OPENAI, local) == "ws://127.0.0.1:9/v1/realtime?model=gpt-realtime-2.1"
