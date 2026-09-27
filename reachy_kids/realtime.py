"""One realtime conversation over a websocket: mic in, voice out, tool calls, barge-in."""

import json
import time
import base64
import asyncio
import logging
from typing import Any, Protocol
from collections.abc import Callable, Awaitable

import numpy as np
from numpy.typing import NDArray
from websockets.asyncio.client import ClientConnection, connect

from reachy_kids.tools import TOOL_SPECS
from reachy_kids.config import Settings
from reachy_kids.prompts import build_greeting
from reachy_kids.listening import (
    FloatAudio,
    StreamResampler,
    KidsAudioFrontEnd,
    float_to_pcm16,
    pcm16_to_float,
    to_mono_float32,
)
from reachy_kids.providers import Provider, connect_url, get_provider, build_session, connect_headers


logger = logging.getLogger(__name__)

# Send microphone audio in ~40 ms packets: small enough for snappy VAD, large enough to keep overhead low.
SEND_CHUNK_S = 0.04
# How long to keep ducking the mic after the robot's last queued sample (room echo tail).
ECHO_TAIL_S = 0.3
MAX_TOOL_WAIT_S = 10.0

EventCallback = Callable[[str, dict[str, Any]], None]


class AudioIO(Protocol):
    """Microphone and speaker used by the session."""

    input_rate: int
    output_rate: int

    def read(self) -> NDArray[np.generic] | None:
        """Return the next microphone chunk, or None if nothing is ready."""
        ...

    def play(self, audio: FloatAudio) -> None:
        """Queue mono float32 audio for playback."""
        ...

    def clear(self) -> None:
        """Drop all queued playback immediately."""
        ...


class ToolRunner(Protocol):
    """Executes a model tool call and returns a JSON-serializable result."""

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run the tool."""
        ...


Connector = Callable[[str, dict[str, str]], Awaitable[ClientConnection]]


async def default_connector(url: str, headers: dict[str, str]) -> ClientConnection:
    """Open the provider websocket."""
    return await connect(url, additional_headers=headers, max_size=None, open_timeout=15)


class RealtimeConversation:
    """Runs a single websocket session until it closes or ``stop`` is set."""

    def __init__(
        self,
        settings: Settings,
        audio: AudioIO,
        tools: ToolRunner,
        on_event: EventCallback,
        connector: Connector = default_connector,
    ) -> None:
        """Prepare audio conversion for the configured provider."""
        self.settings = settings
        self.provider: Provider = get_provider(settings.provider)
        self.audio = audio
        self.tools = tools
        self.on_event = on_event
        self.connector = connector
        self.front_end = KidsAudioFrontEnd(audio.input_rate) if settings.kids_mode else None
        self._to_provider = StreamResampler(audio.input_rate, self.provider.input_rate)
        self._to_robot = StreamResampler(self.provider.output_rate, audio.output_rate)
        self._speaking_until = 0.0
        self._pending_calls: dict[str, tuple[str, str]] = {}
        self._ws: ClientConnection | None = None
        self._background: set[asyncio.Task[None]] = set()

    @property
    def robot_speaking(self) -> bool:
        """Return whether queued robot speech is still playing (plus the echo tail)."""
        return time.monotonic() < self._speaking_until + ECHO_TAIL_S

    async def run(self, stop: asyncio.Event) -> None:
        """Connect, configure the session, and pump audio/events until closed or stopped."""
        self.on_event("status", {"state": "connecting", "provider": self.provider.label})
        url = connect_url(self.provider, self.settings)
        ws = await self.connector(url, connect_headers(self.settings))
        self._ws = ws
        try:
            await self._send(
                {"type": "session.update", "session": build_session(self.provider, self.settings, TOOL_SPECS)}
            )
            await self._send({"type": "response.create", "response": {"instructions": build_greeting(self.settings)}})
            self.on_event("status", {"state": "listening", "provider": self.provider.label})
            tasks = [
                asyncio.create_task(self._pump_microphone(), name="mic"),
                asyncio.create_task(self._receive(ws), name="receive"),
                asyncio.create_task(stop.wait(), name="stop"),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in [*pending, *self._background]:
                task.cancel()
            await asyncio.gather(*pending, *self._background, return_exceptions=True)
            for task in done:
                task.result()  # surface connection errors to the caller
        finally:
            self._ws = None
            await ws.close()

    async def _send(self, event: dict[str, Any]) -> None:
        if self._ws is not None:
            await self._ws.send(json.dumps(event))

    async def _pump_microphone(self) -> None:
        chunk_samples = int(self.provider.input_rate * SEND_CHUNK_S)
        pending: list[FloatAudio] = []
        pending_samples = 0
        while True:
            frame = self.audio.read()
            if frame is None:
                await asyncio.sleep(0.005)
                continue
            mono = to_mono_float32(frame)
            if self.front_end is not None:
                mono = self.front_end.process(mono, robot_speaking=self.robot_speaking)
            converted = self._to_provider.process(mono)
            pending.append(converted)
            pending_samples += converted.size
            if pending_samples >= chunk_samples:
                payload = base64.b64encode(float_to_pcm16(np.concatenate(pending))).decode("ascii")
                pending, pending_samples = [], 0
                await self._send({"type": "input_audio_buffer.append", "audio": payload})
            else:
                await asyncio.sleep(0)

    async def _receive(self, ws: ClientConnection) -> None:
        async for message in ws:
            if isinstance(message, bytes):
                continue
            await self.handle_event(json.loads(message))

    async def handle_event(self, event: dict[str, Any]) -> None:
        """Apply one server event."""
        kind = event.get("type", "")
        if kind in ("response.output_audio.delta", "response.audio.delta"):
            self._play(event.get("delta", ""))
        elif kind == "input_audio_buffer.speech_started":
            if self.robot_speaking:
                logger.info("Barge-in: child started talking, stopping playback")
                self.audio.clear()
                self._speaking_until = 0.0
            self.on_event("status", {"state": "hearing"})
        elif kind == "input_audio_buffer.speech_stopped":
            self.on_event("status", {"state": "thinking"})
        elif kind == "conversation.item.input_audio_transcription.completed":
            self.on_event(
                "transcript",
                {"role": "child" if self.settings.kids_mode else "user", "text": event.get("transcript", "")},
            )
        elif kind in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
            self.on_event("transcript", {"role": "robot", "text": event.get("transcript", "")})
        elif kind == "response.function_call_arguments.done":
            self._pending_calls[event["call_id"]] = (event.get("name", ""), event.get("arguments", "{}"))
        elif kind == "response.output_item.done" and event.get("item", {}).get("type") == "function_call":
            item = event["item"]
            self._pending_calls[item["call_id"]] = (item.get("name", ""), item.get("arguments", "{}"))
        elif kind == "response.created":
            self.on_event("status", {"state": "speaking"})
        elif kind == "response.done":
            # Runs as a task: waiting for playback must not block barge-in events.
            task = asyncio.create_task(self._finish_response())
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        elif kind == "error":
            error = event.get("error", {})
            logger.warning("Realtime API error: %s", error)
            self.on_event("error", {"message": error.get("message", str(error))})

    def _play(self, delta: str) -> None:
        if not delta:
            return
        audio = self._to_robot.process(pcm16_to_float(base64.b64decode(delta)))
        if audio.size == 0:
            return
        self.audio.play(audio)
        now = time.monotonic()
        self._speaking_until = max(now, self._speaking_until) + audio.size / self.audio.output_rate

    async def _finish_response(self) -> None:
        if not self._pending_calls:
            self.on_event("status", {"state": "listening"})
            return
        calls, self._pending_calls = self._pending_calls, {}
        for call_id, (name, raw_arguments) in calls.items():
            try:
                arguments = json.loads(raw_arguments or "{}")
                result = self.tools.call(name, arguments if isinstance(arguments, dict) else {})
            except json.JSONDecodeError as e:
                result = {"error": f"invalid arguments: {e}"}
            logger.info("Tool %s(%s) -> %s", name, raw_arguments, result)
            self.on_event("tool", {"name": name, "arguments": raw_arguments, "result": result})
            await self._send(
                {
                    "type": "conversation.item.create",
                    "item": {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result)},
                }
            )
        # Let the current sentence finish before the follow-up so the two turns don't overlap.
        deadline = time.monotonic() + MAX_TOOL_WAIT_S
        while time.monotonic() < min(self._speaking_until, deadline):
            await asyncio.sleep(0.05)
        await self._send({"type": "response.create"})
