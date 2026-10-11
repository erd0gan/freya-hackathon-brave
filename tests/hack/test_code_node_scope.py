"""Stress C-08 on Freya, 11 Oct: a code node whose config went into the packed part failed on
every turn with "cannot access local variable 'CFG'" (the platform runs a node's code inside a
function, where `CFG = prepare_cfg(CFG, ...)` makes CFG a local), the error handler failed the
same way, and the agent said the account read-out again and again. Also the year rollover the
customer asked for there: 31 December on a Sunday-Thursday week."""

import base64
import copy
import os
import textwrap

from fb.harness.engine_sim import simulate
from fb.nodecode.embed import snippet
from test_engine import CASES, CFG_A

Y = {"kind": "yes", "text": "Yes, I confirm."}
BYE = {"kind": "unclear", "text": "Thanks, bye."}


def run_in_function(src, v):
    """The node's code the way the platform runs it: the body of a function."""
    env = {}
    exec(compile("def _node(vars):\n" + textwrap.indent(src, "    "), "node", "exec"), env)
    env["_node"](v)
    return v


def test_a_config_in_the_packed_part_is_bound_inside_a_function():
    # a config too big for the readable form (random bytes do not compress)
    pad = base64.b64encode(os.urandom(40_000)).decode()
    cfg = {"k": 7, "pad": pad, "holidays": [], "workdays": [0, 1, 2, 3, 4]}
    src = snippet("vars['x'] = CFG['k']", cfg)
    assert "CFG: in the packed part above" in src
    v = run_in_function(src, {"now": "2026-12-30T11:00:00"})
    assert v.get("x") == 7 and "code_error" not in v, v.get("code_error")


def test_a_readable_config_still_runs_inside_a_function():
    v = run_in_function(snippet("vars['x'] = CFG['k']", {"k": 3}), {"now": "2026-12-30T11:00"})
    assert v.get("x") == 3 and "code_error" not in v


def brief_c_week():
    cfg = copy.deepcopy(CFG_A)
    cfg["workdays"] = [6, 0, 1, 2, 3]  # Sunday to Thursday
    cfg["holidays"] = ["2026-10-18", "2026-12-31"]
    cfg["bands"] = [
        {
            "name": "1-10",
            "min_days_late": 1,
            "max_days_late": 10,
            "deadline_business_days": 6,
            "deadline_unit": "business",
            "attempts": 1,
        }
    ]
    case = copy.deepcopy(CASES["PUB-01"])
    case["variables"]["now"] = "2026-12-30T11:00:00"
    case["products"][0].update(past_due=900.0, days_late=4, minimum_payment=300.0)
    return case, cfg


def test_a_holiday_on_new_years_eve_moves_the_promise_into_the_next_year():
    case, cfg = brief_c_week()
    offer = {
        "kind": "offer",
        "date": "December thirty-first",
        "amount": "the full amount",
        "text": "I can't do it today, but I can pay the full amount on December thirty-first.",
    }
    r = simulate(case, [offer, Y, Y, BYE], cfg)
    assert not r.code_errors, r.code_errors
    assert "3 January 2027" in r.says[1] and r.says[1] != r.says[0], r.says
    assert [(p["amount"], p["date"]) for p in r.ledger["promises"]] == [(900.0, "2027-01-03")]
