"""Train / validation / holdout split, stratified by category (memo §34.1–34.2).

The optimizer never sees validation traces; holdout is touched only for the
final generalization estimate.
"""

from __future__ import annotations

import random
from collections import defaultdict
from itertools import zip_longest

from builder.schema.scenario import ScenarioSpec, Split


def split_scenarios(
    scenarios: list[ScenarioSpec],
    validation_fraction: float = 0.34,
    holdout_fraction: float = 0.0,
    seed: int = 0,
) -> dict[Split, list[ScenarioSpec]]:
    if (
        not 0 <= validation_fraction < 1
        or not 0 <= holdout_fraction < 1
        or validation_fraction + holdout_fraction >= 1
    ):
        raise ValueError("split fractions must leave a nonempty training fraction")
    rng = random.Random(seed)
    by_cat: dict[str, list[ScenarioSpec]] = defaultdict(list)
    for s in scenarios:
        by_cat[s.category.value].append(s)
    groups = []
    for cat in sorted(by_cat):
        g = sorted(by_cat[cat], key=lambda s: s.scenario_id)
        rng.shuffle(g)
        groups.append(g)
    # Interleave categories so evenly spaced picks are stratified.
    ordered = [s for row in zip_longest(*groups) for s in row if s is not None]
    n = len(ordered)
    n_hold = max(1, round(n * holdout_fraction)) if holdout_fraction and n >= 3 else 0
    n_val = max(1, round(n * validation_fraction)) if validation_fraction and n >= 2 else 0
    n_val = min(n_val, max(0, n - n_hold - 1))
    assign: list[Split] = [Split.TRAIN] * n
    for k in range(n_hold):
        assign[int((k + 0.5) * n / n_hold)] = Split.HOLDOUT
    free = [i for i in range(n) if assign[i] is Split.TRAIN]
    for k in range(min(n_val, len(free))):
        assign[free[int((k + 0.5) * len(free) / n_val)]] = Split.VALIDATION
    out: dict[Split, list[ScenarioSpec]] = {sp: [] for sp in Split}
    for s, sp in zip(ordered, assign, strict=True):
        out[sp].append(s.model_copy(update={"split": sp}))
    return out
