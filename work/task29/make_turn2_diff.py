# -*- coding: utf-8 -*-
"""生成本轮（guard-introduced 收口）在 amo_state_consistency.py 上的 diff。

基线 = work/task29/after/amo_state_consistency.py（上一轮 task-29 完成时快照；
其中不含 task-32 收件人守卫，diff 里会带出那一块，已标注非本轮改动）。
"""
import difflib
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.join(ROOT, "work", "task29", "after", "amo_state_consistency.py")
CUR = os.path.join(ROOT, "amo_state_consistency.py")
OUT = os.path.join(ROOT, "work", "task29", "turn2_state_consistency.diff")
with io.open(BASE, encoding="utf-8") as fh:
    before = fh.read().splitlines(True)
with io.open(CUR, encoding="utf-8") as fh:
    after = fh.read().splitlines(True)
chunks = list(difflib.unified_diff(before, after,
                                   fromfile="a/amo_state_consistency.py(task-29 完成时)",
                                   tofile="b/amo_state_consistency.py(本轮收口后)", n=3))
with io.open(OUT, "w", encoding="utf-8", newline="") as fh:
    fh.writelines(chunks)
add = sum(1 for line in chunks if line.startswith("+") and not line.startswith("+++"))
dele = sum(1 for line in chunks if line.startswith("-") and not line.startswith("---"))
print("diff lines:", len(chunks), "+%d -%d" % (add, dele))
