#!/usr/bin/env bash
# ============================================================================
# install_whisper.sh -- set up the Tier-4 audio-transcription backend.
#
# Tier 4 of the transcript fallback hierarchy transcribes earnings-call webcast
# audio locally with whisper. Two backends are supported; the Python wrapper
# (pipeline/agents/whisper_transcribe.py) auto-detects whichever is present:
#
#   1. whisper.cpp     -- fast C++ binary, built from source here. Preferred.
#   2. openai-whisper  -- pure-Python pip package. Same model weights, slower,
#                         but needs no compiler toolchain. Installed as the
#                         fallback for environments where the C++ build is
#                         fragile (e.g. a Windows dev box without MSVC/WSL).
#
# Usage:
#   bash pipeline/quant/install_whisper.sh             # both backends
#   bash pipeline/quant/install_whisper.sh --cpp-only  # only whisper.cpp
#   bash pipeline/quant/install_whisper.sh --pip-only  # only openai-whisper
#
# Every step is idempotent -- safe to re-run.
#
# NOTE: this script lives under pipeline/quant/ because the project spec
# placed it there; it is infrastructure setup, not a quant model.
# ============================================================================
set -euo pipefail

MODEL="medium.en"                       # ~1.5 GB; good accuracy/speed balance
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENDOR_DIR="$REPO_ROOT/pipeline/vendor"
WHISPER_DIR="$VENDOR_DIR/whisper.cpp"

DO_CPP=1
DO_PIP=1
case "${1:-}" in
  --cpp-only) DO_PIP=0 ;;
  --pip-only) DO_CPP=0 ;;
  "")         ;;
  *) echo "unknown argument: $1"; echo "usage: $0 [--cpp-only|--pip-only]"; exit 2 ;;
esac

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[warn] %s\033[0m\n' "$*"; }

# ---- ffmpeg (both backends need it to decode mp3/m4a) ----------------------
if ! command -v ffmpeg >/dev/null 2>&1; then
  warn "ffmpeg not found on PATH. Install one of:"
  warn "  Linux:   sudo apt-get install -y ffmpeg"
  warn "  macOS:   brew install ffmpeg"
  warn "  Windows: winget install Gyan.FFmpeg"
  warn "The Python wrapper falls back to the imageio-ffmpeg bundled binary,"
  warn "so this is a warning, not a hard failure."
fi

# ---- whisper.cpp -----------------------------------------------------------
if [ "$DO_CPP" -eq 1 ]; then
  log "Installing whisper.cpp -> $WHISPER_DIR"
  mkdir -p "$VENDOR_DIR"
  if [ -d "$WHISPER_DIR/.git" ]; then
    log "Repo already present; pulling latest"
    git -C "$WHISPER_DIR" pull --ff-only || warn "git pull failed; using existing checkout"
  else
    git clone --depth 1 https://github.com/ggerganov/whisper.cpp "$WHISPER_DIR"
  fi

  log "Building whisper.cpp"
  cd "$WHISPER_DIR"
  # Modern whisper.cpp builds with CMake; older revisions used a Makefile.
  if command -v cmake >/dev/null 2>&1; then
    cmake -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build --config Release -j
  else
    warn "cmake not found; trying the legacy 'make' build"
    make -j
  fi

  log "Downloading the '$MODEL' model (~1.5 GB; one-time)"
  bash ./models/download-ggml-model.sh "$MODEL"

  # The built binary's name/location varies across whisper.cpp versions.
  BIN=""
  for cand in build/bin/whisper-cli build/bin/main whisper-cli main; do
    if [ -x "$WHISPER_DIR/$cand" ]; then BIN="$WHISPER_DIR/$cand"; break; fi
  done
  if [ -z "$BIN" ]; then
    warn "Could not locate the whisper.cpp binary after the build."
    warn "Check the build output above; the openai-whisper fallback still works."
  else
    MODEL_BIN="$WHISPER_DIR/models/ggml-$MODEL.bin"
    if [ -f "$WHISPER_DIR/samples/jfk.wav" ] && [ -f "$MODEL_BIN" ]; then
      log "Verifying with the bundled sample (samples/jfk.wav)"
      "$BIN" -m "$MODEL_BIN" -f "$WHISPER_DIR/samples/jfk.wav" \
        || warn "whisper.cpp smoke test returned non-zero -- inspect the output above"
    fi
    log "whisper.cpp ready:"
    log "  binary: $BIN"
    log "  model:  $MODEL_BIN"
  fi
  cd "$REPO_ROOT"
fi

# ---- openai-whisper (pip fallback) ----------------------------------------
if [ "$DO_PIP" -eq 1 ]; then
  log "Installing the openai-whisper pip package (fallback backend)"
  PIP="python -m pip"
  if   [ -x "$REPO_ROOT/pipeline/venv/bin/python" ]; then
    PIP="$REPO_ROOT/pipeline/venv/bin/python -m pip"
  elif [ -x "$REPO_ROOT/pipeline/venv/Scripts/python.exe" ]; then
    PIP="$REPO_ROOT/pipeline/venv/Scripts/python.exe -m pip"
  fi
  $PIP install --quiet --upgrade openai-whisper imageio-ffmpeg
  log "openai-whisper installed. The medium.en weights (~1.5 GB) download"
  log "automatically on first transcription, cached under ~/.cache/whisper."
fi

log "Done. pipeline/agents/whisper_transcribe.py auto-detects the backend."
log "Verify the install end-to-end with:"
log "  python pipeline/agents/whisper_transcribe.py --check"
