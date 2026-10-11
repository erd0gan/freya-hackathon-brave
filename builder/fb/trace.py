"""Plain-text run trace (hackathon rules: start/end time, every model call with its
purpose, what was created on Freya, every self-test and its result). Stdlib only."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

_SECRET = re.compile(r"(fak_[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)")


def _utc(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def redact(text: str) -> str:
    out = _SECRET.sub("<redacted>", text)
    for name in ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        val = os.environ.get(name)
        if val and len(val) >= 8:
            out = out.replace(val, "<redacted>")
    return out


class Trace:
    def __init__(self, path: Path | None = None, echo: bool = True) -> None:
        self.t0 = time.time()
        self.path = path
        self.echo = echo
        self.model_calls = 0
        self.model_failures = 0
        self.cost_usd = 0.0
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def line(self, kind: str, msg: str) -> None:
        text = redact(f"{time.strftime('%H:%M:%S', time.gmtime())}  {kind:<6} {msg}")
        if self.echo:
            try:
                print(text, file=sys.stderr, flush=True)
            except UnicodeEncodeError:  # a cp1254/cp1252 console cannot print '₺' or '→'
                enc = getattr(sys.stderr, "encoding", None) or "ascii"
                print(text.encode(enc, "replace").decode(enc), file=sys.stderr, flush=True)
        if self.path:
            # the file is UTF-8 whatever the platform's locale: one '₺' in a Turkish document
            # killed the whole build on a cp1254 Windows console (11 Oct audit)
            with self.path.open("a", encoding="utf-8", errors="replace") as f:
                f.write(text + "\n")

    def start(self, **info: object) -> None:
        self.line("RUN", f"start {_utc(self.t0)} " + " ".join(f"{k}={v}" for k, v in info.items()))

    def model(self, model: str, purpose: str, cost_usd: float = 0.0, tokens: int = 0) -> None:
        self.model_calls += 1
        self.cost_usd += cost_usd
        self.line("MODEL", f"{model}  purpose: {purpose}  tokens={tokens} cost=${cost_usd:.4f}")

    def model_failed(self, model: str, purpose: str, seconds: float, error: str) -> None:
        """A model call that returned no answer (HTTP error, timeout, error body)."""
        self.model_failures += 1
        self.line(
            "MODEL",
            f"{model}  purpose: {purpose}  FAILED after {seconds:.1f}s: "
            + " ".join(str(error).split())[:300],
        )

    def freya(self, msg: str) -> None:
        self.line("FREYA", msg)

    def test(self, msg: str) -> None:
        self.line("TEST", msg)

    def note(self, msg: str) -> None:
        self.line("NOTE", msg)

    def elapsed(self) -> float:
        return time.time() - self.t0

    def end(self, **info: object) -> None:
        t1 = time.time()
        dur = t1 - self.t0
        self.line(
            "END",
            f"end {_utc(t1)} duration={int(dur // 60)}m{int(dur % 60):02d}s "
            f"model_calls={self.model_calls} model_cost=${self.cost_usd:.2f} "
            + (f"model_failures={self.model_failures} " if self.model_failures else "")
            + " ".join(f"{k}={v}" for k, v in info.items()),
        )
