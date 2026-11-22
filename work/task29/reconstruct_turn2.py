# -*- coding: utf-8 -*-
"""重建「上一轮 task-29 完成时」文件，并 diff 当前文件，识别并发改动。"""
import difflib
import io
import os

HERE = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
BASE = os.path.join(HERE, "work", "task29", "after", "amo_state_consistency.py")
DIFF = os.path.join(HERE, "work", "task29", "turn2_state_consistency.diff")
CUR = os.path.join(HERE, "amo_state_consistency.py")
OUT = os.path.join(HERE, "work", "task29", "concurrent_state_diff.txt")

with io.open(BASE, encoding="utf-8") as fh:
    lines = fh.read().splitlines(True)
with io.open(DIFF, encoding="utf-8") as fh:
    patch = fh.read().splitlines(True)

# 极简 apply：按 hunk 头把 BASE 行替换成 patch 的 +/- 行
out = []
i = 0
idx = 0
hunks = []
for line in patch:
    if line.startswith("@@"):
        hunks.append(line)
    elif line.startswith(("+++", "---")) or not line.strip():
        continue
hunk_iter = iter(hunk_lines for hunk_lines in [])
# 重新组织：按位置顺序消费
ops = []
for line in patch:
    if line.startswith("@@"):
        hdr = line
        import re
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", hdr)
        ops.append([int(m.group(1)), []])
    elif ops:
        ops[-1][1].append(line)

pos = 0
for start, body in ops:
    target = start - 1
    out.extend(lines[pos:target])
    for line in body:
        if line.startswith("-"):
            continue
        if line.startswith("+"):
            out.append(line[1:])
        else:
            out.append(line[1:] if line.startswith(" ") else line)
    # 计算本 hunk 消耗的旧行数
    old_count = sum(1 for line in body if line.startswith((" ", "-")))
    pos = target + old_count
out.extend(lines[pos:])
rebuilt = "".join(out)
with io.open(os.path.join(HERE, "work", "task29", "turn2_final_state_consistency.py"), "w",
             encoding="utf-8") as fh:
    fh.write(rebuilt)
with io.open(CUR, encoding="utf-8") as fh:
    cur = fh.read()
diff = list(difflib.unified_diff(rebuilt.splitlines(True), cur.splitlines(True),
                                 fromfile="turn2_final(我的上一版)", tofile="current(并发后)",
                                 n=2))
io.open(OUT, "w", encoding="utf-8").writelines(diff)
print("diff lines:", len(diff))
print("".join(diff)[:15000])
