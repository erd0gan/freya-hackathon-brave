"""IntellAgent-lite policy graph sampling (memo §58).

Nodes are policy rules weighted by complexity; edges carry a co-occurrence
likelihood (shared tools / category). Random walks produce policy combinations
spread uniformly across complexity buckets.
"""

from __future__ import annotations

import random

from builder.schema.policy import PolicySpec


def edge_weights(policy: PolicySpec) -> dict[str, dict[str, float]]:
    rules = policy.rules
    w: dict[str, dict[str, float]] = {r.id: {} for r in rules}
    for a in rules:
        for b in rules:
            if a.id == b.id:
                continue
            shared = len(set(a.affected_tools) & set(b.affected_tools))
            score = 0.2 + shared + (0.5 if a.category and a.category == b.category else 0.0)
            # Authorization rules co-occur with everything that writes.
            if b.level.value == "authorization" and a.affected_tools:
                score += 0.5
            w[a.id][b.id] = score
    return w


def sample_combinations(
    policy: PolicySpec, n: int, seed: int = 0, max_complexity: int = 8
) -> list[list[str]]:
    rules = {r.id: r for r in policy.rules}
    if not rules:
        return [[] for _ in range(n)]
    rng = random.Random(seed)
    w = edge_weights(policy)
    lo = min(r.complexity for r in rules.values())
    hi = max(lo + 1, min(max_complexity, sum(r.complexity for r in rules.values())))
    out: list[list[str]] = []
    for i in range(n):
        target = lo + (hi - lo) * i / max(n - 1, 1)  # uniform over complexity range
        cur = rng.choice(list(rules))
        walk = [cur]
        total = rules[cur].complexity
        while total < target:
            nbrs = {k: v for k, v in w[cur].items() if k not in walk}
            if not nbrs:
                break
            cur = rng.choices(list(nbrs), weights=list(nbrs.values()))[0]
            walk.append(cur)
            total += rules[cur].complexity
        out.append(walk)
    rng.shuffle(out)
    return out
