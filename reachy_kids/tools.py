"""Robot moves the model can trigger while it talks."""

import logging
from typing import Any, Protocol
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np
from numpy.typing import NDArray
from reachy_mini.utils import create_head_pose


logger = logging.getLogger(__name__)

# (head pose kwargs in degrees, antennas [right, left] in degrees, duration in seconds)
Keyframe = tuple[dict[str, float], tuple[float, float], float]

NEUTRAL: Keyframe = ({}, (0.0, 0.0), 0.6)

LOOKS: dict[str, dict[str, float]] = {
    "left": {"yaw": 35.0},
    "right": {"yaw": -35.0},
    "up": {"pitch": -20.0},
    "down": {"pitch": 20.0},
    "center": {},
}

EXPRESSIONS: dict[str, list[Keyframe]] = {
    "happy": [({"roll": 8.0}, (40.0, -40.0), 0.3), ({"roll": -8.0}, (-40.0, 40.0), 0.3), NEUTRAL],
    "excited": [({"pitch": -10.0}, (70.0, -70.0), 0.25), ({"pitch": 5.0}, (-70.0, 70.0), 0.25)] * 2 + [NEUTRAL],
    "curious": [({"roll": 18.0, "pitch": -5.0}, (30.0, 10.0), 0.6), NEUTRAL],
    "surprised": [({"pitch": -15.0, "z": 10.0}, (80.0, -80.0), 0.25), NEUTRAL],
    "sad": [({"pitch": 20.0, "z": -5.0}, (-60.0, 60.0), 1.0), NEUTRAL],
    "sleepy": [({"pitch": 15.0, "roll": 10.0}, (-45.0, 45.0), 1.4), NEUTRAL],
    "silly": [({"roll": 20.0, "yaw": 15.0}, (80.0, 20.0), 0.35), ({"roll": -20.0, "yaw": -15.0}, (20.0, -80.0), 0.35)]
    + [NEUTRAL],
}

DANCE: list[Keyframe] = [
    ({"yaw": 20.0, "roll": 10.0}, (60.0, -20.0), 0.35),
    ({"yaw": -20.0, "roll": -10.0}, (-20.0, 60.0), 0.35),
] * 3 + [({"pitch": -12.0, "z": 8.0}, (80.0, -80.0), 0.3), NEUTRAL]

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "look",
        "description": "Turn your head to look in a direction, e.g. toward something the child mentions.",
        "parameters": {
            "type": "object",
            "properties": {"direction": {"type": "string", "enum": list(LOOKS)}},
            "required": ["direction"],
        },
    },
    {
        "type": "function",
        "name": "express",
        "description": "Show a feeling with your head and antennas. Use it often; kids love it.",
        "parameters": {
            "type": "object",
            "properties": {"emotion": {"type": "string", "enum": list(EXPRESSIONS)}},
            "required": ["emotion"],
        },
    },
    {
        "type": "function",
        "name": "dance",
        "description": "Do a short happy dance, for celebrations or when asked to dance.",
        "parameters": {"type": "object", "properties": {}},
    },
]


class Robot(Protocol):
    """The subset of ``ReachyMini`` used by the tools."""

    def goto_target(
        self,
        head: NDArray[np.float64] | None = ...,
        antennas: list[float] | None = ...,
        duration: float = ...,
        body_yaw: float | None = ...,
    ) -> None:
        """Move to a target pose over ``duration`` seconds (blocking)."""
        ...


class RobotTools:
    """Runs tool calls as robot motions on a single worker thread so moves never overlap."""

    def __init__(self, robot: Robot) -> None:
        """Bind the tools to a connected robot."""
        self.robot = robot
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="robot-moves")

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Start the named tool and return immediately with a result for the model."""
        if name == "look":
            direction = arguments.get("direction")
            if direction not in LOOKS:
                return {"error": f"unknown direction {direction!r}"}
            self._play([(LOOKS[direction], (0.0, 0.0), 0.8)])
            return {"status": f"looking {direction}"}
        if name == "express":
            emotion = arguments.get("emotion")
            if emotion not in EXPRESSIONS:
                return {"error": f"unknown emotion {emotion!r}"}
            self._play(EXPRESSIONS[emotion])
            return {"status": f"showing {emotion}"}
        if name == "dance":
            self._play(DANCE)
            return {"status": "dancing"}
        return {"error": f"unknown tool {name!r}"}

    def _play(self, keyframes: list[Keyframe]) -> Future[None]:
        return self._executor.submit(self._run_keyframes, keyframes)

    def _run_keyframes(self, keyframes: list[Keyframe]) -> None:
        try:
            for pose, (right, left), duration in keyframes:
                head = create_head_pose(**pose, degrees=True, mm=True)
                self.robot.goto_target(head=head, antennas=[np.deg2rad(right), np.deg2rad(left)], duration=duration)
        except Exception as e:  # the move is cosmetic; a failure must not end the conversation
            logger.warning("Robot move failed: %s", e)

    def close(self) -> None:
        """Stop accepting moves and wait for the current one to finish."""
        self._executor.shutdown(wait=True, cancel_futures=True)
