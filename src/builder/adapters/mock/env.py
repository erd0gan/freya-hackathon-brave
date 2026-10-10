"""Deterministic mock environment: a JSON database plus Python tool implementations.

A domain pack supplies the seed DB, the tool implementations and the input
bundle. Faults can be injected per tool to exercise recovery paths (memo §15.6).
"""

from __future__ import annotations

import contextlib
import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from builder.schema.bundle import InputBundle
from builder.testsynth.statediff import get_path

__all__ = ["DomainPack", "Environment", "ToolError", "get_path", "require"]


class ToolError(Exception):
    """Raised by a tool implementation; surfaced to the agent as an error result."""


ToolImpl = Callable[["Environment", dict[str, Any]], Any]


@dataclass
class DomainPack:
    name: str
    bundle: InputBundle
    seed_db: dict[str, Any]
    tools: dict[str, ToolImpl]
    # False for LLM-simulated backends: generated values (ids, dates) vary between runs.
    deterministic: bool = True


@dataclass
class Environment:
    db: dict[str, Any]
    tools: dict[str, ToolImpl]
    faults: dict[str, Any] = field(default_factory=dict)
    session: dict[str, Any] = field(default_factory=dict)
    call_log: list[tuple[str, dict[str, Any], bool]] = field(default_factory=list)
    salt: str = ""  # varies LLM-simulated backend answers (oracle stability check)

    @classmethod
    def from_pack(
        cls,
        pack: DomainPack,
        initial_state: dict[str, Any] | None,
        faults: dict[str, Any],
        salt: str = "",
    ) -> Environment:
        db = copy.deepcopy(initial_state if initial_state else pack.seed_db)
        return cls(db=db, tools=pack.tools, faults=dict(faults), salt=salt)

    def call(self, name: str, args: dict[str, Any]) -> tuple[bool, Any]:
        impl = self.tools.get(name)
        if impl is None:
            self.call_log.append((name, args, False))
            return False, {"error": f"unknown tool '{name}'"}
        fault = self.faults.get(name)
        if fault:
            consumed = self._consume_fault(name, fault)
            if consumed is not None:
                if consumed.get("apply_then_fail"):
                    # Side effect happens but the caller sees a timeout (memo §28 example).
                    with contextlib.suppress(ToolError):
                        impl(self, args)
                self.call_log.append((name, args, False))
                return False, {"error": consumed.get("error", "service unavailable")}
        try:
            result = impl(self, args)
        except ToolError as exc:
            self.call_log.append((name, args, False))
            return False, {"error": str(exc)}
        except (KeyError, TypeError, ValueError) as exc:
            self.call_log.append((name, args, False))
            return False, {"error": f"invalid arguments: {exc}"}
        self.call_log.append((name, args, True))
        return True, result

    def _consume_fault(self, name: str, fault: Any) -> dict[str, Any] | None:
        spec: dict[str, Any] = fault if isinstance(fault, dict) else {"error": str(fault)}
        times = int(spec.get("times", 1))
        if times <= 0:
            return None
        spec = {**spec, "times": times - 1}
        self.faults[name] = spec
        return spec


def require(args: dict[str, Any], *names: str) -> None:
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise ToolError(f"missing required argument(s): {', '.join(missing)}")
