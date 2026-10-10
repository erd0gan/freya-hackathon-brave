"""Regression tests from diagnosed failures (memo §28 step 6).

Each failure yields a variant of its scenario (new id, different persona style
and seed) that joins the TRAIN split, so a fix must hold beyond one phrasing.
"""

from __future__ import annotations

from builder.schema.failure import FailureReport
from builder.schema.scenario import PersonaStyle, ScenarioSpec, Split

_STYLES = list(PersonaStyle)


def regression_variant(scenario: ScenarioSpec, failure: FailureReport, k: int = 0) -> ScenarioSpec:
    style = _STYLES[(_STYLES.index(scenario.persona.style) + 1 + k) % len(_STYLES)]
    return scenario.model_copy(
        update={
            "scenario_id": f"{scenario.scenario_id}~reg{k}",
            "persona": scenario.persona.model_copy(update={"style": style}),
            "seed": scenario.seed + 7919 * (k + 1),
            "split": Split.TRAIN,
            "provenance": f"regression:{failure.failure_type.value}:{failure.run_id}",
        },
        deep=True,
    )


def regression_suite(
    pairs: list[tuple[ScenarioSpec, FailureReport]], max_new: int = 8
) -> list[ScenarioSpec]:
    seen: set[str] = set()
    out: list[ScenarioSpec] = []
    for s, f in pairs:
        if s.split is not Split.TRAIN:
            continue  # never derive train data from validation/holdout (memo §34.2)
        key = f"{s.scenario_id}:{f.failure_type.value}"
        if key in seen:
            continue
        seen.add(key)
        out.append(regression_variant(s, f))
        if len(out) >= max_new:
            break
    return out
