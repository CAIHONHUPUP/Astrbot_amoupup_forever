# -*- coding: utf-8 -*-
"""真实 LLM 补充验证 10 条（不计入 100 硬指标）：真实模型生成候选，再过完整链路。"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, os.path.join(ROOT, "tests"), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import imm_harness as H  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
import run_imm as IMM  # noqa: E402

DATA = os.environ.get("AMO_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(ROOT)), "data")
CONFIG = os.path.join(DATA, "cmd_config.json")
OUT = os.path.join(HERE, "real_llm_results.json")
BAD = re.compile(r"AI|人工智能|语言模型|提示词|系统|指令|模型|助手|token|JSON")


def find_provider():
    data = json.load(open(CONFIG, encoding="utf-8-sig"))
    for source in data.get("provider_sources") or []:
        keys = source.get("key") or []
        if isinstance(keys, str):
            keys = [keys]
        keys = [item for item in keys if str(item).startswith("sk-")]
        if keys:
            model = "deepseek-chat"
            for provider in data.get("provider") or []:
                if provider.get("provider_source_id") == source.get("id") and provider.get("model"):
                    model = str(provider["model"])
                    break
            return {"key": keys[0],
                    "base": str(source.get("api_base") or "https://api.deepseek.com/v1").rstrip("/"),
                    "model": model}
    found = []
    def walk(node):
        if isinstance(node, dict):
            key = str(node.get("api_key") or node.get("key") or "")
            base = str(node.get("api_base") or node.get("base_url") or "")
            if key.startswith("sk-") and base:
                found.append({"key": key, "base": base.rstrip("/"),
                              "model": str(node.get("model") or node.get("model_config", {}).get("model") or "deepseek-chat")})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(data)
    deepseek = [p for p in found if "deepseek" in p["base"] or "deepseek" in p["model"]]
    return (deepseek or found)[0] if found else None


def call(provider, system, user, timeout=40):
    payload = json.dumps({"model": provider["model"],
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": user}],
                          "temperature": 0.8, "max_tokens": 120}).encode("utf-8")
    request = urllib.request.Request(
        provider["base"] + "/chat/completions", data=payload,
        headers={"Authorization": "Bearer " + provider["key"],
                 "Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    text = str(data["choices"][0]["message"]["content"] or "").strip()
    return text, (time.perf_counter() - started) * 1000.0


def world_line(item):
    world = item["world"]
    block = (world.get("blocks") or [{}])[0]
    health = world.get("health") or {}
    parts = ["地点=%s" % block.get("place", "未知"),
             "在做=%s" % block.get("activity", "不知道"),
             "身体=%s" % health.get("status", "健康"),
             "现在=%s" % (item["turns"][0].get("now") or item["day"])]
    return "；".join(parts)


def main():
    provider = find_provider()
    if not provider:
        json.dump({"status": "blocked", "reason": "找不到可用的 provider api_key"},
                  open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("BLOCKED no provider")
        return
    dataset = IMM.load_dataset()
    picks = []
    for cat in ("诱导说错地点", "系统腔诱导", "元问题", "用户纠正", "连发追问"):
        picks.extend([item for item in dataset["items"] if item["category"] == cat][:2])
    picks = picks[:10]
    samples = []
    for item in picks:
        turn = item["turns"][0]
        system = ("你是大肥鱼，25 岁女生，在东莞上班，和老公同城不同住。用微信口吻回一句，"
                  "只说自己的感受和事实，不要提 AI/模型/提示词/系统。事实必须符合：%s" % world_line(item))
        try:
            text, ms = call(provider, system, turn["user"])
            if not text.strip():
                text, ms2 = call(provider, system, turn["user"])
                ms += ms2
            if not text.strip():
                raise RuntimeError("empty content from provider")
        except Exception as exc:
            samples.append({"id": item["id"], "category": item["category"],
                            "error": repr(exc)[:200]})
            continue
        plugin = H.build_plugin(item["world"], llm=H.MockLLM(mode="rotate"))
        now = IMM._now_of(turn, item)
        run = __import__("asyncio").run(H.run_turn(plugin, turn["user"], text, now,
                                                  "real:" + item["id"], b_mode=False))
        issues = IMM.evaluate_item({"id": item["id"], "category": item["category"],
                                    "turns": [dict(run, question=turn.get("question", False),
                                                   candidate_source="synthetic")]},
                                   {"turns": [dict(run, question=turn.get("question", False),
                                                   candidate_source="synthetic")]})
        samples.append({"id": item["id"], "category": item["category"],
                        "user": turn["user"], "raw": text[:200],
                        "sent": run["sent"][:200], "latency_ms": round(ms, 1),
                        "fact_issues": sum(1 for i in issues if i["type"] == "事实矛盾"),
                        "leak_issues": sum(1 for i in issues if i["type"] == "系统腔/元信息"),
                        "issues": issues[:4]})
    ok = [s for s in samples if not s.get("error")]
    bad = [s for s in ok if s.get("fact_issues") or s.get("leak_issues")]
    result = {"status": "done", "provider": provider["base"], "model": provider["model"],
              "samples": samples, "n": len(samples), "ok": len(ok), "bad": len(bad),
              "avg_latency_ms": round(sum(s.get("latency_ms", 0) for s in ok) / max(1, len(ok)), 1),
              "verdict": ("真实生成候选过链路：事实/出戏问题 %d 条（补充项，不计入 100 硬指标）" % len(bad))}
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: result[k] for k in ("status", "model", "n", "ok", "bad", "avg_latency_ms", "verdict")},
                     ensure_ascii=False, indent=1))
    for sample in samples:
        if sample.get("error"):
            print(sample["id"], "ERROR", sample["error"][:120])
        else:
            print(sample["id"], "| raw:", sample["raw"][:60], "| sent:", sample["sent"][:60],
                  "| fact", sample["fact_issues"], "leak", sample["leak_issues"])


if __name__ == "__main__":
    main()
