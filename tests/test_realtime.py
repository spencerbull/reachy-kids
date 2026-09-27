import json
import base64
import asyncio

import numpy as np
import pytest
from fake_realtime import FakeAudio, FakeTools, FakeRealtimeServer

from reachy_kids.config import Settings
from reachy_kids.realtime import RealtimeConversation


async def start(server: FakeRealtimeServer, provider: str = "openai", **settings_kwargs):
    settings = Settings(provider=provider, realtime_url=server.url, openai_api_key="sk-test", xai_api_key="xai-test")
    settings = settings.updated(settings_kwargs)
    audio, tools, events = FakeAudio(), FakeTools(), []
    conversation = RealtimeConversation(settings, audio, tools, lambda kind, payload: events.append((kind, payload)))
    stop = asyncio.Event()
    task = asyncio.create_task(conversation.run(stop))
    await asyncio.wait_for(server.connected.wait(), 5)
    return conversation, audio, tools, events, stop, task


@pytest.mark.parametrize(("provider", "rate", "key"), [("openai", 24000, "sk-test"), ("xai", 16000, "xai-test")])
async def test_session_setup_and_microphone_streaming(provider, rate, key):
    async with FakeRealtimeServer() as server:
        _, _, _, events, stop, task = await start(server, provider)

        update = (await server.wait_for("session.update"))[0]
        greeting = (await server.wait_for("response.create"))[0]
        appends = await server.wait_for("input_audio_buffer.append", count=10)

        assert server.headers[0]["authorization"] == f"Bearer {key}"
        assert "model=" in server.paths[0]
        assert server.received[0]["type"] == "session.update"
        assert update["session"]["audio"]["input"]["format"]["rate"] == rate
        assert "instructions" in greeting["response"]
        samples = sum(len(base64.b64decode(a["audio"])) // 2 for a in appends)
        # Kids front-end + resampling still yields ~40 ms packets at the provider's rate.
        assert samples == pytest.approx(10 * 0.04 * rate, rel=0.35)
        assert any(kind == "status" and payload["state"] == "listening" for kind, payload in events)

        stop.set()
        await asyncio.wait_for(task, 5)


async def test_robot_voice_is_resampled_and_played():
    async with FakeRealtimeServer() as server:
        conversation, audio, _, _, stop, task = await start(server, "openai")
        await server.wait_for("session.update")

        await server.send_audio(0.5, 24000)
        for _ in range(50):
            if audio.played:
                break
            await asyncio.sleep(0.02)

        played = np.concatenate(audio.played)
        assert played.size == pytest.approx(0.5 * audio.output_rate, abs=4)
        assert conversation.robot_speaking
        stop.set()
        await asyncio.wait_for(task, 5)


async def test_tool_calls_run_then_conversation_continues():
    async with FakeRealtimeServer() as server:
        _, _, tools, events, stop, task = await start(server, "xai")
        await server.wait_for("response.create")

        await server.send(
            {
                "type": "response.function_call_arguments.done",
                "call_id": "call_1",
                "name": "express",
                "arguments": json.dumps({"emotion": "happy"}),
            }
        )
        await server.send({"type": "response.function_call_arguments.done", "call_id": "call_2", "name": "dance"})
        await server.send({"type": "response.done"})

        outputs = await server.wait_for("conversation.item.create", count=2)
        creates = await server.wait_for("response.create", count=2)

        assert tools.calls == [("express", {"emotion": "happy"}), ("dance", {})]
        assert {o["item"]["call_id"] for o in outputs} == {"call_1", "call_2"}
        assert json.loads(outputs[0]["item"]["output"]) == {"status": "ok"}
        # The follow-up response is only requested after every tool output was sent.
        assert server.received.index(creates[1]) > server.received.index(outputs[1])
        assert any(kind == "tool" for kind, _ in events)
        stop.set()
        await asyncio.wait_for(task, 5)


async def test_openai_output_item_function_calls_are_handled_once():
    async with FakeRealtimeServer() as server:
        _, _, tools, _, stop, task = await start(server, "openai")
        await server.wait_for("response.create")
        call = {"type": "function_call", "call_id": "c1", "name": "look", "arguments": '{"direction": "left"}'}
        arguments_done = {k: call[k] for k in ("call_id", "name", "arguments")}
        await server.send({"type": "response.function_call_arguments.done", **arguments_done})
        await server.send({"type": "response.output_item.done", "item": call})
        await server.send({"type": "response.done"})

        await server.wait_for("response.create", count=2)
        assert tools.calls == [("look", {"direction": "left"})]
        stop.set()
        await asyncio.wait_for(task, 5)


async def test_child_interrupting_stops_robot_speech():
    async with FakeRealtimeServer() as server:
        conversation, audio, _, events, stop, task = await start(server, "openai")
        await server.wait_for("session.update")
        await server.send_audio(2.0, 24000)
        while not audio.played:
            await asyncio.sleep(0.02)

        await server.send({"type": "input_audio_buffer.speech_started"})
        await server.send({"type": "conversation.item.input_audio_transcription.completed", "transcript": "wait!"})
        for _ in range(50):
            if audio.clears:
                break
            await asyncio.sleep(0.02)

        assert audio.clears == 1
        assert not conversation.robot_speaking
        await asyncio.sleep(0.05)
        assert ("transcript", {"role": "child", "text": "wait!"}) in events
        stop.set()
        await asyncio.wait_for(task, 5)


async def test_server_errors_are_reported_without_closing():
    async with FakeRealtimeServer() as server:
        _, _, _, events, stop, task = await start(server, "openai")
        await server.wait_for("session.update")
        await server.send({"type": "error", "error": {"message": "invalid_api_key"}})
        await asyncio.sleep(0.1)

        assert ("error", {"message": "invalid_api_key"}) in events
        assert not task.done()
        stop.set()
        await asyncio.wait_for(task, 5)


async def test_child_interrupting_during_tool_follow_up_does_not_stall():
    async with FakeRealtimeServer() as server:
        _, audio, _, _, stop, task = await start(server, "openai")
        await server.wait_for("response.create")
        await server.send_audio(8.0, 24000)
        while not audio.played:
            await asyncio.sleep(0.02)

        await server.send({"type": "response.function_call_arguments.done", "call_id": "c1", "name": "dance"})
        await server.send({"type": "response.done"})
        await server.wait_for("conversation.item.create")
        await asyncio.sleep(0.2)
        assert len(server.of_type("response.create")) == 1  # still waiting for Reachy to finish talking

        await server.send({"type": "input_audio_buffer.speech_started"})
        await server.wait_for("response.create", count=2, timeout=1.0)
        stop.set()
        await asyncio.wait_for(task, 5)
