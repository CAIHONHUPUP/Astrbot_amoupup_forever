#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日记质检 CLI（默认 dry-run）：历史回补 / 人工核对。

用法：
    python work/diary_audit.py --days 3
    python work/diary_audit.py --date 2026-11-15
    python work/diary_audit.py --days 3 --apply

dry-run 先把线上库快照到临时目录再审计，不写 plugin_data；--apply 才写回线上库
（只标 stale + 写报告 + 记幂等键，补写交给线上调度，CLI 不调模型、不发通知）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if os.path.join(ROOT, 'tests') not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, 'tests'))
try:
    import astrbot_stub  # noqa: E402

    astrbot_stub.install()
except Exception:  # cleanup: 线上 astrbot 环境就用真的
    pass

import amo_diary_audit as audit  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_shared import PLUGIN_NAME, _now, logger  # noqa: E402

DB_NAME = "amo_forever.db"


def _candidate_roots():
    """插件目录上一级是 plugins，再上一级就是 AstrBot 的 data 目录。"""
    return [os.path.dirname(os.path.dirname(ROOT)), ROOT]


def _live_db_path():
    """返回 (AstrBot data 目录, amo_forever.db)；找不到返回 ("", "")。"""
    for root in _candidate_roots():
        path = os.path.join(root, "plugin_data", PLUGIN_NAME, DB_NAME)
        if os.path.exists(path):
            return root, path
    fallback = os.path.join(ROOT, "data", DB_NAME)
    if os.path.exists(fallback):
        return ROOT, fallback
    return "", ""


def _install_data_root(data_root):
    """让 AmoDB 把指定目录当成 AstrBot data 目录（dry-run 指向临时快照）。"""
    def get_astrbot_data_path():
        return data_root

    parts = ("astrbot", "astrbot.core", "astrbot.core.utils")
    for name in parts:
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
    sys.modules["astrbot"].core = sys.modules["astrbot.core"]
    sys.modules["astrbot.core"].utils = sys.modules["astrbot.core.utils"]
    path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
    path_module.get_astrbot_data_path = get_astrbot_data_path
    sys.modules["astrbot.core.utils.astrbot_path"] = path_module
    sys.modules["astrbot.core.utils"].astrbot_path = path_module


def _snapshot(source, dest):
    """把线上库快照到 dest：优先 sqlite 只读 backup，失败退回复制文件。"""
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    except OSError as exc:
        logger.debug("[diary-audit] 建临时目录失败: %s", exc)
        return False
    uri = Path(source).as_uri() + "?mode=ro"
    try:
        src = sqlite3.connect(uri, uri=True, timeout=20)
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return True
    except sqlite3.Error as exc:
        logger.debug("[diary-audit] sqlite 快照失败，退回文件复制: %s", exc)
    try:
        shutil.copy2(source, dest)
        for suffix in ("-wal", "-shm"):
            extra = source + suffix
            if os.path.exists(extra):
                shutil.copy2(extra, dest + suffix)
        return True
    except OSError as exc:
        logger.debug("[diary-audit] 快照复制失败: %s", exc)
        return False


class _CliPlugin:
    """只实现质检需要的成员：配置 + db + 空 state（日程从库里读）。"""

    def __init__(self, db, config=None):
        self.db = db
        self.config = dict(config or {})
        self.state = types.SimpleNamespace(schedule={})

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    async def push_to_owner(self, text):
        # CLI 不发私聊；运行时 tick 才会推给主人。
        return False


def _parse_day(text):
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return ""


def _target_days(args, now):
    if args.date:
        day = _parse_day(args.date)
        if not day:
            raise SystemExit("日期格式应为 YYYY-MM-DD")
        return [day]
    count = max(1, int(args.days or 1))
    return [(now.date() - timedelta(days=offset)).isoformat()
            for offset in range(count, 0, -1)]


def _print_report(report):
    day = report.get("day") or "?"
    counts = report.get("counts") or {}
    tail = ""
    if report.get("applied"):
        tail = "，已标 stale %s 块，入队 %s 块" % (report.get("stale", 0),
                                                report.get("requeued", 0))
    print("[%s] 已结束块 %s，HARD %s，WARN %s%s" % (
        day, counts.get("blocks", 0), counts.get("hard", 0), counts.get("warn", 0), tail))
    for entry in report.get("hard") or []:
        print("  HARD %s-%s %s" % (entry.get("start"), entry.get("end"), entry.get("kind")))
    for entry in report.get("warn") or []:
        detail = str(entry.get("detail") or "")
        print("  WARN %s-%s %s%s" % (entry.get("start"), entry.get("end"),
                                     entry.get("kind"), (" " + detail) if detail else ""))


def main(argv=None):
    parser = argparse.ArgumentParser(description="日记质检（默认 dry-run）")
    parser.add_argument("--date", default="", help="只审这一天（YYYY-MM-DD）")
    parser.add_argument("--days", type=int, default=3, help="往前审最近 N 天（默认 3）")
    parser.add_argument("--apply", action="store_true", help="写回线上库（默认只读）")
    args = parser.parse_args(argv)
    now = _now()
    data_root, source = _live_db_path()
    if not source:
        print("[diary-audit] 找不到 amo_forever.db，先确认插件数据目录。")
        return 1
    temp_root = ""
    db = None
    try:
        if args.apply:
            _install_data_root(data_root)
            print("[diary-audit] apply=1 直接写线上库: %s" % source)
        else:
            temp_root = tempfile.mkdtemp(prefix="amo-diary-audit-")
            target = os.path.join(temp_root, "plugin_data", PLUGIN_NAME, DB_NAME)
            if not _snapshot(source, target):
                print("[diary-audit] 数据库快照失败，已中止（线上库保持不动）。")
                return 1
            _install_data_root(temp_root)
            print("[diary-audit] apply=0 只读快照，不写 plugin_data")
        db = AmoDB(ROOT)
        db.init()
        plugin = _CliPlugin(db)
        totals = {"days": 0, "hard": 0, "warn": 0}
        for day in _target_days(args, now):
            report = audit.run(plugin, day, apply=bool(args.apply), now=now)
            _print_report(report)
            totals["days"] += 1
            totals["hard"] += int((report.get("counts") or {}).get("hard") or 0)
            totals["warn"] += int((report.get("counts") or {}).get("warn") or 0)
        print("[summary] 审了 %d 天：HARD %d 条，WARN %d 条%s" % (
            totals["days"], totals["hard"], totals["warn"],
            "（已写回）" if args.apply else "（dry-run，未写库）"))
        return 0
    finally:
        if db is not None:
            try:
                db.close()
            except Exception as exc:  # 关连接失败不影响结果
                logger.debug("[diary-audit] 关闭数据库失败: %s", exc)
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
