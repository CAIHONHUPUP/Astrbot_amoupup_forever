# -*- coding: utf-8 -*-
"""真实模型复跑（隔离 provider，拿不到就明确 blocked）。

规则（写死在代码里）：
- 只用环境变量或本机 ollama 拿 provider，**绝不读 AstrBot 的 cmd_config.json、绝不碰生产库**；
- 有 provider 时：失败组 + 10% 通过组，用同一份日程/同一套守卫链路复跑；
- 拿不到就写 real_llm_layer.json（status=blocked + 证据），报告里注明「真实模型语义未覆盖」。

用法：
    set AMO_SIM_LLM_BASE=https://api.deepseek.com/v1
    set AMO_SIM_LLM_KEY=sk-...
    set AMO_SIM_LLM_MODEL=deepseek-chat
    python work/sim/real_probe.py
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import sim_env as E  # noqa: E402
import sim_worlds as W  # noqa: E402
import daily_checks as C  # noqa: E402

REPORT_JSON = os.path.join(HERE, "daily_report.json")
OUT = os.path.join(HERE, "real_llm_layer.json")
PERSONA = ("你是大肥鱼，25 岁女生，在东莞上班，和老公同城不同住。用微信口吻回一句，"
           "只说自己的感受和事实，不要提 AI/模型/提示词/系统；事实必须符合下面状态。")
GROUP_PLAN = {"picks": []}


def env_provider():
    base = str(os.environ.get("AMO_SIM_LLM_BASE") or "").strip()
    key = str(os.environ.get("AMO_SIM_LLM_KEY") or "").strip()
    model = str(os.environ.get("AMO_SIM_LLM_MODEL") or "").strip()
    if base and key and model:
        return {"kind": "env", "base": base.rstrip("/"), "key": key, "model": model}
    return None


def ollama_provider():
    """本机 ollama 有对话模型时可用（embedding 模型不算）。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:  # cleanup: 仿真容错
        return None, []
    models = data.get("models") or []
    chat = [str(item.get("name")) for item in models
            if "embedding" not in (item.get("capabilities") or [])]
    if chat:
        return {"kind": "ollama", "base": "http://127.0.0.1:11434/v1",
                "key": "ollama", "model": chat[0]}, chat
    return None, [str(item.get("name")) for item in models]


def call_chat(provider, system, user, timeout=40):
    payload = json.dumps({"model": provider["model"],
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": user}],
                          "temperature": 0.8, "max_tokens": 160}).encode("utf-8")
    request = urllib.request.Request(
        provider["base"] + "/chat/completions", data=payload,
        headers={"Authorization": "Bearer " + provider["key"],
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data["choices"][0]["message"]["content"] or "").strip()


def pick_groups():
    try:
        report = json.load(open(REPORT_JSON, encoding="utf-8"))
    except Exception:  # cleanup: 仿真容错
        return {"failed": [], "passed_sample": [], "note": "daily_report.json 不存在，先跑 run_daily.py"}
    groups = report.get("groups") or []
    failed = [item["gid"] for item in groups if not item.get("passed")]
    passed = [item["gid"] for item in groups if item.get("passed")]
    sample = passed[:max(1, int(round(len(passed) * 0.1)))] if passed else []
    return {"failed": failed, "passed_sample": sample,
            "note": "失败组全部复跑 + 通过组 10% 抽样"}


def system_for(turn_row):
    snap = turn_row.get("snap") or {}
    return (PERSONA + "\n【当前状态·最高事实】现在%s，你在「%s」（zone=%s），在做：%s。"
            % (turn_row.get("time"), snap.get("place"), snap.get("zone"), snap.get("activity")))


async def replay_group(group, provider, rows_by_turn):
    plugin = E.build_plugin(group["blocks"], kv={}, health=group["health"])
    prev = None
    out_rows = []
    for turn in group["turns"]:
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
        reference = rows_by_turn.get((group["gid"], turn["index"]), {})
        system = (PERSONA + "\n【当前状态·最高事实】现在%s，你在「%s」（zone=%s），在做：%s。"
                  % (turn["time"], snap.get("place"), snap.get("zone"), snap.get("activity")))
        try:
            candidate = call_chat(provider, system, turn["user"])
        except Exception as exc:
            out_rows.append({"gid": group["gid"], "turn": turn["index"], "error": repr(exc)[:160]})
            prev = snap
            continue
        out = await E.run_turn(plugin, turn["user"], candidate, turn["time"])
        ctx = {"snap": snap, "prev_snap": prev, "probe": turn["probe"],
               "together": together, "meal_ok": meal_now}
        _ci, cand_codes = C.evaluate(candidate, ctx)
        _si, sent_codes = C.evaluate(out["sent"], ctx)
        out_rows.append({"gid": group["gid"], "turn": turn["index"], "time": turn["time"],
                         "probe": turn["probe"], "drift": turn.get("drift"),
                         "snap": {"place": snap.get("place"), "zone": snap.get("zone"),
                                  "activity": snap.get("activity")},
                         "candidate": candidate, "sent": out["sent"],
                         "cand_codes": cand_codes, "sent_codes": sent_codes,
                         "guard_error": out.get("guard_error") or "",
                         "mock_reference": reference.get("sent")})
        prev = snap
    return out_rows


async def main():
    plan = pick_groups()
    GROUP_PLAN.update(plan)
    provider = env_provider()
    chat_models = []
    if provider is None:
        provider, chat_models = ollama_provider()
    evidence = {
        "env_provider": bool(env_provider()),
        "ollama_chat_models": chat_models,
        "policy": "未读取 AstrBot cmd_config.json / 生产库；只接受环境变量或本机 ollama",
    }
    if provider is None:
        result = {"status": "blocked", "reason": "隔离环境里没有可用 provider（无 env key，本机 ollama 无对话模型）",
                  "evidence": evidence, "sampling_plan": plan,
                  "mock_layer_note": "真实模型语义质量未覆盖；模拟层结论见 daily_report.md"}
        json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("BLOCKED:", result["reason"])
        print("wrote", OUT)
        return
    try:
        report = json.load(open(REPORT_JSON, encoding="utf-8"))
    except Exception:  # cleanup: 仿真容错
        report = {"groups": [], "turns": []}
    rows_by_turn = {(row["gid"], row["turn"]): row for row in report.get("turns") or []}
    groups = {group["gid"]: group for group in W.build_groups(50)}
    picks = list(plan.get("failed") or []) + list(plan.get("passed_sample") or [])
    all_rows = []
    started = time.time()
    for gid in picks:
        group = groups.get(gid)
        if group is None:
            continue
        all_rows.extend(await replay_group(group, provider, rows_by_turn))
    failed = [row for row in all_rows if row.get("sent_codes")]
    result = {
        "status": "ok", "provider": {"kind": provider["kind"], "model": provider["model"],
                                     "base": provider["base"]},
        "groups": picks, "turns": len(all_rows), "failed_turns": len(failed),
        "turn_pass_rate": round(1 - len(failed) / len(all_rows), 4) if all_rows else 0.0,
        "codes": sorted({code for row in failed for code in row["sent_codes"]}),
        "elapsed_sec": round(time.time() - started, 1),
        "rows": all_rows,
        "note": "真实模型语义层：system prompt = 人设 + 官方 snapshot 行 + 日程；不含生产完整提示词",
    }
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({key: result[key] for key in ("status", "provider", "groups", "turns",
                                                    "failed_turns", "turn_pass_rate", "codes")},
                     ensure_ascii=False, indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    asyncio.run(main())
