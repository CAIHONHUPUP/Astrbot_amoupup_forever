# -*- coding: utf-8 -*-
"""用最新真实模型层记录下来的候选，离线重放完整 272 轮守卫链。

用途：验证 task-29 收口后 guard-introduced=0 / 单轮通过率，且不重新调模型
（隔离环境没有 chat provider，候选就是 lead 12:39 那次真实模型输出）。
"""
import asyncio
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SIM = os.path.join(HERE, "work", "sim")
sys.path.insert(0, SIM)

import daily_checks as C  # noqa: E402
import sim_env as E  # noqa: E402
import sim_worlds as W  # noqa: E402

REPORT = os.path.join(SIM, "real_llm_layer.json")
OUT = os.path.join(HERE, "work", "task29", "replay_after_fix.json")
data = json.load(io.open(REPORT, encoding="utf-8"))
rows = data["rows"]
picks = list(data.get("groups") or [])
groups = {g["gid"]: g for g in W.build_groups(50)}
old = {(r["gid"], r["turn"]): r for r in rows}


async def replay_group(gid):
    group = groups[gid]
    plugin = E.build_plugin(group["blocks"], kv={}, health=group["health"])
    prev = None
    out_rows = []
    for turn in group["turns"]:
        src = old.get((gid, turn["index"]))
        if src is None:
            continue
        block = W.block_at(group["blocks"], turn["time"])
        plugin.db.kv["date_session"] = (
            {"phase": "active", "together": True, "place": block.get("place")}
            if block.get("together") else {"phase": "ended"})
        if group["trip"]:
            plugin.db.kv["trip"] = dict(group["trip"])
        else:
            plugin.db.kv.pop("trip", None)
        with E.frozen_clock(turn["time"]):
            snap = dict(plugin.state.current_activity() or {})
        meal_now = W.meal_ok(group["blocks"], turn["time"])
        together = bool(snap.get("together"))
        candidate = str(src.get("candidate") or "")
        out = await E.run_turn(plugin, turn["user"], candidate, turn["time"])
        ctx = {"snap": snap, "prev_snap": prev, "probe": turn["probe"],
               "together": together, "meal_ok": meal_now}
        _ci, cand_codes = C.evaluate(candidate, ctx)
        _si, sent_codes = C.evaluate(out["sent"], ctx)
        out_rows.append({
            "gid": gid, "turn": turn["index"], "time": turn["time"],
            "probe": turn["probe"], "snap": {k: snap.get(k) for k in ("place", "zone", "activity")},
            "user": turn["user"], "candidate": candidate,
            "old_sent": src.get("sent"), "sent": out["sent"],
            "cand_codes": cand_codes, "sent_codes": sent_codes,
            "old_codes": src.get("sent_codes") or [],
            "guard_error": out.get("guard_error") or "",
        })
        prev = snap
    return out_rows


async def main():
    all_rows = []
    for gid in picks:
        all_rows.extend(await replay_group(gid))
    introduced = [r for r in all_rows
                  if set(r["sent_codes"]) - set(r["cand_codes"])]
    failed = [r for r in all_rows if r["sent_codes"]]
    changed = [r for r in all_rows if (r["sent"] or "") != (r["old_sent"] or "")]
    new_fail = [r for r in all_rows
                if r["sent_codes"] and not r["old_codes"]]
    fixed = [r for r in all_rows
             if not r["sent_codes"] and r["old_codes"]]
    summary = {
        "turns": len(all_rows),
        "guard_introduced": len(introduced),
        "failed_turns": len(failed),
        "turn_pass_rate": round(1 - len(failed) / len(all_rows), 4) if all_rows else 0,
        "changed_vs_old_report": len(changed),
        "new_failures_vs_old": len(new_fail),
        "fixed_vs_old": len(fixed),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("\n--- guard-introduced ---")
    for row in introduced:
        print(row["gid"], row["turn"], row["probe"], row["sent_codes"],
              "| user:", row["user"], "| sent:", row["sent"])
    print("\n--- sent changed vs old report ---")
    for row in changed:
        print(row["gid"], row["turn"], row["probe"], "| user:", row["user"])
        print("   old:", row["old_sent"], "| codes:", row["old_codes"])
        print("   new:", row["sent"], "| codes:", row["sent_codes"])
    print("\n--- new failures ---")
    for row in new_fail:
        print(row["gid"], row["turn"], row["probe"], row["sent_codes"],
              "| user:", row["user"], "| sent:", row["sent"])
    print("\n--- fixed ---")
    for row in fixed:
        print(row["gid"], row["turn"], row["probe"], "| user:", row["user"],
              "| old codes:", row["old_codes"])
    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "rows": all_rows}, fh,
                  ensure_ascii=False, indent=1)
    print("\nwrote", OUT)


asyncio.run(main())
