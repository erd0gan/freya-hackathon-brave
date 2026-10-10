from __future__ import annotations

from test_pipeline_e2e import agent, judge, user
from test_testsynth import GOOD

from builder.adapters.mock.adapter import MockAdapter
from builder.adapters.mock.domains.retail_bank import make_pack
from builder.eval.evaluator import Evaluator
from builder.failure.first_deviation import localize
from builder.ingest.compile import compile_domain
from builder.llm.fake import FakeProvider
from builder.optimize.fork_probe import prefilter, probe
from builder.schema.agent import Architecture
from builder.schema.failure import FailureType
from builder.schema.scenario import PersonaStyle, ScenarioCategory
from builder.synth.candidate_generator import build_candidate, platform_default
from builder.testsynth.scenario_generator import ScenarioDraft, ScenarioGenerator


def test_probe_distinguishes_fixed_child(make_client):
    pack = make_pack()
    d = compile_domain(pack.bundle)
    llm = make_client(FakeProvider(by_role={"agent": agent, "user_sim": user, "judge": judge}))
    adapter = MockAdapter(pack, d.tools, llm)
    gen = ScenarioGenerator(d, llm, backend=adapter)
    sc = gen.finalize(
        "s",
        ScenarioCategory.HAPPY_PATH,
        PersonaStyle.COOPERATIVE,
        ScenarioDraft.model_validate(GOOD),
    )
    assert sc is not None
    b0 = platform_default(d)
    tr = adapter.run_simulation(adapter.deploy_agent(b0), sc)
    res = Evaluator(d.tools).evaluate(tr, sc)
    f = localize(tr, sc, res, d.tools)
    assert f.failure_type is FailureType.MISSING_CONFIRMATION
    good = build_candidate(d, Architecture.GUARDED_REACT)
    assert probe(adapter, good, tr, sc, f) is True
    assert probe(adapter, b0, tr, sc, f) is False
    keep, outcomes = prefilter(adapter, b0, [(tr, sc, f)])
    assert not keep and outcomes == [False]
