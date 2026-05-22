"""
Whisper transcription wrapper  (Tier-4 audio-transcription support)

Auto-detects the available speech-to-text backend and exposes ONE function,
transcribe_audio(), with a uniform return shape. Backends, in preference order:

  1. whisper.cpp     -- C++ binary built by pipeline/quant/install_whisper.sh
                        into pipeline/vendor/whisper.cpp (or found on PATH, or
                        pointed to by $WHISPER_CPP_BIN). Fast, no Python
                        overhead. Audio is converted to 16 kHz mono WAV first.
  2. openai-whisper  -- the pip package. Same model weights, ~2-3x slower in
                        Python, but needs no compiler toolchain.

Install either / both with:   bash pipeline/quant/install_whisper.sh

The medium.en model is used by default: a good accuracy/speed balance, and
English-only (every US-listed issuer's earnings call is in English).

transcribe_audio(path) -> {
    "text":             full transcript text (verbatim -- no words altered),
    "segments":         [{"start": sec, "end": sec, "text": str}, ...],
    "confidence":       0..1 mean segment/token confidence (None if unknown),
    "backend":          "whisper.cpp" | "openai-whisper",
    "model":            "medium.en",
    "duration_seconds": wall-clock transcription time,
}

CLI:
    python pipeline/agents/whisper_transcribe.py --check          # report backend
    python pipeline/agents/whisper_transcribe.py --file AUDIO.mp3 # transcribe one file
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent          # pipeline/
VENDOR_DIR = ROOT / "vendor"
WHISPER_CPP_DIR = VENDOR_DIR / "whisper.cpp"

DEFAULT_MODEL = "medium.en"
DEFAULT_TIMEOUT = 1800          # 30 min hard cap per call (matches the spec)

# whisper.cpp binary names/locations have changed across versions.
_CPP_BIN_CANDIDATES = (
    "build/bin/whisper-cli", "build/bin/main", "whisper-cli", "main",
)


# ══════════════════════════════════════════════════════════════════════════════
# Backend / dependency discovery
# ══════════════════════════════════════════════════════════════════════════════

def _find_whisper_cpp() -> tuple[Path, Path] | None:
    """Return (binary, model_file) for whisper.cpp, or None if unavailable."""
    # Explicit override
    env_bin = os.environ.get("WHISPER_CPP_BIN")
    bin_path: Path | None = None
    if env_bin and Path(env_bin).is_file():
        bin_path = Path(env_bin)
    else:
        for cand in _CPP_BIN_CANDIDATES:
            p = WHISPER_CPP_DIR / cand
            if p.is_file() and os.access(p, os.X_OK):
                bin_path = p
                break
        if bin_path is None:
            on_path = shutil.which("whisper-cli") or shutil.which("whisper.cpp")
            if on_path:
                bin_path = Path(on_path)
    if bin_path is None:
        return None

    env_model = os.environ.get("WHISPER_CPP_MODEL")
    if env_model and Path(env_model).is_file():
        return bin_path, Path(env_model)
    model_path = WHISPER_CPP_DIR / "models" / f"ggml-{DEFAULT_MODEL}.bin"
    if model_path.is_file():
        return bin_path, model_path
    # Binary present but no model -- treat as unavailable so we fall through.
    log.warning("whisper.cpp binary found (%s) but model %s is missing",
                bin_path, model_path)
    return None


def _openai_whisper_available() -> bool:
    try:
        import whisper  # noqa: F401
        return True
    except Exception:
        return False


def _find_ffmpeg() -> str | None:
    """Locate an ffmpeg executable: system PATH first, then imageio-ffmpeg."""
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _ensure_ffmpeg_on_path() -> bool:
    """openai-whisper invokes the bare command ``ffmpeg``. If the system has
    none, expose the imageio-ffmpeg binary under that name via a small shim
    directory prepended to PATH. Best-effort; returns True if ffmpeg is usable."""
    if shutil.which("ffmpeg"):
        return True
    try:
        import imageio_ffmpeg
        src = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return False
    shim_dir = VENDOR_DIR / "ffmpeg_shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not shim.exists():
        for link in (lambda: shim.symlink_to(src),
                     lambda: os.link(src, shim),
                     lambda: shutil.copy2(src, shim)):
            try:
                link()
                break
            except Exception:
                continue
    if shim.exists():
        os.environ["PATH"] = str(shim_dir) + os.pathsep + os.environ.get("PATH", "")
        return shutil.which("ffmpeg") is not None
    return False


def available_backend() -> str | None:
    """Return the backend transcribe_audio() would use, or None if neither is
    installed."""
    if _find_whisper_cpp() is not None:
        return "whisper.cpp"
    if _openai_whisper_available():
        return "openai-whisper"
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Audio prep
# ══════════════════════════════════════════════════════════════════════════════

def _to_wav16k(src: Path, dst: Path) -> bool:
    """Convert any audio file to 16 kHz mono PCM WAV (what whisper.cpp expects)."""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        log.error("ffmpeg not available -- cannot convert %s for whisper.cpp", src)
        return False
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
           str(dst)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=900)
        return dst.is_file() and dst.stat().st_size > 0
    except Exception as exc:
        log.error("ffmpeg conversion failed for %s: %s", src, exc)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Backend: whisper.cpp
# ══════════════════════════════════════════════════════════════════════════════

def _transcribe_cpp(audio_path: Path, binary: Path, model: Path,
                    timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="whisper_") as tmp:
        tmpd = Path(tmp)
        wav = tmpd / "audio16k.wav"
        if not _to_wav16k(audio_path, wav):
            raise RuntimeError("audio -> 16 kHz WAV conversion failed")
        out_prefix = tmpd / "out"
        cmd = [
            str(binary),
            "-m", str(model),
            "-f", str(wav),
            "-l", "en",
            "-t", str(os.cpu_count() or 4),
            "--output-json-full",          # JSON with per-token probabilities
            "--output-file", str(out_prefix),
        ]
        log.info("whisper.cpp: transcribing %s", audio_path.name)
        try:
            subprocess.run(cmd, check=True, capture_output=True,
                           text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"whisper.cpp exceeded the {timeout}s timeout")
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"whisper.cpp failed: {(exc.stderr or '')[-400:]}")

        out_json = out_prefix.with_suffix(".json")
        if not out_json.is_file():
            raise RuntimeError("whisper.cpp produced no JSON output")
        data = json.loads(out_json.read_text(encoding="utf-8"))

    raw_segments = data.get("transcription") or []
    segments, parts, token_ps = [], [], []
    for seg in raw_segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        off = seg.get("offsets") or {}
        segments.append({
            "start": (off.get("from") or 0) / 1000.0,
            "end":   (off.get("to") or 0) / 1000.0,
            "text":  text,
        })
        parts.append(text)
        for tok in seg.get("tokens") or []:
            p = tok.get("p")
            ttext = (tok.get("text") or "")
            # Skip whisper's special tokens ([_BEG_], [_TT_..], timestamps).
            if isinstance(p, (int, float)) and 0.0 <= p <= 1.0 \
                    and not ttext.strip().startswith("["):
                token_ps.append(float(p))

    confidence = round(sum(token_ps) / len(token_ps), 4) if token_ps else None
    return {
        "text": " ".join(parts).strip(),
        "segments": segments,
        "confidence": confidence,
        "backend": "whisper.cpp",
        "model": DEFAULT_MODEL,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Backend: openai-whisper
# ══════════════════════════════════════════════════════════════════════════════

_OPENAI_MODELS: dict[str, object] = {}


def _transcribe_openai(audio_path: Path, model_size: str) -> dict:
    if not _ensure_ffmpeg_on_path():
        raise RuntimeError(
            "openai-whisper needs ffmpeg on PATH and none was found "
            "(install ffmpeg, or `pip install imageio-ffmpeg`)")
    import whisper
    model = _OPENAI_MODELS.get(model_size)
    if model is None:
        log.info("openai-whisper: loading model '%s' (first use downloads ~1.5 GB)",
                 model_size)
        model = whisper.load_model(model_size)
        _OPENAI_MODELS[model_size] = model

    log.info("openai-whisper: transcribing %s", audio_path.name)
    result = model.transcribe(str(audio_path), language="en", fp16=False)

    segments, confs = [], []
    for seg in result.get("segments") or []:
        segments.append({
            "start": float(seg.get("start") or 0.0),
            "end":   float(seg.get("end") or 0.0),
            "text":  (seg.get("text") or "").strip(),
        })
        # avg_logprob is a natural-log probability; exp() maps it to ~0..1.
        alp = seg.get("avg_logprob")
        if isinstance(alp, (int, float)):
            confs.append(min(1.0, max(0.0, math.exp(alp))))

    confidence = round(sum(confs) / len(confs), 4) if confs else None
    return {
        "text": (result.get("text") or "").strip(),
        "segments": segments,
        "confidence": confidence,
        "backend": "openai-whisper",
        "model": model_size,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def transcribe_audio(audio_path: str | Path,
                     model_size: str = DEFAULT_MODEL,
                     timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Transcribe an audio file with whichever whisper backend is installed.

    Raises RuntimeError if no backend is available or transcription fails.
    """
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)

    t0 = time.monotonic()
    cpp = _find_whisper_cpp()
    if cpp is not None:
        binary, model = cpp
        result = _transcribe_cpp(audio_path, binary, model, timeout)
    elif _openai_whisper_available():
        result = _transcribe_openai(audio_path, model_size)
    else:
        raise RuntimeError(
            "No whisper backend installed. Run: "
            "bash pipeline/quant/install_whisper.sh")

    result["duration_seconds"] = round(time.monotonic() - t0, 1)
    log.info("transcribed %s via %s in %.0fs (%d segments, confidence=%s)",
             audio_path.name, result["backend"], result["duration_seconds"],
             len(result["segments"]), result["confidence"])
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    if args[0] == "--check":
        backend = available_backend()
        ffmpeg = _find_ffmpeg()
        print(f"whisper backend : {backend or 'NONE -- run install_whisper.sh'}")
        print(f"ffmpeg          : {ffmpeg or 'NONE -- install ffmpeg or imageio-ffmpeg'}")
        cpp = _find_whisper_cpp()
        if cpp:
            print(f"whisper.cpp bin : {cpp[0]}")
            print(f"whisper.cpp mdl : {cpp[1]}")
        print(f"openai-whisper  : {'available' if _openai_whisper_available() else 'not installed'}")
        sys.exit(0 if backend else 1)

    if args[0] == "--file" and len(args) >= 2:
        r = transcribe_audio(args[1])
        print(f"\nbackend     : {r['backend']} ({r['model']})")
        print(f"confidence  : {r['confidence']}")
        print(f"segments    : {len(r['segments'])}")
        print(f"time        : {r['duration_seconds']}s")
        print(f"text length : {len(r['text']):,} chars")
        print(f"\n--- first 600 chars ---\n{r['text'][:600]}")
        return

    print(__doc__)


if __name__ == "__main__":
    _main()
