"""
ABOUTME: Exhaustive sweep for deserved_attention — every 1- and 2-term combination of atomic
ABOUTME: predicates, scored on dev/held-out/goldens, with a permutation test to separate signal from noise.
"""
import hashlib, itertools, json, random, sys, pathlib
from collections import defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from eval_takehome import SignalEvaluator

FFR_CEILING = 0.55
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
golds = json.load(open('/tmp/golden_deserved.json'))

ev = SignalEvaluator()
ev.load_context(rd('data/accounts.jsonl'), rd('data/owners.jsonl'),
                rd('data/telemetry.jsonl'), rd('data/artifacts.jsonl'), doss)
spec, facts, rules_of = {}, {}, {}
for d in doss:
    r = ev.explain(d)
    spec[d['signal_id']] = r['deserved_attention']
    facts[d['signal_id']] = r['_facts']
    rules_of[d['signal_id']] = {v['rule'] for v in r['violations']}

sev  = lambda s: (by_id[s].get('scoring') or {}).get('severity')
hyp  = lambda s: (by_id[s].get('hypotheses') or [{}])[0].get('hypothesis')
conf = lambda s: (by_id[s].get('hypotheses') or [{}])[0].get('confidence')
dtr  = lambda s: facts[s].get('days_to_renewal')
art  = lambda s: 'artifact' in (facts[s].get('claim_status') or [])

# Atomic predicates. Tag each with whether it reads the AGENT'S OWN ANSWER or the EVIDENCE.
ATOMS = {
  'sev_P0P1':       (lambda s: sev(s) in ('P0','P1'),                                     'agent'),
  'sev_not_P3':     (lambda s: sev(s) != 'P3',                                            'agent'),
  'nonbenign':      (lambda s: hyp(s) not in ('benign_variation','no_hypothesis'),        'agent'),
  'conf_not_low':   (lambda s: conf(s) != 'low',                                          'agent'),
  'renew_le90':     (lambda s: dtr(s) is not None and dtr(s) <= 90,                       'evidence'),
  'renew_le60':     (lambda s: dtr(s) is not None and dtr(s) <= 60,                       'evidence'),
  'renew_le30':     (lambda s: dtr(s) is not None and dtr(s) <= 30,                       'evidence'),
  'customer_text':  (lambda s: bool(facts[s].get('has_customer_text')),                   'evidence'),
  'artifact_claim': (lambda s: art(s),                                                    'evidence'),
  'src_ge2':        (lambda s: len(facts[s].get('verified_sources') or []) >= 2,           'evidence'),
  'src_ge1':        (lambda s: len(facts[s].get('verified_sources') or []) >= 1,           'evidence'),
  'any_trigger':    (lambda s: bool(facts[s]['trigger_source']['confirmed']) or bool(facts[s]['trigger_source']['uncertain']), 'evidence'),
  'I6_fabricated':  (lambda s: 'I6' in rules_of[s],                                        'evidence'),
  'reached_human':  (lambda s: bool(facts[s].get('reached_human')),                        'agent'),
}

def metrics(pred, ids):
    fires = [s for s in ids if pred(s)]
    want  = [s for s in ids if maj[s]]
    tp = sum(1 for s in fires if maj[s])
    cov = tp/len(want) if want else 0.0
    ffr = (len(fires)-tp)/len(fires) if fires else 0.0
    a = 2*cov*(1-ffr)/(cov+(1-ffr)) if (cov+1-ffr) else 0.0
    return cov, ffr, a, len(fires), tp

def gold_score(pred):
    return sum(1 for s, g in golds.items() if pred(s) == g)

def perm_p(pred, ids, n=3000, seed=11):
    """Permutation test: shuffle the labels, how often does a random relabelling beat this alignment?
    Separates real signal from a predicate that just happens to fire at a lucky rate."""
    obs = metrics(pred, ids)[2]
    rng = random.Random(seed)
    labels = [maj[s] for s in ids]
    fires = [pred(s) for s in ids]
    nwant = sum(labels)
    hits = 0
    for _ in range(n):
        rng.shuffle(labels)
        tp = sum(1 for f, l in zip(fires, labels) if f and l)
        nf = sum(fires)
        cov = tp/nwant if nwant else 0
        ffr = (nf-tp)/nf if nf else 0
        a = 2*cov*(1-ffr)/(cov+(1-ffr)) if (cov+1-ffr) else 0
        if a >= obs: hits += 1
    return hits/n

# build every 1- and 2-term AND combination, each OR'd with the spec rule
policies = {'SPEC ONLY': (lambda s: spec[s], 'spec')}
for name, (fn, kind) in ATOMS.items():
    policies[f'spec OR {name}'] = (lambda s, f=fn: spec[s] or f(s), kind)
for (n1, (f1, k1)), (n2, (f2, k2)) in itertools.combinations(ATOMS.items(), 2):
    kind = 'evidence' if k1 == 'evidence' and k2 == 'evidence' else 'agent'
    policies[f'spec OR ({n1}+{n2})'] = (lambda s, a=f1, b=f2: spec[s] or (a(s) and b(s)), kind)

print(f'sweep: {len(policies)} policies   FFR ceiling {FFR_CEILING}   '
      f'dev n={len(DEV)} held n={len(HELD)} goldens n={len(golds)}')
rows = []
for name, (fn, kind) in policies.items():
    dc, df, da, dn, dtp = metrics(fn, DEV)
    hc, hf, ha, hn, htp = metrics(fn, HELD)
    rows.append(dict(name=name, kind=kind, da=da, ha=ha, df=df, hf=hf, dc=dc, hc=hc,
                     gold=gold_score(fn), stable=min(da, ha), fn=fn))

# keep: must clear FFR on BOTH halves and not break a single golden
survivors = [r for r in rows if r['df'] <= FFR_CEILING and r['hf'] <= FFR_CEILING and r['gold'] == len(golds)]
print(f'\nsurvivors (FFR ok on both halves AND 18/18 goldens): {len(survivors)}')

print('\n' + '=' * 118)
print('TOP 25 BY WORST-HALF ALIGNMENT (stability, not dev performance) — gold column is the spec tiebreaker')
print('=' * 118)
print(f'{"policy":38s} {"reads":>9s} {"devAl":>6s} {"heldAl":>6s} {"worst":>6s} {"devFFR":>7s} {"heldFFR":>8s} {"gold":>6s} {"p(perm)":>8s}')
for r in sorted(rows, key=lambda x: -x['stable'])[:25]:
    p = perm_p(r['fn'], DEV)
    flag = ''
    if r['gold'] < len(golds): flag = f' BREAKS {len(golds)-r["gold"]} GOLD'
    elif r['df'] > FFR_CEILING or r['hf'] > FFR_CEILING: flag = ' over FFR'
    print(f'{r["name"]:38s} {r["kind"]:>9s} {r["da"]:6.3f} {r["ha"]:6.3f} {r["stable"]:6.3f} '
          f'{r["df"]:7.3f} {r["hf"]:8.3f} {r["gold"]:4d}/{len(golds)} {p:8.3f}{flag}')

print('\n' + '=' * 118)
print('EVIDENCE-ONLY POLICIES (no agent self-report) — the admissible set under the original principle')
print('=' * 118)
ev_only = [r for r in rows if r['kind'] in ('evidence','spec')]
print(f'{"policy":38s} {"devAl":>6s} {"heldAl":>6s} {"worst":>6s} {"devFFR":>7s} {"heldFFR":>8s} {"gold":>6s}')
for r in sorted(ev_only, key=lambda x: -x['stable'])[:12]:
    print(f'{r["name"]:38s} {r["da"]:6.3f} {r["ha"]:6.3f} {r["stable"]:6.3f} '
          f'{r["df"]:7.3f} {r["hf"]:8.3f} {r["gold"]:4d}/{len(golds)}')
