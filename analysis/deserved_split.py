"""
ABOUTME: Deterministic dev/held-out split of the 130 triple-annotated dossiers, then the breakage
ABOUTME: sample — the misses and false alarms on DEV ONLY, with the annotators' own notes.
"""
import hashlib, json, sys, pathlib
from collections import Counter, defaultdict
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

def half(sid):
    return int(hashlib.sha1(f'deserved-v2|{sid}'.encode()).hexdigest(), 16) % 2
DEV = [s for s in triple if half(s) == 0]
HELD = [s for s in triple if half(s) == 1]

ev = SignalEvaluator()
ev.load_context(rd('data/accounts.jsonl'), rd('data/owners.jsonl'),
                rd('data/telemetry.jsonl'), rd('data/artifacts.jsonl'), doss)
ours, facts = {}, {}
for d in doss:
    r = ev.explain(d)
    ours[d['signal_id']] = r['deserved_attention']
    facts[d['signal_id']] = r['_facts']

print(f'SPLIT  dev={len(DEV)}  held-out={len(HELD)}   (yes-rate dev {sum(maj[s] for s in DEV)/len(DEV):.2f}, '
      f'held {sum(maj[s] for s in HELD)/len(HELD):.2f})')
json.dump({'dev': DEV, 'held': HELD, 'maj': maj}, open('analysis/.cache/deserved_split.json','w'))

miss = [s for s in DEV if maj[s] and not ours[s]]
fa   = [s for s in DEV if ours[s] and not maj[s]]
print(f'DEV breakage: {len(miss)} misses, {len(fa)} false alarms\n')

print('=' * 100)
print('THE MISSES — annotators wanted a human, we said no')
print('=' * 100)
for s in miss:
    d, f = by_id[s], facts[s]
    sc = d.get('scoring') or {}
    h = (d.get('hypotheses') or [{}])[0]
    votes = ''.join('Y' if ann[a][s]['deserved_attention'] else 'n' for a in ('a1','a2','a3'))
    print(f'\n--- {s}  votes={votes}  detector={d.get("detector")}  sev={sc.get("severity")}  '
          f'disp={(d.get("decision") or {}).get("disposition")}  reached_human={f.get("reached_human")}')
    print(f'    hypothesis={h.get("hypothesis")}/{h.get("confidence")}  arr_at_risk={sc.get("arr_at_risk")}  '
          f'days_to_renewal={f.get("days_to_renewal")}')
    print(f'    triggers: confirmed={f["trigger_source"]["confirmed"]} uncertain={f["trigger_source"]["uncertain"]} '
          f'unattributed={f["trigger_source"].get("unattributed")}')
    print(f'    claim_status={f.get("claim_status")}  cohort_match={f.get("cohort_match")}  '
          f'verified_sources={f.get("verified_sources")}  has_customer_text={f.get("has_customer_text")}')
    print(f'    our rules fired: {sorted({v["rule"] for v in ev.evaluate(d)["violations"]})}')
    for a in ('a1','a2','a3'):
        r = ann[a][s]
        if r['deserved_attention']:
            cats = [fp.get('category') for fp in (r.get('failure_points') or [])]
            print(f'    {a} YES q={r.get("quality_score")} flags={r.get("risk_flags")} cats={cats}')
            print(f'         "{(r.get("overall_assessment") or "")[:150]}"')

print()
print('=' * 100)
print('THE FALSE ALARMS — we said yes, majority said no (these set the FFR ceiling)')
print('=' * 100)
for s in fa:
    d, f = by_id[s], facts[s]
    sc = d.get('scoring') or {}
    votes = ''.join('Y' if ann[a][s]['deserved_attention'] else 'n' for a in ('a1','a2','a3'))
    print(f'{s}  votes={votes}  reason={f["deserved_reason"][:60]}  sev={sc.get("severity")} '
          f'disp={(d.get("decision") or {}).get("disposition")} reached_human={f.get("reached_human")}')

print()
print('=' * 100)
print('FEATURE PROFILE — misses vs the rest of dev (candidate generation material)')
print('=' * 100)
def prof(name, fn):
    m = sum(1 for s in miss if fn(s)); mr = m/len(miss)
    rest = [s for s in DEV if s not in miss]
    o = sum(1 for s in rest if fn(s)); orr = o/len(rest)
    want = sum(1 for s in DEV if fn(s) and maj[s]); fires = sum(1 for s in DEV if fn(s))
    prec = want/fires if fires else 0
    print(f'{name:52s} miss {m:2d}/{len(miss)} ({mr:.0%})  rest {o:2d}/{len(rest)} ({orr:.0%})  '
          f'| fires {fires:2d}, majority-wants {want:2d}, prec {prec:.2f}')

prof('hypothesis == benign_variation', lambda s: (by_id[s].get('hypotheses') or [{}])[0].get('hypothesis') == 'benign_variation')
prof('claim_status has artifact', lambda s: 'artifact' in (facts[s].get('claim_status') or []))
prof('benign_variation AND artifact claim', lambda s: (by_id[s].get('hypotheses') or [{}])[0].get('hypothesis') == 'benign_variation' and 'artifact' in (facts[s].get('claim_status') or []))
prof('days_to_renewal <= 60', lambda s: (facts[s].get('days_to_renewal') is not None and facts[s]['days_to_renewal'] <= 60))
prof('days_to_renewal <= 90', lambda s: (facts[s].get('days_to_renewal') is not None and facts[s]['days_to_renewal'] <= 90))
prof('has_customer_text', lambda s: bool(facts[s].get('has_customer_text')))
prof('customer text AND renewal <= 90', lambda s: bool(facts[s].get('has_customer_text')) and facts[s].get('days_to_renewal') is not None and facts[s]['days_to_renewal'] <= 90)
prof('>= 2 verified sources', lambda s: len(facts[s].get('verified_sources') or []) >= 2)
prof('suppressed', lambda s: (by_id[s].get('decision') or {}).get('disposition') == 'suppressed')
prof('suppressed AND artifact claim', lambda s: (by_id[s].get('decision') or {}).get('disposition') == 'suppressed' and 'artifact' in (facts[s].get('claim_status') or []))
prof('any I6 fabricated evidence', lambda s: any(v['rule']=='I6' for v in ev.evaluate(by_id[s])['violations']))
prof('any uncertain trigger', lambda s: bool(facts[s]['trigger_source']['uncertain']))
