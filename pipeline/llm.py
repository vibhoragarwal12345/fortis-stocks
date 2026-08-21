"""
Shared LLM gateway -- quota-aware provider waterfall.
====================================================

One ``complete()`` call that every agent uses instead of rolling its own
Gemini/Groq fallback. Tries each eligible free provider in order until one
returns text; a provider that hits a quota / rate-limit (HTTP 429) is disabled
for the rest of the process so we don't keep hammering it.

WHY: a single full deep scan can exhaust one free tier's daily token budget
(that is exactly what took the critic agent down in June 2026). Fanning the
same workload across several free providers multiplies the effective daily
budget and removes the single point of failure.

PROVIDER TIERS (selection order):

    tier 0  groq / cerebras / nvidia   -- frontier-class primaries, balanced
                                          by remaining daily budget
    tier 1  gemini                     -- different model family, sits behind
                                          the primaries
    tier 2  openrouter                 -- last-resort overflow router
                                          (free gpt-oss-120b + Llama slots)

Within tier 0 the gateway picks the provider with the LARGEST remaining
fraction of its daily budget, so load spreads across Groq/Cerebras/NVIDIA
instead of draining them serially. Budget state lives in the ``llm_usage``
Supabase table (migration 048), incremented after every call, so awareness
survives across agent subprocesses and across the three daily CI scan jobs.
Ledger access is best-effort: if the DB is unreachable the gateway falls back
to in-process counting and keeps serving.

Groq and Cerebras serve ``gpt-oss-120b``; NVIDIA still serves
``llama-3.3-70b-instruct``; Gemini serves ``gemini-2.5-flash``; OpenRouter
serves the free ``llama-3.3-70b-instruct`` slot. Groq retired
``llama-3.3-70b-versatile`` around 2026-08-17 -- every call 404'd for two days
before it was caught, so if a provider suddenly fails wholesale, check its
model list first (``GET /v1/models``). Everything except Gemini is
OpenAI-compatible (same ``/chat/completions`` shape) and goes through the
``openai`` SDK by swapping ``base_url``.

PRIVACY GUARDRAIL: every provider here is a FREE tier that may train on
submitted prompts. Only PUBLIC market data (prices, filings, news, options
flow, public analysis) may flow through this gateway. NEVER route user
account data, user portfolio holdings, or any private/confidential
information through ``complete()``.

Add a provider by dropping one line in ``_providers()``. A provider with no
API key configured is silently skipped, so the chain degrades cleanly.

USAGE
    from llm import complete   # or: from pipeline.llm import complete
    text, provider = complete(prompt, system=_SYSTEM, temperature=0.5, max_tokens=1600)
    if text is None:
        ...  # every provider was unavailable / rate-limited
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass

from config import (  # noqa: E402
    CEREBRAS_API_KEY,
    CEREBRAS_API_KEY_2,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    GROQ_API_KEY_2,
    NVIDIA_API_KEY,
    OPENROUTER_API_KEY,
    MISTRAL_API_KEY,
    SAMBANOVA_API_KEY,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Provider:
    name: str
    kind: str            # "openai" (OpenAI-compatible) | "gemini"
    api_key: str
    model: str
    base_url: str | None = None
    tier: int = 0        # 0 = llama-class primary, 1 = gemini, 2 = overflow
    rpd: int = 1000      # free-tier requests/day budget
    tpd: int | None = None  # free-tier tokens/day budget (None = not token-capped)
    speed: int = 0       # 0 = fast (1-3 s/call); 1 = slow -- only used when
                         # every fast same-tier peer is exhausted. Budget
                         # balancing alone routed ~45% of calls to NVIDIA at
                         # 60-90 s/call and pushed scans past the CI timeout
                         # (June 12 2026).


def _providers() -> list[_Provider]:
    """Build the waterfall from whatever API keys are configured.

    rpd/tpd are the published free-tier daily caps (mid-2026); they bound how
    much the gateway will ask of a provider per day, NOT hard truths -- a
    provider that 429s earlier is still handled by the error path.
    """
    candidates = [
        # Groq serves gpt-oss-120b, the most capable model on its free tier.
        # It replaced llama-3.3-70b-versatile, which Groq retired ~2026-08-17.
        # NOTE: gpt-oss is a REASONING model -- it spends tokens thinking before
        # the answer, so callers must not use tight max_tokens. Measured on
        # Groq: a JSON-mode call at max_tokens=200 returns HTTP 400 "Failed to
        # validate JSON" (the object never closes) and a prose call at 150 can
        # come back empty; 512 is the safe floor. Its binding free-tier
        # constraint is tokens (~100K/day), not the 1K requests/day.
        _Provider("groq", "openai", GROQ_API_KEY,
                  "openai/gpt-oss-120b", "https://api.groq.com/openai/v1",
                  tier=0, rpd=1000, tpd=100_000),
        # Cerebras has by far the biggest free token quota (~1M/day) and is
        # very fast; it absorbs the heavy load Groq's token cap can't carry.
        _Provider("cerebras", "openai", CEREBRAS_API_KEY,
                  "gpt-oss-120b", "https://api.cerebras.ai/v1",
                  tier=0, rpd=2000, tpd=1_000_000),
        # Second free accounts for the two fast workhorses -- own name => own
        # budget row in llm_usage, so they roughly DOUBLE the daily tier-0
        # ceiling. Dropped automatically (line below) when the _2 key is unset.
        _Provider("cerebras_2", "openai", CEREBRAS_API_KEY_2,
                  "gpt-oss-120b", "https://api.cerebras.ai/v1",
                  tier=0, rpd=2000, tpd=1_000_000),
        _Provider("groq_2", "openai", GROQ_API_KEY_2,
                  "openai/gpt-oss-120b", "https://api.groq.com/openai/v1",
                  tier=0, rpd=1000, tpd=100_000),
        # ── Cerebras replacements (both OPTIONAL; dropped when the key is
        # unset, so this is inert until a key is added) ────────────────────
        # Cerebras went HTTP 402 "Payment required" on BOTH keys in Aug 2026 --
        # a billing wall, not a daily cap, so it does not reset. That removed
        # ~2M of the ~2.2M designed daily tokens and starved dossier
        # generation, which froze the dashboard for six days.
        #
        # Mistral's free "Experiment" tier is the biggest like-for-like
        # replacement available without a card: ~1B tokens/MONTH (~33M/day)
        # versus the 1M/day Cerebras gave. Endpoint verified OpenAI-shaped
        # (POST /v1/chat/completions returns 401 without a key, not 404).
        # tpd is set conservatively below the headline figure because the free
        # tier's RPM ceiling, not the token pool, is the real constraint.
        _Provider("mistral", "openai", MISTRAL_API_KEY,
                  "mistral-large-latest", "https://api.mistral.ai/v1",
                  tier=0, rpd=2000, tpd=1_000_000),
        # SambaNova Cloud -- free via email signup, Llama/Qwen. Second string:
        # smaller and less documented than Mistral, so speed=1 tries it last.
        _Provider("sambanova", "openai", SAMBANOVA_API_KEY,
                  "Meta-Llama-3.3-70B-Instruct", "https://api.sambanova.ai/v1",
                  tier=0, speed=1, rpd=1000, tpd=500_000),
        _Provider("nvidia", "openai", NVIDIA_API_KEY,
                  "meta/llama-3.3-70b-instruct", "https://integrate.api.nvidia.com/v1",
                  tier=0, rpd=500, speed=1),
        # Gemini sits behind the llama-class primaries: different model
        # family (voice drift) and its 2.5-flash free tier is ~250 req/day
        # (the older 1,500/day figure applied to 1.5-flash).
        _Provider("gemini", "gemini", GEMINI_API_KEY,
                  "gemini-2.5-flash",
                  tier=1, rpd=250),
        # Gemini free quota is metered PER MODEL, so each additional model is a
        # genuinely separate bucket rather than a relabelling of the same pool.
        # That matters now that Cerebras is 402 (billing wall) and NVIDIA hangs:
        # these two are real replacement capacity, both verified for prose AND
        # JSON mode on 2026-08-20. gemini-3.1-pro-preview is NOT here -- it
        # returns 429 on the free tier.
        _Provider("gemini-3-flash", "gemini", GEMINI_API_KEY,
                  "gemini-3-flash-preview",
                  tier=1, rpd=250),
        _Provider("gemini-3-lite", "gemini", GEMINI_API_KEY,
                  "gemini-3.1-flash-lite",
                  tier=1, speed=1, rpd=250),
        # OpenRouter free slots: last-resort overflow when the primaries are
        # rate-limited. The account-level FREE cap is ~50 req/day (shared
        # across these models) + ~20 req/min; two models give per-minute
        # rotation so one model's throttle doesn't stall the chain. BOTH are
        # FREE (`:free`) -- never a paid slot. nemotron-3-ultra-550b goes first
        # (the most capable free slot on offer, prose + JSON verified);
        # nemotron-3.5-lightning is the fast backup (speed=1 => tried last).
        # Both previous slots (openai/gpt-oss-120b:free and
        # meta-llama/llama-3.3-70b-instruct:free) now return
        # 404 "This model is unavailable" -- OpenRouter rotated its free
        # lineup, so this entire overflow tier had been silently dead. Verified
        # against GET /api/v1/models and a live prose+JSON call on 2026-08-20.
        # Re-check these ids whenever the last-resort tier looks unused: a free
        # slot disappearing is normal and fails 404, not loudly.
        _Provider("openrouter-nemotron", "openai", OPENROUTER_API_KEY,
                  "nvidia/nemotron-3-ultra-550b-a55b:free",
                  "https://openrouter.ai/api/v1",
                  tier=2, rpd=50),
        _Provider("openrouter-lightning", "openai", OPENROUTER_API_KEY,
                  "nvidia/nemotron-3.5-lightning:free",
                  "https://openrouter.ai/api/v1",
                  tier=2, speed=1, rpd=50),
    ]
    return [p for p in candidates if p.api_key]


# ── Daily usage ledger ──────────────────────────────────────────────────────
# Source of truth is the llm_usage Supabase table (migration 048): one row per
# (day, provider), incremented via the increment_llm_usage RPC after every
# call. We read it once lazily, then keep local deltas and re-sync every
# _LEDGER_REFRESH_CALLS calls to see what parallel jobs have consumed.
# All DB access is best-effort -- a ledger outage must never block completion.

_LEDGER_REFRESH_CALLS = 25

_usage: dict[str, dict[str, int]] = {}   # provider -> {"requests": n, "tokens": n}
_ledger_db = None                        # cached supabase client (or False = unavailable)
_calls_since_refresh = 0
_lock = threading.Lock()                 # guards _usage / _exhausted under concurrent dossier generation


def _ledger_client():
    global _ledger_db
    if _ledger_db is None:
        try:
            from config import SUPABASE_SERVICE_KEY, SUPABASE_URL
            from supabase import create_client
            _ledger_db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        except Exception as exc:  # noqa: BLE001
            log.debug("LLM ledger unavailable (%s) -- in-process tracking only", exc)
            _ledger_db = False
    return _ledger_db or None


def _refresh_usage() -> None:
    """Pull today's per-provider totals from the shared ledger."""
    global _calls_since_refresh
    _calls_since_refresh = 0
    db = _ledger_client()
    if not db:
        return
    try:
        from datetime import date
        rows = (db.table("llm_usage").select("provider,requests,total_tokens")
                .eq("usage_date", date.today().isoformat()).execute().data or [])
        with _lock:
            for r in rows:
                _usage[r["provider"]] = {
                    "requests": int(r["requests"] or 0),
                    "tokens":   int(r["total_tokens"] or 0),
                }
    except Exception as exc:  # noqa: BLE001
        log.debug("LLM ledger read failed: %s", exc)


def _record_usage(provider: str, tokens: int) -> None:
    with _lock:
        u = _usage.setdefault(provider, {"requests": 0, "tokens": 0})
        u["requests"] += 1
        u["tokens"] += tokens
    db = _ledger_client()
    if not db:
        return
    try:
        db.rpc("increment_llm_usage",
               {"p_provider": provider, "p_requests": 1,
                "p_tokens": tokens}).execute()
    except Exception as exc:  # noqa: BLE001
        log.debug("LLM ledger write failed: %s", exc)


def _remaining_fraction(p: _Provider) -> float:
    """Smallest remaining fraction across the provider's request and token
    budgets (0.0 = a daily cap is spent)."""
    u = _usage.get(p.name, {"requests": 0, "tokens": 0})
    frac = max(0.0, 1.0 - u["requests"] / p.rpd)
    if p.tpd:
        frac = min(frac, max(0.0, 1.0 - u["tokens"] / p.tpd))
    return frac


def usage_summary() -> dict[str, dict]:
    """Today's {provider: {requests, tokens, remaining_fraction}} -- for logs
    and the orchestrator's budget report."""
    _refresh_usage()
    out = {}
    for p in _providers():
        u = _usage.get(p.name, {"requests": 0, "tokens": 0})
        out[p.name] = {**u, "remaining_fraction": round(_remaining_fraction(p), 3)}
    return out


# ── Failure handling ────────────────────────────────────────────────────────
# Providers that hit a *daily* cap are skipped for the rest of THIS process
# (one scan run / one agent subprocess). A fresh CI job starts clean.
_exhausted: set[str] = set()

# Per-call retry budget for transient per-minute rate-limits (HTTP 429 without
# a daily-cap signal). Cerebras free tier is ~30 req/min, so a short backoff
# lets it keep carrying load instead of being dropped for the whole run.
_MAX_RETRIES = 3
_BACKOFF_BASE_SEC = 4


def _is_daily_cap(exc: Exception) -> bool:
    """A hard cap that won't recover within this run -- disable the provider."""
    m = str(exc).lower()
    return any(s in m for s in (
        "per day", "tpd", "rpd", "requests per day", "tokens per day",
        "daily", "resource_exhausted", "quota",
    ))


def _is_unreachable(exc: Exception) -> bool:
    """A provider that hangs or refuses the connection is unusable THIS run.

    NVIDIA currently accepts the request and never answers: measured at 120s
    with no response, on a model its own /v1/models still lists. Because the
    generic failure path deliberately does not disable a provider ("may recover
    next call"), every single LLM call was paying the full client timeout on
    NVIDIA before moving on. Inside a scan with a 46-minute soft deadline that
    is ruinous -- it is exactly how debate_synthesizer ended up with 180s and
    produced zero dossiers.

    A timeout is a strong signal, not a blip: disable for the process, same as a
    daily cap. The next process starts clean, so a recovered provider comes back
    on the next run.
    """
    m = str(exc).lower()
    return ("timed out" in m or "timeout" in m or "connection error" in m
            or "connection aborted" in m or "apiconnectionerror" in m)


def _is_rate_limit(exc: Exception) -> bool:
    """A transient per-minute throttle -- back off and retry the same provider."""
    m = str(exc).lower()
    return ("429" in m or "too many requests" in m
            or "rate limit" in m or "rate_limit" in m)


def _call_openai(p: _Provider, prompt: str, system: str | None,
                 temperature: float, max_tokens: int,
                 json_mode: bool) -> tuple[str | None, int]:
    from openai import OpenAI
    # max_retries=0 is deliberate. The SDK defaults to 2 retries and HONOURS the
    # provider's Retry-After header, so a rate-limited provider made it sleep
    # ~10 minutes INSIDE this call -- before the gateway ever saw the 429 and
    # could fail over. On 2026-08-19 that burned 20 of multibagger Stage 3's
    # 30-minute budget on two sleeps and killed the job:
    #     19:04:59 cerebras_2 daily cap hit
    #     19:14:59 Retrying request...
    #     19:25:00 Retrying request...
    #     19:29:45 nvidia failed  -> job cancelled at 30 min
    # This module already knows what to do with a 429: disable that provider and
    # move to the next one. Retrying inside the SDK defeats the whole waterfall,
    # because a provider that asks for 600 seconds is simply unavailable for
    # this run. timeout bounds a single call so one slow provider cannot stall
    # the chain either.
    client = OpenAI(api_key=p.api_key, base_url=p.base_url,
                    max_retries=0, timeout=90.0)
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    kwargs: dict = dict(model=p.model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    tokens = int(getattr(getattr(resp, "usage", None), "total_tokens", 0) or 0)
    return (resp.choices[0].message.content or "").strip() or None, tokens


def _call_gemini(p: _Provider, prompt: str, system: str | None,
                 temperature: float, max_tokens: int,
                 json_mode: bool) -> tuple[str | None, int]:
    import google.generativeai as genai
    genai.configure(api_key=p.api_key)
    model = genai.GenerativeModel(p.model, system_instruction=system or None)
    gen_cfg: dict = {"temperature": temperature, "max_output_tokens": max_tokens}
    if json_mode:
        gen_cfg["response_mime_type"] = "application/json"
    resp = model.generate_content(prompt, generation_config=gen_cfg)
    meta = getattr(resp, "usage_metadata", None)
    tokens = int(getattr(meta, "total_token_count", 0) or 0)
    return (resp.text or "").strip() or None, tokens


def _eligible_providers() -> list[_Provider]:
    """Configured providers with daily budget left, ordered: tier first, fast
    before slow within a tier, then largest remaining budget fraction
    (balances load across the FAST tier-0 primaries; slow providers are
    same-tier fallbacks, not load-bearing -- see _Provider.speed)."""
    ps = [p for p in _providers()
          if p.name not in _exhausted and _remaining_fraction(p) > 0.0]
    # Under concurrent dossier generation every thread would otherwise pick the
    # SAME top provider (largest budget) at the same instant -> a per-minute 429
    # storm on one account, then a lockstep backoff that starves the critic. We
    # bucket the budget fraction to 2dp and tie-break RANDOMLY, so near-equal
    # providers (e.g. cerebras + cerebras_2) get sharded across threads --
    # roughly doubling effective per-minute throughput on the fast tier.
    return sorted(
        ps,
        key=lambda p: (p.tier, p.speed, round(-_remaining_fraction(p), 2), random.random()),
    )


def complete(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.5,
    max_tokens: int = 1600,
    json_mode: bool = False,
) -> tuple[str | None, str]:
    """Run the prompt through the provider waterfall.

    Set ``json_mode=True`` to ask providers for a JSON object response
    (OpenAI ``response_format`` / Gemini ``response_mime_type``).

    Returns ``(text, provider_name)``. ``text`` is ``None`` and provider is
    ``"none"`` only if every configured provider was missing, empty, or
    rate-limited.
    """
    global _calls_since_refresh
    if not _providers():
        log.warning("LLM gateway: no provider API keys configured "
                    "(set GROQ_API_KEY / CEREBRAS_API_KEY / NVIDIA_API_KEY / "
                    "GEMINI_API_KEY / OPENROUTER_API_KEY)")
        return None, "none"

    if not _usage or _calls_since_refresh >= _LEDGER_REFRESH_CALLS:
        _refresh_usage()
    _calls_since_refresh += 1

    providers = _eligible_providers()
    if not providers:
        log.warning("LLM gateway: every configured provider is over its "
                    "daily budget -- %s", usage_summary())
        return None, "none"

    for p in providers:
        # Per-provider retry loop: a transient per-minute 429 backs off and
        # retries the SAME provider (so a fast, big-quota provider like
        # Cerebras keeps carrying load); a daily cap disables it; anything
        # else falls through to the next provider.
        for attempt in range(_MAX_RETRIES):
            try:
                if p.kind == "openai":
                    text, tokens = _call_openai(p, prompt, system, temperature,
                                                max_tokens, json_mode)
                else:
                    text, tokens = _call_gemini(p, prompt, system, temperature,
                                                max_tokens, json_mode)
                _record_usage(p.name, tokens)
                if text:
                    return text, p.name
                log.debug("LLM %s returned empty -- trying next provider", p.name)
                break  # empty completion -- next provider
            except Exception as exc:  # noqa: BLE001 -- providers raise many error types
                if _is_daily_cap(exc):
                    log.warning("LLM %s daily cap hit -- disabling for the rest "
                                "of this run", p.name)
                    with _lock:
                        _exhausted.add(p.name)
                    break  # next provider
                if _is_rate_limit(exc) and attempt < _MAX_RETRIES - 1:
                    wait = _BACKOFF_BASE_SEC * (attempt + 1)
                    log.info("LLM %s rate-limited (per-minute) -- backing off %ds "
                             "and retrying", p.name, wait)
                    time.sleep(wait)
                    continue  # retry SAME provider
                if _is_unreachable(exc):
                    log.warning("LLM %s unreachable (%s) -- disabling for the "
                                "rest of this run so it stops costing a full "
                                "timeout on every call", p.name, exc)
                    with _lock:
                        _exhausted.add(p.name)
                    break  # next provider
                log.warning("LLM %s failed: %s -- trying next provider", p.name, exc)
                break  # next provider (do not disable -- may recover next call)

    return None, "none"


def available_providers() -> list[str]:
    """Names of providers that currently have an API key configured."""
    return [p.name for p in _providers()]


def reset_exhausted() -> None:
    """Clear the per-process exhausted set + cached usage (useful in tests)."""
    _exhausted.clear()
    _usage.clear()
