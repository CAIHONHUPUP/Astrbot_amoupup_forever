# -*- coding: utf-8 -*-
"""保留策略（Phase 7）：表驱动 + dry-run 计划。

做两件事
- plan(now)：只统计将删行数，一条都不删（dry-run）；
- apply(now)：调现有 purge_*，删除口径与原调用点逐字一致（边界日保留）。

表登记：table / 时间列 / 默认天数 / 配置键或常量 / 现有 purge 函数。
只有 messages 有配置键（chat_log_days=14），其余用常量并写进 note。

为什么有些表只统计不删
- todo：purge_todos(before_day) 存在，但目前没有任何调用点；本阶段只统计，
  不接线（接线等于新增删除行为，属于产品决策，不是重构）；
- proactive_log / dialects / relation_network：本来就没有清理函数（task 说明里
  也点了这三张表），只给出候选行数，等 Phase 20 的显式运维入口；
- kv：并入报告用的是 amo_state_store.prune 的 dry-run 计划，只报告不删键。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import datetime as _dt

try:  # 包模式优先
    from .amo_shared import _now, logger, now_ts
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import _now, logger, now_ts  # type: ignore[no-redef]

#: 没有配置键的表用这些常量（单位：天）
WALLET_LEDGER_DAYS = 90
TODO_KEEP_DAYS = 14
SCHEDULE_KEEP_DAYS = 14
DIARY_KEEP_DAYS = 30
PROACTIVE_LOG_DAYS = 180
RELATION_NETWORK_DAYS = 365

#: 表登记：table / 时间列 / 天数口径 / 默认天数 / 配置键 / 现有 purge 函数
RETENTION_TABLE = (
    {
        "table": "messages", "time_column": "created_at", "kind": "epoch",
        "default_days": 14, "config_key": "chat_log_days",
        "purge": "purge_messages", "purge_arg": "days", "wired": True,
        "note": "聊天记录；跟着配置 chat_log_days（默认 14 天，每小时清一次）",
    },
    {
        "table": "wallet_ledger", "time_column": "ts", "kind": "epoch",
        "default_days": WALLET_LEDGER_DAYS, "config_key": "",
        "purge": "purge_wallet_ledger", "purge_arg": "days", "wired": True,
        "note": "钱包流水；常量 90 天（无配置键）",
    },
    {
        "table": "schedule", "time_column": "day", "kind": "day",
        "default_days": SCHEDULE_KEEP_DAYS, "config_key": "",
        "purge": "purge_schedules", "purge_arg": "days", "wired": True,
        "note": "日程；常量 14 天，和 purge_schedules(14) 一致",
    },
    {
        "table": "schedule_diary", "time_column": "day", "kind": "day",
        "default_days": DIARY_KEEP_DAYS, "config_key": "",
        "purge": "purge_diaries", "purge_arg": "days", "wired": True,
        "note": "整点小日记；常量 30 天，purge_diaries 连 block_diary 一起删",
    },
    {
        "table": "block_diary", "time_column": "day", "kind": "day",
        "default_days": DIARY_KEEP_DAYS, "config_key": "",
        "purge": "purge_diaries", "purge_arg": "days", "wired": True,
        "note": "块日记；与 schedule_diary 同一天数，由 purge_diaries 一并删",
    },
    {
        "table": "todo", "time_column": "day", "kind": "day",
        "default_days": TODO_KEEP_DAYS, "config_key": "",
        "purge": "purge_todos", "purge_arg": "before_day", "wired": False,
        "note": "待办；purge_todos 目前没有调用点，本阶段只统计不接线",
    },
    {
        "table": "proactive_log", "time_column": "created", "kind": "epoch",
        "default_days": PROACTIVE_LOG_DAYS, "config_key": "",
        "purge": "", "purge_arg": "", "wired": False,
        "note": "主动消息日志；没有 purge_* 函数，只统计（常量 180 天）",
    },
    {
        "table": "relation_network", "time_column": "updated_at", "kind": "epoch",
        "default_days": RELATION_NETWORK_DAYS, "config_key": "",
        "purge": "", "purge_arg": "", "wired": False,
        "note": "关系网；没有 purge_* 函数，只统计（常量 365 天）",
    },
    {
        "table": "dialects", "time_column": "", "kind": "none",
        "default_days": 0, "config_key": "",
        "purge": "", "purge_arg": "", "wired": False,
        "note": "方言词表；没有时间列，不参与保留（n/a）",
    },
)


def _epoch_now(moment):
    """把 now 归一成 epoch 秒（None 用插件时钟的"现在"）。"""
    if moment is None:
        return float(now_ts())
    if isinstance(moment, bool):
        return float(now_ts())
    if isinstance(moment, (int, float)):
        return float(moment)
    if isinstance(moment, _dt.datetime):
        return float(moment.timestamp())
    if isinstance(moment, _dt.date):
        return float(_dt.datetime.combine(moment, _dt.time()).timestamp())
    return float(now_ts())


def _day_of(moment):
    """把 now 归一成 date（日口径边界用）。"""
    if moment is None:
        return _now().date()
    if isinstance(moment, _dt.datetime):
        return moment.date()
    if isinstance(moment, _dt.date):
        return moment
    return _dt.datetime.fromtimestamp(_epoch_now(moment)).date()


def resolve_days(entry, cfg_getter=None, overrides=None):
    """算这张表的天数：overrides > 配置键 > 默认常量；返回 (days, 来源)。"""
    table = entry["table"]
    if overrides and table in overrides:
        return max(1, int(overrides[table])), "override"
    key = entry.get("config_key") or ""
    if key and callable(cfg_getter):
        try:
            return max(1, int(cfg_getter(key, entry["default_days"]))), "config:" + key
        except (TypeError, ValueError) as exc:
            logger.debug("[amo] 保留天数配置 %s 取值失败，回落默认: %r", key, exc)
    return max(1, int(entry["default_days"])), ("config:" + key if key else "const")


def _cutoff(entry, moment, day, days):
    """删除边界（严格小于）：epoch 表给时间戳，天表给 YYYY-MM-DD。"""
    if entry["kind"] == "epoch":
        return float(moment) - max(1, int(days)) * 86400
    return (day - _dt.timedelta(days=max(1, int(days)))).isoformat()


def _lock_of(db):
    return getattr(db, "lock", None)


def _count(db, entry, cutoff):
    """统计将删行数（只 SELECT COUNT，不删）。边界日保留。"""
    column = entry["time_column"]
    table = entry["table"]
    sql = "SELECT COUNT(*) FROM %s WHERE %s < ?" % (table, column)
    lock = _lock_of(db)
    if lock is not None:
        with lock:
            row = db.conn.execute(sql, (cutoff,)).fetchone()
    else:
        row = db.conn.execute(sql, (cutoff,)).fetchone()
    if not row:
        return 0
    return int(row[0] or 0)


def kv_prune_plan(db, now=None, limit=50):
    """kv 清理计划（amo_state_store.prune 的 dry-run），只报告不删键。"""
    try:  # 包模式优先
        from .amo_state_store import StateStore
    except ImportError:  # 直接按文件加载时
        from amo_state_store import StateStore
    moment = _epoch_now(now)
    store = StateStore(db, now=lambda: moment)
    plan = store.prune(now=moment)
    return {
        "dry_run": True,
        "applied": False,
        "generated_at": moment,
        "candidate_count": int(plan.get("candidate_count") or 0),
        "candidates": list(plan.get("candidates") or [])[:limit],
        "note": "kv 只报告；删键要 Phase 20 的显式运维入口（confirm_token）",
    }


def plan(db, now=None, cfg_getter=None, overrides=None, include_kv=False, limit=50):
    """保留计划（dry-run）：逐表统计将删行数，一条都不删。"""
    moment = _epoch_now(now)
    day = _day_of(now)
    items = []
    total = 0
    for entry in RETENTION_TABLE:
        days, source = resolve_days(entry, cfg_getter, overrides)
        item = {
            "table": entry["table"],
            "time_column": entry["time_column"],
            "days": days,
            "days_source": source,
            "purge": entry.get("purge") or "",
            "wired": bool(entry.get("wired")),
            "note": entry.get("note", ""),
        }
        if not entry.get("time_column"):
            item["cutoff"] = ""
            item["pending_rows"] = 0
        else:
            item["cutoff"] = _cutoff(entry, moment, day, days)
            try:
                item["pending_rows"] = _count(db, entry, item["cutoff"])
            except Exception as exc:
                item["pending_rows"] = 0
                item["error"] = "%s: %s" % (type(exc).__name__, exc)
            total += int(item["pending_rows"] or 0)
        items.append(item)
    report = {
        "dry_run": True,
        "applied": False,
        "now": moment,
        "day": day.isoformat(),
        "items": items,
        "total_pending": total,
    }
    if include_kv:
        report["kv_prune"] = kv_prune_plan(db, moment, limit=limit)
    return report


def wired_entries():
    """真正会删的项：按 purge 函数去重，保持登记顺序。"""
    seen = set()
    out = []
    for entry in RETENTION_TABLE:
        fn_name = entry.get("purge") or ""
        if not entry.get("wired") or not fn_name or fn_name in seen:
            continue
        seen.add(fn_name)
        out.append(entry)
    return out


def apply(db, now=None, cfg_getter=None, overrides=None, include_kv=False, limit=50):
    """按计划调现有 purge_*（删除口径与原调用点一致），返回前后对比。"""
    kwargs = {"now": now, "cfg_getter": cfg_getter, "overrides": overrides,
              "include_kv": include_kv, "limit": limit}
    before = plan(db, **kwargs)
    executed = []
    errors = []
    for entry in wired_entries():
        fn_name = entry["purge"]
        fn = getattr(db, fn_name, None)
        if not callable(fn):
            errors.append("%s：db 上没有这个方法" % fn_name)
            continue
        days, _source = resolve_days(entry, cfg_getter, overrides)
        try:
            fn(max(1, int(days)))
        except Exception as exc:
            errors.append("%s: %s: %s" % (fn_name, type(exc).__name__, exc))
            logger.warning("[amo] 保留策略 %s 执行失败：%s", fn_name, exc)
        else:
            executed.append(fn_name)
    after = plan(db, **kwargs)
    removed = {}
    for old, new in zip(before["items"], after["items"]):
        removed[old["table"]] = (int(old.get("pending_rows") or 0)
                                 - int(new.get("pending_rows") or 0))
    return {
        "dry_run": False,
        "applied": True,
        "now": before["now"],
        "day": before["day"],
        "executed": executed,
        "errors": errors,
        "removed": removed,
        "skipped_unwired": [entry["table"] for entry in RETENTION_TABLE
                            if not entry.get("wired")],
        "before": before,
        "after": after,
    }
