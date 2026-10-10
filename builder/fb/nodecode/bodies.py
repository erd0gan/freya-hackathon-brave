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
answer_first(st, r)
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


SETUP_TP = (
    SETUP
    + """
vars['cb_say'] = ''
vars['cb_tries'] = '0'
vars['cb_state'] = ''
"""
)

# Third party: what the person said about a day and a time -> a callback inside the window,
# a re-ask, or no callback. CFG["callback"]: start, end ("HH:MM", both included), reasks.
CB_CHECK = """
dt = parse_now(vars.get('now'))
txt_day = str(vars.get('cb_day_text') or '')
txt_time = str(vars.get('cb_time_text') or '')
vars['cb_day_text'] = ''
vars['cb_time_text'] = ''
vars['tp_reply'] = ''
cb = CFG.get('callback') or dict()
start = str(cb.get('start') or '10:00')
end = str(cb.get('end') or '19:00')
days = [d for d in str(vars.get('callback_days') or '').split(',') if d]
now16 = str(vars.get('now') or '')[:16].replace(' ', 'T')
iso = ''
if dt is not None and txt_day.strip():
    iso, _kind = parse_spoken_date(txt_day, dt.date())
hhmm = parse_spoken_time(txt_time) or parse_spoken_time(txt_day)
if hhmm and not iso and days:
    iso = days[0] if (days[0] + 'T' + hhmm) > now16 else days[-1]
state = 'none'
if iso and hhmm:
    ok = iso in days and start <= hhmm <= end and (iso + 'T' + hhmm) > now16
    state = 'ok' if ok else 'outside'
elif iso or hhmm:
    state = 'partial'
if state == 'ok':
    vars['cb_date'] = iso
    vars['cb_time'] = hhmm
    vars['cb_say'] = ''
elif state == 'outside':
    vars['cb_tries'] = str(int(vars.get('cb_tries') or 0) + 1)
    vars['cb_say'] = ('That time is not possible. Say so and ask once for another time: '
                      + str(vars.get('callback_window_text') or ''))
elif state == 'partial':
    vars['cb_say'] = 'Ask for the missing ' + ('time.' if iso else 'day.')
else:
    vars['cb_say'] = ''
vars['cb_state'] = state
"""

WRAP = """
vars['wrapped'] = 'yes'
"""


def finish_pre(outcome: str, reason: str, say: str) -> str:
    """Body of a code node that closes a path before the negotiation (not reached, not
    verified). Values are spec strings, embedded as Python literals."""
    return (
        f"vars['final_outcome'] = {outcome!r}\n"
        f"vars['final_reason'] = {reason!r}\n"
        f"vars['final_say'] = {say!r}\n"
    )
