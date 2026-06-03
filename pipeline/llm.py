"""
Shared LLM gateway -- quota-aware provider waterfall.
====================================================

One ``complete()`` call that every agent uses instead of rolling its own
Gemini/Groq fallback. Tries each configured free provider in order until one
returns text; a provider that hits a quota / rate-limit (HTTP 429) is disabled
for the rest of the process so we don't keep hammering it.

WHY: a single full deep scan can exhaust one free tier's daily token budget
(that is exactly what took the critic agent down in June 2026). Fanning the
same workload across several free providers multiplies the effective daily
budget and removes the single point of failure.

PROVIDER ORDER:

    groq  ->  cerebras  ->  nvidia  ->  gemini

Groq (the tuned default) leads; Cerebras -- by far the largest free quota --
is the overflow that absorbs heavy load once Groq's daily cap drains.

Groq and NVIDIA serve ``llama-3.3-70b``; Cerebras serves ``gpt-oss-120b``
(it no longer offers Llama) and Gemini serves ``gemini-2.5-flash``. Cerebras /
Groq / NVIDIA are all OpenAI-compatible (same ``/chat/completions`` shape) and
go through the ``openai`` SDK by swapping ``base_url``; Gemini uses its own SDK
and sits last
(its free tier may train on inputs -- keep proprietary theses off it when a
peer is available).

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
from dataclasses import dataclass

from config import (  # noqa: E402
    CEREBRAS_API_KEY,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    NVIDIA_API_KEY,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Provider:
    name: str
    kind: str            # "openai" (OpenAI-compatible) | "gemini"
    api_key: str
    model: str
    base_url: str | None = None


def _providers() -> list[_Provider]:
    """Build the waterfall from whatever API keys are configured."""
    candidates = [
        # Groq (llama) is the tuned default and serves short-output tasks
        # (e.g. catalyst's 120-token calls) that the gpt-oss reasoning model
        # can't. Cerebras has by far the biggest free quota, so it sits second
        # as the overflow that absorbs heavy load once Groq's daily cap drains.
        _Provider("groq", "openai", GROQ_API_KEY,
                  "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1"),
        _Provider("cerebras", "openai", CEREBRAS_API_KEY,
                  "gpt-oss-120b", "https://api.cerebras.ai/v1"),
        _Provider("nvidia", "openai", NVIDIA_API_KEY,
                  "meta/llama-3.3-70b-instruct", "https://integrate.api.nvidia.com/v1"),
        _Provider("gemini", "gemini", GEMINI_API_KEY,
                  "gemini-2.5-flash"),
    ]
    return [p for p in candidates if p.api_key]


# Providers that returned a quota / rate-limit error are skipped for the rest
# of THIS process (one scan run / one agent invocation). A fresh CI job starts
# with a clean slate.
_exhausted: set[str] = set()


def _is_quota_error(exc: Exception) -> bool:
    m = str(exc).lower()
    return any(s in m for s in (
        "429", "resource_exhausted", "quota",
        "rate limit", "rate_limit", "too many requests",
    ))


def _call_openai(p: _Provider, prompt: str, system: str | None,
                 temperature: float, max_tokens: int, json_mode: bool) -> str | None:
    from openai import OpenAI
    client = OpenAI(api_key=p.api_key, base_url=p.base_url)
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    kwargs: dict = dict(model=p.model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip() or None


def _call_gemini(p: _Provider, prompt: str, system: str | None,
                 temperature: float, max_tokens: int, json_mode: bool) -> str | None:
    import google.generativeai as genai
    genai.configure(api_key=p.api_key)
    model = genai.GenerativeModel(p.model, system_instruction=system or None)
    gen_cfg: dict = {"temperature": temperature, "max_output_tokens": max_tokens}
    if json_mode:
        gen_cfg["response_mime_type"] = "application/json"
    resp = model.generate_content(prompt, generation_config=gen_cfg)
    return (resp.text or "").strip() or None


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
    providers = _providers()
    if not providers:
        log.warning("LLM gateway: no provider API keys configured "
                    "(set CEREBRAS_API_KEY / GROQ_API_KEY / NVIDIA_API_KEY / GEMINI_API_KEY)")
        return None, "none"

    for p in providers:
        if p.name in _exhausted:
            continue
        try:
            if p.kind == "openai":
                text = _call_openai(p, prompt, system, temperature, max_tokens, json_mode)
            else:
                text = _call_gemini(p, prompt, system, temperature, max_tokens, json_mode)
            if text:
                return text, p.name
            # Empty completion -- treat as a miss and try the next provider.
            log.debug("LLM %s returned empty -- trying next provider", p.name)
        except Exception as exc:  # noqa: BLE001 -- providers raise many error types
            if _is_quota_error(exc):
                log.warning("LLM %s quota/rate-limit hit -- disabling for the "
                            "rest of this run", p.name)
                _exhausted.add(p.name)
            else:
                log.warning("LLM %s failed: %s -- trying next provider", p.name, exc)

    return None, "none"


def available_providers() -> list[str]:
    """Names of providers that currently have an API key configured."""
    return [p.name for p in _providers()]


def reset_exhausted() -> None:
    """Clear the per-process exhausted set (useful in tests)."""
    _exhausted.clear()
