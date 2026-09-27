"""A scriptable stand-in for the OpenAI/xAI realtime websocket, plus fake robot audio and tools."""

import json
import time
import base64
import asyncio
from typing import Any

import numpy as np
from websockets.asyncio.server import Server, ServerConnection, serve


class FakeRealtimeServer:
    """Records client events and lets a test push server events."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.paths: list[str] = []
        self.connection: ServerConnection | None = None
        self.connected = asyncio.Event()
        self._server: Server | None = None
        self._new_event = asyncio.Event()

    @property
    def url(self) -> str:
        assert self._server is not None
        port = next(iter(self._server.sockets)).getsockname()[1]
        return f"ws://127.0.0.1:{port}/v1/realtime"

    async def __aenter__(self) -> "FakeRealtimeServer":
        self._server = await serve(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, connection: ServerConnection) -> None:
        self.connection = connection
        assert connection.request is not None
        self.paths.append(connection.request.path)
        self.headers.append(dict(connection.request.headers))
        self.connected.set()
        await connection.send(json.dumps({"type": "session.created"}))
        async for message in connection:
            event = json.loads(message)
            self.received.append(event)
            self._new_event.set()

    async def send(self, event: dict[str, Any]) -> None:
        assert self.connection is not None
        await self.connection.send(json.dumps(event))

    async def send_audio(self, seconds: float, rate: int, frequency: float = 440.0) -> None:
        t = np.arange(int(seconds * rate)) / rate
        pcm = (0.3 * np.sin(2 * np.pi * frequency * t) * 32767).astype("<i2").tobytes()
        await self.send({"type": "response.output_audio.delta", "delta": base64.b64encode(pcm).decode()})

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.received if e.get("type") == kind]

    async def wait_for(self, kind: str, count: int = 1, timeout: float = 5.0) -> list[dict[str, Any]]:
        async def poll() -> list[dict[str, Any]]:
            while len(self.of_type(kind)) < count:
                self._new_event.clear()
                await self._new_event.wait()
            return self.of_type(kind)

        return await asyncio.wait_for(poll(), timeout)


class FakeAudio:
    """Microphone that produces a quiet tone; speaker that records what it was asked to play."""

    def __init__(self, input_rate: int = 16000, output_rate: int = 16000) -> None:
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.played: list[np.ndarray] = []
        self.clears = 0
        self._phase = 0
        self._started = time.monotonic()

    def read(self) -> np.ndarray | None:
        n = 160
        # Deliver audio in real time, like a microphone.
        if self._phase + n > (time.monotonic() - self._started) * self.input_rate:
            return None
        t = (np.arange(n) + self._phase) / self.input_rate
        self._phase += n
        tone = (0.01 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
        return np.stack([tone, tone], axis=1)

    def play(self, audio: np.ndarray) -> None:
        self.played.append(audio)

    def clear(self) -> None:
        self.clears += 1


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return {"status": "ok"}
