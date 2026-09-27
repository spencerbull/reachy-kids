import json
import stat

import pytest

from reachy_kids.config import Settings, load_settings, save_settings, with_env_overrides


def test_round_trip_is_private(isolated_settings):
    save_settings(Settings(provider="xai", xai_api_key="xai-secret", child_name="Mia", child_age=5))

    assert stat.S_IMODE(isolated_settings.stat().st_mode) == 0o600
    loaded = load_settings()
    assert (loaded.provider, loaded.api_key, loaded.child_name, loaded.child_age) == ("xai", "xai-secret", "Mia", 5)


def test_defaults_to_kids_mode_on_openai():
    settings = load_settings()
    assert settings.kids_mode is True
    assert settings.provider == "openai"
    assert settings.api_key == ""


def test_environment_overrides_stored_values(monkeypatch):
    save_settings(Settings(openai_api_key="stored"))
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    monkeypatch.setenv("REACHY_KIDS_PROVIDER", "xai")

    effective = with_env_overrides(load_settings())

    assert effective.openai_api_key == "from-env"
    assert effective.provider == "xai"
    assert load_settings().openai_api_key == "stored"


@pytest.mark.parametrize(
    "changes",
    [{"provider": "gemini"}, {"child_age": 40}, {"child_age": "5"}, {"kids_mode": "yes"}, {"nope": 1}],
)
def test_invalid_updates_are_rejected(changes):
    with pytest.raises(ValueError):
        Settings().updated(changes)


def test_corrupt_file_falls_back_to_defaults(isolated_settings):
    isolated_settings.write_text("{not json")
    assert load_settings() == Settings()
    isolated_settings.write_text(json.dumps({"provider": "gemini"}))
    assert load_settings() == Settings()


def test_public_dict_hides_keys():
    public = Settings(openai_api_key="sk-secret").public_dict()
    assert public["openai_api_key"] is True
    assert public["xai_api_key"] is False
    assert "sk-secret" not in json.dumps(public)
