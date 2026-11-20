# -*- coding: utf-8 -*-
"""口头禅 A/B：30 条多轮回复，统计「逐字复读率 / 连续同开头率 / 情绪句保留率」。

同一批模型原始输出，A = 不跑守卫（现状），B = 跑 tone_guard（改后）。
只读 + 打印，不写 plugin_data、不碰数据库。

用法：python work/tone_ab.py [--json]
"""
from __future__ import annotations

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tone_guard as tg  # noqa: E402

TURNS = 30
CATCHPHRASES = ("我跟你说", "说真的", "你懂吧", "好家伙", "绝了", "其实吧")
EMOTION_BODIES = ("我好难过啊", "今天真的好累", "心里有点委屈", "我挺开心的")
FACT_BODIES = ("3 点在公司开会", "下午要去医院复查", "昨天买了双鞋")
NEUTRAL_BODIES = ("外面风挺大", "刚看完了那部剧", "楼下桂花开了")


def build_turns():
    """造 30 条多轮原始输出：口头禅连续三条一组，三分之一的句子是情绪句。"""
    rng = random.Random(20261117)
    turns = []
    for index in range(TURNS):
        catchphrase = CATCHPHRASES[(index // 3) % len(CATCHPHRASES)]
        rng.random()  # 固定随机序列，保证每次跑同一批数据
        if index % 3 == 0:
            kind, body = "emotion", EMOTION_BODIES[(index // 3) % len(EMOTION_BODIES)]
        elif index % 3 == 1:
            kind, body = "fact", FACT_BODIES[(index // 3) % len(FACT_BODIES)]
        else:
            kind, body = "neutral", NEUTRAL_BODIES[(index // 3) % len(NEUTRAL_BODIES)]
        turns.append({"turn": index + 1, "kind": kind, "catchphrase": catchphrase,
                      "text": "%s，%s。" % (catchphrase, body), "body": body})
    return turns


def _starts_with_catchphrase(text):
    body = str(text or "").lstrip()
    return any(body.startswith(item) for item in CATCHPHRASES)


def _opening(text):
    return tg._opening_of(text)[0]


def _distinct_openings(finals):
    return len({_opening(text) for text in finals})


def measure(finals, turns):
    total = len(finals)
    verbatim = sum(1 for text in finals if _starts_with_catchphrase(text))
    consecutive = 0
    for index in range(1, total):
        left = str(finals[index - 1] or "").strip()[:3]
        right = str(finals[index] or "").strip()[:3]
        if left and left == right:
            consecutive += 1
    compared = max(0, total - 1)
    emotion_total = sum(1 for turn in turns if turn["kind"] == "emotion")
    emotion_kept = sum(1 for turn, text in zip(turns, finals)
                       if turn["kind"] == "emotion" and turn["body"] in text)
    return {
        "verbatim_rate": round(verbatim / total, 4) if total else 0.0,
        "consecutive_opening_rate": round(consecutive / compared, 4) if compared else 0.0,
        "emotion_kept_rate": round(emotion_kept / emotion_total, 4) if emotion_total else 0.0,
        "verbatim_count": verbatim,
        "consecutive_count": consecutive,
        "emotion_kept": emotion_kept,
        "emotion_total": emotion_total,
    }


def build_report():
    turns = build_turns()
    tg.reset()
    baseline = [turn["text"] for turn in turns]
    tg.reset("ab")
    guarded = []
    for turn in turns:
        fixed, _actions = tg.guard(turn["text"], umo="ab")
        guarded.append(fixed)
    before = measure(baseline, turns)
    after = measure(guarded, turns)
    return {
        "turns": len(turns),
        "catchphrases": list(CATCHPHRASES),
        "before": before,
        "after": after,
        "delta": {key: round(after[key] - before[key], 4) for key in
                  ("verbatim_rate", "consecutive_opening_rate", "emotion_kept_rate")},
        "distinct_openings": {"before": _distinct_openings(baseline),
                              "after": _distinct_openings(guarded)},
        "samples": [{"turn": turns[i]["turn"], "before": baseline[i], "after": guarded[i]}
                    for i in (0, 1, 2, 3, 5, 13, 14, 18, 19, 28) if i < len(turns)],
    }


def render(report):
    before, after = report["before"], report["after"]
    rows = (
        ("逐字复读率（以人设口头禅原句开头）", before["verbatim_rate"], after["verbatim_rate"]),
        ("连续同开头率（与前一条前 3 字相同）", before["consecutive_opening_rate"],
         after["consecutive_opening_rate"]),
        ("情绪句保留率", before["emotion_kept_rate"], after["emotion_kept_rate"]),
    )
    lines = ["口头禅 A/B：%d 条多轮回复（A=无守卫 / B=有 tone_guard）" % report["turns"], ""]
    for label, old, new in rows:
        lines.append("%-36s A=%5.1f%%  B=%5.1f%%  delta=%+5.1f%%"
                     % (label, old * 100, new * 100, (new - old) * 100))
    lines.append("")
    lines.append("不同开头种类：A=%d / B=%d"
                 % (report["distinct_openings"]["before"], report["distinct_openings"]["after"]))
    for sample in report["samples"]:
        lines.append("第 %2d 条  A: %s  ->  B: %s"
                     % (sample["turn"], sample["before"], sample["after"]))
    return lines


def main(argv):
    report = build_report()
    if "--json" in argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for line in render(report):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
