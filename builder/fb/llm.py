"""OpenRouter chat completions over urllib (the builder is stdlib only). Every call is written
to the trace with its model, purpose, tokens and cost."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from fb.trace import Trace

DEFAULT_MODEL = "anthropic/claude-opus-5.5"
# USD per token (OpenRouter list prices, 10 Oct 2026); unknown models are logged at 0.
PRICES = {
    "anthropic/claude-opus-5.5": (4e-6, 20e-6),
    "anthropic/claude-sonnet-5.5": (2e-6, 10e-6),
    "anthropic/claude-haiku-5.5": (1e-7, 5e-7),
    "anthropic/claude-fable-5.1": (1e-5, 5e-5),
}


class LLMError(RuntimeError):
    pass


def model_for(role: str) -> str:
    return os.environ.get("BUILDER_MODEL_" + role.upper()) or DEFAULT_MODEL


def chat(
    trace: Trace,
    purpose: str,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    max_tokens: int = 16000,
    temperature: float = 0.0,
    timeout: float = 300.0,
    retries: int = 2,
    deadline: float | None = None,
) -> str:
    """`deadline`: time.monotonic() value by which the call (with its retries) must be over."""
    model = model or model_for("builder")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise LLMError("OPENROUTER_API_KEY is not set")
    base = os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    ).encode()
    last = ""
    for attempt in range(retries + 1):
        left = (deadline - time.monotonic()) if deadline is not None else timeout
        if left < 20:
            raise LLMError(f"no time left for the model call ({purpose}); last error: {last}")
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (build-the-builder)",
                "X-Title": "build-the-builder",
            },
            method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=min(timeout, left)) as r:
                out = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else ""
            last = f"{type(e).__name__}: {e} {detail}"
            trace.note(f"model call failed ({purpose}), attempt {attempt + 1}: {last[:200]}")
            time.sleep(3 * (attempt + 1))
            continue
        if "error" in out and not out.get("choices"):
            last = json.dumps(out["error"])[:300]
            trace.note(f"model call error ({purpose}): {last}")
            time.sleep(3 * (attempt + 1))
            continue
        usage = out.get("usage") or {}
        pin, pout = PRICES.get(model, (0.0, 0.0))
        tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        cost = float(usage.get("cost") or (tin * pin + tout * pout))
        trace.model(model, f"{purpose} ({time.time() - t0:.0f}s)", cost, tin + tout)
        msg = (out.get("choices") or [{}])[0].get("message") or {}
        return str(msg.get("content") or "")
    raise LLMError(f"model call failed after {retries + 1} attempts: {last}")


def parse_json(text: str) -> Any:
    """The first JSON object in a reply, with or without a ```json fence."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{") : text.rfind("}") + 1]
    return json.loads(raw)
