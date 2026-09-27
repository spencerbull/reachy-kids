#!/usr/bin/env bash
# Install Reachy Kids from GitHub Releases and add it to the app launcher.
#   curl -fsSL https://github.com/spencerbull/reachy-kids/releases/latest/download/install.sh | bash
#   ... | bash -s -- v0.1.0     # a specific version
set -euo pipefail

REPO="spencerbull/reachy-kids"
VERSION="${1:-latest}"

if ! command -v uv >/dev/null 2>&1; then
  echo "Reachy Kids is installed with uv. Install it first:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

if [[ $VERSION == "latest" ]]; then
  api="https://api.github.com/repos/$REPO/releases/latest"
else
  api="https://api.github.com/repos/$REPO/releases/tags/$VERSION"
fi
wheel_url=$(curl -fsSL "$api" | grep -o '"browser_download_url": *"[^"]*\.whl"' | head -n1 | sed 's/.*"\(https[^"]*\)"/\1/')
if [[ -z $wheel_url ]]; then
  echo "Could not find a wheel for release '$VERSION' of $REPO." >&2
  exit 1
fi

echo "Installing $wheel_url"
uv tool install --force --python 3.12 "reachy-kids[sim] @ $wheel_url"

if command -v gst-inspect-1.0 >/dev/null 2>&1 && ! gst-inspect-1.0 webrtcsink >/dev/null 2>&1; then
  echo
  echo "Note: a USB-connected Reachy Mini Lite needs the GStreamer WebRTC plugin for audio."
  if command -v pacman >/dev/null 2>&1; then
    echo "  sudo pacman -S gst-plugin-rswebrtc"
  else
    echo "  Install gst-plugins-rs (webrtc) from your distribution."
  fi
fi

echo
"$(uv tool dir --bin)/reachy-kids" install-launcher
