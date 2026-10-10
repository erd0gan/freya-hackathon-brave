"""Offline verification of the new public results and Northwind build artifacts."""
import copy
import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'builder'))
from fb.generate import build
from fb.openapi_tools import parse_openapi
from fb.spec_extract import to_generator
from fb.validate import validate

def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))

out = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
out['public_repetitions'] = []
for name in ('gen-a-3.json', 'gen-b-3.json'):
    d = read('kit/results/' + name)
    rows = d['final']
    batches = d.get('all_rows') or []
    out['public_repetitions'].append({
        'file': 'kit/results/' + name,
        'passed': d['passed'], 'total': d['total'], 'runs': d['runs'],
        'seconds': d['seconds'],
        'all_cases_three_of_three': all(r['passes'] == r['runs'] == 3 and r['pass'] for r in rows),
        'batch_sizes': [len(b) for b in batches],
        'all_trials_pass': bool(batches) and all(r['pass'] for b in batches for r in b),
        'spec_origin': 'handwritten; as reported by user',
    })

n = read('submission/northwind.spec.json')
nw_api = read('tests/metamorphic/northwind/openapi.json')
nw_tools = parse_openapi(nw_api)
nw, policy = build(to_generator(n), nw_tools)
rep = validate(nw, nw_tools, policy)
exported = read('submission/northwind.workflow.json')['workflow']
out['northwind'] = {
    'stage_2_attempts': next(b['attempts'] for b in n['bands'] if b['min_days_late'] == 15),
    'memo_holiday_present': '2026-10-30' in n['calendar']['holidays'],
    'callback_end': n['callback']['end'],
    'identity_fields': [q['field'] for q in n['identity']['questions']],
    'call_variables': n['call_variables'],
    'request_bindings': n['tool_fields'],
    'validator_errors': [str(e) for e in rep.errors],
    'export_nodes': len(exported['nodes']),
    'agent_model': exported.get('agent_config', {}).get('llmConfig', {}).get('model'),
    'behavior_simulated': False,
}

# Verify a broader request-field rename through the generated workflow, with explicit mappings.
s = copy.deepcopy(read('submission/brief-ab-run.spec.json'))
api = read('kit/tools/openapi.json')
schema = api['paths']['/verify_identity']['post']['requestBody']['content']['application/json']['schema']
for old, new in [('field','question_code'), ('answer','response')]:
    schema['properties'][new] = schema['properties'].pop(old)
    schema['required'] = [new if f == old else f for f in schema['required']]
s.setdefault('tool_fields', {}).setdefault('verify_identity', {}).update(field='question_code', answer='response')
wf, _ = build(to_generator(s), parse_openapi(api))
fn = wf['nodes']['identity']['tools'][0]
request = {'body': {'customer_id':'synthetic', 'question_code':'date_of_birth', 'response':'14 March 1988'}}
exec(fn['pre_process_code'], {'vars':{}, 'config':request})
out['explicit_identity_rename'] = {
    'request_body_fields': list(fn['function']['parameters']['properties']['body']['properties']),
    'body_after_hook': request['body'],
    'expected_response': '1988-03-14',
    'normalized_correctly': request['body']['response'] == '1988-03-14' and 'answer' not in request['body'],
}

paths = ['builder/build.py','builder/fb/generate.py','builder/fb/spec_extract.py',
         'submission/northwind.spec.json','submission/northwind.workflow.json',
         'traces/northwind.log','kit/results/gen-a-3.json','kit/results/gen-b-3.json']
out['sha256'] = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
Path(__file__).with_suffix('.json').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(out, ensure_ascii=False, indent=2))
