# -*- coding: utf-8 -*-
"""日程观测层：safe_view 接线、8 项指标落盘、影子快照（薄封装，无业务规则）。

分工：
- ``schedule_engine_v2``：纯函数（解析/派生/校验/降级），可 1000 天属性测试；
- 本模块：把纯函数接到真实 db/日志上，负责 **指标计数** 与 **schedule_shadow** 影子写。

影子键（不建表、不改 schema，旧版本读到也只会当普通 kv）：
- ``schedule_shadow:<day>`` = {at, version, source, counts, raw, view, diff}
- ``schedule_metrics:<day>`` = {counts, sources, updated}
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

try:  # 仓库统一的可观测性吞异常助手
    from .amo_observability import note as _note
except ImportError:  # 直接按文件加载时
    try:
        from amo_observability import note as _note
    except ImportError:  # 单测/独立运行
        def _note(_name: str, _exc: BaseException) -> None:
            return None

try:
    from . import schedule_engine_v2 as engine
except ImportError:
    import schedule_engine_v2 as engine

SHADOW_PREFIX = "schedule_shadow:"
METRICS_PREFIX = "schedule_metrics:"
#: 影子只保留最近多少天（与日程 fade/purge 节奏一致）。
SHADOW_KEEP = 14


def zone_mapper() -> Callable[[str], str] | None:
    """真实地点表（单测/独立运行没有就返回 None，引擎回落到内置词表）。"""
    try:
        from .amo_place import zone_for_place
    except ImportError:
        try:
            from amo_place import zone_for_place
        except ImportError:
            return None
    return zone_for_place


def minutes_of() -> Callable[[str, int, str, str], int] | None:
    """真实路程函数（amo_transition.transit_minutes）；拿不到就用引擎内置确定性函数。"""
    try:
        from .amo_transition import transit_minutes
    except ImportError:
        try:
            from amo_transition import transit_minutes
        except ImportError:
            return None
    return transit_minutes


def view_rows(
    rows: Sequence[Mapping[str, Any]] | None,
    day: str,
    *,
    now_min: int | None = None,
) -> tuple[list[dict[str, Any]], list[engine.Defect]]:
    """读侧安全视图：坏块降级、重叠压直、过渡全派生（不落库）。"""
    return engine.safe_view(
        rows, day=str(day or ""), now_min=now_min,
        zone_mapper=zone_mapper(), minutes_of=minutes_of(),
    )


def forbidden_names(db: Any) -> list[str]:
    """旧社交人名：npc 名单 + 迁移归档里出现过的同席人（A1 后日程里不许再有）。"""
    names: list[str] = []
    try:
        summary = db.kv_get("npc_summary", {}) or {}
        if isinstance(summary, dict):
            names.extend(str(key) for key in summary if str(key).strip())
    except Exception as exc:
        _note("schedule_observer.forbidden.npc", exc)
    try:
        archive = db.kv_get("social_block_archive", []) or []
        for entry in archive[-50:]:
            for change in (entry or {}).get("changes") or []:
                for name in change.get("people") or []:
                    if str(name).strip():
                        names.append(str(name))
    except Exception as exc:
        _note("schedule_observer.forbidden.archive", exc)
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def record_metrics(db: Any, day: str, counts: Mapping[str, int] | None,
                   *, source: str = "") -> dict[str, int]:
    """把一次检查的 8 项计数写进当天指标：按项取最大值（一次坏 = 当天坏）。"""
    key = METRICS_PREFIX + str(day or "")
    try:
        current = db.kv_get(key, {}) or {}
    except Exception as exc:
        _note("schedule_observer.metrics.read", exc)
        current = {}
    merged = dict(current.get("counts") or {})
    for code in engine.METRIC_KEYS:
        try:
            value = int((counts or {}).get(code) or 0)
        except (TypeError, ValueError):
            value = 0
        merged[code] = max(int(merged.get(code) or 0), value)
    sources = dict(current.get("sources") or {})
    if source:
        sources[source] = int(sources.get(source) or 0) + 1
    record = {"counts": merged, "sources": sources, "updated": str(day or "")}
    try:
        db.kv_set(key, record)
    except Exception as exc:
        _note("schedule_observer.metrics.write", exc)
    return merged


def read_metrics(db: Any, day: str) -> dict[str, int]:
    """读当天 8 项指标（没记录返回全 0，不区分「没跑过」与「全 0」）。"""
    try:
        record = db.kv_get(METRICS_PREFIX + str(day or ""), {}) or {}
    except Exception as exc:
        _note("schedule_observer.metrics.read_one", exc)
        record = {}
    return engine.merge_counts(record.get("counts") or {})


def write_shadow(
    db: Any,
    day: str,
    *,
    source: str,
    version: int,
    raw_rows: Sequence[Mapping[str, Any]] | None,
    view: Sequence[Mapping[str, Any]] | None,
    defects: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """影子快照：旧引擎写进库的行 vs v2 视图行，逐块对比留证（只读产物）。"""
    diff = engine.shadow_diff(raw_rows, view, limit=24)
    counts = engine.count_defects(defects or ())
    payload = {
        "at": int(version or 0),
        "source": str(source or ""),
        "version": int(version or 0),
        "counts": counts,
        "raw": [dict(row) for row in (raw_rows or ()) if isinstance(row, Mapping)],
        "view": [dict(row) for row in (view or ()) if isinstance(row, Mapping)],
        "diff": diff,
    }
    try:
        db.kv_set(SHADOW_PREFIX + str(day or ""), payload)
    except Exception as exc:
        _note("schedule_observer.shadow.write", exc)
    return payload


def read_shadow(db: Any, day: str) -> dict[str, Any]:
    try:
        record = db.kv_get(SHADOW_PREFIX + str(day or ""), {}) or {}
    except Exception as exc:
        _note("schedule_observer.shadow.read", exc)
        return {}
    return record if isinstance(record, dict) else {}


def purge_shadow(db: Any, keep_days: int = SHADOW_KEEP) -> int:
    """删过期影子键（按 key 里的日期字符串比较）。"""
    lister = getattr(db, "kv_keys", None)
    if not callable(lister):
        return 0
    try:
        keys = [str(key) for key in lister(prefix=SHADOW_PREFIX) or ()
                if str(key).startswith(SHADOW_PREFIX)]
    except Exception as exc:
        _note("schedule_observer.shadow.list", exc)
        return 0
    if len(keys) <= int(keep_days):
        return 0
    removed = 0
    for key in sorted(keys)[:-int(keep_days)]:
        try:
            if db.kv_delete(key):
                removed += 1
        except Exception as exc:
            _note("schedule_observer.shadow.purge", exc)
    return removed


def inspect_day(
    db: Any,
    day: str,
    rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    now_min: int | None = None,
    record: bool = False,
    source: str = "read",
) -> dict[str, Any]:
    """对一天做完整 8 项体检：读侧视图 + 写侧校验 + 社交残留 + 页面/聊天一致性。

    rows 为空时从 schedule 表读；返回 {"counts", "defects", "text"}。
    """
    if rows is None:
        try:
            rows = db.get_schedule(str(day)) or []
        except Exception as exc:
            _note("schedule_observer.inspect.read", exc)
            rows = []
    view, view_defects = view_rows(rows, day, now_min=now_min)
    defects: list[engine.Defect] = list(view_defects)
    try:
        from .schedule_social_a1 import AUTO_SOURCES as _auto_sources
    except ImportError:
        from schedule_social_a1 import AUTO_SOURCES as _auto_sources
    defects.extend(engine.validate(
        view if view else rows, day=str(day), zone_mapper=zone_mapper(),
        require_full_day=False, forbidden_names=forbidden_names(db),
        auto_sources=set(_auto_sources) | {""}))
    counts = engine.count_defects(defects)
    text = engine.timeline_text(view)
    if record:
        record_metrics(db, str(day), counts, source=source)
    return {"day": str(day), "counts": counts, "defects": defects,
            "text": text, "view": view}
