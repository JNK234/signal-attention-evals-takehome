"""
ABOUTME: Independent check on the §6.3 deserved row — do its 57 newly-flagged dossiers actually churn
ABOUTME: or waste a CSM slot? Outcomes are read by no rule, so this cannot be circular.
"""
import json, sys, pathlib
from collections import Counter
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from eval_takehome import SignalEvaluator

rd = lambda p: [json.loads(l) for l in open(p)]
doss = rd('data/signal_dossiers.jsonl'); by_id = {d['signal_id']: d for d in doss}
out = {o['signal_id']: o for o in rd('data/outcomes.jsonl')}
ev = SignalEvaluator()
ev.load_context(rd('data/accounts.jsonl'), rd('data/owners.jsonl'),
                rd('data/telemetry.jsonl'), rd('data/artifacts.jsonl'), doss)

new, spec_only, rest = [], [], []
for d in doss:
    r = ev.explain(d); s = d['signal_id']
    if r['deserved_attention']:
        (new if '6.3' in r['_facts']['deserved_reason'] else spec_only).append(s)
    else:
        rest.append(s)

def rates(ids, label):
    known = [s for s in ids if out[s].get('renewal_outcome') not in (None, 'pending')]
    bad = sum(1 for s in known if out[s]['renewal_outcome'] in ('churned', 'downgraded'))
    wasted = sum(1 for s in ids if out[s].get('escalation_was_wasted'))
    comp = sum(1 for s in ids if out[s].get('customer_complained_about_outreach'))
    arr = sum(out[s].get('arr_delta') or 0 for s in known)
    print(f'{label:34s} n={len(ids):3d}  churn/downgrade {bad:3d}/{len(known):3d} = {bad/len(known) if known else 0:.1%}'
          f'   wasted {wasted:3d} = {wasted/len(ids) if ids else 0:.1%}   complaints {comp:2d}   ARR delta ${arr:,.0f}')

print('base rates over the whole corpus: churn/downgrade 28.0%, wasted escalation 18.3%\n')
rates(spec_only, 'deserved via §8.1/§4.6')
rates(new,       'deserved via §6.3 (the new 57)')
rates(rest,      'not deserved')
print()
print('what the new 57 look like:')
print('  severity :', dict(Counter((by_id[s].get('scoring') or {}).get('severity') for s in new)))
print('  hypothesis:', dict(Counter((by_id[s].get('hypotheses') or [{}])[0].get('hypothesis') for s in new).most_common(5)))
print('  reached a human:', dict(Counter(bool(out[s].get('reached_human')) for s in new)))
