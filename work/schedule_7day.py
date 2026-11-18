# -*- coding: utf-8 -*-
"""seed=7 的 7 天日程 golden：确定性生成入口（测试与 work 脚本共用）。

复用 work/simulate_weeks 的确定性入口（make_plugin + build_day_skeleton），
并把时钟冻结到每天 00:00；不读真实 plugin_data、不写库，全部对象在临时目录里。

为什么允许更新 tests/golden/schedule_7day_seed7.json：
    只有「日程主干生成规则」被有意改动时才允许重新生成，例如新增/删除活动模板、
    改时段切分、改 zone/place 映射、调整事件/节假日概率。重新生成后必须人工看
    一遍 diff，确认变化只来自这次规则改动，并在改动说明里写清原因。随手执行
    python work/schedule_7day.py --write 刷新快照而没有规则改动 = 测试失效。

用法：
    python work/schedule_7day.py            # 打印当前 payload，不落盘
    python work/schedule_7day.py --write    # 重新生成 golden（需在说明里写原因）
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

_WORK = os.path.dirname(os.path.abspath(__file__))
if _WORK not in sys.path:
    sys.path.insert(0, _WORK)

import sim_lib  # noqa: E402

ROOT = sim_lib.ROOT
sim_lib.install_stub()

import work.simulate_weeks as weeks  # noqa: E402

GOLDEN_PATH = os.path.join(ROOT, "tests", "golden", "schedule_7day_seed7.json")
SEED = 7
CITY = weeks.DEFAULT_CITY
DAYS = 7
FIELDS = ("start", "end", "activity", "place", "category", "zone")

UPDATE_POLICY = (
    "只有日程主干生成规则被有意改动时才允许更新本文件（新增/删除活动模板、"
    "改时段切分、改 zone/place 映射、调整事件概率）。更新方式：python "
    "work/schedule_7day.py --write；更新前必须人工看 diff，确认变化只来自这次"
    "规则改动，并在 CHANGELOG/PR 说明里写原因。禁止为了「让测试变绿」随手刷新。",
)


def _day_list(days=DAYS):
    return [(weeks.START_DAY + dt.timedelta(days=index)).isoformat()
            for index in range(days)]


def build_days(city=CITY, days=DAYS):
    """按 simulate_weeks.run() 的顺序生成 N 天主干（冻结时钟到每天 00:00）。"""
    plugin, state = weeks.make_plugin(weeks.START_DAY.isoformat(), city)
    out = {}
    for index, day in enumerate(_day_list(days)):
        kind = weeks.day_kind_for(index, day)
        weeks.sim24.set_clock(weeks.at(day, 0, 0))
        _ctx, items = weeks.build_day_skeleton(plugin, state, day, kind)
        out[day] = {"kind": kind, "blocks": [
            [str(row.get(field) or "") for field in FIELDS] for row in items]}
    return out


def block_hash(blocks):
    """块清单的稳定哈希：sha256(紧凑 JSON)，用来在一行 diff 里看出哪天变了。"""
    raw = json.dumps(blocks, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def payload(days=None):
    days = days if days is not None else build_days()
    out = {}
    for day, item in days.items():
        out[day] = {"kind": item["kind"], "block_count": len(item["blocks"]),
                    "sha256": block_hash(item["blocks"]), "blocks": item["blocks"]}
    return {
        "schema": "schedule_7day_golden_v1",
        "seed": SEED,
        "city": CITY,
        "start_day": weeks.START_DAY.isoformat(),
        "clock": "frozen_at_day_start_00:00",
        "generator": "work.schedule_7day.payload (work.simulate_weeks.build_day_skeleton)",
        "_update_policy": list(UPDATE_POLICY),
        "days": out,
    }


def dumps(data):
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def restore_clock():
    """把假时钟（含 time.time）还原；测试 tearDownClass 必须调用。"""
    weeks.sim24.restore_clock()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    text = dumps(payload())
    if "--write" not in args:
        sys.stdout.write(text)
        return 0
    os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
    with open(GOLDEN_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print("WROTE %s bytes=%d" % (GOLDEN_PATH, len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
