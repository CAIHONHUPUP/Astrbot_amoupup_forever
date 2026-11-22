# -*- coding: utf-8 -*-
import difflib, io
before = io.open("work/task29/after/amo_state_consistency.py", encoding="utf-8").read().splitlines(True)
after = io.open("amo_state_consistency.py", encoding="utf-8").read().splitlines(True)
diff = list(difflib.unified_diff(before, after, fromfile="task29_after", tofile="current", n=4))
print("diff lines:", len(diff))
print("".join(diff)[:12000])
