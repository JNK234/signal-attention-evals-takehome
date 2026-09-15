"""
ABOUTME: deserved_attention v3 experiments — AND/OR combinations, negative (veto) conditions, the
ABOUTME: agent-severity question, and a ranking scored by precision@budget instead of a threshold.
"""
import hashlib, itertools, json, sys, pathlib
from collections import defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from eval_takehome import SignalEvaluator

rd = lambda p: [json.loads(l) for l in open(p)]
doss = rd('data/signal_dossiers.jsonl')
by_id = {d['signal_id']: d for d in doss}
ann = defaultdict(dict)
for i in (1, 2, 3):
    for r in rd(f'data/annotations/annotator_{i}.jsonl'):
        ann[f'a{i}'][r['signal_id']] = r
triple = sorted(set(ann['a1']) & set(ann['a2']) & set(ann['a3']))
maj = {s: sum(bool(ann[a][s]['deserved_attention']) for a in ('a1','a2','a3')) >= 2 for s in triple}
half = lambda sid: int(hashlib.sha1(f'deserved-v2|{sid}'.encode()).hexdigest(), 16) % 2
DEV  = [s for s in triple if half(s) == 0]
HELD = [s for s in triple if half(s) == 1]

ev = SignalEvaluator()
ev.load_context(rd('data/accounts.jsonl'), rd('data/owners.jsonl'),
                rd('data/telemetry.jsonl'), rd('data/artifacts.jsonl'), doss)
ours, facts, rules_of = {}, {}, {}
for d in doss:
    r = ev.explain(d)
    ours[d['signal_id']] = r['deserved_attention']
    facts[d['signal_id']] = r['_facts']
    rules_of[d['signal_id']] = {v['rule'] for v in r['violations']}

sev  = lambda s: (by_id[s].get('scoring') or {}).get('severity')
hyp  = lambda s: (by_id[s].get('hypotheses') or [{}])[0].get('hypothesis')
conf = lambda s: (by_id[s].get('hypotheses') or [{}])[0].get('confidence')
dtr  = lambda s: facts[s].get('days_to_renewal')
disp = lambda s: (by_id[s].get('decision') or {}).get('disposition')
art  = lambda s: 'artifact' in (facts[s].get('claim_status') or [])
ctext= lambda s: bool(facts[s].get('has_customer_text'))
nsrc = lambda s: len(facts[s].get('verified_sources') or [])

def stats(pred, ids):
    fires = [s for s in ids if pred(s)]
    want  = [s for s in ids if maj[s]]
    tp = sum(1 for s in fires if maj[s])
    cov = tp/len(want) if want else 0.0
    ffr = (len(fires)-tp)/len(fires) if fires else 0.0
    a = 2*cov*(1-ffr)/(cov+(1-ffr)) if (cov+1-ffr) else 0.0
    return cov, ffr, a, len(fires), tp

print('#' * 92)
print('A. NEGATIVE / VETO CONDITIONS — precision on the NO class (your idea)')
print('#' * 92)
print('A veto is useful when it is almost never wrong about "not deserving".')
print(f'{"veto condition":44s} {"fires":>6s} {"of which majority-NO":>21s} {"veto precision":>15s}')
VETOS = {
  'severity == P3':               lambda s: sev(s) == 'P3',
  'severity in (P2,P3)':          lambda s: sev(s) in ('P2','P3'),
  'hypothesis == benign_variation': lambda s: hyp(s) == 'benign_variation',
  'confidence == low':            lambda s: conf(s) == 'low',
  'no customer text at all':      lambda s: not ctext(s),
  'zero verified sources':        lambda s: nsrc(s) == 0,
  'renewal > 120 days away':      lambda s: dtr(s) is not None and dtr(s) > 120,
  'renewal > 90 days away':       lambda s: dtr(s) is not None and dtr(s) > 90,
  'P3 AND no customer text':      lambda s: sev(s) == 'P3' and not ctext(s),
  'P2/P3 AND renewal > 90':       lambda s: sev(s) in ('P2','P3') and dtr(s) is not None and dtr(s) > 90,
}
for name, fn in VETOS.items():
    for label, ids in (('dev', DEV), ('held', HELD)):
        fires = [s for s in ids if fn(s)]
        no = sum(1 for s in fires if not maj[s])
        p = no/len(fires) if fires else 0.0
        print(f'{name:44s} {label:>4s} {len(fires):4d} {no:21d} {p:15.3f}')

print()
print('#' * 92)
print('B. THE AGENT-SEVERITY QUESTION — severity as evidence, gated on evidence supporting it')
print('#' * 92)
CANDS = {
  'severity in (P0,P1)  [reads agent]':      lambda s: sev(s) in ('P0','P1'),
  'P0/P1 AND >=1 verified source':           lambda s: sev(s) in ('P0','P1') and nsrc(s) >= 1,
  'P0/P1 AND customer text':                 lambda s: sev(s) in ('P0','P1') and ctext(s),
  'P0/P1 AND no Q3 overconfidence':          lambda s: sev(s) in ('P0','P1') and 'Q3' not in rules_of[s],
  'P0/P1 AND renewal <= 90':                 lambda s: sev(s) in ('P0','P1') and dtr(s) is not None and dtr(s) <= 90,
  'P0/P1 AND not benign hypothesis':         lambda s: sev(s) in ('P0','P1') and hyp(s) not in ('benign_variation','no_hypothesis'),
  'current spec rules (baseline)':           lambda s: ours[s],
}
print(f'{"condition":44s} {"set":>5s} {"cov":>6s} {"FFR":>6s} {"align":>6s} {"fires":>6s} {"tp":>4s}')
for name, fn in CANDS.items():
    for label, ids in (('dev', DEV), ('held', HELD)):
        c, f, a, n, tp = stats(fn, ids)
        print(f'{name:44s} {label:>5s} {c:6.3f} {f:6.3f} {a:6.3f} {n:6d} {tp:4d}')

print()
print('#' * 92)
print('C. COMBINATIONS — spec rules OR'"'"'d with one candidate, then vetoed (the gap I left last run)')
print('#' * 92)
BEST = {
  'spec OR (P0/P1 AND source)':   lambda s: ours[s] or (sev(s) in ('P0','P1') and nsrc(s) >= 1),
  '  ... minus veto P2/P3+far':   lambda s: (ours[s] or (sev(s) in ('P0','P1') and nsrc(s) >= 1)) and not (sev(s) in ('P2','P3') and dtr(s) is not None and dtr(s) > 90),
  'spec OR (P0/P1 AND renew<=90)':lambda s: ours[s] or (sev(s) in ('P0','P1') and dtr(s) is not None and dtr(s) <= 90),
  'spec OR (renew<=60 AND ctext)':lambda s: ours[s] or (dtr(s) is not None and dtr(s) <= 60 and ctext(s)),
  'spec OR (renew<=60 AND artif)':lambda s: ours[s] or (dtr(s) is not None and dtr(s) <= 60 and art(s)),
  'P0/P1 AND source, no spec':    lambda s: sev(s) in ('P0','P1') and nsrc(s) >= 1,
}
print(f'{"policy":44s} {"set":>5s} {"cov":>6s} {"FFR":>6s} {"align":>6s} {"fires":>6s} {"tp":>4s}')
for name, fn in BEST.items():
    for label, ids in (('dev', DEV), ('held', HELD)):
        c, f, a, n, tp = stats(fn, ids)
        print(f'{name:44s} {label:>5s} {c:6.3f} {f:6.3f} {a:6.3f} {n:6d} {tp:4d}')
