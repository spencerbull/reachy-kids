"""Realtime speech-to-speech providers: OpenAI Realtime and xAI Grok Voice.

Both speak the same websocket event protocol; they differ in endpoint, audio rates and the shape of
``session.update``.
"""

from typing import Any
from dataclasses import dataclass
from urllib.parse import urlencode

from reachy_kids.config import Settings
from reachy_kids.prompts import build_instructions
from reachy_kids.listening import KIDS_LISTENING, ADULT_LISTENING, ListeningProfile


@dataclass(frozen=True)
class Provider:
    """Static facts about a realtime provider."""

    name: str
    label: str
    url: str
    voices: tuple[str, ...]
    default_voice: str
    kids_voice: str
    input_rate: int
    output_rate: int


OPENAI = Provider(
    name="openai",
    label="OpenAI Realtime",
    url="wss://api.openai.com/v1/realtime",
    voices=("marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"),
    default_voice="marin",
    kids_voice="coral",
    # OpenAI realtime PCM is 24 kHz only.
    input_rate=24000,
    output_rate=24000,
)

XAI = Provider(
    name="xai",
    label="xAI Grok Voice",
    url="wss://api.x.ai/v1/realtime",
    voices=("eve", "ara", "rex", "sal", "leo"),
    default_voice="eve",
    kids_voice="ara",
    # Grok accepts the robot's native 16 kHz directly, which avoids resampling.
    input_rate=16000,
    output_rate=16000,
)

PROVIDERS = {p.name: p for p in (OPENAI, XAI)}


def get_provider(name: str) -> Provider:
    """Return the provider called ``name``."""
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(f"unknown provider {name!r}; expected one of {sorted(PROVIDERS)}") from None


def listening_profile(settings: Settings) -> ListeningProfile:
    """Return the turn-taking profile for the configured mode."""
    return KIDS_LISTENING if settings.kids_mode else ADULT_LISTENING


def resolve_voice(provider: Provider, settings: Settings) -> str:
    """Return the configured voice if the provider supports it, else the mode default."""
    if settings.voice.lower() in provider.voices:
        return settings.voice.lower()
    return provider.kids_voice if settings.kids_mode else provider.default_voice


def connect_url(provider: Provider, settings: Settings) -> str:
    """Return the websocket URL including the model query parameter."""
    base = settings.realtime_url or provider.url
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode({'model': settings.model})}"


def connect_headers(settings: Settings) -> dict[str, str]:
    """Return the auth headers for the websocket handshake."""
    return {"Authorization": f"Bearer {settings.api_key}"}


def keyterms(settings: Settings) -> list[str]:
    """Return words the recognizer should expect: the robot's name, the child's name, custom terms."""
    terms = ["Reachy", *settings.keyterms]
    if settings.child_name.strip():
        terms.append(settings.child_name.strip())
    return list(dict.fromkeys(t[:50] for t in terms if t))[:100]


def build_session(provider: Provider, settings: Settings, tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the ``session`` object for ``session.update``."""
    profile = listening_profile(settings)
    instructions = build_instructions(settings)
    voice = resolve_voice(provider, settings)

    if provider.name == "openai":
        transcription: dict[str, Any] = {"model": "gpt-4o-transcribe", "language": settings.language}
        prompt = profile.transcription_prompt
        if settings.child_name.strip():
            prompt = f"{prompt} The child's name is {settings.child_name.strip()}.".strip()
        if prompt:
            transcription["prompt"] = prompt
        turn_detection: dict[str, Any] = {
            "type": "semantic_vad",
            "eagerness": profile.semantic_eagerness,
            "create_response": True,
            "interrupt_response": True,
        }
        return {
            "type": "realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": provider.input_rate},
                    "noise_reduction": {"type": profile.noise_reduction},
                    "transcription": transcription,
                    "turn_detection": turn_detection,
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": provider.output_rate},
                    "voice": voice,
                    "speed": profile.speech_speed,
                },
            },
            "tools": tools,
            "tool_choice": "auto",
        }

    return {
        "instructions": instructions,
        "voice": voice,
        "turn_detection": {
            "type": "server_vad",
            "threshold": profile.vad_threshold,
            "silence_duration_ms": profile.silence_duration_ms,
            "prefix_padding_ms": profile.prefix_padding_ms,
        },
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": provider.input_rate},
                "transcription": {"language_hint": settings.language, "keyterms": keyterms(settings)},
            },
            "output": {
                "format": {"type": "audio/pcm", "rate": provider.output_rate},
                "speed": profile.speech_speed,
            },
        },
        "tools": tools,
    }
