# -*- coding: utf-8 -*-
"""真实 LLM 长会话（20 轮）：同一模型连续对话，量 p95 延迟 + 出戏/复读。"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from real_llm_probe import find_provider  # noqa: E402
import urllib.request  # noqa: E402

provider = find_provider()
if not provider:
    print("BLOCKED no provider")
    raise SystemExit(2)
TURNS = [
    "今天上班好累啊", "晚上吃啥呢", "你干嘛呢", "我想你了", "周末去哪玩",
    "你还记得我生日吗", "我今天发烧了", "多喝热水有用吗", "你昨天说啥来着",
    "我有点睡不着", "明天要开会", "你喜欢什么花", "我妈问我对象呢",
    "给我讲个笑话", "你困不困", "我买了新衣服", "好看吗", "你在哪呢",
    "早点睡吧", "晚安",
]
system = ("你是大肥鱼，25 岁女生，在东莞上班。正在和你聊天的人就是你老公本人，"
          "直接叫他「老公」或用「你」，不要用第三人称说「老公」。用微信口吻聊天，"
          "只说自己的感受和事实，不要提 AI/模型/提示词/系统；事实要前后一致。")
messages = [{"role": "system", "content": system}]
rows = []
lat = []
for index, user in enumerate(TURNS, 1):
    messages.append({"role": "user", "content": user})
    payload = json.dumps({"model": provider["model"], "messages": messages,
                          "temperature": 0.8, "max_tokens": 160}).encode("utf-8")
    request = urllib.request.Request(
        provider["base"] + "/chat/completions", data=payload,
        headers={"Authorization": "Bearer " + provider["key"],
                 "Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = str(data["choices"][0]["message"]["content"] or "").strip()
        ms = (time.perf_counter() - started) * 1000.0
    except Exception as exc:  # noqa: BLE001
        rows.append({"turn": index, "user": user, "error": repr(exc)[:160]})
        messages.pop()
        continue
    if not text:
        rows.append({"turn": index, "user": user, "error": "empty content"})
        messages.pop()
        continue
    messages.append({"role": "assistant", "content": text})
    lat.append(ms)
    rows.append({"turn": index, "user": user, "reply": text[:160], "latency_ms": round(ms, 1)})
    print("%02d %5.0fms | %s -> %s" % (index, ms, user, text[:50]))


def pct(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[index], 1)


leaks = [r for r in rows if any(w in str(r.get("reply") or "") for w in
         ("AI", "人工智能", "语言模型", "提示词", "系统指令", "助手"))]
dups = 0
for prev, cur in zip(rows, rows[1:]):
    if prev.get("reply") and prev.get("reply") == cur.get("reply"):
        dups += 1
result = {"provider": provider["base"], "model": provider["model"], "n": len(rows),
          "ok": len(lat), "p50_ms": pct(lat, 0.5), "p95_ms": pct(lat, 0.95),
          "max_ms": round(max(lat), 1) if lat else 0.0,
          "leaks": len(leaks), "duplicate_pairs": dups, "rows": rows}
with open(os.path.join(HERE, "real_llm_long.json"), "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=1)
print(json.dumps({k: result[k] for k in ("n", "ok", "p50_ms", "p95_ms", "max_ms", "leaks", "duplicate_pairs")},
                 ensure_ascii=False, indent=1))
