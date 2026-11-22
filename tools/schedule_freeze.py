# -*- coding: utf-8 -*-
"""日程代码冻结检查（切流后生效）。

冻结期规则（用户 2026-11-22 决策）：
- 日程相关文件一律冻结；任何改动必须同时带「不变量测试」和「离线影子对比证据」，
  否则不合并；
- 冻结期内只允许修 8 项指标里的回归（非法块/空洞/重叠/路径断裂/旧社交人名残留/
  页面聊天不一致/并发丢更新/写异常）。

用法：
  python tools/schedule_freeze.py            # 检查（有漂移 exit 1）
  python tools/schedule_freeze.py --update   # 发布时写基线（仅发布负责人）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "work", "audit", "schedule_freeze_baseline.json")
FILES = (
    "schedule_engine_v2.py",
    "schedule_observer.py",
    "schedule_social_a1.py",
    "amo_state_schedule.py",
    "amo_state_todo.py",
    "amo_state_jit.py",
    "amo_transition.py",
    "amo_timeline.py",
    "schedule_skeleton.py",
    "schedule_plan.py",
    "schedule_slots.py",
    "schedule_diary.py",
    "schedule_change.py",
    "amo_api_schedule.py",
    "domains/schedule/place.py",
    "domains/schedule/social_segments.py",
    "domains/schedule/jit_plan.py",
    "domains/schedule/day_types.py",
    "tools/schedule_shadow.py",
    "tools/migrate_social.py",
)


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def snapshot():
    rows = {}
    for name in FILES:
        path = os.path.join(ROOT, name)
        rows[name] = _digest(path) if os.path.exists(path) else ""
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="日程代码冻结检查")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args(argv)
    current = snapshot()
    if args.update:
        payload = {"frozen_at": "2026-11-22", "files": current,
                   "rule": "冻结期改动必须带不变量测试 + 离线影子对比证据；只修 8 项指标回归"}
        with open(BASELINE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        print("baseline updated:", BASELINE)
        return 0
    try:
        with open(BASELINE, encoding="utf-8") as handle:
            baseline = json.load(handle).get("files") or {}
    except (OSError, ValueError):
        print("FROZEN: 没有基线，请先 --update")
        return 2
    changed = [name for name, digest in current.items()
               if baseline.get(name) != digest]
    if changed:
        print("FROZEN VIOLATION: %d 个日程文件被改动" % len(changed))
        for name in changed:
            print("  -", name)
        print("规则：改动必须同时提交不变量测试 + 离线影子对比证据；冻结期只修 8 项指标回归。")
        return 1
    print("FROZEN OK:", len(current), "个日程文件与基线一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
