"""Dev tool: generate the workflow from a hand-written public spec, validate, save, print ids.

    python3 builder/tools/deploy_spec.py A|B [--slot gen-a] [--dry] [--out out/gen-a.json]

The specs come from tests/hack/spec_fixtures.py (public briefs only). The real builder gets
its spec from the extractor.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "kit"))
sys.path.insert(0, str(ROOT / "tests" / "hack"))

import freya  # noqa: E402
from fb.generate import build  # noqa: E402
from fb.openapi_tools import parse_openapi  # noqa: E402
from fb.validate import validate  # noqa: E402
from spec_fixtures import SPEC_A, SPEC_B  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("brief", choices=["A", "B"])
    ap.add_argument("--slot")
    ap.add_argument("--dry", action="store_true", help="validate only, do not save")
    ap.add_argument("--out")
    a = ap.parse_args()
    tools = parse_openapi(json.loads((ROOT / "kit" / "tools" / "openapi.json").read_text()))
    wf, policy = build(SPEC_A if a.brief == "A" else SPEC_B, tools)
    raw = json.dumps(wf)
    print(f"nodes {len(wf['nodes'])}, json {len(raw)} bytes")
    rep = validate(wf, tools, policy)
    for i in rep.issues:
        print(i)
    print("local validator:", rep.summary())
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(wf, indent=1))
    if not rep.ok:
        return 1
    try:
        freya.validate_workflow("dry", wf)
        print("platform dry run: ok")
    except RuntimeError as e:
        print("platform dry run FAILED:", str(e)[:2000])
        return 1
    if a.dry:
        return 0
    os.chdir(ROOT / "kit")
    saved = freya.save_workflow(a.slot or f"gen-{a.brief.lower()}", wf)
    print(json.dumps(saved))
    return 0


if __name__ == "__main__":
    sys.exit(main())
