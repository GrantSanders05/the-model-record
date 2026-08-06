import csv
rows=[r for r in csv.DictReader(open('/tmp/nflgames.csv'))
      if r['home_score'] not in ('','NA') and r['spread_line'] not in ('','NA')]
def f(v): return float(v)
# verify convention
g=[(f(r['spread_line']), f(r['home_score'])-f(r['away_score'])) for r in rows]
n=len(g); mx=sum(a for a,_ in g)/n; my=sum(b for _,b in g)/n
sxy=sum((a-mx)*(b-my) for a,b in g); sxx=sum((a-mx)**2 for a,_ in g); syy=sum((b-my)**2 for _,b in g)
slope=sxy/sxx; r=sxy/((sxx*syy)**.5)
print(f"NFL 1999-2026, {n} games with closing lines\n")
print(f"--- Is the Vegas line well calibrated? (actual_margin ~ a + b*spread_line) ---")
print(f"  slope   : {slope:.3f}  (1.000 = perfect)")
print(f"  intercept: {my-slope*mx:+.3f}")
print(f"  r       : {r:.3f}   r^2 = {r*r:.3f}")
print(f"  RMSE    : {(sum((a-b)**2 for a,b in g)/n)**.5:.2f} pts")
print(f"  MAE     : {(sum(abs(a-b) for a,b in g)/n):.2f} pts")

# straight-up baselines
homew=sum(1 for r in rows if f(r['home_score'])>f(r['away_score']))
fav=0; favn=0
for r in rows:
    s=f(r['spread_line']); m=f(r['home_score'])-f(r['away_score'])
    if s==0 or m==0: continue
    favn+=1
    if (s>0 and m>0) or (s<0 and m<0): fav+=1
print(f"\n--- straight-up (moneyline) benchmarks ---")
print(f"  always pick HOME        : {homew/n*100:.2f}%")
print(f"  always pick VEGAS FAVE  : {fav/favn*100:.2f}%   <-- the bar your ML% must beat")

# ATS: betting the favorite
w=l=p=0
for r in rows:
    s=f(r['spread_line']); m=f(r['home_score'])-f(r['away_score'])
    d=m-s
    if d==0: p+=1
    elif (s>0 and d>0) or (s<0 and d<0): w+=1
    else: l+=1
print(f"\n--- ATS benchmarks (vs closing line) ---")
print(f"  bet the favorite every game: {w/(w+l)*100:.2f}%  ({w}-{l}-{p})")
print(f"  break-even at -110 juice   : 52.38%")

# totals
tl=[(f(r['total_line']), f(r['home_score'])+f(r['away_score'])) for r in rows if r['total_line'] not in ('','NA')]
n2=len(tl); a2=sum(a for a,_ in tl)/n2; b2=sum(b for _,b in tl)/n2
sxy2=sum((a-a2)*(b-b2) for a,b in tl); sxx2=sum((a-a2)**2 for a,_ in tl); syy2=sum((b-b2)**2 for _,b in tl)
over=sum(1 for a,b in tl if b>a); under=sum(1 for a,b in tl if b<a)
print(f"\n--- totals (O/U) benchmarks ---")
print(f"  line slope vs actual : {sxy2/sxx2:.3f}   r = {sxy2/((sxx2*syy2)**.5):.3f}")
print(f"  MAE of the Vegas total: {sum(abs(a-b) for a,b in tl)/n2:.2f} pts")
print(f"  st.dev of actual total: {(syy2/n2)**.5:.2f} pts  <-- a naive 'always predict avg' model")
print(f"  overs {over} / unders {under}  ({over/(over+under)*100:.2f}% over)")
