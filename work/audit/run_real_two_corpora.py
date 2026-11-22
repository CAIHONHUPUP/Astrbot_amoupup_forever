# -*- coding: utf-8 -*-
"""真模型两层复跑：272 条（22 组）与 397 条（32 组）固定抽样，算单轮通过率与 guard_introduced。

只复用 work/sim/real_probe.py 的链路，不改生产代码；抽样计划取自历史证据
(_diag/final_real_probe2.txt 的 22 组、_diag/real_probe_chat.txt 的 32 组)。
"""
import asyncio, json, os, shutil, sys

ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
SIM = os.path.join(ROOT, "work", "sim")
sys.path.insert(0, SIM)
sys.path.insert(0, os.path.join(ROOT, "tests"))

# provider.env（不读 AstrBot cmd_config）
env_path = os.path.join(SIM, "provider.env")
if os.path.exists(env_path):
    for line in open(env_path, encoding="utf-8"):
        if "=" in line:
            key, value = line.strip().split("=", 1)
            os.environ.setdefault(key, value)

import real_probe as R  # noqa: E402

PLANS = {
    "272": {
        "failed": ["G02", "G04", "G06", "G08", "G11", "G13", "G17", "G19",
                   "G22", "G23", "G24", "G31", "G33", "G34", "G36", "G43",
                   "G46", "G47", "G48"],
        "passed_sample": ["G01", "G03", "G05"],
    },
    "397": {
        "failed": ["G02", "G03", "G04", "G06", "G07", "G08", "G09", "G11",
                   "G12", "G13", "G16", "G17", "G19", "G22", "G23", "G24",
                   "G27", "G28", "G29", "G31", "G33", "G34", "G36", "G42",
                   "G43", "G44", "G46", "G47", "G48", "G49"],
        "passed_sample": ["G01", "G05"],
    },
}

OUT = os.path.join(ROOT, "work", "sim", "real_llm_layer.json")
summary = {}
for label, plan in PLANS.items():
    R.pick_groups = lambda plan=plan: dict(plan)
    asyncio.run(R.main())
    data = json.load(open(OUT, encoding="utf-8"))
    rows = data.get("rows") or []
    introduced = [row for row in rows if not row.get("cand_codes") and row.get("sent_codes")]
    data["guard_introduced"] = len(introduced)
    data["guard_introduced_rows"] = [
        {"gid": row.get("gid"), "turn": row.get("turn"), "sent": row.get("sent"),
         "sent_codes": row.get("sent_codes")} for row in introduced]
    dest = r"C:\Users\123\Documents\deepseek-harness\default-workspace\_diag\real_" + label + ".json"
    shutil.copyfile(OUT, dest)
    summary[label] = {
        "turns": data.get("turns"), "failed_turns": data.get("failed_turns"),
        "turn_pass_rate": data.get("turn_pass_rate"),
        "guard_introduced": len(introduced), "codes": data.get("codes"),
        "groups": len(data.get("groups") or []), "file": dest,
    }

print("SUMMARY", json.dumps(summary, ensure_ascii=False, indent=1))
with open(r"C:\Users\123\Documents\deepseek-harness\default-workspace\_diag\real_two_corpora_summary.json", "w", encoding="utf-8") as fh:
    json.dump(summary, fh, ensure_ascii=False, indent=1)
