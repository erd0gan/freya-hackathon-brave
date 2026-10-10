"""Build a Freya phone agent from one brief and its tool API file.

    python3 builder/build.py <brief_path> <tool_api_path>

Stdlib only. Writes traces/<brief>.log and submission/<brief>.workflow.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from fb.openapi_tools import parse_openapi  # noqa: E402
from fb.trace import Trace  # noqa: E402


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    brief_path, api_path = Path(argv[1]), Path(argv[2])
    slot = brief_path.stem
    trace = Trace(ROOT / "traces" / f"{slot}.log")
    trace.start(brief=brief_path, tool_api=api_path, commit=git_commit())
    brief = brief_path.read_text()
    tools = parse_openapi(json.loads(api_path.read_text()))
    trace.note(f"brief: {len(brief)} chars; tools: {', '.join(sorted(tools))}")
    # TODO(next): spec extraction -> workflow generation -> validate -> save -> smoke -> export
    trace.end(status="skeleton")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
