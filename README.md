---
title: Reachy Kids
emoji: 🤖
colorFrom: red
colorTo: yellow
sdk: static
pinned: false
short_description: Kid-friendly voice conversations for Reachy Mini
tags:
 - reachy_mini
 - reachy_mini_python_app
---

# Reachy Kids

A voice conversation app for [Reachy Mini](https://github.com/pollen-robotics/reachy_mini), in the spirit of
Pollen Robotics' conversation app, with two additions:

- **Bring your own realtime provider:** [OpenAI Realtime](https://developers.openai.com/api/docs/guides/realtime-conversations)
  or [xAI Grok Voice](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech), each with your own API key.
- **Kids mode:** kid-safe instructions and a listening pipeline tuned for children.

Reachy talks, listens, looks around, shows emotions with its head and antennas, and dances.

## Install

Reachy Kids is published to [GitHub Releases](https://github.com/spencerbull/reachy-kids/releases). It needs
[uv](https://docs.astral.sh/uv/).

```bash
curl -fsSL https://github.com/spencerbull/reachy-kids/releases/latest/download/install.sh | bash
```

This installs the `reachy-kids` command (with the MuJoCo simulator) and adds two entries to your app launcher
(SUPER + SPACE on Omarchy): **Reachy Kids** for a real robot and **Reachy Kids Simulator**.

To install a wheel by hand instead:

```bash
uv tool install "reachy-kids[sim] @ https://github.com/spencerbull/reachy-kids/releases/download/v0.1.1/reachy_kids-0.1.1-py3-none-any.whl"
reachy-kids install-launcher
```

On Linux, a USB-connected Reachy Mini Lite also needs the GStreamer WebRTC plugin (`sudo pacman -S gst-plugin-rswebrtc`
on Arch/Omarchy). The simulator does not.

## Use it

Open **Reachy Kids** (or **Reachy Kids Simulator**) from the launcher, or run:

```bash
reachy-kids launch          # real robot: starts the daemon if needed
reachy-kids launch --sim    # MuJoCo simulator, using your computer's mic and speakers
reachy-kids run             # just the app, against a daemon that is already running
```

`launch` opens the settings page at <http://localhost:8042>. Pick **OpenAI Realtime** or **xAI Grok Voice**, paste the
API key, and save. Reachy greets you and starts listening. The page shows what Reachy heard and said, and lets you
switch providers, voices, and modes at any time.

Reachy Kids is also a regular Reachy Mini app: install it into the daemon's environment and start it from the Reachy
Mini dashboard. Started that way (for example on a Wireless robot), the settings page is reachable from your local
network so you can open it from a phone. It has no login and can change the API keys, so only do this on a network you
trust. Saved keys are never sent back to the browser.

### Settings

Settings live in `~/.config/reachy-kids/settings.json` (owner-only permissions, since they hold API keys). Environment
variables override the file:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` / `XAI_API_KEY` | API keys |
| `REACHY_KIDS_PROVIDER` | `openai` or `xai` |
| `REACHY_KIDS_PORT` | Settings page port (default `8042`) |
| `REACHY_KIDS_HOST` | Settings page address. `reachy-kids` binds `127.0.0.1`; started from the robot dashboard it binds `0.0.0.0` |
| `REACHY_KIDS_MEDIA_BACKEND` | Force a Reachy Mini media backend (`launch --sim` sets `local`) |
| `REACHY_KIDS_CONFIG` | Use a different settings file |

## Kids mode

The stock conversation app streams raw microphone audio to a default server VAD. That works for an adult sitting close
to the robot, but children speak more softly, from further away, at a higher pitch, and pause mid-sentence while they
think, so they get missed or cut off. Kids mode changes three things:

**On the robot, before audio leaves it** (`reachy_kids/listening.py`)

- An 80 Hz high-pass filter removes table and motor rumble.
- A speech-aware automatic gain lifts quiet voices by up to +18 dB. It only adapts on speech, so room noise and silence
  are not amplified, and it never clips.
- While Reachy is talking, the mic is ducked by about 10 dB so its own voice doesn't trigger a false interruption. A
  child who talks over Reachy still interrupts it, and playback stops immediately.

**At the provider** (`reachy_kids/providers.py`)

| | OpenAI Realtime | xAI Grok Voice |
| --- | --- | --- |
| Turn detection | `semantic_vad`, eagerness `low` (waits for a thought to finish) | `server_vad`, threshold 0.5 (default 0.85), 1.2 s silence, 500 ms pre-roll |
| Recognition hints | `gpt-4o-transcribe` prompt describing a young child's speech, plus the child's name | key terms: "Reachy", the child's name, and your own words |
| Other | far-field noise reduction, slower speech (0.9×) | native 16 kHz audio (no resampling), slower speech (0.9×) |

**In the conversation** (`reachy_kids/prompts.py`): short simple sentences, one question at a time, patience with
restarts and mispronunciations, asking again instead of guessing, age-appropriate topics only, no collecting personal
details, and pointing to a trusted grown-up for anything about safety or big worries.

Turn kids mode off for a regular adult conversation with snappier turn-taking.

## Development

```bash
uv sync --extra sim
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

The tests run the realtime protocol against a local fake server, so they need no API keys. To run the whole app
against the simulator end to end:

```bash
uv run reachy-mini-daemon --sim --headless &
REACHY_KIDS_SIM_E2E=1 uv run pytest tests/test_sim_e2e.py
```

### Releasing

Bump `version` in `pyproject.toml`, merge, then push a matching tag. The release workflow builds the wheel and sdist
and publishes them with `install.sh` to GitHub Releases.

```bash
git tag v0.1.1 && git push origin v0.1.1
```
