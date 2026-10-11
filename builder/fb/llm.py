"""Model calls over urllib (the builder is stdlib only): OpenRouter's chat completions and
Anthropic's Messages API, side by side. Every call is written to the trace with its model,
provider, purpose, tokens and cost.

A Claude model goes to Anthropic's API first (when ANTHROPIC_API_KEY is set); a failed attempt (a
timeout, a gateway error, an error body) is tried again on OpenRouter, so one provider being down
does not stop a build. Without an Anthropic key everything goes to OpenRouter."""

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
# USD per token (list prices, 10 Oct 2026; the same on both providers); unknown models log 0.
PRICES = {
    "anthropic/claude-opus-5.5": (4e-6, 20e-6),
    "anthropic/claude-sonnet-5.5": (2e-6, 10e-6),
    "anthropic/claude-haiku-5.5": (1e-7, 5e-7),
    "anthropic/claude-fable-5.1": (1e-5, 5e-5),
}
PROVIDERS = ("openrouter", "anthropic")
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(RuntimeError):
    pass


def model_for(role: str) -> str:
    return os.environ.get("BUILDER_MODEL_" + role.upper()) or DEFAULT_MODEL


def anthropic_model(model: str) -> str:
    """OpenRouter's name of a Claude model -> Anthropic's ("anthropic/claude-opus-5.5" ->
    "claude-opus-5-5"); a name already in Anthropic's form is kept."""
    name = model.split("/", 1)[1] if model.startswith("anthropic/") else model
    return name.replace(".", "-")


def is_claude(model: str) -> bool:
    return model.startswith(("anthropic/", "claude-"))


def configured(model: str = DEFAULT_MODEL) -> list[str]:
    """Providers with a key for `model`, in the order they are tried. A Claude model goes to
    Anthropic's own API first and to OpenRouter only when that fails; any other model goes to
    OpenRouter alone. BUILDER_PROVIDER=openrouter puts OpenRouter first."""
    have = {
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")) and is_claude(model),
    }
    first = str(os.environ.get("BUILDER_PROVIDER") or "").strip().lower()
    if first not in PROVIDERS:
        first = "anthropic"
    order = [first] + [p for p in PROVIDERS if p != first]
    return [p for p in order if have.get(p)]


def _request(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float | None,
) -> tuple[urllib.request.Request, str]:
    if provider == "anthropic":
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        body: dict[str, Any] = {
            "model": anthropic_model(model),
            "max_tokens": max_tokens,
            "messages": [m for m in messages if m.get("role") != "system"],
        }
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        base = os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
        headers = {
            "x-api-key": os.environ.get("ANTHROPIC_API_KEY") or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        url = base.rstrip("/") + "/v1/messages"
        name = "anthropic:" + anthropic_model(model)
    else:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        base = os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
        headers = {
            "Authorization": "Bearer " + (os.environ.get("OPENROUTER_API_KEY") or ""),
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (build-the-builder)",
            "X-Title": "build-the-builder",
        }
        url = base.rstrip("/") + "/chat/completions"
        name = model
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    return req, name


def _read(provider: str, out: dict[str, Any]) -> tuple[str | None, int, int, float | None, str]:
    """(text or None, tokens in, tokens out, cost if the provider gave it, error text)."""
    if provider == "anthropic":
        if out.get("type") == "error" or ("error" in out and not out.get("content")):
            return None, 0, 0, None, json.dumps(out.get("error") or out)[:300]
        text = "".join(
            str(b.get("text") or "") for b in out.get("content") or [] if b.get("type") == "text"
        )
        u = out.get("usage") or {}
        tin = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
        tin += int(u.get("cache_creation_input_tokens") or 0)
        return text, tin, int(u.get("output_tokens") or 0), None, ""
    if "error" in out and not out.get("choices"):
        return None, 0, 0, None, json.dumps(out["error"])[:300]
    u = out.get("usage") or {}
    msg = (out.get("choices") or [{}])[0].get("message") or {}
    cost = float(u["cost"]) if u.get("cost") is not None else None
    return (
        str(msg.get("content") or ""),
        int(u.get("prompt_tokens") or 0),
        int(u.get("completion_tokens") or 0),
        cost,
        "",
    )


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
    """`deadline`: time.monotonic() value by which the call (with its retries) must be over.
    Attempt n goes to provider n mod (number of providers with a key), starting with the
    first one (configured()): a failed attempt is tried again on the other provider."""
    model = model or model_for("builder")
    order = configured(model)
    if not order:
        raise LLMError("no model provider key is set (OPENROUTER_API_KEY or ANTHROPIC_API_KEY)")
    last = ""
    no_temperature: set[str] = set()
    for attempt in range(retries + 1):
        provider = order[attempt % len(order)]
        left = (deadline - time.monotonic()) if deadline is not None else timeout
        if left < 20:
            raise LLMError(f"no time left for the model call ({purpose}); last error: {last}")
        temp = None if provider in no_temperature else temperature
        req, name = _request(provider, model, messages, max_tokens, temp)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=min(timeout, left)) as r:
                out = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else ""
            if isinstance(e, urllib.error.HTTPError) and e.code == 400 and "temperature" in detail:
                no_temperature.add(provider)  # a model that takes no sampling settings
            last = f"{type(e).__name__}: {e} {detail}"
            trace.model_failed(name, f"{purpose} (attempt {attempt + 1})", time.time() - t0, last)
            if len(order) == 1:
                time.sleep(3 * (attempt + 1))
            continue
        except Exception as e:  # not retried, as before; traced before it propagates
            trace.model_failed(
                name,
                f"{purpose} (attempt {attempt + 1})",
                time.time() - t0,
                f"{type(e).__name__}: {e}",
            )
            raise
        text, tin, tout, cost, err = _read(provider, out if isinstance(out, dict) else {})
        if text is None:
            last = err
            trace.model_failed(
                name, f"{purpose} (attempt {attempt + 1})", time.time() - t0, "error body: " + last
            )
            if len(order) == 1:
                time.sleep(3 * (attempt + 1))
            continue
        if cost is None:
            pin, pout = PRICES.get(model, (0.0, 0.0))
            cost = tin * pin + tout * pout
        trace.model(name, f"{purpose} ({time.time() - t0:.0f}s)", cost, tin + tout)
        return text
    raise LLMError(f"model call failed after {retries + 1} attempts: {last}")


def parse_json(text: str) -> Any:
    """The first JSON object in a reply, with or without a ```json fence."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{") : text.rfind("}") + 1]
    return json.loads(raw)
