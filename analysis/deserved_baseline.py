"""
ABOUTME: Dev baseline for deserved_attention — pairwise agreement (never folding us into the panel),
ABOUTME: per-slice reads, PABAK beside kappa, bootstrapped CIs, and the false-negative profile.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import json, random, statistics as st
from collections import Counter, defaultdict
from eval_takehome import SignalEvaluator

rd = lambda p: [json.loads(l) for l in open(p)]
doss = rd('data/signal_dossiers.jsonl')
accts = {a['account_id']: a for a in rd('data/accounts.jsonl')}
ann = defaultdict(dict)
for i in (1, 2, 3):
    for r in rd(f'data/annotations/annotator_{i}.jsonl'):
        ann[f'a{i}'][r['signal_id']] = r

ev = SignalEvaluator()
ev.load_context(rd('data/accounts.jsonl'), rd('data/owners.jsonl'),
                rd('data/telemetry.jsonl'), rd('data/artifacts.jsonl'), doss)
ours = {}
facts = {}
for d in doss:
    r = ev.explain(d)
    ours[d['signal_id']] = r['deserved_attention']
    facts[d['signal_id']] = r['_facts']

def prf(pairs):
    """pairs = [(ours_bool, theirs_bool)]"""
    tp = sum(1 for o, t in pairs if o and t)
    fp = sum(1 for o, t in pairs if o and not t)
    fn = sum(1 for o, t in pairs if not o and t)
    tn = sum(1 for o, t in pairs if not o and not t)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, precision=p, recall=r, f1=f,
                raw=(tp + tn) / len(pairs) if pairs else 0.0, n=len(pairs))

def kappa(pairs):
    n = len(pairs)
    if not n: return 0.0
    po = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe != 1 else 0.0

def pabak(pairs):
    """Byrt/Bishop/Carlin 1993: 2*Po - 1. Removes prevalence and bias effects."""
    if not pairs: return 0.0
    po = sum(1 for a, b in pairs if a == b) / len(pairs)
    return 2 * po - 1

def boot_ci(pairs, fn, n_boot=2000, seed=7):
    """Bootstrap whole rating rows (vault: the asymptotic CI is invalid off H0: k=0)."""
    rng = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        s = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        vals.append(fn(s))
    vals.sort()
    return vals[int(.025 * n_boot)], vals[int(.975 * n_boot)]

print('=' * 78)
print('1. PAIRWISE AGREEMENT — all six pairs, never folding us into the panel')
print('=' * 78)
raters = {k: {s: bool(v['deserved_attention']) for s, v in d.items()} for k, d in ann.items()}
raters['OURS'] = ours
names = ['a1', 'a2', 'a3', 'OURS']
for i, x in enumerate(names):
    for y in names[i+1:]:
        common = sorted(set(raters[x]) & set(raters[y]))
        pairs = [(raters[x][s], raters[y][s]) for s in common]
        k, pb = kappa(pairs), pabak(pairs)
        klo, khi = boot_ci(pairs, kappa)
        po = sum(1 for a, b in pairs if a == b) / len(pairs)
        tag = '  <-- us' if 'OURS' in (x, y) else ''
        print(f'{x:5s} vs {y:5s}  n={len(pairs):4d}  raw={po:.3f}  k={k:+.3f} [{klo:+.3f},{khi:+.3f}]  PABAK={pb:+.3f}{tag}')

print()
print('=' * 78)
print('2. OUR PRECISION / RECALL / F1 — three ground truths, not one')
print('=' * 78)
for a in ('a1', 'a2', 'a3'):
    common = sorted(set(raters[a]) & set(ours))
    m = prf([(ours[s], raters[a][s]) for s in common])
    print(f'vs {a}          n={m["n"]:4d}  P={m["precision"]:.3f} R={m["recall"]:.3f} F1={m["f1"]:.3f}  '
          f'(tp={m["tp"]} fp={m["fp"]} fn={m["fn"]})')

triple = sorted(set(raters['a1']) & set(raters['a2']) & set(raters['a3']))
maj = {s: sum(raters[a][s] for a in ('a1','a2','a3')) >= 2 for s in triple}
m = prf([(ours[s], maj[s]) for s in triple])
print(f'vs MAJORITY     n={m["n"]:4d}  P={m["precision"]:.3f} R={m["recall"]:.3f} F1={m["f1"]:.3f}  '
      f'(tp={m["tp"]} fp={m["fp"]} fn={m["fn"]})')

unan_y = [s for s in triple if all(raters[a][s] for a in ('a1','a2','a3'))]
unan_n = [s for s in triple if not any(raters[a][s] for a in ('a1','a2','a3'))]
print(f'UNANIMOUS YES   n={len(unan_y):4d}  we say yes to {sum(ours[s] for s in unan_y)}')
print(f'UNANIMOUS NO    n={len(unan_n):4d}  we say yes to {sum(ours[s] for s in unan_n)}  (false alarms on agreed-no)')

print()
print('=' * 78)
print('3. BASELINES — what a one-line policy achieves on the same ground truth')
print('=' * 78)
def score_policy(fn, label):
    m = prf([(fn(s), maj[s]) for s in triple])
    print(f'{label:46s} P={m["precision"]:.3f} R={m["recall"]:.3f} F1={m["f1"]:.3f}')
by_id = {d['signal_id']: d for d in doss}
score_policy(lambda s: (by_id[s].get('scoring') or {}).get('severity') in ('P0','P1'), 'severity in (P0,P1)')
score_policy(lambda s: (by_id[s].get('hypotheses') or [{}])[0].get('hypothesis') not in ('benign_variation','no_hypothesis'), 'hypothesis not benign/none')
score_policy(lambda s: bool(facts[s].get('reached_human')), 'the agent routed it')
score_policy(lambda s: True, 'always yes')
score_policy(lambda s: ours[s], '>>> OUR EVALUATOR')

print()
print('=' * 78)
print('4. PER-SLICE AGREEMENT — the vault: disagreement concentrates at the boundary')
print('=' * 78)
def slices(name, keyfn):
    print(f'\n-- by {name} --')
    buckets = defaultdict(list)
    for s in triple:
        buckets[keyfn(s)].append(s)
    for k in sorted(buckets, key=lambda x: -len(buckets[x])):
        ss = buckets[k]
        if len(ss) < 8: continue
        pairs = [(ours[s], maj[s]) for s in ss]
        m = prf(pairs)
        # human-human agreement on the same slice, for the ceiling
        hh = []
        for i, x in enumerate(('a1','a2','a3')):
            for y in ('a1','a2','a3')[i+1:]:
                hh += [(raters[x][s], raters[y][s]) for s in ss if s in raters[x] and s in raters[y]]
        hpo = sum(1 for a,b in hh if a==b)/len(hh) if hh else float('nan')
        print(f'  {str(k):26s} n={len(ss):3d}  ours-vs-maj raw={m["raw"]:.3f} F1={m["f1"]:.3f}  '
              f'| human-human raw={hpo:.3f}  (their yes-rate {sum(maj[s] for s in ss)/len(ss):.2f})')
slices('detector', lambda s: by_id[s].get('detector'))
slices('severity', lambda s: (by_id[s].get('scoring') or {}).get('severity'))
slices('disposition', lambda s: (by_id[s].get('decision') or {}).get('disposition'))
slices('reached_human', lambda s: bool(facts[s].get('reached_human')))

print()
print('=' * 78)
print('5. WHERE OUR YESES COME FROM, AND WHAT WE MISS')
print('=' * 78)
print('our deserved=True total:', sum(ours.values()), 'of', len(ours))
print('by reason:')
for r, n in Counter(facts[s]['deserved_reason'] for s in ours if ours[s]).most_common():
    print(f'   {n:4d}  {r}')
print()
fn_ids = [s for s in triple if maj[s] and not ours[s]]
print(f'FALSE NEGATIVES vs majority: {len(fn_ids)} dossiers the annotators wanted and we did not flag')
print('  what those dossiers look like:')
print('   severity:', dict(Counter((by_id[s].get('scoring') or {}).get('severity') for s in fn_ids)))
print('   detector:', dict(Counter(by_id[s].get('detector') for s in fn_ids).most_common(5)))
print('   hypothesis:', dict(Counter((by_id[s].get('hypotheses') or [{}])[0].get('hypothesis') for s in fn_ids).most_common(5)))
print('   reached_human:', dict(Counter(bool(facts[s].get('reached_human')) for s in fn_ids)))
print('   has any uncertain trigger:', sum(1 for s in fn_ids if facts[s]['trigger_source']['uncertain']))
print('   has any unattributed:', sum(1 for s in fn_ids if facts[s]['trigger_source'].get('unattributed')))
