"""Bodies of the generated code nodes. Each runs inside `embed.snippet` (the libraries,
`CFG` and a try/except are around it). `CFG["neg"]` holds the negotiation config."""

SETUP = """
for k, val in calendar_facts(vars.get('now'), CFG).items():
    vars[k] = val
vars['final_outcome'] = ''
vars['final_reason'] = ''
vars['final_say'] = ''
"""

FACTS = """
products = find_products(vars)
pf = product_facts(products, vars.get('now'), CFG)
for k, val in pf.items():
    vars[k] = val
st = new_state(pf, CFG.get('neg') or dict(), vars.get('now'))
publish(vars, st, CFG.get('neg') or dict())
clear_reply(vars)
"""

DECIDE = """
neg = CFG.get('neg') or dict()
now = vars.get('now')
cal = calendar_facts(now, CFG)
today = parse_now(now).date()
st = load_state(vars)
r = read_reply(vars, today)
step(st, r, neg, cal, now, CFG)
if st.get('action') == 'record':
    vars['rec_ok'] = ''
publish(vars, st, neg)
clear_reply(vars)
"""

AFTER_RECORD = """
neg = CFG.get('neg') or dict()
cal = calendar_facts(vars.get('now'), CFG)
st = load_state(vars)
ok = str(vars.get('rec_ok') or '').strip().lower() == 'true'
record_result(st, ok, neg, cal)
if st.get('action') == 'record':
    vars['rec_ok'] = ''
publish(vars, st, neg)
"""

AFTER_NOTE = """
pop_note(vars)
"""


def finish_pre(outcome: str, reason: str, say: str) -> str:
    """Body of a code node that closes a path before the negotiation (not reached, not
    verified). Values are spec strings, embedded as Python literals."""
    return (
        f"vars['final_outcome'] = {outcome!r}\n"
        f"vars['final_reason'] = {reason!r}\n"
        f"vars['final_say'] = {say!r}\n"
    )
