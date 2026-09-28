"""Settings for Reachy Kids: persisted JSON file with environment overrides."""

import os
import json
import logging
from typing import Any
from pathlib import Path
from dataclasses import field, asdict, fields, dataclass

from platformdirs import user_config_dir


logger = logging.getLogger(__name__)

PROVIDERS = ("openai", "xai")

# Environment variables win over the settings file so CI, launchers and power users can inject keys.
ENV_OVERRIDES = {
    "provider": "REACHY_KIDS_PROVIDER",
    "openai_api_key": "OPENAI_API_KEY",
    "xai_api_key": "XAI_API_KEY",
    "realtime_url": "REACHY_KIDS_REALTIME_URL",
}


def settings_path() -> Path:
    """Return the settings file location (overridable with REACHY_KIDS_CONFIG)."""
    override = os.getenv("REACHY_KIDS_CONFIG")
    if override:
        return Path(override)
    return Path(user_config_dir("reachy-kids")) / "settings.json"


@dataclass
class Settings:
    """User-facing configuration for a conversation session."""

    provider: str = "openai"
    openai_api_key: str = ""
    xai_api_key: str = ""
    kids_mode: bool = True
    child_name: str = ""
    child_age: int | None = None
    language: str = "en"
    voice: str = ""
    openai_model: str = "gpt-realtime-2.1"
    xai_model: str = "grok-voice-latest"
    # Only for testing against a local/mock server; empty means the provider's official endpoint.
    realtime_url: str = ""
    keyterms: list[str] = field(default_factory=list)

    @property
    def api_key(self) -> str:
        """Return the API key for the active provider."""
        return self.xai_api_key if self.provider == "xai" else self.openai_api_key

    @property
    def model(self) -> str:
        """Return the realtime model for the active provider."""
        return self.xai_model if self.provider == "xai" else self.openai_model

    def validate(self) -> None:
        """Raise ValueError when a field holds an unusable value."""
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "child_age":
                valid = value is None or (isinstance(value, int) and not isinstance(value, bool))
            elif f.name == "keyterms":
                valid = isinstance(value, list) and all(isinstance(t, str) for t in value)
            elif f.name == "kids_mode":
                valid = isinstance(value, bool)
            else:
                valid = isinstance(value, str)
            if not valid:
                raise ValueError(f"invalid value for {f.name}: {value!r}")
        if self.provider not in PROVIDERS:
            raise ValueError(f"provider must be one of {PROVIDERS}, got {self.provider!r}")
        if self.child_age is not None and not 1 <= self.child_age <= 17:
            raise ValueError("child_age must be between 1 and 17")
        if len(self.keyterms) > 100:
            raise ValueError("at most 100 keyterms are supported")

    def public_dict(self) -> dict[str, Any]:
        """Return settings safe to show in the UI: keys are reduced to a set/unset flag."""
        data = asdict(self)
        for key_field in ("openai_api_key", "xai_api_key"):
            data[key_field] = bool(data[key_field])
        return data

    def updated(self, changes: dict[str, Any]) -> "Settings":
        """Return a validated copy with ``changes`` applied; unknown fields are rejected."""
        known = {f.name for f in fields(self)}
        unknown = set(changes) - known
        if unknown:
            raise ValueError(f"unknown settings: {sorted(unknown)}")
        merged = {**asdict(self), **changes}
        updated = Settings(**merged)
        updated.validate()
        return updated


def load_settings(path: Path | None = None) -> Settings:
    """Load the settings file as stored, without environment overrides."""
    path = path or settings_path()
    stored: dict[str, Any] = {}
    if path.exists():
        try:
            stored = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Ignoring unreadable settings file %s: %s", path, e)
    if not isinstance(stored, dict):
        logger.warning("Ignoring settings file %s: expected a JSON object", path)
        return Settings()
    known = {f.name for f in fields(Settings)}
    try:
        return Settings(**{k: v for k, v in stored.items() if k in known}).updated({})
    except (TypeError, ValueError) as e:
        logger.warning("Ignoring invalid settings file %s: %s", path, e)
        return Settings()


def with_env_overrides(settings: Settings) -> Settings:
    """Return the effective settings: environment variables override stored values."""
    changes = {name: os.environ[env] for name, env in ENV_OVERRIDES.items() if os.getenv(env)}
    return settings.updated(changes)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Write settings to disk with owner-only permissions (they contain API keys)."""
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(asdict(settings), f, indent=2)
    os.chmod(path, 0o600)
