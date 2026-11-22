# -*- coding: utf-8 -*-
"""模拟 N 天日程并严格审计（2026-11-21 用户要求）。

检查项：
- 重叠 / 空档 / <5 分钟短块；
- 路径断裂（path_break 指标：跨地点但借不出最短路程）；
- 活动文案与 zone 冲突（在公司却写在家、在家却写工位）；
- 相邻同内容块（整点碎片，重构目标）。

用法：
    python work/audit_schedule_sim.py            # 默认 14 天
    python work/audit_schedule_sim.py 30         # 30 天
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "work")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sim_lib  # noqa: E402

sim_lib.install_stub()

import work.schedule_7day as s7  # noqa: E402
import schedule_engine_v2 as _engine  # noqa: E402

FIELDS = s7.FIELDS
HOME_WORDS = ("家", "客厅", "沙发", "卧室", "被窝", "厨房", "床")
OFFICE_WORDS = ("公司", "工位", "办公室", "会议室", "写字楼")


def _m(text):
    try:
        hour, minute = str(text)[:5].split(":")
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def audit_day(blocks):
    rows = [dict(zip(FIELDS, block)) for block in blocks]
    rows.sort(key=lambda row: _m(row["start"]) if _m(row["start"]) is not None else 9999)
    overlap = gap = short = same = 0
    for prev, row in zip(rows, rows[1:]):
        prev_end, start = _m(prev["end"]), _m(row["start"])
        if prev_end is None or start is None:
            continue
        if start < prev_end:
            overlap += 1
        elif start > prev_end:
            gap += 1
        if (prev_end == start and prev["activity"] == row["activity"]
                and prev["place"] == row["place"]):
            same += 1
    for row in rows:
        start, end = _m(row["start"]), _m(row["end"])
        if start is not None and end is not None and 0 < end - start < 5:
            short += 1
    # 阶段 0：旧 place_continuity_problems 已删，改用 8 项指标里的 path_break。
    path_break = _engine.count_defects(_engine.validate(rows))[_engine.METRIC_PATH_BREAK]
    jumps = ["path_break"] * path_break
    zone_bad = []
    for row in rows:
        activity = str(row.get("activity") or "")
        zone = str(row.get("zone") or "")
        if any(word in activity for word in OFFICE_WORDS) and zone.startswith("home") \
                and "梦" not in activity:
            zone_bad.append((row["start"], "公司文案/在家", activity[:24]))
        if any(word in activity for word in HOME_WORDS) and zone == "office":
            zone_bad.append((row["start"], "在家文案/在公司", activity[:24]))
    return {"blocks": len(rows), "overlap": overlap, "gap": gap, "short": short,
            "jumps": jumps, "zone_bad": zone_bad, "same": same}


def main(argv):
    block_mode = "--blocks" in argv
    argv = [item for item in argv if item != "--blocks"]
    days = int(argv[0]) if argv else 14
    if block_mode:
        # 强制打开实验开关：验证「一块 = 一件事」时碎片是否归零
        import work.simulate_weeks as weeks
        original = weeks.make_plugin

        def _patched(*args, **kwargs):
            plugin, state = original(*args, **kwargs)
            real = plugin.cfg_bool

            def _cfg(key, default=False):
                if key == "schedule_block_mode":
                    return True
                return real(key, default)

            plugin.cfg_bool = _cfg
            return plugin, state

        weeks.make_plugin = _patched
    payload = s7.build_days(days=days)
    totals = {"days": 0, "blocks": 0, "overlap": 0, "gap": 0, "short": 0,
              "jumps": 0, "zone_bad": 0, "same": 0}
    lines = []
    for day in sorted(payload):
        result = audit_day(payload[day]["blocks"])
        totals["days"] += 1
        for key in ("blocks", "overlap", "gap", "short", "same"):
            totals[key] += result[key]
        totals["jumps"] += len(result["jumps"])
        totals["zone_bad"] += len(result["zone_bad"])
        if (result["overlap"] or result["gap"] or result["short"]
                or result["jumps"] or result["zone_bad"]):
            lines.append("%s %s: overlap=%d gap=%d short=%d jumps=%d zone_bad=%d same=%d"
                         % (day, payload[day]["kind"], result["overlap"], result["gap"],
                            result["short"], len(result["jumps"]),
                            len(result["zone_bad"]), result["same"]))
            for jump in result["jumps"][:2]:
                lines.append("   J " + str(jump))
            for bad in result["zone_bad"][:2]:
                lines.append("   Z " + str(bad))
    sys.stdout.write(str(totals) + "\n")
    if lines:
        sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
