"""Keeps a conversation running: waits for an API key, reconnects, restarts on settings changes."""

import time
import asyncio
import logging
import threading
from typing import Any
from pathlib import Path
from collections import deque

from reachy_kids.config import Settings, load_settings, save_settings, with_env_overrides
from reachy_kids.realtime import AudioIO, Connector, ToolRunner, RealtimeConversation, default_connector
from reachy_kids.providers import get_provider


logger = logging.getLogger(__name__)

MAX_BACKOFF_S = 30.0
RETRY_STATES = ("connecting", "reconnecting")


class ConversationRunner:
    """Owns the settings and the reconnect loop; safe to query and update from other threads."""

    def __init__(
        self,
        audio: AudioIO,
        tools: ToolRunner,
        settings_file: Path | None = None,
        connector: Connector = default_connector,
    ) -> None:
        """Load settings from ``settings_file`` (default location when None)."""
        self.audio = audio
        self.tools = tools
        self.settings_file = settings_file
        self.connector = connector
        self._stored = load_settings(settings_file)
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"state": "starting", "provider": "", "error": ""}
        self._transcript: deque[dict[str, str]] = deque(maxlen=50)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._restart: asyncio.Event | None = None

    @property
    def settings(self) -> Settings:
        """Return the effective settings (stored values plus environment overrides)."""
        with self._lock:
            return with_env_overrides(self._stored)

    def update_settings(self, changes: dict[str, Any]) -> Settings:
        """Validate, persist and apply ``changes``; the active session restarts with them."""
        with self._lock:
            # Empty key fields from the UI mean "keep the saved key", never "erase it".
            changes = {k: v for k, v in changes.items() if not (k.endswith("_api_key") and not v)}
            self._stored = self._stored.updated(changes)
            save_settings(self._stored, self.settings_file)
        self.restart()
        return self.settings

    def restart(self) -> None:
        """Ask the loop to drop the current session and start a new one."""
        if self._loop is not None and self._restart is not None:
            self._loop.call_soon_threadsafe(self._restart.set)

    def snapshot(self) -> dict[str, Any]:
        """Return state for the UI: connection state, recent transcript, public settings."""
        with self._lock:
            return {
                **self._state,
                "transcript": list(self._transcript),
                "settings": with_env_overrides(self._stored).public_dict(),
            }

    def _on_event(self, kind: str, payload: dict[str, Any]) -> None:
        with self._lock:
            if kind == "status":
                # Keep the last failure visible while retrying; it clears once a session is up again.
                error = self._state["error"] if payload.get("state") in RETRY_STATES else ""
                self._state.update(payload, error=error)
            elif kind == "transcript" and payload.get("text"):
                self._transcript.append({"role": payload["role"], "text": payload["text"]})
            elif kind == "error":
                self._state["error"] = payload.get("message", "")

    async def run(self, stop: threading.Event) -> None:
        """Run sessions until ``stop`` is set."""
        self._loop = asyncio.get_running_loop()
        self._restart = restart_event = asyncio.Event()

        async def watch_stop() -> None:
            while not stop.is_set():
                await asyncio.sleep(0.2)
            restart_event.set()

        watcher = asyncio.create_task(watch_stop())
        backoff = 1.0
        try:
            while not stop.is_set():
                self._restart.clear()
                settings = self.settings
                if not settings.api_key:
                    label = get_provider(settings.provider).label
                    self._on_event("status", {"state": "needs_setup", "provider": label})
                    await self._restart.wait()
                    continue

                started = time.monotonic()
                conversation = RealtimeConversation(settings, self.audio, self.tools, self._on_event, self.connector)
                session_stop = asyncio.Event()
                session = asyncio.create_task(conversation.run(session_stop))
                restart = asyncio.create_task(self._restart.wait())
                await asyncio.wait({session, restart}, return_when=asyncio.FIRST_COMPLETED)
                if not session.done():
                    session_stop.set()
                    await asyncio.gather(session, return_exceptions=True)
                # Drop speech still queued from the old session, however it ended.
                self.audio.clear()
                restart.cancel()

                error = session.exception() if session.done() and not session.cancelled() else None
                if self._restart.is_set():
                    backoff = 1.0
                    continue
                if time.monotonic() - started > MAX_BACKOFF_S:
                    backoff = 1.0
                if error is not None:
                    logger.warning("Realtime session failed: %s", error)
                    self._on_event("error", {"message": str(error)})
                else:
                    logger.info("Realtime session closed by server; reconnecting")
                self._on_event("status", {"state": "reconnecting"})
                try:
                    await asyncio.wait_for(self._restart.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, MAX_BACKOFF_S)
        finally:
            watcher.cancel()
            self._on_event("status", {"state": "stopped"})
