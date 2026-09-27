import pytest

from reachy_kids.config import ENV_OVERRIDES


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep tests away from the real settings file and any keys in the developer's environment."""
    monkeypatch.setenv("REACHY_KIDS_CONFIG", str(tmp_path / "settings.json"))
    for env_var in ENV_OVERRIDES.values():
        monkeypatch.delenv(env_var, raising=False)
    return tmp_path / "settings.json"
