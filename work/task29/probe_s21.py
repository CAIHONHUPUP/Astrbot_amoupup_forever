# -*- coding: utf-8 -*-
"""对比 S21 在「复检开/关」下的发送序列（判断 IMM 失败是否由 task-29 引入）。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "work", "immersion_check")
for path in (ROOT, os.path.join(ROOT, "tests"), WORK):
    sys.path.insert(0, path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_grounding_critic as GC  # noqa: E402
import imm_harness as H  # noqa: F401,E402
import run_imm as IMM  # noqa: E402

with open(IMM.DATASET_PATH, encoding="utf-8") as fh:
    DS = json.load(fh)


def run(item, patch):
    if patch:
        orig = GC.post_rewrite_recheck
        GC.post_rewrite_recheck = lambda o, n, u, answered=None: n
    try:
        state = {}
        result = IMM.run_item(item, "off", 0.0, state)
        return [t["sent"] for t in result["turns"]]
    finally:
        if patch:
            GC.post_rewrite_recheck = orig


for item_id in ("S06", "S21", "S22", "S25", "R38"):
    item = next(i for i in DS["items"] if i["id"] == item_id)
    on = run(item, False)
    off = run(item, True)
    same = on == off
    print(item_id, "same_on_off:", same)
    if not same:
        print("  recheck ON :", on)
        print("  recheck OFF:", off)
