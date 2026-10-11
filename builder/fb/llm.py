"""Model calls over urllib (the builder is stdlib only): OpenRouter's chat completions and
Anthropic's Messages API, side by side. Every call is written to the trace with its model,
provider, purpose, tokens and cost.

A Claude model goes to Anthropic's API first (when ANTHROPIC_API_KEY is set); a failed attempt (a
timeout, a gateway error, an error body) is tried again on OpenRouter, so one provider being down
does not stop a build. Without an Anthropic key everything goes to OpenRouter."""

from __future__ import annotations

import http.client
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
# A reply cut at max_tokens is asked again with a larger budget, up to this many tokens (a
# Turkish document set and its notes need far more output than the public briefs did).
MAX_TOKENS_CAP = 32000


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


def text_of(content: Any) -> str:
    """The text of a message's content: a plain string, or a list of content blocks
    ({"type": "text", "text": ...} in both providers' forms); other blocks (documents, images)
    have no text here."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text") or "") if isinstance(b, dict) else str(b)
            for b in content
            if (isinstance(b, dict) and b.get("type") in ("text", None) and b.get("text"))
            or isinstance(b, str)
        )
    return "" if content is None else str(content)


def _openai_blocks(content: Any) -> Any:
    """Anthropic-style content blocks -> OpenRouter's (OpenAI) form: text stays text, a base64
    image becomes an image_url data URL, a base64 PDF document a file part. A block neither
    form knows is dropped, so one document never stops the call."""
    if not isinstance(content, list):
        return content if isinstance(content, str) else text_of(content)
    out: list[dict[str, Any]] = []
    for b in content:
        if isinstance(b, str):
            out.append({"type": "text", "text": b})
            continue
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        src = b.get("source") if isinstance(b.get("source"), dict) else {}
        if kind == "text":
            out.append({"type": "text", "text": str(b.get("text") or "")})
        elif kind in ("image_url", "file"):
            out.append(b)  # already in OpenAI form
        elif kind == "image" and src.get("type") == "base64":
            url = f"data:{src.get('media_type') or 'image/png'};base64,{src.get('data') or ''}"
            out.append({"type": "image_url", "image_url": {"url": url}})
        elif kind == "document" and src.get("type") == "base64":
            media = src.get("media_type") or "application/pdf"
            data = f"data:{media};base64,{src.get('data') or ''}"
            name = str(b.get("title") or "document.pdf")
            out.append({"type": "file", "file": {"filename": name, "file_data": data}})
        elif kind == "document" and src.get("type") == "text":
            out.append({"type": "text", "text": str(src.get("data") or "")})
    return out


def _request(
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float | None,
) -> tuple[urllib.request.Request, str]:
    if provider == "anthropic":
        # a system message may carry content blocks too; Anthropic's system field is text
        system = "\n\n".join(
            text_of(m.get("content")) for m in messages if m.get("role") == "system"
        ).strip()
        body: dict[str, Any] = {
            "model": anthropic_model(model),
            "max_tokens": max_tokens,
            "messages": [m for m in messages if m.get("role") != "system"],
        }
        if system:
            body["system"] = system
        # no temperature: the current Claude models refuse it ("`temperature` is deprecated for
        # this model", 400, measured 11 Oct)
        base = os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
        headers = {
            "x-api-key": os.environ.get("ANTHROPIC_API_KEY") or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        url = base.rstrip("/") + "/v1/messages"
        name = "anthropic:" + anthropic_model(model)
    else:
        msgs = [{**m, "content": _openai_blocks(m.get("content"))} for m in messages]
        body = {"model": model, "messages": msgs, "max_tokens": max_tokens}
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


def _read(
    provider: str, out: dict[str, Any]
) -> tuple[str | None, int, int, float | None, str, bool]:
    """(text or None, tokens in, tokens out, cost if the provider gave it, error text, cut at
    max_tokens)."""
    if provider == "anthropic":
        if out.get("type") == "error" or ("error" in out and not out.get("content")):
            return None, 0, 0, None, json.dumps(out.get("error") or out)[:300], False
        blocks = out.get("content") if isinstance(out.get("content"), list) else []
        text = "".join(
            str(b.get("text") or "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
        u = out.get("usage") if isinstance(out.get("usage"), dict) else {}
        tin = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
        tin += int(u.get("cache_creation_input_tokens") or 0)
        cut = out.get("stop_reason") == "max_tokens"
        return text, tin, int(u.get("output_tokens") or 0), None, "", cut
    if "error" in out and not out.get("choices"):
        return None, 0, 0, None, json.dumps(out["error"])[:300], False
    u = out.get("usage") if isinstance(out.get("usage"), dict) else {}
    choices = out.get("choices") if isinstance(out.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    msg = first.get("message") if isinstance(first.get("message"), dict) else {}
    cost = float(u["cost"]) if u.get("cost") is not None else None
    return (
        text_of(msg.get("content")),  # some providers answer with a list of content parts
        int(u.get("prompt_tokens") or 0),
        int(u.get("completion_tokens") or 0),
        cost,
        "",
        first.get("finish_reason") == "length",
    )


def chat(
    trace: Trace,
    purpose: str,
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    max_tokens: int = 16000,
    temperature: float = 0.0,
    timeout: float = 300.0,
    retries: int = 5,
    deadline: float | None = None,
) -> str:
    """`deadline`: time.monotonic() value by which the call (with its retries) must be over.
    Attempt n goes to provider n mod (number of providers with a key), starting with the
    first one (configured()): a failed attempt is tried again on the other provider. A dropped
    connection (IncompleteRead, reset) is a failed attempt like a gateway error. `retries`
    counts rounds (each provider once): once every provider failed in a round, the next round
    waits longer (3 s, 6 s, 12 s, 24 s, 30 s), so an outage of a minute or so does not end the
    build (review SX-5: three tries in about 18 s did). A provider that refuses the key (401,
    403) is not asked again. A reply cut at max_tokens is asked again with twice the budget;
    when nothing better comes, the cut reply is returned (the caller's parser and its retry
    decide what to do with it)."""
    model = model or model_for("builder")
    order = configured(model)
    if not order:
        raise LLMError("no model provider key is set (OPENROUTER_API_KEY or ANTHROPIC_API_KEY)")
    last = ""
    cut_text = ""
    no_temperature: set[str] = set()
    refused: set[str] = set()  # providers that refused the key
    attempts = (retries + 1) * len(order)
    for attempt in range(attempts):
        provider = order[attempt % len(order)]
        if provider in refused:
            if len(refused) == len(order):
                break
            continue
        left = (deadline - time.monotonic()) if deadline is not None else timeout
        if left < 20:
            if cut_text:
                return cut_text
            raise LLMError(f"no time left for the model call ({purpose}); last error: {last}")
        temp = None if provider in no_temperature else temperature
        req, name = _request(provider, model, messages, max_tokens, temp)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=min(timeout, left)) as r:
                out = json.loads(r.read())
        except (
            urllib.error.URLError,  # HTTPError too
            TimeoutError,
            json.JSONDecodeError,
            http.client.HTTPException,  # IncompleteRead, RemoteDisconnected
            OSError,  # ConnectionResetError and the like
        ) as e:
            detail = ""
            if isinstance(e, urllib.error.HTTPError):
                try:
                    detail = e.read().decode(errors="replace")[:300]
                except Exception:
                    detail = ""
            if isinstance(e, urllib.error.HTTPError) and e.code == 400 and "temperature" in detail:
                no_temperature.add(provider)  # a model that takes no sampling settings
            if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403):
                refused.add(provider)  # a wrong key stays wrong: no rounds of waiting on it
            last = f"{type(e).__name__}: {e} {detail}"
            trace.model_failed(name, f"{purpose} (attempt {attempt + 1})", time.time() - t0, last)
            if attempt + 1 < attempts and len(refused) < len(order):
                _pause(attempt, len(order), deadline)
            continue
        except Exception as e:  # not retried, as before; traced before it propagates
            trace.model_failed(
                name,
                f"{purpose} (attempt {attempt + 1})",
                time.time() - t0,
                f"{type(e).__name__}: {e}",
            )
            raise
        text, tin, tout, cost, err, cut = _read(provider, out if isinstance(out, dict) else {})
        if text is not None and not text.strip():
            # a 200 with no text ({} or empty content) is a failed attempt too: the other
            # provider gets the next one (review 11 Oct: the build stopped at parsing '')
            text, err = None, "empty reply: " + json.dumps(out)[:200]
        if text is None:
            last = err
            trace.model_failed(
                name, f"{purpose} (attempt {attempt + 1})", time.time() - t0, "error body: " + last
            )
            if attempt + 1 < attempts:
                _pause(attempt, len(order), deadline)
            continue
        if cost is None:
            pin, pout = PRICES.get(model, (0.0, 0.0))
            cost = tin * pin + tout * pout
        if cut and attempt + 1 < attempts and max_tokens < MAX_TOKENS_CAP:
            # a JSON answer cut short never parses: ask again with room for all of it
            trace.model(name, f"{purpose} (cut at {max_tokens} tokens)", cost, tin + tout)
            cut_text, last = text, f"reply cut at max_tokens={max_tokens}"
            max_tokens = min(MAX_TOKENS_CAP, max_tokens * 2)
            continue
        trace.model(name, f"{purpose} ({time.time() - t0:.0f}s)", cost, tin + tout)
        return text
    if cut_text:
        return cut_text
    raise LLMError(f"model call failed after {attempt + 1} attempts: {last}")


def _pause(attempt: int, providers: int, deadline: float | None) -> None:
    """The wait after failed attempt `attempt`: none while another provider has not been tried
    in this round, then 3 s, 6 s, 12 s ... (at most 30 s, never into the deadline's margin)."""
    if providers > 1 and (attempt + 1) % providers:
        return
    wait = min(30.0, 3.0 * 2 ** (attempt // max(1, providers)))
    if deadline is not None:
        wait = min(wait, max(0.0, deadline - time.monotonic() - 25))
    if wait > 0:
        time.sleep(wait)


def parse_json(text: str) -> Any:
    """The JSON object of a reply: in a ```json fence or bare, with prose before or after it
    (prose with braces too, such as {{title}}). A raw line break in a string, a // or /* */
    comment and a trailing comma are tolerated. When several objects parse, the largest is the
    answer. Raises ValueError (a json.JSONDecodeError) when none parses."""
    text = str(text or "")
    sources = [m.group(1) for m in re.finditer(r"```(?:json|JSON)?\s*(.*?)```", text, re.S)]
    sources.append(text)
    first_err: ValueError | None = None
    for src in sources:
        best: Any = None
        best_len = -1
        # the cleaned text first: a comment in the outer object must not leave only an inner
        # object parseable (the largest object wins either way)
        for candidate in (_lenient(src), src):
            found, size, err = _largest_object(candidate)
            first_err = first_err or err
            if found is not None and size > best_len:
                best, best_len = found, size
        if best is not None:
            return best
    raise first_err or json.JSONDecodeError("no JSON object in the reply", text, 0)


def _largest_object(src: str) -> tuple[Any, int, ValueError | None]:
    dec = json.JSONDecoder(strict=False)
    best: Any = None
    best_len = -1
    err: ValueError | None = None
    pos = src.find("{")
    tries = 0
    while pos != -1 and tries < 400:
        tries += 1
        try:
            obj, end = dec.raw_decode(src, pos)
        except ValueError as e:
            err = err or e
            pos = src.find("{", pos + 1)
            continue
        if isinstance(obj, dict) and end - pos > best_len:
            best, best_len = obj, end - pos
        pos = src.find("{", end)  # the objects inside this one are parts of it
    return best, best_len, (None if best is not None else err)


def _lenient(src: str) -> str:
    """`src` without comments outside strings and without a comma right before } or ]
    (strings are left as they are)."""
    out: list[str] = []
    i, n = 0, len(src)
    in_str = False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        elif c == ",":
            j = i + 1
            while j < n and src[j] in " \t\r\n":
                j += 1
            if j < n and src[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)
