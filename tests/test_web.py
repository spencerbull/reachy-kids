from fastapi import FastAPI
from fake_realtime import FakeAudio, FakeTools
from fastapi.testclient import TestClient

from reachy_kids.web import register_routes
from reachy_kids.config import load_settings
from reachy_kids.runner import ConversationRunner


def make_client() -> TestClient:
    app = FastAPI()
    register_routes(app, ConversationRunner(FakeAudio(), FakeTools()))
    return TestClient(app)


def test_settings_round_trip_without_leaking_keys():
    client = make_client()

    response = client.post("/api/settings", json={"provider": "xai", "xai_api_key": "xai-secret", "child_age": 6})

    assert response.status_code == 200
    assert "xai-secret" not in response.text
    assert response.json()["settings"]["xai_api_key"] is True
    assert load_settings().xai_api_key == "xai-secret"
    assert client.get("/api/state").json()["settings"]["child_age"] == 6


def test_invalid_settings_are_rejected():
    client = make_client()
    assert client.post("/api/settings", json={"provider": "gemini"}).status_code == 400
    assert client.post("/api/settings", json={"child_age": "six"}).status_code == 400


def test_providers_list_voices():
    providers = make_client().get("/api/providers").json()
    assert {p["name"] for p in providers} == {"openai", "xai"}
    assert all(p["kids_voice"] in p["voices"] for p in providers)
