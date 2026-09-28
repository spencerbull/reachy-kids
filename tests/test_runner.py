import asyncio
import threading

from fake_realtime import FakeAudio, FakeTools, FakeRealtimeServer

from reachy_kids.config import load_settings
from reachy_kids.runner import ConversationRunner


async def wait_for_state(runner: ConversationRunner, state: str, timeout: float = 5.0) -> None:
    async def poll() -> None:
        while runner.snapshot()["state"] != state:
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout)


async def test_waits_for_a_key_then_connects_and_restarts_on_change():
    async with FakeRealtimeServer() as server:
        runner = ConversationRunner(FakeAudio(), FakeTools())
        stop = threading.Event()
        task = asyncio.create_task(runner.run(stop))

        await wait_for_state(runner, "needs_setup")
        assert not server.connected.is_set()

        await asyncio.to_thread(runner.update_settings, {"openai_api_key": "sk-1", "realtime_url": server.url})
        await server.wait_for("session.update")
        await wait_for_state(runner, "listening")

        await asyncio.to_thread(runner.update_settings, {"provider": "xai", "xai_api_key": "xai-1"})
        await server.wait_for("session.update", count=2)
        assert len(server.headers) == 2
        assert server.headers[1]["authorization"] == "Bearer xai-1"
        assert "grok-voice" in server.paths[1]

        stop.set()
        await asyncio.wait_for(task, 5)
        assert runner.snapshot()["state"] == "stopped"


async def test_reconnects_after_the_server_drops():
    async with FakeRealtimeServer() as server:
        audio = FakeAudio()
        runner = ConversationRunner(audio, FakeTools())
        runner.update_settings({"openai_api_key": "sk-1", "realtime_url": server.url})
        stop = threading.Event()
        task = asyncio.create_task(runner.run(stop))

        await server.wait_for("session.update")
        clears_before = audio.clears
        await server.connection.close()
        await server.wait_for("session.update", count=2, timeout=10)
        # Speech queued by the dropped session must not keep playing into the new one.
        assert audio.clears > clears_before

        stop.set()
        await asyncio.wait_for(task, 5)


def test_blank_key_from_ui_keeps_saved_key_and_env_keys_are_not_persisted(monkeypatch):
    runner = ConversationRunner(FakeAudio(), FakeTools())
    runner.update_settings({"openai_api_key": "sk-saved"})
    runner.update_settings({"openai_api_key": "", "child_name": "Mia"})
    assert load_settings().openai_api_key == "sk-saved"

    monkeypatch.setenv("XAI_API_KEY", "xai-env")
    runner.update_settings({"provider": "xai"})
    assert runner.settings.api_key == "xai-env"
    assert load_settings().xai_api_key == ""
    assert runner.snapshot()["settings"]["xai_api_key"] is True


async def test_connection_errors_stay_visible_while_retrying():
    runner = ConversationRunner(FakeAudio(), FakeTools())
    runner.update_settings({"openai_api_key": "sk-1", "realtime_url": "ws://127.0.0.1:9/v1/realtime"})
    stop = threading.Event()
    task = asyncio.create_task(runner.run(stop))

    async def failed_twice() -> None:
        # Past the first retry, the next attempt's "connecting" status must not wipe the error.
        while not (runner.snapshot()["error"] and runner.snapshot()["state"] in ("connecting", "reconnecting")):
            await asyncio.sleep(0.02)
        await asyncio.sleep(1.5)

    await asyncio.wait_for(failed_twice(), 10)
    assert runner.snapshot()["error"]

    stop.set()
    await asyncio.wait_for(task, 5)
