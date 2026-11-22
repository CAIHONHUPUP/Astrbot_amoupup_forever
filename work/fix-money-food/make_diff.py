# -*- coding: utf-8 -*-
import difflib, json, os, sys

ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
BEFORE = os.path.join(ROOT, "work", "fix-money-food", "before")
OUT = os.path.join(ROOT, "work", "fix-money-food", "diff")
os.makedirs(OUT, exist_ok=True)
shared = {"amo_schedule.py", "amo_proactive.py", "amo_state_jit.py", "amo_feature_api.py"}
files = [name for name in sorted(os.listdir(BEFORE))
         if name.endswith(".py") or name == "_conf_schema.json"]
files += ["amo_state_todo.py", "amo_runtime_tick.py"]
stats = []
for name in files:
    src = os.path.join(BEFORE, name)
    cur = os.path.join(ROOT, name)
    if not os.path.exists(cur):
        continue
    old = open(src, encoding="utf-8").read().splitlines() if os.path.exists(src) else []
    new = open(cur, encoding="utf-8").read().splitlines()
    diff = list(difflib.unified_diff(old, new, fromfile="before/" + name,
                                     tofile="after/" + name, lineterm="", n=2))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    path = os.path.join(OUT, name + ".diff")
    open(path, "w", encoding="utf-8").write("\n".join(diff) + "\n")
    stats.append({"file": name, "added": added, "removed": removed,
                  "shared_with_other_tasks": name in shared,
                  "diff_lines": len(diff)})
print(json.dumps(stats, ensure_ascii=False, indent=2))
