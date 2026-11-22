# -*- coding: utf-8 -*-
import io, os
path = os.path.join("work", "task29", "task29.diff")
lines = io.open(path, encoding="utf-8").read().splitlines()
stats = {}
cur = None
for line in lines:
    if line.startswith("+++"):
        cur = line[6:].strip()
        stats.setdefault(cur, {"+": 0, "-": 0, "hunks": 0})
    elif line.startswith("@@") and cur:
        stats[cur]["hunks"] += 1
    elif cur and line.startswith("+") and not line.startswith("+++"):
        stats[cur]["+"] += 1
    elif cur and line.startswith("-") and not line.startswith("---"):
        stats[cur]["-"] += 1
total_add = total_del = 0
for name, item in stats.items():
    print("%-42s +%-4d -%-3d hunks=%d" % (name, item["+"], item["-"], item["hunks"]))
    total_add += item["+"]; total_del += item["-"]
print("TOTAL +%d -%d, diff lines %d" % (total_add, total_del, len(lines)))
