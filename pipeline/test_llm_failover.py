"""
LLM gateway failover simulation -- no network, no API keys consumed.
====================================================================

Verifies the provider waterfall in pipeline/llm.py:

  1. Groq 429 (daily cap)            -> falls through to Cerebras
  2. Groq + Cerebras + NVIDIA + Gemini all capped -> OpenRouter serves
  3. Every provider capped           -> (None, "none"), no crash
  4. Ledger budget exhaustion        -> provider skipped without a single call
  5. Tier-0 balancing                -> least-used primary is tried first
  6. Transient 429 (per-minute)      -> same provider retried, then serves

Usage:
    python pipeline/test_llm_failover.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm  # noqa: E402


def _fake_keys():
    """Patch every provider key so the full chain is configured."""
    return patch.multiple(
        llm,
        GROQ_API_KEY="k", CEREBRAS_API_KEY="k", NVIDIA_API_KEY="k",
        GEMINI_API_KEY="k", OPENROUTER_API_KEY="k",
    )


def _no_ledger():
    """Keep the test offline: no Supabase reads/writes."""
    return patch.object(llm, "_ledger_client", lambda: None)


def _reset():
    llm.reset_exhausted()


DAILY_CAP = Exception("429: rate limit reached, requests per day exceeded")
PER_MIN   = Exception("429 Too Many Requests: rate limit, retry shortly")


def test_groq_daily_cap_falls_to_cerebras():
    _reset()
    calls = []

    def fake_openai(p, *a, **kw):
        calls.append(p.name)
        if p.name == "groq":
            raise DAILY_CAP
        return f"text-from-{p.name}", 100

    with _fake_keys(), _no_ledger(), \
         patch.object(llm, "_call_openai", fake_openai):
        text, provider = llm.complete("prompt")
    assert provider == "cerebras", f"expected cerebras, got {provider} ({calls})"
    assert text == "text-from-cerebras"
    assert "groq" in llm._exhausted
    print("ok  1. groq daily cap -> cerebras serves")


def test_all_primaries_capped_falls_to_openrouter():
    _reset()

    def fake_openai(p, *a, **kw):
        if p.name in ("groq", "cerebras", "nvidia"):
            raise DAILY_CAP
        return f"text-from-{p.name}", 100

    def fake_gemini(p, *a, **kw):
        raise DAILY_CAP

    with _fake_keys(), _no_ledger(), \
         patch.object(llm, "_call_openai", fake_openai), \
         patch.object(llm, "_call_gemini", fake_gemini):
        text, provider = llm.complete("prompt")
    assert provider == "openrouter", f"expected openrouter, got {provider}"
    print("ok  2. groq+cerebras+nvidia+gemini capped -> openrouter serves")


def test_every_provider_capped_returns_none():
    _reset()

    def fail(p, *a, **kw):
        raise DAILY_CAP

    with _fake_keys(), _no_ledger(), \
         patch.object(llm, "_call_openai", fail), \
         patch.object(llm, "_call_gemini", fail):
        text, provider = llm.complete("prompt")
    assert text is None and provider == "none"
    print("ok  3. all providers capped -> (None, 'none')")


def test_ledger_budget_skips_provider_without_calling():
    _reset()
    calls = []

    def fake_openai(p, *a, **kw):
        calls.append(p.name)
        return f"text-from-{p.name}", 100

    with _fake_keys(), _no_ledger(), \
         patch.object(llm, "_call_openai", fake_openai):
        # Ledger says groq already burned its full request budget today
        # (e.g. by the two earlier scans) -- it must not even be attempted.
        llm._usage["groq"] = {"requests": 1000, "tokens": 0}
        text, provider = llm.complete("prompt")
    assert "groq" not in calls, f"groq was called despite spent budget ({calls})"
    assert provider in ("cerebras", "nvidia")
    print("ok  4. ledger-spent provider skipped without an API call")


def test_tier0_balancing_prefers_least_used():
    _reset()
    with _fake_keys(), _no_ledger():
        # groq 60% spent, cerebras 10% spent, nvidia 50% spent
        llm._usage.update({
            "groq":     {"requests": 600, "tokens": 0},
            "cerebras": {"requests": 200, "tokens": 0},
            "nvidia":   {"requests": 250, "tokens": 0},
        })
        order = [p.name for p in llm._eligible_providers()]
    assert order[0] == "cerebras", f"expected cerebras first, got {order}"
    assert order[:3] == ["cerebras", "nvidia", "groq"], order
    assert order[3:] == ["gemini", "openrouter"], order
    print("ok  5. tier-0 load balanced by remaining budget; gemini/openrouter last")


def test_transient_429_retries_same_provider():
    _reset()
    attempts = {"n": 0}

    def fake_openai(p, *a, **kw):
        if p.name == "groq":
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise PER_MIN
            return "groq-after-retry", 100
        return f"text-from-{p.name}", 100

    with _fake_keys(), _no_ledger(), \
         patch.object(llm, "_call_openai", fake_openai), \
         patch.object(llm.time, "sleep", lambda s: None):
        text, provider = llm.complete("prompt")
    assert provider == "groq" and text == "groq-after-retry"
    assert attempts["n"] == 2
    print("ok  6. transient 429 retried on same provider, then served")


if __name__ == "__main__":
    test_groq_daily_cap_falls_to_cerebras()
    test_all_primaries_capped_falls_to_openrouter()
    test_every_provider_capped_returns_none()
    test_ledger_budget_skips_provider_without_calling()
    test_tier0_balancing_prefers_least_used()
    test_transient_429_retries_same_provider()
    print("\nall 6 failover simulations passed")
