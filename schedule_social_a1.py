# -*- coding: utf-8 -*-
"""A1 社交政策：日程不再自动生成社交块 + 旧社交块迁移（关系数据保留）。

背景（2026-11-22 用户决策 A1）：
- 自动生成侧（JIT/ensure/regen…）**任何** category=social/date 的块都不许再落库；
- 用户/聊天/命令/面板/约会会话/外部 watch 写入的社交块保留（那是"人"的决定，不是自动生成）；
- 历史日程里自动生成的社交块要**迁移**：时间线保留（换成一个人的同类活动），
  同席人名与关系数据搬进归档，不删关系表、不删 npc_summary。

本模块只提供纯策略 + 迁移/备份流程；落库仍走 commit_schedule_change 唯一入口。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

try:  # 仓库统一的可观测性吞异常助手
    from .amo_observability import note as _note
except ImportError:  # 直接按文件加载时
    try:
        from amo_observability import note as _note
    except ImportError:  # 单测/独立运行
        def _note(_name: str, _exc: BaseException) -> None:
            return None

SOCIAL_CATEGORIES = ("social", "date")
#: 自动生成来源：这些来源写进来的社交块一律剥掉（A1）。
AUTO_SOURCES = frozenset({
    "jit", "jit_recover", "ensure", "ensure_regen", "regen", "snapshot",
    "auto", "segment", "life", "wander", "generated", "normalize",
    "ensure_normalize", "transit", "transit_repair", "health_audit",
})
ARCHIVE_KEY = "social_block_archive"
BACKUP_PREFIX = "social_migration_backup:"
BACKUP_KEEP_DAYS = 7
#: 一眼能看出是"饭"的活动词：迁移成一个人吃饭，而不是凭空安排别的事。
_MEAL_WORDS = ("饭", "餐", "吃", "火锅", "烧烤", "咖啡", "茶", "夜宵", "早餐", "午饭", "晚饭")


def is_social_block(row: Mapping[str, Any]) -> bool:
    """社交块判定：category=social/date，或 source=social/date。

    不能用 appointment 字段：旅游日池/预约块都会带 appointment=True，那不是社交。
    """
    if not isinstance(row, Mapping):
        return False
    if str(row.get("category") or "") in SOCIAL_CATEGORIES:
        return True
    return str(row.get("source") or "") in SOCIAL_CATEGORIES


def has_companion_names(row: Mapping[str, Any], names: Iterable[str]) -> list[str]:
    """块文案/people 字段里残留的旧社交人名。"""
    text = "%s %s" % (row.get("activity") or "", row.get("detail") or "")
    people = row.get("people") or row.get("companions") or []
    if isinstance(people, str):
        people = [people]
    haystack = text + " " + " ".join(str(item) for item in people if str(item))
    return [str(name) for name in (names or ()) if str(name or "").strip() and str(name) in haystack]


def strip_auto_social(
    rows: Sequence[Mapping[str, Any]] | None,
    source: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """写入口用：自动来源的社交块直接剥掉，返回 (保留行, 被剥行)。

    只对 AUTO_SOURCES 生效；人/约会/外部写入原样保留（A1 只禁"自动生成"）。
    """
    if str(source or "") not in AUTO_SOURCES:
        # 非自动来源：原样返回，连 dict 都不复制（调用方可能依赖行对象身份/共享引用）。
        return list(rows or ()), []
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        if is_social_block(row):
            removed.append(dict(row))
            continue
        kept.append(dict(row))
    return kept, removed


def solo_replacement(row: Mapping[str, Any]) -> dict[str, Any]:
    """旧社交块 → 一个人的同时间段活动：保留时间/地点/区域，去掉同席人。"""
    activity = str(row.get("activity") or "")
    place = str(row.get("place") or "")
    if any(word in activity for word in _MEAL_WORDS):
        new_activity = "自己吃饭、刷手机"
        category = "meal"
    elif "逛" in activity or "商场" in place:
        new_activity = "一个人逛逛"
        category = "life"
    elif "散步" in activity or "走" in activity:
        new_activity = "一个人散步"
        category = "life"
    else:
        new_activity = "一个人待着"
        category = "rest"
    out = dict(row)
    out["activity"] = new_activity
    out["category"] = category
    out["migrated_from_social"] = True
    for key in ("people", "companions", "appointment"):
        out.pop(key, None)
    detail = str(out.get("detail") or "")
    if detail:
        # 细节里也可能带人名（"和柳如烟约好…"），迁移后一并清掉，改写成中性细节。
        out["detail"] = "原本约了人，后来改成自己待着"
    return out


def plan_day(rows: Sequence[Mapping[str, Any]] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """一天的迁移计划：返回 (新行, 变更列表)。幂等：没有社交块就原样返回。"""
    new_rows: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        if not is_social_block(row):
            new_rows.append(dict(row))
            continue
        replaced = solo_replacement(row)
        new_rows.append(replaced)
        changes.append({
            "start": str(row.get("start") or ""),
            "end": str(row.get("end") or ""),
            "before": str(row.get("activity") or ""),
            "after": str(replaced.get("activity") or ""),
            "people": list(row.get("people") or []) if isinstance(row.get("people"), list) else [],
            "source": str(row.get("source") or ""),
        })
    return new_rows, changes


def backup_rows(db: Any, day: str, rows: Sequence[Mapping[str, Any]], now_ts: int) -> bool:
    """迁移前整份备份到 kv（每天一份，保留 7 天）。"""
    try:
        db.kv_set(BACKUP_PREFIX + str(day), {
            "day": str(day), "at": int(now_ts),
            "rows": [dict(row) for row in rows or () if isinstance(row, Mapping)],
        })
        return True
    except Exception as exc:  # cleanup: 备份失败不应阻断迁移（调用方会记指标）
        _note("schedule_social_a1.backup", exc)
        return False


def purge_backups(db: Any, now_ts: int, keep_days: int = BACKUP_KEEP_DAYS) -> int:
    """清理超过保留期的迁移备份，返回删除条数。"""
    cutoff = int(now_ts) - int(keep_days) * 86400
    removed = 0
    lister = getattr(db, "kv_keys", None)
    if not callable(lister):
        return 0
    try:
        keys = [str(key) for key in lister(prefix=BACKUP_PREFIX) or ()
                if str(key).startswith(BACKUP_PREFIX)]
    except Exception as exc:
        _note("schedule_social_a1.purge_list", exc)
        return 0
    for key in keys:
        try:
            record = db.kv_get(key, {}) or {}
        except Exception as exc:
            _note("schedule_social_a1.purge_get", exc)
            continue
        try:
            at = int((record or {}).get("at") or 0)
        except (TypeError, ValueError):
            at = 0
        if at and at >= cutoff:
            continue
        try:
            if db.kv_delete(key):
                removed += 1
        except Exception as exc:
            _note("schedule_social_a1.purge_delete", exc)
    return removed


def migrate_day(
    db: Any,
    day: str,
    rows: Sequence[Mapping[str, Any]],
    commit: Callable[..., Any],
    *,
    now_ts: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """迁移一天：备份 → 改写 → 走唯一提交入口。返回报告。"""
    new_rows, changes = plan_day(rows)
    if not changes:
        return {"day": str(day), "changed": False, "changes": []}
    report: dict[str, Any] = {"day": str(day), "changed": False, "changes": changes,
                              "backup": False}
    if dry_run:
        report["dry_run"] = True
        return report
    report["backup"] = backup_rows(db, day, rows, now_ts)
    try:
        result = commit(str(day), new_rows, old_items=list(rows),
                        source="social_migration", reason="A1：旧自动社交块迁移")
    except Exception as exc:  # cleanup: 单天失败不影响其它天
        _note("schedule_social_a1.commit", exc)
        report["error"] = str(exc)[:200]
        return report
    report["changed"] = bool((result or {}).get("changed"))
    report["result"] = result
    return report


def archive_changes(db: Any, report: Mapping[str, Any], now_ts: int) -> None:
    """把迁移替换掉的旧块记进归档（关系数据保留在归档里，不删）。"""
    if not report.get("changes"):
        return
    try:
        archive = db.kv_get(ARCHIVE_KEY, []) or []
    except Exception as exc:
        _note("schedule_social_a1.archive_read", exc)
        archive = []
    if not isinstance(archive, list):
        archive = []
    entry = {"day": str(report.get("day") or ""), "at": int(now_ts),
             "changes": list(report.get("changes") or [])}
    archive.append(entry)
    try:
        db.kv_set(ARCHIVE_KEY, archive[-200:])
    except Exception as exc:
        _note("schedule_social_a1.archive_write", exc)
