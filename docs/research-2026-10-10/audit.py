"""Read-only competition research probes; no LLM or Freya calls."""
import collections
import copy
import datetime
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'builder'))
sys.path.insert(0, str(ROOT / 'tests' / 'hack'))
from fb.generate import build, identity_hooks
from fb.nodecode import bodies
from fb.nodecode.embed import namespace, snippet
from fb.openapi_tools import parse_openapi
from fb.spec_extract import check, to_generator
from fb.validate import validate
from fb.harness.engine_sim import simulate
from test_engine import CASES, CFG_A, SCENARIOS

api = json.loads((ROOT / 'kit/tools/openapi.json').read_text(encoding='utf-8'))
tools = parse_openapi(api)
spec = json.loads((ROOT / 'submission/brief-ab-run.spec.json').read_text(encoding='utf-8'))
brief = (ROOT / 'kit/briefs/brief-a.md').read_text(encoding='utf-8') + (ROOT / 'kit/briefs/brief-b.md').read_text(encoding='utf-8')
ns = namespace()
out = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'probes': {}}
probes = out['probes']

for name, change in [
    ('unsupported_holidays', lambda s: s['calendar'].update(holidays=['2030-01-01'])),
    ('changed_deadline', lambda s: s['bands'][0].update(deadline_days=999)),
    ('overlapping_bands', lambda s: s['bands'][1].update(min_days_late=1)),
    ('negative_attempts', lambda s: s['bands'][0].update(attempts=-1)),
    ('string_boolean', lambda s: s['neg'].update(notes='false')),
    ('invented_reason', lambda s: s['outcomes'].update(unclear=['not_reached','invented_reason'])),
    ('invalid_callback_window', lambda s: s['callback'].update(start='25:99',end='01:00')),
]:
    s = copy.deepcopy(spec)
    change(s)
    try:
        problems = check(s, tools, brief)
        probes[name] = {'check_problems': problems}
    except Exception as e:
        probes[name] = {'exception': str(e)}

today = datetime.date(2026,10,14)
probes['speech'] = {
    'dates': {t: ns['parse_spoken_date'](t,today) for t in [
        '2026-10-16, sorry I mean tomorrow',
        'May 5', '5 May', 'this Friday, sorry next Monday',
        'this Friday, sorry I mean next Monday',
    ]},
    'amounts': {t: ns['parse_amount'](t) for t in ['-100','minus one hundred','1,234.56','637.50, sorry 500']},
}

cb_v = {'now':'2026-10-14T11:00','cb_day_text':'tomorrow','cb_time_text':''}
exec(snippet(bodies.SETUP_TP, CFG_A), {'vars':cb_v})
exec(snippet(bodies.CB_CHECK, CFG_A), {'vars':cb_v})
first = {k:cb_v.get(k) for k in ['cb_state','cb_date','cb_say']}
cb_v.update(cb_day_text='',cb_time_text='three pm')
exec(snippet(bodies.CB_CHECK, CFG_A), {'vars':cb_v})
probes['split_callback'] = {'after_day':first,'after_time':{k:cb_v.get(k) for k in ['cb_state','cb_date','cb_time','cb_say']}}

renamed = copy.deepcopy(spec)
renamed['call_variables'].update(customer_id='client_ref',now='call_time',last_name='surname')
wf,pol = build(to_generator(renamed),tools)
raw = json.dumps(wf)
probes['renamed_call_variables'] = {
    'old_customer_placeholder':raw.count('{{customer_id}}'),
    'new_customer_placeholder':raw.count('{{client_ref}}'),
    'reads_old_now':"vars.get('now')" in raw,
    'reads_new_now':"vars.get('call_time')" in raw,
    'aliases_new_now': "vars['now'] = vars.get('call_time')" in raw,
}

pre,post = identity_hooks(to_generator(spec))
vars_id = {}
config = {'body':{'question_code':'date_of_birth','response':'14 March 1988'}}
exec(pre, {'vars':vars_id,'config':config})
probes['renamed_identity_arguments'] = config['body']

case = copy.deepcopy(CASES['PUB-01'])
for name,replies in [
    ('unclear_nonanswer',[{'kind':'unclear'}]),
    ('hardship_with_date',[{'kind':'hardship','date':'this Friday'}]),
    ('support_question_then_accept',[{'kind':'hardship'},{'kind':'question'},{'kind':'yes'}]),
    ('recorded_change',[{'kind':'yes'},{'kind':'yes'},{'kind':'offer','date':'this Friday'}]),
]:
    r = simulate(case,replies,CFG_A)
    probes[name] = {'says':r.says,'phase':r.vars.get('neg_phase'),'action':r.vars.get('neg_action'),'errors':r.code_errors}

wf,pol = build(to_generator(spec),tools)
rep = validate(wf,tools,pol)
probes['generated_workflow'] = {'nodes':len(wf['nodes']),'bytes':len(json.dumps(wf).encode()),'errors':[str(x) for x in rep.errors],'warnings':[str(x) for x in rep.warnings]}

for f in ['.builder/call_pub01.json','.builder/probe_after.json']:
    d = json.loads((ROOT/f).read_text(encoding='utf-8'))
    events = []
    for m in d.get('messages',[]):
        if m.get('role')=='system':
            try:
                e=json.loads(m.get('message') or '{}')
                events.append(e.get('event'))
            except (ValueError,TypeError):
                pass
    probes[f] = {'ended':d.get('endedReason'),'duration':d.get('duration'),'events':dict(collections.Counter(events)),'assistant':[m.get('message') for m in d.get('messages',[]) if m.get('role')=='assistant']}

out['file_hashes'] = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'builder/build.py',ROOT/'builder/fb/generate.py',ROOT/'builder/fb/spec_extract.py']}
out['saved_platform_results'] = []
for p in sorted((ROOT/'kit/results').glob('*.json')):
    d=json.loads(p.read_text(encoding='utf-8'))
    out['saved_platform_results'].append({
        'file':str(p.relative_to(ROOT)),
        'passed':d.get('passed'),'total':d.get('total'),'runs':d.get('runs'),
        'seconds':d.get('seconds'),
        'ledger_passed':sum(not any(not x.startswith('conversation:') for x in r.get('failed',[])) for r in d.get('rows',[])),
        'failed':[{'id':r.get('id'),'failed':r.get('failed')} for r in d.get('rows',[]) if not r.get('pass')],
        'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
    })
destination = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).parent/'audit.json'
destination.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
