# -*- coding: utf-8 -*-
"""日程影子对比 / 8 项指标证据工具（只读，不改生产库）。

用法：
  python tools/schedule_shadow.py real --db <amo_forever.db> --days 7
  python tools/schedule_shadow.py sim --seeds 7 11 42 --days 3

real：逐日对比「旧引擎落库行」与「v2 安全视图」，并扫事故文案（刚出X…回家）。
sim：用 work/sim 隔离环境生成 3 天新引擎日程，逐日跑 8 项指标（切流门禁口径）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "work"),
              os.path.join(ROOT, "work", "sim")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import schedule_engine_v2 as engine  # noqa: E402
import schedule_observer as observer  # noqa: E402


def _load_days(db_path, days):
    conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT day, items FROM schedule ORDER BY day DESC LIMIT ?",
            (int(days),)).fetchall()
    finally:
        conn.close()
    out = []
    for day, items in rows:
        try:
            parsed = json.loads(items)
        except (TypeError, ValueError):
            parsed = []
        out.append((str(day), parsed if isinstance(parsed, list) else []))
    return out


def _incident_rows(rows):
    """旧数据里「刚出X…回家」但前一块就在家的自相矛盾行。"""
    hits = []
    ordered = sorted([row for row in rows or [] if isinstance(row, dict)],
                     key=lambda row: engine.span_of(row) or (0, 0))
    for index, row in enumerate(ordered):
        activity = str(row.get("activity") or "")
        if "刚出" not in activity or "回家" not in activity:
            continue
        if index == 0:
            continue
        prev = ordered[index - 1]
        if engine._is_home(prev):
            hits.append({"start": row.get("start"), "activity": activity,
                         "prev_place": prev.get("place")})
    return hits


def cmd_real(args):
    days = _load_days(args.db, args.days)
    report = {"db": args.db, "days": [], "totals": engine.empty_counts()}
    for day, rows in days:
        view, view_defects = observer.view_rows(rows, day)
        inspect = observer.inspect_day(None, day, rows, record=False)
        counts = engine.merge_counts(engine.count_defects(view_defects), inspect["counts"])
        diff = engine.shadow_diff(rows, view, limit=40)
        incidents = _incident_rows(rows)
        entry = {
            "day": day, "stored_rows": len(rows), "view_rows": len(view),
            "counts": counts,
            "incidents_before": incidents,
            "incidents_after": _incident_rows(view),
            "diff_count": sum(1 for line in diff["old_lines"]
                              if line not in diff["new_lines"]),
            "old_lines": diff["old_lines"], "new_lines": diff["new_lines"],
        }
        report["days"].append(entry)
        report["totals"] = engine.merge_counts(report["totals"], counts)
    report["zero"] = engine.zero(report["totals"])
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0 if report["zero"] else 1


def cmd_sim(args):
    import schedule_sim as sim  # noqa: E402

    report = {"seeds": args.seeds, "days": [], "totals": engine.empty_counts()}
    for seed in args.seeds:
        plugin, state = sim.make_plugin(int(seed), args.profile)
        mood = sim.initial_mood(int(seed))
        day = sim.START_DAY if hasattr(sim, "START_DAY") else "2026-11-21"
        for offset in range(int(args.days)):
            current = sim.dt.date.fromisoformat(day) + sim.dt.timedelta(days=offset)
            current_text = current.isoformat()
            sim.generate_day(plugin, state, current_text, int(seed), args.profile, mood)
            inspect = observer.inspect_day(state.db, current_text, record=True)
            report["days"].append({"seed": int(seed), "day": current_text,
                                   "rows": len(state.schedule.get(current_text) or []),
                                   "counts": inspect["counts"],
                                   "defects": [dict(item) for item in inspect["defects"]][:6]})
            report["totals"] = engine.merge_counts(report["totals"], inspect["counts"])
            mood = sim.evolve_mood(mood, state.schedule.get(current_text) or [], seed,
                                   current_text)
    report["zero"] = engine.zero(report["totals"])
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0 if report["zero"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="日程影子对比 / 8 项指标")
    sub = parser.add_subparsers(dest="mode", required=True)
    real = sub.add_parser("real")
    real.add_argument("--db", required=True)
    real.add_argument("--days", type=int, default=7)
    real.add_argument("--out", default="")
    sim_parser = sub.add_parser("sim")
    sim_parser.add_argument("--seeds", nargs="+", type=int, default=[7, 11, 42])
    sim_parser.add_argument("--days", type=int, default=3)
    sim_parser.add_argument("--profile", default="event_rich")
    sim_parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    return cmd_real(args) if args.mode == "real" else cmd_sim(args)


if __name__ == "__main__":
    raise SystemExit(main())
