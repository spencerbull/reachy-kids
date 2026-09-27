"""End-to-end run of the real app against the MuJoCo simulator and a fake realtime server.

Start the simulator first (`reachy-mini-daemon --sim --headless`), then:
    REACHY_KIDS_SIM_E2E=1 pytest tests/test_sim_e2e.py -s
"""

import os
import json
import asyncio
import threading
import urllib.request

import pytest
from fake_realtime import FakeRealtimeServer

from reachy_kids.config import Settings, save_settings


DAEMON = "http://127.0.0.1:8000"
UI = "http://127.0.0.1:8042"

pytestmark = pytest.mark.skipif(
    os.getenv("REACHY_KIDS_SIM_E2E") != "1", reason="needs a running simulator; set REACHY_KIDS_SIM_E2E=1"
)


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


async def poll(predicate, timeout: float = 20.0, interval: float = 0.1):
    async def loop():
        while not (value := await asyncio.to_thread(predicate)):
            await asyncio.sleep(interval)
        return value

    return await asyncio.wait_for(loop(), timeout)


async def test_app_talks_listens_and_moves_in_simulator(monkeypatch):
    monkeypatch.setenv("REACHY_KIDS_MEDIA_BACKEND", "local")
    assert get_json(f"{DAEMON}/api/daemon/status")["simulation_enabled"]

    from reachy_kids.main import ReachyKids

    async with FakeRealtimeServer() as server:
        save_settings(Settings(provider="openai", openai_api_key="sk-test", realtime_url=server.url, child_name="Mia"))
        app = ReachyKids()
        thread = threading.Thread(target=app.wrapped_run, name="reachy-kids-app")
        thread.start()
        try:
            update = (await server.wait_for("session.update", timeout=60))[0]
            assert update["session"]["audio"]["input"]["turn_detection"]["eagerness"] == "low"

            # Real microphone audio from the simulator's media pipeline reaches the provider.
            appends = await server.wait_for("input_audio_buffer.append", count=25, timeout=20)
            assert all(a["audio"] for a in appends)

            # The settings UI served by the app reports the live session.
            state = await poll(lambda: get_json(f"{UI}/api/state"))
            assert state["settings"]["openai_api_key"] is True
            assert "sk-test" not in json.dumps(state)

            # Robot speech plays through the SDK speaker path without errors.
            await server.send_audio(1.0, 24000)

            # A tool call from the model moves the simulated robot.
            start_yaw = get_json(f"{DAEMON}/api/state/present_head_pose")["yaw"]
            await server.send(
                {
                    "type": "response.function_call_arguments.done",
                    "call_id": "call_look",
                    "name": "look",
                    "arguments": json.dumps({"direction": "left"}),
                }
            )
            await server.send({"type": "response.done"})
            output = (await server.wait_for("conversation.item.create", timeout=10))[0]
            assert json.loads(output["item"]["output"]) == {"status": "looking left"}

            def head_turned_left() -> bool:
                return get_json(f"{DAEMON}/api/state/present_head_pose")["yaw"] > start_yaw + 0.3

            await poll(head_turned_left, timeout=10)
            await server.wait_for("response.create", count=2, timeout=15)
        finally:
            app.stop()
            await asyncio.to_thread(thread.join, 30)
        assert not thread.is_alive()
        assert not app.error, app.error
