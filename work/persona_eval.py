# -*- coding: utf-8 -*-
"""人设评估集：生成盲测表（不调模型，供人工/A-B 用）。"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_persona_pack as pack  # noqa: E402

CASES = os.path.join(ROOT, "persona_eval", "cases.json")
OUT = os.path.join(ROOT, "persona_eval", "report.md")


def main():
    cases = json.load(open(CASES, encoding="utf-8"))
    data = pack.default_pack()
    lines = ["# 人设 A/B 评估表", "", "每个场景生成 2 版，盲选更像她的一版。", ""]
    for case in cases:
        block = pack.prompt_block(data, scene=str(case.get("scene") or ""),
                                  mood=str(case.get("mood") or ""))
        lines += ["## %s" % case.get("id"), "",
                  "- 场景：%s / %s" % (case.get("scene"), case.get("mood")),
                  "- 输入：%s" % case.get("input"),
                  "- 必须：%s" % case.get("must"),
                  "- 人设块：", "~~~", block, "~~~", ""]
    with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    print("WROTE", OUT, len(cases))


if __name__ == "__main__":
    main()
