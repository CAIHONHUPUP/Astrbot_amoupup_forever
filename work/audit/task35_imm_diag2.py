# -*- coding: utf-8 -*-
"""task-35 诊断二：逐轮记录 candidate/guard 输出/发送结果，验证 sent 是不是上一轮的残留。"""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "work", "immersion_check")
for _p in (ROOT, os.path.join(ROOT, "tests"), WORK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub
astrbot_stub.install()
import imm_harness as H
import run_imm as IMM
import amo_outbound as OB

orig_send = OB.send
events = []

async def traced_send(plugin, umo, text, **kwargs):
    before = len(plugin.context.sent)
    result = await orig_send(plugin, umo, text, **kwargs)
    events.append({"umo": umo, "text": str(text)[:70], "source": kwargs.get("source"),
                   "result": result, "before": before, "after": len(plugin.context.sent)})
    return result

OB.send = traced_send

with open(IMM.DATASET_PATH, encoding="utf-8") as fh:
    dataset = json.load(fh)

report = {}
for item in dataset["items"]:
    if item["id"] not in ("R01", "S06", "S21", "R13"):
        continue
    events.clear()
    state = {}
    run = IMM.run_item(item, "off", 0.0, state)
    report[item["id"]] = {
        "turns_now": [t.get("now") for t in item["turns"]],
        "turns_candidate": [t.get("candidate", "")[:70] for t in item["turns"]],
        "sent": [t["sent"][:70] for t in run["turns"]],
        "send_events": list(events),
    }

with open(os.path.join(ROOT, "work", "audit", "task35_imm_diag2.json"), "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1)
print("done")
