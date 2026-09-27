"""Microphone front-end and turn-taking profiles tuned for children's speech.

Children speak more quietly, from further away and with a higher pitch than adults, and they pause
mid-sentence while they think. The stock conversation app sends raw microphone audio to a default
server VAD, which misses soft voices and cuts children off. This module fixes the signal locally
(rumble filter, speech-aware gain, echo ducking while the robot talks) and describes the more patient
turn detection each provider should use.
"""

from dataclasses import dataclass

import numpy as np
from scipy import signal
from numpy.typing import NDArray


FloatAudio = NDArray[np.float32]


@dataclass(frozen=True)
class ListeningProfile:
    """Turn-taking parameters sent to the realtime provider."""

    # OpenAI semantic VAD: "low" waits longer for a thought to finish.
    semantic_eagerness: str
    # Energy VAD (xAI): lower threshold catches soft voices; longer silence tolerates thinking pauses.
    vad_threshold: float
    silence_duration_ms: int
    prefix_padding_ms: int
    # The robot sits on a table an arm's length or more away from the speaker.
    noise_reduction: str
    speech_speed: float
    transcription_prompt: str


KIDS_LISTENING = ListeningProfile(
    semantic_eagerness="low",
    vad_threshold=0.5,
    silence_duration_ms=1200,
    prefix_padding_ms=500,
    noise_reduction="far_field",
    speech_speed=0.9,
    transcription_prompt=(
        "A young child talking to a friendly robot named Reachy. Expect short, simple sentences, "
        "pauses, restarts, invented words and childlike pronunciation."
    ),
)

ADULT_LISTENING = ListeningProfile(
    semantic_eagerness="auto",
    vad_threshold=0.7,
    silence_duration_ms=600,
    prefix_padding_ms=333,
    noise_reduction="far_field",
    speech_speed=1.0,
    transcription_prompt="",
)


def to_mono_float32(frame: NDArray[np.generic]) -> FloatAudio:
    """Return a 1-D float32 array in [-1, 1] from any mic frame (int16/float, mono or multi-channel)."""
    audio = np.asarray(frame)
    if audio.ndim == 2:
        # Channels-last from the SDK, but tolerate channels-first. Channel 0 carries the robot's
        # echo-cancelled, beamformed voice channel, so it is used rather than a mix.
        if audio.shape[0] < audio.shape[1]:
            audio = audio.T
        audio = audio[:, 0]
    if audio.dtype == np.int16:
        return (audio.astype(np.float32) / 32768.0).astype(np.float32)
    return audio.astype(np.float32, copy=False)


def float_to_pcm16(audio: FloatAudio) -> bytes:
    """Encode float audio as little-endian PCM16 bytes."""
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def pcm16_to_float(data: bytes) -> FloatAudio:
    """Decode little-endian PCM16 bytes to float audio."""
    return (np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0).astype(np.float32)


class StreamResampler:
    """Continuous sample-rate converter for chunked audio (no clicks at chunk boundaries)."""

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        """Prepare the anti-alias filter (when downsampling) and interpolation state."""
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self._step = src_rate / dst_rate
        self._position = 0.0  # next output sample, in source-sample units relative to _previous
        self._previous = np.zeros(1, dtype=np.float32)
        self._sos: NDArray[np.float64] | None = None
        self._zi: NDArray[np.float64] | None = None
        if dst_rate < src_rate:
            self._sos = signal.butter(8, 0.45 * dst_rate, btype="lowpass", fs=src_rate, output="sos")
            self._zi = np.zeros((self._sos.shape[0], 2))

    def process(self, audio: FloatAudio) -> FloatAudio:
        """Resample one chunk; state carries over to the next call."""
        if self.src_rate == self.dst_rate or audio.size == 0:
            return audio
        if self._sos is not None:
            filtered, self._zi = signal.sosfilt(self._sos, audio, zi=self._zi)
            audio = np.asarray(filtered, dtype=np.float32)
        # Prepend the last sample of the previous chunk so interpolation spans the boundary.
        extended = np.concatenate([self._previous, audio.astype(np.float32)])
        last_index = extended.size - 1
        positions = np.arange(self._position, last_index, self._step)
        out = np.asarray(np.interp(positions, np.arange(extended.size), extended), dtype=np.float32)
        self._position = (positions[-1] + self._step - last_index) if positions.size else self._position - audio.size
        self._previous = extended[-1:]
        return out


class KidsAudioFrontEnd:
    """Rumble filter + speech-aware automatic gain + echo ducking for a far-field child's voice."""

    def __init__(
        self,
        sample_rate: int,
        target_rms: float = 0.08,
        max_gain: float = 8.0,
        duck_gain: float = 0.3,
        highpass_hz: float = 80.0,
    ) -> None:
        """Configure the front-end; ``max_gain`` 8.0 is +18 dB, ``duck_gain`` 0.3 is about -10 dB."""
        self.sample_rate = sample_rate
        self.target_rms = target_rms
        self.max_gain = max_gain
        self.duck_gain = duck_gain
        self._sos = signal.butter(2, highpass_hz, btype="highpass", fs=sample_rate, output="sos")
        self._zi = np.zeros((self._sos.shape[0], 2))
        self.noise_floor = 1e-3
        self.gain = 1.0

    def process(self, audio: FloatAudio, robot_speaking: bool = False) -> FloatAudio:
        """Return the processed chunk. Gain only adapts on chunks that look like speech."""
        if audio.size == 0:
            return audio
        sos_out, self._zi = signal.sosfilt(self._sos, audio, zi=self._zi)
        filtered = np.asarray(sos_out, dtype=np.float32)
        rms = float(np.sqrt(np.mean(filtered**2))) + 1e-9

        is_speech = rms > 3.0 * self.noise_floor and rms > 2e-3
        if is_speech:
            # Track the floor slowly upward during speech so a noisy room doesn't count as speech forever.
            self.noise_floor += 0.0005 * (rms - self.noise_floor)
            desired = float(np.clip(self.target_rms / rms, 1.0, self.max_gain))
            # Fast attack when loud (avoid blasting), slow release when a child gets quieter.
            rate = 0.5 if desired < self.gain else 0.05
            self.gain += rate * (desired - self.gain)
        else:
            rate = 0.1 if rms < self.noise_floor else 0.002
            self.noise_floor = max(1e-4, self.noise_floor + rate * (rms - self.noise_floor))

        gain = self.gain * (self.duck_gain if robot_speaking else 1.0)
        peak = float(np.max(np.abs(filtered))) + 1e-9
        gain = min(gain, 0.98 / peak)
        return (filtered * gain).astype(np.float32)
