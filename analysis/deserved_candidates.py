"""
ABOUTME: Scores every candidate deserved_attention condition on alignment (EvalGen) over dev and
ABOUTME: held-out, alone and OR'd onto the current two spec rules. FFR ceiling stated up front.
"""
import hashlib, json, sys, pathlib
from collections import defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from eval_takehome import SignalEvaluator

FFR_CEILING = 0.55     # attention is 14 CSMs x 5/week; a false alarm burns a real slot

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
DEV = [s for s in triple if half(s) == 0]
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

def hyp(s):  return (by_id[s].get('hypotheses') or [{}])[0].get('hypothesis')
def dtr(s):  return facts[s].get('days_to_renewal')
def disp(s): return (by_id[s].get('decision') or {}).get('disposition')

CANDIDATES = {
  'A renewal<=90':                 lambda s: dtr(s) is not None and dtr(s) <= 90,
  'B renewal<=60':                 lambda s: dtr(s) is not None and dtr(s) <= 60,
  'C artifact claim':              lambda s: 'artifact' in (facts[s].get('claim_status') or []),
  'D benign + artifact claim':     lambda s: hyp(s) == 'benign_variation' and 'artifact' in (facts[s].get('claim_status') or []),
  'E suppressed + artifact claim': lambda s: disp(s) == 'suppressed' and 'artifact' in (facts[s].get('claim_status') or []),
  'F customer text + renewal<=90': lambda s: bool(facts[s].get('has_customer_text')) and dtr(s) is not None and dtr(s) <= 90,
  'G uncertain trigger':           lambda s: bool(facts[s]['trigger_source']['uncertain']),
  'H uncertain OR unattributed':   lambda s: bool(facts[s]['trigger_source']['uncertain']) or bool(facts[s]['trigger_source'].get('unattributed')),
  'I I6 fabricated evidence':      lambda s: 'I6' in rules_of[s],
  'J >=2 verified sources':        lambda s: len(facts[s].get('verified_sources') or []) >= 2,
  'K customer text + artifact':    lambda s: bool(facts[s].get('has_customer_text')) and 'artifact' in (facts[s].get('claim_status') or []),
  'L renewal<=30':                 lambda s: dtr(s) is not None and dtr(s) <= 30,
}

def align(pred, ids):
    """EvalGen alignment: harmonic mean of coverage and (1-FFR). Positive class = majority wants a human."""
    want  = [s for s in ids if maj[s]]
    fires = [s for s in ids if pred(s)]
    tp = sum(1 for s in fires if maj[s])
    cov = tp / len(want) if want else 0.0
    ffr = (len(fires) - tp) / len(fires) if fires else 0.0
    a = 2 * cov * (1 - ffr) / (cov + (1 - ffr)) if (cov + 1 - ffr) else 0.0
    return dict(cov=cov, ffr=ffr, align=a, fires=len(fires), tp=tp, fp=len(fires) - tp)

def show(title, ids):
    print(f'\n{title}   n={len(ids)}  majority-wants={sum(maj[s] for s in ids)}  base-rate={sum(maj[s] for s in ids)/len(ids):.2f}')
    print(f'  {"candidate":34s} {"cov":>5s} {"FFR":>5s} {"align":>6s} {"fires":>6s} {"tp":>4s} {"fp":>4s}   verdict')
    cur = align(lambda s: ours[s], ids)
    print(f'  {"CURRENT (spec §8.1 + §4.6)":34s} {cur["cov"]:.3f} {cur["ffr"]:.3f} {cur["align"]:.3f} '
          f'{cur["fires"]:6d} {cur["tp"]:4d} {cur["fp"]:4d}   baseline')
    rows = []
    for name, fn in CANDIDATES.items():
        a = align(fn, ids)
        # OR'd onto the current rule, which is how it would actually ship (set-level, OR semantics)
        o = align(lambda s, f=fn: ours[s] or f(s), ids)
        rows.append((name, a, o))
    for name, a, o in sorted(rows, key=lambda r: -r[2]['align']):
        ok = 'PASS' if o['ffr'] <= FFR_CEILING and o['align'] > cur['align'] else (
             'over FFR ceiling' if o['ffr'] > FFR_CEILING else 'no gain')
        print(f'  {name:34s} {a["cov"]:.3f} {a["ffr"]:.3f} {a["align"]:.3f} '
              f'{a["fires"]:6d} {a["tp"]:4d} {a["fp"]:4d}   alone')
        print(f'  {"  +current (OR)":34s} {o["cov"]:.3f} {o["ffr"]:.3f} {o["align"]:.3f} '
              f'{o["fires"]:6d} {o["tp"]:4d} {o["fp"]:4d}   {ok}')

print(f'FFR ceiling = {FFR_CEILING}  (stated before scoring, not tuned after)')
show('=== DEV ===', DEV)
show('=== HELD-OUT ===', HELD)

print()
print('=== §4.6 vs the annotators, both halves ===')
for label, ids in (('dev', DEV), ('held-out', HELD)):
    t = [s for s in ids if facts[s].get('enrichment_timed_out')]
    print(f'  {label:9s} §4.6 fires on {len(t):2d};  majority wants a human on {sum(maj[s] for s in t):2d};  '
          f'unanimous-no on {sum(1 for s in t if not any(ann[a][s]["deserved_attention"] for a in ("a1","a2","a3"))):2d}')
