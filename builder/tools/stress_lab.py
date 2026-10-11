"""Runnable synthetic bank/customer lab. Stdlib only; see --help."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fb.trace import Trace
from stresslab.bank import Bank, make_server
from stresslab.evaluate import assess
from stresslab.fixtures import contract
from stresslab.runner import Models, load_env, model_for, rebind_workflow, review, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List runnable fixtures")
    export = sub.add_parser("contract", help="Write a deployable tool contract")
    export.add_argument("brief", choices=list("CDEFGHIJ"))
    export.add_argument("--bank-url", default="http://127.0.0.1:8765")
    export.add_argument("--output", required=True)
    fixtures = sub.add_parser(
        "fixtures",
        help="Export all compiled private fixtures for human review; never use this as a customer prompt",
    )
    fixtures.add_argument("--output", required=True)
    serve = sub.add_parser("serve", help="Run fixture bank; admin routes need generated token")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--public-url")
    clone = sub.add_parser(
        "clone", help="Write a test copy of an exported workflow with fixture URLs"
    )
    clone.add_argument("workflow")
    clone.add_argument("--bank-url", required=True)
    clone.add_argument("--output", required=True)
    clone.add_argument(
        "--publish",
        action="store_true",
        help="Publish an isolated Freya test slot; never updates the official slot",
    )
    clone.add_argument("--state-file", default="traces/stress-lab/test-workflows.json")
    audit = sub.add_parser(
        "review", help="Reassess a saved call; preserves the original result and conversation"
    )
    audit.add_argument("run_directory")
    audit.add_argument("--judge-model")
    audit.add_argument("--max-cost", type=float, default=0.50)
    test = sub.add_parser(
        "run", help="Run independent model customer against baseline or Freya workflow"
    )
    test.add_argument("cases", nargs="+")
    test.add_argument("--mode", choices=["baseline", "freya"], default="baseline")
    test.add_argument("--workflow-id")
    test.add_argument("--port", type=int, default=8765)
    test.add_argument(
        "--public-url", help="Reachable tunnel URL forwarded to this process's --port"
    )
    test.add_argument("--repeat", type=int, default=1)
    test.add_argument("--output", default="traces/stress-lab")
    test.add_argument("--max-steps", type=int, default=36)
    test.add_argument("--max-calls", type=int, default=80)
    test.add_argument(
        "--max-cost",
        type=float,
        default=2.0,
        help="Reported USD threshold per case; one final request may cross it",
    )
    test.add_argument("--timeout", type=int, default=120)
    test.add_argument("--agent-model")
    test.add_argument("--customer-model")
    test.add_argument("--judge-model")
    test.add_argument(
        "--no-judge", action="store_true", help="Leave conversation/overall unassessed"
    )
    args = parser.parse_args()
    if args.command in {"contract", "clone"}:
        if args.command == "contract":
            value = contract(args.brief, args.bank_url.rstrip("/") + "/v1")
        else:
            value = json.loads(Path(args.workflow).read_text(encoding="utf-8"))
            value = rebind_workflow(value.get("workflow", value), args.bank_url)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        print(path)
        if args.command == "clone" and args.publish:
            if not args.bank_url.startswith("https://"):
                parser.error("Publishing a cloud test workflow requires a reachable HTTPS bank URL")
            load_env()
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "kit"))
            import freya

            state_path = Path(args.state_file)
            if state_path.name == ".freya-agents.json":
                parser.error("Use a separate test state file, not the production state file")
            state_path.parent.mkdir(parents=True, exist_ok=True)
            slot = "stress-lab-" + Path(args.workflow).stem
            freya.validate_workflow(slot, value)
            saved = freya.save_workflow(slot, value, str(state_path))
            # Newly created slots need the same publish/model-normalization step as updates.
            freya.update_workflow(saved["workflow_id"], value)
            print(json.dumps(saved))
        return
    bank = Bank()
    if args.command == "fixtures":
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "format": "stress-lab-compiled-v1",
                    "synthetic": True,
                    "private_test_data": True,
                    "fixtures": list(bank.fixtures.values()),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(path)
        return
    if args.command == "review":
        load_env()
        folder = Path(args.run_directory)
        original = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        ledger = json.loads((folder / "ledger.json").read_text(encoding="utf-8"))
        transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
        fixture = bank.fixtures[original["case_id"]]
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        trace = Trace(folder / f"review-{stamp}.log")
        result = assess(fixture, ledger)
        review(
            Models(trace, limit=1, max_cost=args.max_cost),
            fixture,
            transcript,
            ledger,
            result,
            args.judge_model or model_for("judge"),
        )
        result.update(
            case_id=original["case_id"],
            engine=original["engine"],
            original_result="result.json",
            original_error=original.get("error"),
            reported_model_cost_usd=trace.cost_usd,
        )
        if original.get("error"):
            result["overall_status"] = "error"
        report = folder / f"review-{stamp}.json"
        report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{result['overall_status']}: {report}")
        return
    if args.command == "list":
        for case in bank.fixtures.values():
            print(
                f"{case['id']}  brief={case['brief']}  {'branching persona' if 'evaluator_private' in case else 'scripted stress case'}"
            )
        return
    if args.command == "serve":
        server = make_server(bank, port=args.port, public_url=args.public_url)
        print(f"Synthetic bank: http://127.0.0.1:{server.server_port}")
        print(f"Admin token (local use only): {server.admin_token}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    if (
        not 1 <= args.repeat <= 10
        or not 1 <= args.max_steps <= 120
        or args.max_calls < 1
        or args.max_cost <= 0
    ):
        parser.error("Invalid run limits")
    if any(c not in bank.fixtures for c in args.cases):
        parser.error("Unknown case; use list")
    if args.mode == "freya" and (not args.workflow_id or not args.public_url):
        parser.error(
            "Freya needs --workflow-id and --public-url for this process's bank; localhost is unreachable from Freya"
        )
    load_env()
    invocation = Path(args.output) / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    server = make_server(bank, port=args.port, public_url=args.public_url)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = []
    try:
        if args.mode == "freya":
            with urllib.request.urlopen(
                args.public_url.rstrip("/") + "/health", timeout=20
            ) as response:
                health = json.load(response)
            if health.get("instance_id") != bank.instance_id:
                raise RuntimeError(
                    "Public URL does not reach this run's bank; point the tunnel to --port"
                )
        for case in args.cases:
            for repetition in range(1, args.repeat + 1):
                result = run(
                    bank,
                    case,
                    invocation / case / str(repetition),
                    mode=args.mode,
                    workflow_id=args.workflow_id,
                    max_steps=args.max_steps,
                    max_calls=args.max_calls,
                    max_cost=args.max_cost,
                    timeout=args.timeout,
                    judge=not args.no_judge,
                    agent_model=args.agent_model,
                    customer_model=args.customer_model,
                    judge_model=args.judge_model,
                )
                results.append(result)
                print(
                    f"{case} #{repetition}: {result['overall_status']} (records={result['record_status']})",
                    flush=True,
                )
        summary = {
            "official": False,
            "engine": args.mode,
            "runs": results,
            "suite_passed": all(r["overall_status"] == "pass" for r in results),
            "repeats": args.repeat,
        }
        (invocation / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Report: {invocation / 'summary.json'}")
    finally:
        server.shutdown()
        server.server_close()
    if not summary["suite_passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
