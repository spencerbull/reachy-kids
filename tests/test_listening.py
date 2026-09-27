import numpy as np
import pytest

from reachy_kids.listening import (
    StreamResampler,
    KidsAudioFrontEnd,
    float_to_pcm16,
    pcm16_to_float,
    to_mono_float32,
)


def tone(frequency: float, seconds: float, rate: int, amplitude: float) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio**2)))


def dominant_frequency(audio: np.ndarray, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(audio.size)))
    return float(np.fft.rfftfreq(audio.size, 1 / rate)[np.argmax(spectrum)])


def run_chunks(stage, audio: np.ndarray, chunk: int = 160, **kwargs) -> np.ndarray:
    return np.concatenate([stage.process(audio[i : i + chunk], **kwargs) for i in range(0, audio.size, chunk)])


@pytest.mark.parametrize(("src", "dst"), [(16000, 24000), (24000, 16000), (48000, 16000)])
def test_resampler_keeps_pitch_and_duration_across_chunks(src, dst):
    out = run_chunks(StreamResampler(src, dst), tone(440, 1.0, src, 0.5), chunk=137)

    assert abs(out.size - dst) <= 2
    assert abs(dominant_frequency(out, dst) - 440) < 3
    # Chunk boundaries must not click: sample-to-sample jumps stay tiny for a 440 Hz tone.
    assert np.max(np.abs(np.diff(out[dst // 10 :]))) < 0.2


def test_downsampling_removes_content_above_new_nyquist():
    out = run_chunks(StreamResampler(24000, 16000), tone(10000, 1.0, 24000, 0.5))
    assert rms(out[2000:]) < 0.02


def test_quiet_child_voice_is_boosted_toward_target():
    front_end = KidsAudioFrontEnd(16000)
    quiet_voice = tone(300, 3.0, 16000, 0.01)  # ~ -43 dBFS: a small voice across the room
    out = run_chunks(front_end, quiet_voice)

    assert rms(out[-16000:]) > 4 * rms(quiet_voice)
    assert np.max(np.abs(out)) < 1.0


def test_silence_and_room_noise_are_not_amplified():
    rng = np.random.default_rng(0)
    noise = (0.0005 * rng.standard_normal(16000 * 3)).astype(np.float32)
    out = run_chunks(KidsAudioFrontEnd(16000), noise)
    assert rms(out[-16000:]) < 2 * rms(noise)


def test_loud_voice_never_clips():
    out = run_chunks(KidsAudioFrontEnd(16000), tone(300, 1.0, 16000, 0.95))
    assert np.max(np.abs(out)) <= 0.98 + 1e-6


def test_mic_is_ducked_while_robot_speaks():
    voice = tone(300, 2.0, 16000, 0.05)
    normal = run_chunks(KidsAudioFrontEnd(16000), voice)
    ducked = run_chunks(KidsAudioFrontEnd(16000), voice, robot_speaking=True)
    assert rms(ducked[-8000:]) < 0.5 * rms(normal[-8000:])


def test_low_rumble_is_filtered():
    out = run_chunks(KidsAudioFrontEnd(16000, max_gain=1.0), tone(30, 2.0, 16000, 0.3))
    assert rms(out[-8000:]) < 0.1 * 0.3


def test_format_conversions():
    stereo_int16 = np.array([[1000, 3000], [-2000, -4000]], dtype=np.int16)
    mono = to_mono_float32(stereo_int16)
    assert mono.dtype == np.float32
    np.testing.assert_allclose(mono, [1000 / 32768, -2000 / 32768])
    np.testing.assert_allclose(to_mono_float32(np.ones((2, 5), dtype=np.float32)), np.ones(5))

    audio = np.array([0.0, 0.5, -0.5, 1.5], dtype=np.float32)
    np.testing.assert_allclose(pcm16_to_float(float_to_pcm16(audio)), [0.0, 0.5, -0.5, 1.0], atol=1e-4)
