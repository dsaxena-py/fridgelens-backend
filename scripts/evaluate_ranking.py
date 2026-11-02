"""Offline evaluation of the recipe ranking on the seed data (midterm report, Table 6 and Figure 8).

Re-implements the scoring of recommend_recipes() in Python (weighted match with role weights, staples
excluded, ties by missing count then time) and runs it on random fridges drawn from the 25 detectable
ingredients. It also measures how missed or wrong detections change the top-5 list.
Diet and allergen filters are not applied (default preferences). Seeded, so results are reproducible.

Usage: python scripts/evaluate_ranking.py > ranking_eval.json
"""
import json, random, statistics as st, sys
from collections import Counter
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import build_seed as bs

errs = []
ings = bs.load_ingredients(errs)
recs = bs.load_recipes(ings, errs)
assert not errs, errs[:3]
det = sorted(s for s, i in ings.items() if "class" in i)
staple = {s for s, i in ings.items() if i.get("staple")}
R = []
for r in recs:
    items = [(i["slug"], i["role"], i["weight"]) for i in r["items"] if i["slug"] not in staple]
    R.append(dict(title=r["title"], items=items, time=r.get("prep",0) + r.get("cook",0)))

def rank(F, weighted=True):
    out = []
    for r in R:
        have = [s in F for s, _, _ in r["items"]]
        if not any(have): continue
        m = sum(have) / len(have)
        w = sum(wt for (s, _, wt), h in zip(r["items"], have) if h) / sum(wt for _, _, wt in r["items"])
        miss = sum(1 for (s, role, _), h in zip(r["items"], have) if not h and role != "garnish")
        core_miss = sum(1 for (s, role, _), h in zip(r["items"], have) if not h and role == "core")
        out.append(dict(t=r["title"], m=m, w=w, miss=miss, core=core_miss, time=r["time"]))
    key = (lambda x: (-x["w"], x["miss"], x["time"])) if weighted else (lambda x: (-x["m"], x["miss"], x["time"]))
    return sorted(out, key=key)

res = {}
# dataset stats
per_class = Counter(s for r in R for s, _, _ in {(s, 0, 0) for s, _, _ in r["items"]} if s in det)
res["stats"] = dict(ingredients=len(ings), detectable=len(det), staples=len(staple), recipes=len(R),
    lines=sum(len(r["items"]) for r in recs),
    ing_per_recipe=st.mean(len(r["items"]) for r in R),
    det_per_recipe=st.mean(sum(s in det for s, _, _ in r["items"]) for r in R),
    per_class_min=min(per_class[c] for c in det), per_class_med=st.median(per_class[c] for c in det),
    per_class_max=max(per_class[c] for c in det),
    roles=Counter(role for r in R for _, role, _ in r["items"]))
rng = random.Random(0)
N = 2000
for k in (4, 8, 12):
    rows = dict(cand=[], top1=[], top5w=[], top5miss_w=[], top5core_w=[], top5core_p=[], top5miss_p=[], cookable=[], near=[])
    for _ in range(N):
        F = set(rng.sample(det, k))
        rw, rp = rank(F, True)[:5], rank(F, False)[:5]
        allr = rank(F)
        rows["cand"].append(len(allr))
        rows["top1"].append(rw[0]["w"] if rw else 0)
        rows["top5w"].append(st.mean(x["w"] for x in rw) if rw else 0)
        rows["top5miss_w"].append(st.mean(x["miss"] for x in rw) if rw else 0)
        rows["top5core_w"].append(st.mean(x["core"] for x in rw) if rw else 0)
        rows["top5core_p"].append(st.mean(x["core"] for x in rp) if rp else 0)
        rows["top5miss_p"].append(st.mean(x["miss"] for x in rp) if rp else 0)
        rows["cookable"].append(any(x["miss"] == 0 for x in allr))
        rows["near"].append(any(x["miss"] <= 1 for x in allr))
    res[f"k{k}"] = {a: round(st.mean(v), 3) for a, v in rows.items()}
# robustness to missed detections (false negatives), k=8
for fn in (0.1, 0.2, 0.3):
    ov = []; top1 = []
    for _ in range(N):
        F = set(rng.sample(det, 8))
        D = {s for s in sorted(F) if rng.random() > fn}  # sorted: set order varies with PYTHONHASHSEED
        a = [x["t"] for x in rank(F)[:5]]; b = [x["t"] for x in rank(D)[:5]]
        ov.append(len(set(a) & set(b)) / max(1, len(a))); top1.append(bool(a) and bool(b) and a[0] == b[0])
    res[f"fn{fn}"] = dict(top5_overlap=round(st.mean(ov), 3), top1_same=round(st.mean(top1), 3))
# false positives: one extra wrong ingredient, k=8
ov = []; top1 = []
for _ in range(N):
    F = set(rng.sample(det, 8)); extra = rng.choice([d for d in det if d not in F])
    a = [x["t"] for x in rank(F)[:5]]; b = [x["t"] for x in rank(F | {extra})[:5]]
    ov.append(len(set(a) & set(b)) / 5); top1.append(a[0] == b[0])
res["fp1"] = dict(top5_overlap=round(st.mean(ov), 3), top1_same=round(st.mean(top1), 3))
# example
F = {"egg", "tomato", "onion", "cheese", "bell_pepper", "milk"}
res["example"] = [(x["t"], round(x["m"], 2), round(x["w"], 2), x["miss"]) for x in rank(F)[:5]]
import time
t = time.perf_counter()
for _ in range(1000): rank(set(rng.sample(det, 8)))
res["ms_per_rank_python"] = round((time.perf_counter() - t), 3)
print(json.dumps(res, indent=1, default=str))
