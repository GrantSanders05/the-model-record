import json, statistics as st
from collections import defaultdict

d=json.load(open('/Users/haileyclark/Downloads/S2-Media/_s2-media/config/nba_tracker_data.json'))
games=[]
for tab in d['tabs']:
    for g in tab:
        if isinstance(g,dict) and g.get('home_score') is not None and g.get('model_diff') is not None:
            games.append(g)
print("games with scores+model:",len(games))

# 1. Is model_diff point-in-time or static? Check variance of a team's rating over season
seen=defaultdict(set)
for g in games:
    seen[g['home']].add(round(g['home_hfa_total'],1))
    seen[g['away']].add(round(g['away_hfa_total'],1))
sample=list(seen.items())[:6]
print("\n--- ratings distinct values per team (home/away pooled) ---")
for t,v in sample:
    print(f"  {t:12s} {len(v)} distinct  e.g. {sorted(v)[:6]}")

# 2. Accuracy baselines
mlc=sum(1 for g in games if g.get('ml_correct')=='Y')
home_wins=sum(1 for g in games if g['home_score']>g['away_score'])
print(f"\n--- straight-up (moneyline) ---")
print(f"  model ML accuracy   : {mlc/len(games)*100:.2f}%  ({mlc}/{len(games)})")
print(f"  always-pick-HOME    : {home_wins/len(games)*100:.2f}%")

# 3. Model margin vs actual margin
x=[g['model_diff'] for g in games]              # predicted home margin
y=[g['home_score']-g['away_score'] for g in games]  # actual home margin
n=len(x); mx=sum(x)/n; my=sum(y)/n
sxy=sum((a-mx)*(b-my) for a,b in zip(x,y)); sxx=sum((a-mx)**2 for a in x); syy=sum((b-my)**2 for b in y)
slope=sxy/sxx; icpt=my-slope*mx; r=sxy/((sxx*syy)**.5)
print(f"\n--- calibration: actual_margin ~ a + b*model_diff ---")
print(f"  mean model_diff     : {mx:+.2f}   mean actual margin: {my:+.2f}")
print(f"  slope b             : {slope:.3f}   (1.000 = perfectly scaled)")
print(f"  intercept a         : {icpt:+.3f}  (residual home-court edge, pts)")
print(f"  correlation r       : {r:.3f}   r^2 = {r*r:.3f}")
mae=sum(abs(a-b) for a,b in zip(x,y))/n
rmse=(sum((a-b)**2 for a,b in zip(x,y))/n)**.5
print(f"  MAE  (raw model)    : {mae:.2f} pts")
print(f"  RMSE (raw model)    : {rmse:.2f} pts")
# recalibrated
yc=[icpt+slope*a for a in x]
mae2=sum(abs(p-b) for p,b in zip(yc,y))/n
rmse2=(sum((p-b)**2 for p,b in zip(yc,y))/n)**.5
print(f"  MAE  (recalibrated) : {mae2:.2f} pts")
print(f"  RMSE (recalibrated) : {rmse2:.2f} pts")
print(f"  st.dev of actual margin (null model RMSE): {(syy/n)**.5:.2f} pts")

# 4. what multiplier is implied? his grades are doubled. slope tells us the correction
print(f"\n  -> his 'x2' multiplier should be x{2*slope:.2f} to be calibrated")

# 5. totals: is there any signal? distribution of combined score
tot=[g['home_score']+g['away_score'] for g in games]
print(f"\n--- game totals (for the O/U you want to add) ---")
print(f"  mean combined score : {sum(tot)/len(tot):.1f}")
print(f"  st.dev              : {st.pstdev(tot):.1f}")
print(f"  min / max           : {min(tot)} / {max(tot)}")
