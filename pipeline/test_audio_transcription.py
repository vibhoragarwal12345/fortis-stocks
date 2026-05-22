"""
Tier-4 audio-transcription validation harness.

Exercises the audio-transcription path end to end and checks it against the
quality gates from the build spec. This is the script to run for execution-
order steps 3-6 (the parts that need a real install, real downloads, and CPU
time -- which is why they cannot run inside the assistant).

Usage:
    python pipeline/test_audio_transcription.py                  # defaults
    python pipeline/test_audio_transcription.py AAPL 2026 1      # one call
    python pipeline/test_audio_transcription.py --discover-only TSLA 2026 1

What it checks:
  1. A whisper backend + ffmpeg are installed.
  2. Audio-URL discovery resolves an IR page and a downloadable audio file.
  3. Full transcription runs and produces readable English.
  4. Speaker attribution labels at least a CEO and a CFO.
  5. The second call hits the cache and returns in < 1 second.
  6. The 5-tier orchestrator picks a path and grades the transcript.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def _record(gate: str, status: str, detail: str = "") -> None:
    _results.append((gate, status, detail))
    mark = {"PASS": "[ok]  ", "FAIL": "[FAIL]", "SKIP": "[skip]"}[status]
    print(f"  {mark} {gate}" + (f"  --  {detail}" if detail else ""))


def _looks_like_english(text: str) -> bool:
    """Cheap garbled-audio check: enough words, sane average length, and a
    healthy share of very common English words."""
    words = text.split()
    if len(words) < 200:
        return False
    avg_len = sum(len(w) for w in words) / len(words)
    if not (3.0 <= avg_len <= 9.0):
        return False
    common = {"the", "and", "to", "of", "a", "in", "we", "that", "is", "our",
              "for", "on", "as", "with", "this", "quarter", "year"}
    lower = [w.strip(".,!?;:'\"").lower() for w in words[:2000]]
    hits = sum(1 for w in lower if w in common)
    return hits / max(1, len(lower)) > 0.15


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    discover_only = "--discover-only" in sys.argv
    ticker = (args[0] if len(args) > 0 else "AAPL").upper()
    year = int(args[1]) if len(args) > 1 else 2026
    quarter = int(args[2]) if len(args) > 2 else 1

    print("=" * 72)
    print(f"Tier-4 audio-transcription validation  --  {ticker} {year}Q{quarter}")
    print("=" * 72)

    # ---- Gate 1: backend availability -------------------------------------
    print("\n[1] whisper backend + ffmpeg")
    from agents.whisper_transcribe import available_backend, _find_ffmpeg
    backend = available_backend()
    ffmpeg = _find_ffmpeg()
    if backend:
        _record("whisper backend installed", PASS, backend)
    else:
        _record("whisper backend installed", FAIL,
                "run: bash pipeline/quant/install_whisper.sh")
    _record("ffmpeg available", PASS if ffmpeg else FAIL, ffmpeg or "missing")

    # ---- Gate 2: audio-URL discovery --------------------------------------
    print("\n[2] audio-URL discovery")
    from agents.audio_transcriber import find_earnings_audio_url
    src, outcome = find_earnings_audio_url(ticker, year, quarter)
    if src:
        _record("earnings-call audio located", PASS,
                f"{src.audio_format}, {src.estimated_size_mb} MB")
        print(f"       url: {src.audio_url}")
    else:
        _record("earnings-call audio located", FAIL, f"outcome={outcome}")

    if discover_only:
        _summary()
        return

    # ---- Gate 3 & 4: transcription + speaker attribution ------------------
    print("\n[3] full transcription (this can take 5-10 minutes)")
    if not backend:
        _record("transcription produces readable English", SKIP, "no backend")
        _record("speaker attribution finds CEO + CFO", SKIP, "no backend")
    elif not src:
        _record("transcription produces readable English", SKIP, "no audio URL")
        _record("speaker attribution finds CEO + CFO", SKIP, "no audio URL")
    else:
        from agents.audio_transcriber import fetch_audio_transcript
        t0 = time.monotonic()
        result = fetch_audio_transcript(ticker, year, quarter)
        elapsed = time.monotonic() - t0
        if not result:
            _record("transcription produces readable English", FAIL,
                    "fetch_audio_transcript returned None")
            _record("speaker attribution finds CEO + CFO", SKIP, "no transcript")
        else:
            text = result["transcript_text"]
            print(f"       transcribed in {elapsed:.0f}s, "
                  f"{len(text):,} chars, {len(result['structured_segments'])} segments")
            _record("transcription produces readable English",
                    PASS if _looks_like_english(text) else FAIL,
                    f"{len(text.split()):,} words")
            roles = {s["role"] for s in result["structured_segments"]}
            has_exec = "CEO" in roles and "CFO" in roles
            _record("speaker attribution finds CEO + CFO",
                    PASS if has_exec else FAIL, f"roles={sorted(roles)}")

            # ---- Gate 5: cache hit < 1 second -----------------------------
            print("\n[4] cache verification (re-run should be instant)")
            t1 = time.monotonic()
            cached = fetch_audio_transcript(ticker, year, quarter)
            cache_elapsed = time.monotonic() - t1
            ok = cached is not None and cache_elapsed < 1.0
            _record("cached re-run returns in < 1s", PASS if ok else FAIL,
                    f"{cache_elapsed:.2f}s")

    # ---- Gate 6: full orchestrator ----------------------------------------
    print("\n[5] 5-tier orchestrator (which path wins?)")
    try:
        from agents.sec_transcript_fetcher import fetch_full_transcript
        r = fetch_full_transcript(ticker, year, quarter)
        _record("orchestrator returns a graded transcript", PASS,
                f"quality={r['quality_grade']}, path={r.get('source_path')}")
    except Exception as exc:
        _record("orchestrator returns a graded transcript", FAIL, str(exc)[:120])

    _summary()


def _summary() -> None:
    print("\n" + "=" * 72)
    passed = sum(1 for _, s, _ in _results if s == PASS)
    failed = sum(1 for _, s, _ in _results if s == FAIL)
    skipped = sum(1 for _, s, _ in _results if s == SKIP)
    print(f"RESULT:  {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 72)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
