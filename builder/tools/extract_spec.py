"""Dev tool: run the spec extractor on a brief and compare the result with a hand-written spec.

python3 builder/tools/extract_spec.py <brief.md> <openapi.json> [--expect A|B] [--out spec.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "tests" / "hack"))

from fb.openapi_tools import parse_openapi  # noqa: E402
from fb.spec_extract import check, extract, to_generator  # noqa: E402
from fb.trace import Trace  # noqa: E402


def diff(a: Any, b: Any, path: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in ("lines",):
                continue
            if k not in a:
                out.append(f"{path}.{k}: missing in extracted (expected {json.dumps(b[k])[:80]})")
            elif k not in b:
                continue
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
    elif a != b:
        out.append(f"{path}: got {json.dumps(a)[:100]} expected {json.dumps(b)[:100]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("openapi")
    ap.add_argument("--expect", choices=["A", "B"])
    ap.add_argument("--out")
    a = ap.parse_args()
    openapi = json.loads(Path(a.openapi).read_text(encoding="utf-8"))
    tools = parse_openapi(openapi)
    brief = Path(a.brief).read_text(encoding="utf-8")
    trace = Trace(None)
    spec = extract(brief, openapi, tools, trace)
    if a.out:
        Path(a.out).write_text(json.dumps(spec, indent=1))
    print("checks:", check(spec, tools, brief) or "ok")
    gen = to_generator(spec)
    print(json.dumps(gen["lines"], indent=1))
    if a.expect:
        from spec_fixtures import SPEC_A, SPEC_B

        exp = SPEC_A if a.expect == "A" else SPEC_B
        d = diff(gen["cfg"], exp["cfg"], "cfg") + diff(
            {k: gen[k] for k in ("identity", "outcomes", "callback_reasks")},
            {k: exp[k] for k in ("identity", "outcomes", "callback_reasks")},
        )
        print(f"{len(d)} differences from the hand-written spec:")
        for x in d:
            print("  ", x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
