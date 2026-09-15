"""
ABOUTME: Does the candidate deserved_attention rule agree with the hand-derived golden verdicts?
ABOUTME: The goldens were derived from the spec, independently of the annotator labels.
"""
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from eval_takehome import SignalEvaluator

golds = json.load(open('/tmp/golden_deserved.json'))
rd = lambda p: [json.loads(l) for l in open(p)]
doss = rd('data/signal_dossiers.jsonl')
by_id = {d['signal_id']: d for d in doss}

ev = SignalEvaluator()
ev.load_context(rd('data/accounts.jsonl'), rd('data/owners.jsonl'),
                rd('data/telemetry.jsonl'), rd('data/artifacts.jsonl'), doss)
cur, facts = {}, {}
for d in doss:
    r = ev.explain(d)
    cur[d['signal_id']] = r['deserved_attention']
    facts[d['signal_id']] = r['_facts']

sev = lambda s: (by_id[s].get('scoring') or {}).get('severity')
hyp = lambda s: (by_id[s].get('hypotheses') or [{}])[0].get('hypothesis')
dtr = lambda s: facts[s].get('days_to_renewal')

def candidate(s):
    """spec §8.1 / §4.6, OR (agent called it P0/P1 AND committed to a non-benign explanation)."""
    return cur[s] or (sev(s) in ('P0','P1') and hyp(s) not in ('benign_variation','no_hypothesis'))

def candidate_vetoed(s):
    """...minus the veto: renewal more than 90 days away was never wanted by the majority."""
    far = dtr(s) is not None and dtr(s) > 90
    return candidate(s) and not far

for name, fn in (('SHIPPED rule', lambda s: cur[s]),
                 ('ungated severity (rejected)', candidate),
                 ('CANDIDATE + renewal>90 veto', candidate_vetoed)):
    agree = sum(1 for s, g in golds.items() if fn(s) == g)
    print(f'\n{name:34s}  agrees with {agree}/{len(golds)} hand-derived goldens')
    for s, g in sorted(golds.items()):
        got = fn(s)
        if got != g:
            print(f'    MISMATCH {s}: golden={g} candidate={got}  '
                  f'sev={sev(s)} hyp={hyp(s)} dtr={dtr(s)} reason={facts[s]["deserved_reason"][:48]}')
