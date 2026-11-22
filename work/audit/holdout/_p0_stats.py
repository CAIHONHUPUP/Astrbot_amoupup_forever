# -*- coding: utf-8 -*-
import json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
d = json.load(open(os.path.join(HERE, "replay_prod.json"), encoding="utf-8"))
rows = []
for bucket in ("fixed", "persistent", "clean_other_rewrite", "clean_dropped"):
    rows.extend(d["samples"].get(bucket) or [])
FALLBACK_HEADS = ("还是老样子", "我在休息", "我在敷面膜", "唔…这个先不说了",
                  "我试了试，挺喜欢的", "哈哈哈", "我等会儿煮点面吃",
                  "我等会儿煮点面", "在家呢", "刚进家门")
P0 = ("还是老样子", "我在休息", "我试了试", "唔…这个先不说了")
def filled(t): return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", t or "")
bad = []
for r in rows:
    fin = r.get("final") or ""
    for h in FALLBACK_HEADS:
        if fin.startswith(h):
            bad.append((r["id"], "fallback:"+h, r["raw"][:40], fin[:40]))
            break
# grammar checks on all rows
gram = []
for r in rows:
    raw, fin = r.get("raw") or "", r.get("final") or ""
    issues = []
    if raw.count("（")==raw.count("）") and (fin.count("（")!=fin.count("）") or fin.count("(")!=fin.count(")")):
        issues.append("paren")
    for run in set(re.findall(r"([\u4e00-\u9fff])\1", fin)):
        if run*2 not in raw and run not in ("哈","嘿","呵","嗯","啊","呀","呜","哇"):
            issues.append("dup:"+run*2)
    if re.search(r"^(儿|了|的|们)[\u4e00-\u9fff]", fin):
        issues.append("fragment")
    if "看衣服服" in fin or "在回去了的" in fin:
        issues.append("known_grammar")
    if issues:
        gram.append((r["id"], issues, raw[:36], fin[:48]))
code = {"fallback_final": bad, "grammar": gram,
        "clean_other": [(r["id"], r["diff_kind"], r["final"][:30]) for r in d["samples"].get("clean_other_rewrite") or []]}
print(json.dumps(code, ensure_ascii=False, indent=1))
