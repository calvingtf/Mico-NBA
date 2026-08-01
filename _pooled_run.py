import sys, json
from itertools import combinations
from math import comb
sys.stdout.reconfigure(encoding="utf-8")
from mironba.sim.deadline import run, score, actual_deadline_trades
from mironba.world.calendar import CALENDARS
ALL=435
def p_hit(q,N): return 1.0 if ALL-q<N else 1-comb(ALL-q,N)/comb(ALL,N)
tot={"p":0,"a":0,"m":0,"h":0}; nulls=[]; allq=set(); rows=[]
for s in sorted(CALENDARS):
    r=run(season=s); sc=score(r,season=s)
    pairs={frozenset(p.pair) for p in r.proposals}; a=actual_deadline_trades(s)
    allq |= {frozenset(c) for t in a for c in combinations(sorted(t.teams),2)}
    for t in a:
        nulls.append(p_hit(len({frozenset(c) for c in combinations(sorted(t.teams),2)}), len(pairs)))
    rows.append((s, sc.proposed, len(pairs), sc.actual, sc.actual_matched, sc.pair_hits))
    print(f"{s} proposed={sc.proposed} pairs={len(pairs)} actual={sc.actual} "
          f"matched={sc.actual_matched} prec={sc.precision*100:.2f}%", flush=True)
    tot["p"]+=sc.proposed; tot["a"]+=sc.actual; tot["m"]+=sc.actual_matched; tot["h"]+=sc.pair_hits
np_=len(allq)/ALL*100; obs=tot['h']/tot['p']*100 if tot['p'] else 0
print("\n=== POOLED, 10 seasons ===")
print(f"proposed {tot['p']}  actual {tot['a']}  matched {tot['m']}")
print(f"recall     {tot['m']}/{tot['a']} = {tot['m']/tot['a']*100:.1f}%   null expects {sum(nulls):.1f} of {tot['a']} = {sum(nulls)/tot['a']*100:.1f}%")
print(f"           delta {tot['m']/tot['a']*100 - sum(nulls)/tot['a']*100:+.1f} pts")
print(f"precision  {obs:.2f}%   null {np_:.2f}%   delta {obs-np_:+.2f} pts")
json.dump({"rows":rows,"null_prec":np_,"obs_prec":obs,"null_matched":sum(nulls),
           "tot":tot}, open("bench-pooled-10season.json","w"), indent=1)
