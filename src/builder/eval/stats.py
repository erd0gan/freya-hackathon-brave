"""Eval statistics (memo §17.3, §60; Anthropic 'A statistical approach to model evals').

- standard errors, clustered by scenario (repeated runs are one cluster)
- paired comparisons on identical scenario sets
- Beta-posterior lower bounds for small-n binary outcomes
- power analysis to size an eval before running it
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel

Z95 = 1.959964


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def std_err(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var / n)


def clustered_std_err(values: list[float], clusters: list[str]) -> float:
    """Cluster-robust SE of the mean (CR0 with small-sample correction)."""
    n = len(values)
    groups: dict[str, list[float]] = defaultdict(list)
    for v, c in zip(values, clusters, strict=True):
        groups[c].append(v)
    g = len(groups)
    if n < 2 or g < 2:
        return 0.0
    m = mean(values)
    s = sum((sum(vs) - len(vs) * m) ** 2 for vs in groups.values())
    return math.sqrt(s * g / (g - 1)) / n


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1.0 + aa / c if abs(c) > 1e-30 else 1e-30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1.0 + aa / c if abs(c) > 1e-30 else 1e-30
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-10:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def beta_lower_bound(successes: float, n: float, q: float = 0.05) -> float:
    """q-quantile of Beta(1+s, 1+n-s) (uniform prior) via bisection."""
    a, b = 1.0 + successes, 1.0 + n - successes
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if beta_cdf(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def pass_hat_k(successes: int, trials: int, k: int) -> float:
    """Unbiased pass^k estimate for one task (τ-bench): C(c,k)/C(n,k)."""
    if trials < k:
        return float("nan")
    return math.comb(successes, k) / math.comb(trials, k)


class PairedComparison(BaseModel):
    n: int
    mean_diff: float
    se_diff: float
    wins: int
    losses: int
    ties: int
    sign_test_p: float

    @property
    def ci95(self) -> tuple[float, float]:
        return (self.mean_diff - Z95 * self.se_diff, self.mean_diff + Z95 * self.se_diff)


def sign_test_p(wins: int, losses: int) -> float:
    """One-sided P(X >= wins) under Binomial(wins+losses, 0.5)."""
    n = wins + losses
    if n == 0:
        return 1.0
    return sum(math.comb(n, i) for i in range(wins, n + 1)) / 2**n


def paired(a: dict[str, float], b: dict[str, float]) -> PairedComparison:
    """Compare per-scenario mean success of A vs B on their shared scenarios."""
    keys = sorted(set(a) & set(b))
    diffs = [a[k] - b[k] for k in keys]
    wins = sum(1 for d in diffs if d > 1e-9)
    losses = sum(1 for d in diffs if d < -1e-9)
    return PairedComparison(
        n=len(keys),
        mean_diff=mean(diffs),
        se_diff=std_err(diffs),
        wins=wins,
        losses=losses,
        ties=len(keys) - wins - losses,
        sign_test_p=sign_test_p(wins, losses),
    )


def per_scenario_means(rows: Iterable[tuple[str, float]]) -> dict[str, float]:
    groups: dict[str, list[float]] = defaultdict(list)
    for sid, v in rows:
        groups[sid].append(v)
    return {k: mean(v) for k, v in groups.items()}


def required_n(
    delta: float,
    p: float = 0.7,
    power_z: float = 0.8416,
    alpha_z: float = Z95,
    paired_rho: float = 0.0,
) -> int:
    """Scenarios needed to detect `delta` between two candidates (normal approximation).

    `paired_rho` is the per-scenario outcome correlation between candidates;
    pairing shrinks the variance by (1 - rho).
    """
    var = 2 * p * (1 - p) * (1 - paired_rho)
    return math.ceil(var * ((alpha_z + power_z) / delta) ** 2)
