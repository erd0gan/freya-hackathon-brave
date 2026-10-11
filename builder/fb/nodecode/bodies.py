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
vars['neg_errors'] = '0'
"""

# After an exception in DECIDE / AFTER_RECORD (wired as `on_error`). The action of the turn
# before must not stay (neg_action=record would call create_promise again): one error gets a
# re-ask, a second one in a row (or one right after a write) ends the call. An empty outcome is
# filled by close_call's hook from the state; a promise the bank just confirmed is a promise.
NEG_ERROR = """
n = str(vars.get('neg_errors') or '0')
n = int(n) + 1 if n.isdigit() else 1
vars['neg_errors'] = str(n)
vars['neg_mode'] = 'speak'
was = str(vars.get('neg_action') or '')
if n >= 2 or was == 'record':
    vars['neg_action'] = 'finish'
    neg = CFG.get('neg') or dict()
    ok = str(vars.get('rec_ok') or '').strip().lower() == 'true'
    vars['final_outcome'] = (neg.get('outcomes') or dict()).get('promise', 'promise') if ok else ''
    vars['final_reason'] = ''
    vars['note_count'] = '0'
    vars['final_say'] = str((neg.get('lines') or dict()).get('goodbye') or 'Goodbye.')
else:
    vars['neg_action'] = 'say'
    vars['neg_say'] = 'Sorry, could you say that again?'
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
pop_note(vars, bool(CFG.get('note_check')))
"""

# FACTS failed (an account answer we cannot read): no negotiation, a service error close.
FACTS_ERROR = """
neg = CFG.get('neg') or dict()
vars['neg_action'] = 'finish'
vars['neg_say'] = ''
vars['final_outcome'] = (neg.get('outcomes') or dict()).get('no_promise', 'no_promise')
vars['final_reason'] = (neg.get('reasons') or dict()).get('service_error', 'service_error')
vars['note_count'] = '0'
vars['final_say'] = str((neg.get('lines') or dict()).get('goodbye') or 'Goodbye.')
"""

# After transfer_to_agent: a failed transfer is not closed as `transferred`. One more try, then
# the call closes as no_promise / service_error with a sentence that says so.
AFTER_TRANSFER = """
ok = str(vars.get('transfer_ok') or '').strip().lower() == 'true'
tries = str(vars.get('transfer_tries') or '0')
tries = int(tries) + 1 if tries.isdigit() else 1
vars['transfer_tries'] = str(tries)
vars['transfer_ok'] = ''
neg = CFG.get('neg') or dict()
if ok:
    vars['transfer_next'] = 'done'
elif tries < 2:
    vars['transfer_next'] = 'retry'
else:
    vars['transfer_next'] = 'failed'
    vars['final_outcome'] = (neg.get('outcomes') or dict()).get('no_promise', 'no_promise')
    vars['final_reason'] = (neg.get('reasons') or dict()).get('service_error', 'service_error')
    vars['final_say'] = (
        str((neg.get('lines') or dict()).get('transfer_failed') or
            'I am sorry, I could not connect you.')
        + ' ' + str((neg.get('lines') or dict()).get('goodbye') or 'Goodbye.'))
"""


SETUP_TP = (
    SETUP
    + """
vars['cb_say'] = ''
vars['cb_tries'] = '0'
vars['cb_state'] = ''
vars['cb_pending_date'] = ''
vars['cb_pending_time'] = ''
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
vars['tp_reply'] = 'other'  # '' is rejected for an enum variable
cb = CFG.get('callback') or dict()
start = str(cb.get('start') or '10:00')
end = str(cb.get('end') or '19:00')
days = [d for d in str(vars.get('callback_days') or '').split(',') if d]
now16 = str(vars.get('now') or '')[:16].replace(' ', 'T')
iso = ''
if dt is not None and txt_day.strip():
    iso, _kind = parse_spoken_date(txt_day, dt.date())
said_time = parse_spoken_time(txt_time) or parse_spoken_time(txt_day)
# a day and a time said in different turns belong together: a new value replaces only its part
iso = iso or str(vars.get('cb_pending_date') or '')
hhmm = said_time or str(vars.get('cb_pending_time') or '')
# a time said alone takes the first callback day it still fits; a time kept from a turn whose
# day was not possible waits for a new day
if said_time and not iso and days and not txt_day.strip():
    iso = days[0] if (days[0] + 'T' + hhmm) > now16 else days[-1]
vars['cb_pending_date'] = iso
vars['cb_pending_time'] = hhmm
state = 'none'
day_ok = iso in days
time_ok = start <= hhmm <= end and (iso + 'T' + hhmm) > now16
if iso and hhmm:
    state = 'ok' if (day_ok and time_ok) else 'outside'
elif iso or hhmm:
    state = 'partial'
if state == 'ok':
    vars['cb_date'] = iso
    vars['cb_time'] = hhmm
    vars['cb_say'] = ''
elif state == 'outside':
    # keep the part that was right ('Tomorrow at nine.' -> 'No, eleven.' is tomorrow at 11)
    # and ask only for the wrong one; every 'outside' turn counts as a re-ask
    vars['cb_pending_date'] = iso if day_ok else ''
    vars['cb_pending_time'] = hhmm if (start <= hhmm <= end and not day_ok) else ''
    vars['cb_tries'] = str(int(vars.get('cb_tries') or 0) + 1)
    if day_ok:
        wrong = 'That time is not possible on that day. Say so and ask once for another time: '
    elif vars['cb_pending_time']:
        wrong = 'That day is not possible. Say so and ask once for another day: '
    else:
        wrong = 'That time is not possible. Say so and ask once for another time: '
    vars['cb_say'] = wrong + str(vars.get('callback_window_text') or '')
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


def decline_check(var: str) -> str:
    """Code node after the model labelled a security answer `declines`: it counts only when the
    customer's own words are whole and refuse, end the talk or ask what the call is about."""
    return (
        "raw = last_customer_text(vars)\n"
        "real = (not raw) or (not is_cut(raw) and bool(REFUSAL_WORDS.search(raw) "
        "or STOP_WORDS.search(raw) or ABOUT_WORDS.search(raw)))\n"
        "vars['decline_real'] = 'yes' if real else 'no'\n"
        f"if not real:\n    vars[{var!r}] = 'unclear'\n"
    )
