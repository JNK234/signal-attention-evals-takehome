"""
ABOUTME: The window-respecting variants — severity read as §6.3 defines it, gated so it cannot
ABOUTME: override §8.1's own 90-day departure bound (the clause that broke sig_0059 and sig_0441).
"""
import hashlib, json, random, sys, pathlib
from collections import defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from eval_takehome import SignalEvaluator

rd = lambda p: [json.loads(l) for l in open(p)]
doss = rd('data/signal_dossiers.jsonl'); by_id = {d['signal_id']: d for d in doss}
ann = defaultdict(dict)
for i in (1,2,3):
    for r in rd(f'data/annotations/annotator_{i}.jsonl'): ann[f'a{i}'][r['signal_id']] = r
triple = sorted(set(ann['a1']) & set(ann['a2']) & set(ann['a3']))
maj = {s: sum(bool(ann[a][s]['deserved_attention']) for a in ('a1','a2','a3')) >= 2 for s in triple}
half = lambda x: int(hashlib.sha1(f'deserved-v2|{x}'.encode()).hexdigest(),16) % 2
DEV=[s for s in triple if half(s)==0]; HELD=[s for s in triple if half(s)==1]
golds = json.load(open('/tmp/golden_deserved.json'))

ev = SignalEvaluator()
ev.load_context(rd('data/accounts.jsonl'), rd('data/owners.jsonl'),
                rd('data/telemetry.jsonl'), rd('data/artifacts.jsonl'), doss)
spec, facts, rules_of = {}, {}, {}
for d in doss:
    r = ev.explain(d); sid = d['signal_id']
    spec[sid]=r['deserved_attention']; facts[sid]=r['_facts']
    rules_of[sid]={v['rule'] for v in r['violations']}

sev=lambda s:(by_id[s].get('scoring') or {}).get('severity')
hyp=lambda s:(by_id[s].get('hypotheses') or [{}])[0].get('hypothesis')
dtr=lambda s: facts[s].get('days_to_renewal')

def departure_claim(s):
    """The agent's hypothesis is a champion/buyer departure — the one §8.1 bullet with a 90-day bound."""
    return hyp(s) == 'champion_departure'

def window_ok(s):
    """§8.1 bullet 4 bounds a departure to 90 days of renewal. Outside it, the spec deliberately does
    NOT require a human, so a severity read must not override that."""
    if not departure_claim(s): return True
    return dtr(s) is not None and 0 <= dtr(s) <= 90

POLICIES = {
 'SHIPPED (§8.1/§4.6/§6.3)':
   lambda s: spec[s],
 'A ungated severity (rejected)':
   lambda s: spec[s] or (sev(s) in ('P0','P1') and hyp(s) not in ('benign_variation','no_hypothesis')),
 'B A + respects §8.1 dep window':
   lambda s: spec[s] or (sev(s) in ('P0','P1') and hyp(s) not in ('benign_variation','no_hypothesis') and window_ok(s)),
 'C B + Q3 must not fire':
   lambda s: spec[s] or (sev(s) in ('P0','P1') and hyp(s) not in ('benign_variation','no_hypothesis')
                         and window_ok(s) and 'Q3' not in rules_of[s]),
 'D P0 only (+window)':
   lambda s: spec[s] or (sev(s) == 'P0' and window_ok(s)),
 'E B but P0/P1 + corroboration':
   lambda s: spec[s] or (sev(s) in ('P0','P1') and hyp(s) not in ('benign_variation','no_hypothesis')
                         and window_ok(s) and len(facts[s].get('verified_sources') or []) >= 1),
}

def met(p, ids):
    fires=[s for s in ids if p(s)]; want=[s for s in ids if maj[s]]
    tp=sum(1 for s in fires if maj[s])
    cov=tp/len(want) if want else 0; ffr=(len(fires)-tp)/len(fires) if fires else 0
    return cov, ffr, (2*cov*(1-ffr)/(cov+1-ffr) if (cov+1-ffr) else 0), len(fires), tp

def perm_p(p, ids, n=4000, seed=13):
    obs=met(p,ids)[2]; rng=random.Random(seed)
    labels=[maj[s] for s in ids]; fires=[p(s) for s in ids]; nw=sum(labels); nf=sum(fires); h=0
    for _ in range(n):
        rng.shuffle(labels)
        tp=sum(1 for f,l in zip(fires,labels) if f and l)
        cov=tp/nw if nw else 0; ffr=(nf-tp)/nf if nf else 0
        if (2*cov*(1-ffr)/(cov+1-ffr) if (cov+1-ffr) else 0) >= obs: h+=1
    return h/n

print(f'{"policy":34s} {"devAl":>6s} {"heldAl":>6s} {"devFFR":>7s} {"heldFFR":>8s} {"gold":>7s} {"p(dev)":>7s}  corpus-yes')
for name, p in POLICIES.items():
    dc,df,da,dn,dtp = met(p, DEV); hc,hf,ha,hn,htp = met(p, HELD)
    g = sum(1 for s,v in golds.items() if p(s)==v)
    tot = sum(1 for d in doss if p(d['signal_id']))
    print(f'{name:34s} {da:6.3f} {ha:6.3f} {df:7.3f} {hf:8.3f} {g:4d}/{len(golds)} {perm_p(p,DEV):7.3f}  {tot:4d}/629')
    bad=[s for s,v in golds.items() if p(s)!=v]
    if bad: print(f'{"":34s}   breaks: {bad}')
