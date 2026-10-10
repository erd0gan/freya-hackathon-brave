"""Gap-analysis check, see docs/GAP_ANALYSIS.md. Run: uv run python scripts/gap_checks/llm_lock_concurrency.py"""
# ruff: noqa: E501

import time
from concurrent.futures import ThreadPoolExecutor

from builder.llm import CostLedger, LLMClient, LLMRequest, LLMResponse, Message, Role, Usage


class SlowProvider:
    def complete(self, model, request):
        time.sleep(0.5)  # simulated network latency
        return LLMResponse(
            content="ok", usage=Usage(prompt_tokens=1, completion_tokens=1, cost_usd=0.0)
        )


models = {r: "x" for r in Role}
c = LLMClient(SlowProvider(), models, CostLedger(None, 50.0), cache=None)


def one(i):
    return c.complete(LLMRequest(role=Role.AGENT, messages=[Message(role="user", content=str(i))]))


t = time.monotonic()
with ThreadPoolExecutor(max_workers=4) as p:
    list(p.map(one, range(8)))
print(
    f"8 calls x 0.5s latency, 4 threads: {time.monotonic() - t:.2f}s  (ideal parallel ~1.0s, serial 4.0s)"
)
