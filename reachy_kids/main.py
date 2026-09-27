"""Reachy Mini app entry point: wires the robot's mic, speaker and motors to the conversation."""

import os
import asyncio
import logging
import threading

import numpy as np
from reachy_mini import ReachyMini, ReachyMiniApp
from numpy.typing import NDArray

from reachy_kids.web import register_routes
from reachy_kids.tools import RobotTools
from reachy_kids.runner import ConversationRunner
from reachy_kids.listening import FloatAudio


logger = logging.getLogger(__name__)

UI_PORT = int(os.getenv("REACHY_KIDS_PORT", "8042"))
# The settings page can change API keys and has no login. The CLI keeps it on this computer; when the robot's
# dashboard starts the app (e.g. on a Wireless robot) it stays reachable from the local network like other apps.
UI_HOST = os.getenv("REACHY_KIDS_HOST", "0.0.0.0")


class RobotAudio:
    """Adapts the SDK media manager to the conversation's audio interface."""

    def __init__(self, robot: ReachyMini) -> None:
        """Start the robot's microphone and speaker."""
        self.media = robot.media
        self.media.start_recording()
        self.media.start_playing()
        self.input_rate = self.media.get_input_audio_samplerate()
        self.output_rate = self.media.get_output_audio_samplerate()

    def read(self) -> NDArray[np.generic] | None:
        """Return the next microphone chunk, if any."""
        sample: NDArray[np.generic] | None = self.media.get_audio_sample()
        return sample

    def play(self, audio: FloatAudio) -> None:
        """Queue speech for the speaker (also drives the head wobble)."""
        self.media.push_audio_sample(audio)

    def clear(self) -> None:
        """Flush queued speech on barge-in."""
        if self.media.audio is not None:
            self.media.audio.clear_player()

    def close(self) -> None:
        """Stop the microphone and speaker."""
        self.media.stop_recording()
        self.media.stop_playing()


class ReachyKids(ReachyMiniApp):  # type: ignore[misc]
    """Voice conversation app with a kids mode, for OpenAI Realtime or xAI Grok Voice."""

    custom_app_url: str | None = f"http://{UI_HOST}:{UI_PORT}"
    # "local" is needed in the MuJoCo simulator, which has no media server; unset means SDK auto-detect.
    request_media_backend: str | None = os.getenv("REACHY_KIDS_MEDIA_BACKEND") or None

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run the conversation until the daemon (or Ctrl+C) stops the app."""
        audio = RobotAudio(reachy_mini)
        tools = RobotTools(reachy_mini)
        runner = ConversationRunner(audio, tools)
        if self.settings_app is not None:
            register_routes(self.settings_app, runner)
            logger.info("Settings page: http://localhost:%d", UI_PORT)
        reachy_mini.enable_wobbling()
        try:
            asyncio.run(runner.run(stop_event))
        finally:
            reachy_mini.disable_wobbling()
            tools.close()
            audio.close()


def main() -> None:
    """Run the app standalone against a running daemon (the CLI adds sim/launcher conveniences)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = ReachyKids()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()


if __name__ == "__main__":
    main()
