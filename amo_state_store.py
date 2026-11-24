# -*- coding: utf-8 -*-
"""kv 状态仓（Phase 5）：命名登记、TTL 判定、只读健康报告与 dry-run 清理。

为什么要有这一层：
- kv 是一张单表 kv(key, value, updated)，value 存 JSON；以前 121 个键各写各的，
  约 59 处值里塞 until 时间戳，读侧各自判断过期，过期键从不删除；
- 删除直接摸 DELETE FROM kv（amo_shared / amo_social），绕过封装；
- 面板/日志没有任何「kv 健康」入口，孤儿键（无 TTL 且长期不更新）越攒越多。

本模块只做四件事：
1. 命名登记：新键统一 domain:name[:qualifier]，domain 必须在 DOMAIN_SPECS 里；
   已有键不重命名（重命名会制造孤儿/丢状态），只按 LEGACY_PREFIX_DOMAINS 分类并报告。
2. 统一读写：get/set/delete/iter_prefix/set_ttl/get_fresh，TTL 判定只在 ttl_fresh 一处。
3. 只读健康报告：kv_health() → 过期未清 / 未知域 / 超期日级缓存 / 孤儿候选 /
   总量与按域计数 + dry-run prune 计划；不执行任何删除。
4. dry-run 清理：prune(apply=False) 只出计划；apply=True 需要显式 confirm_token，
   留给 Phase 20 接线，本阶段真实库只读。

约定：本模块只依赖标准库与 astrbot.api.logger，不导入 amo_shared，
避免与 amo_db/amo_shared 形成导入环。
"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import datetime as _dt
import json
import re
import threading

try:  # AstrBot 运行环境和单测桩都提供 astrbot.api.logger
    from astrbot.api import logger
except ImportError:  # 直接按文件加载时的兜底
    import logging

    logger = logging.getLogger("amo_state_store")

DEFAULT_ORPHAN_DAYS = 14
DEFAULT_DAILY_OVERDUE_DAYS = 2
HEALTH_REPORT_INTERVAL = 86400
PRUNE_TOKEN = "prune"

#: 新键形状：domain:name[:qualifier]，全小写域，段内允许字母数字与 _ . -
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_.\-]{1,96}(?::[A-Za-z0-9_.\-]{1,96})?$")
#: 键名里的日级日期（YYYY-MM-DD）
DAY_IN_KEY = re.compile(r"(\d{4}-\d{2}-\d{2})")

#: 域登记表：域 → 用途 / 默认 TTL（秒，0=不设过期）/ 是否允许列表 / 是否允许字典。
#: 只用于「新键」和健康分类；老键不改名。
DOMAIN_SPECS = {
    "diary": {"purpose": "小日记/块日记状态", "default_ttl": 0, "allow_list": True, "allow_dict": True, "legacy": False},
    "schedule": {"purpose": "日程与版本号", "default_ttl": 0, "allow_list": True, "allow_dict": True, "legacy": False},
    "date": {"purpose": "约会事实/收尾", "default_ttl": 21600, "allow_list": False, "allow_dict": True, "legacy": False},
    "travel": {"purpose": "出行覆盖层", "default_ttl": 86400, "allow_list": True, "allow_dict": True, "legacy": False},
    "trip": {"purpose": "旅游计划", "default_ttl": 86400, "allow_list": True, "allow_dict": True, "legacy": False},
    "wander": {"purpose": "闲逛/邀请流程", "default_ttl": 3600, "allow_list": False, "allow_dict": True, "legacy": False},
    "follow": {"purpose": "跟随/拒绝记录", "default_ttl": 3600, "allow_list": False, "allow_dict": True, "legacy": False},
    "weather": {"purpose": "天气缓存与预警", "default_ttl": 10800, "allow_list": True, "allow_dict": True, "legacy": False},
    "proactive": {"purpose": "主动消息节流/抑制", "default_ttl": 3600, "allow_list": False, "allow_dict": False, "legacy": False},
    "health": {"purpose": "身体状态/睡眠/饥饿", "default_ttl": 86400, "allow_list": False, "allow_dict": True, "legacy": False},
    "jit": {"purpose": "JIT 时间轴心跳", "default_ttl": 600, "allow_list": False, "allow_dict": True, "legacy": False},
    "mood": {"purpose": "心情快照", "default_ttl": 3600, "allow_list": False, "allow_dict": True, "legacy": False},
    "late": {"purpose": "熬夜状态", "default_ttl": 3600, "allow_list": False, "allow_dict": True, "legacy": False},
    "home": {"purpose": "回家/收拾流程", "default_ttl": 3600, "allow_list": False, "allow_dict": True, "legacy": False},
    "period": {"purpose": "生理期", "default_ttl": 0, "allow_list": False, "allow_dict": True, "legacy": False},
    "channels": {"purpose": "平台渠道记录", "default_ttl": 0, "allow_list": False, "allow_dict": True, "legacy": False},
    "worldview": {"purpose": "世界观固化文本", "default_ttl": 0, "allow_list": False, "allow_dict": True, "legacy": False},
    "photo": {"purpose": "照片缓存/提示词", "default_ttl": 86400, "allow_list": True, "allow_dict": True, "legacy": False},
    "shared": {"purpose": "共同活动", "default_ttl": 3600, "allow_list": False, "allow_dict": True, "legacy": False},
    "current": {"purpose": "此刻动作", "default_ttl": 3600, "allow_list": False, "allow_dict": True, "legacy": False},
    "micro": {"purpose": "微型事件动作/干扰", "default_ttl": 3600, "allow_list": False, "allow_dict": True, "legacy": False},
    "ledger": {"purpose": "账本/消费/资产", "default_ttl": 0, "allow_list": True, "allow_dict": True, "legacy": False},
    "identity": {"purpose": "uid 身份映射", "default_ttl": 0, "allow_list": False, "allow_dict": True, "legacy": False},
    "social": {"purpose": "问候/拒绝等社交记录", "default_ttl": 86400, "allow_list": False, "allow_dict": True, "legacy": False},
    "migration": {"purpose": "一次性迁移标记", "default_ttl": 0, "allow_list": False, "allow_dict": False, "legacy": False},
    "misc": {"purpose": "杂项（待归域）", "default_ttl": 0, "allow_list": False, "allow_dict": True, "legacy": False},
    "his": {"purpose": "老前缀：他的状态", "default_ttl": 0, "allow_list": False, "allow_dict": True, "legacy": True},
    "last": {"purpose": "老前缀：上次快照", "default_ttl": 0, "allow_list": False, "allow_dict": True, "legacy": True},
}

#: 老键前缀 → 登记域。按顺序匹配，具体前缀放前面。
LEGACY_PREFIX_DOMAINS = (
    ("micro_event:", "micro"),
    ("jit_diary:", "diary"),
    ("jit:", "jit"),
    ("block_diary", "diary"),
    ("diary", "diary"),
    ("daily_digest", "diary"),
    ("schedule_ver:", "schedule"),
    ("schedule_", "schedule"),
    ("schedule", "schedule"),
    ("last_date", "date"),
    ("date_", "date"),
    ("weather", "weather"),
    ("proactive_", "proactive"),
    ("wander_", "wander"),
    ("follow_", "follow"),
    ("late_", "late"),
    ("mood", "mood"),
    ("shared_", "shared"),
    ("travel", "travel"),
    ("trip", "trip"),
    ("channels:", "channels"),
    ("current_", "current"),
    ("activity_now", "current"),
    ("home_", "home"),
    ("period_", "period"),
    ("photo_", "photo"),
    ("his_", "his"),
    ("he_", "his"),
    ("last_", "last"),
    ("health", "health"),
    ("hunger", "health"),
    ("sick_", "health"),
    ("sleep_", "health"),
    ("unwell_", "health"),
    ("expense_", "ledger"),
    ("house_", "ledger"),
    ("housing", "ledger"),
    ("gift_", "ledger"),
    ("shopping_", "ledger"),
    ("owner_", "identity"),
    ("self_", "identity"),
    ("lover_", "identity"),
    ("uid_alias", "identity"),
    ("greeting_last", "social"),
    ("refusal:", "social"),
    ("positive:", "social"),
    ("transfer:", "social"),
    ("scenic_", "migration"),
    ("delay_", "migration"),
    ("typing_realistic", "migration"),
    ("diary_migrate", "migration"),
    ("admin_", "misc"),
    ("day_types", "schedule"),
    ("transition", "misc"),
)

_HEALTH_LOCK = threading.Lock()
_last_health_log_at = 0.0


def _now_ts():
    return _amo_clock.now_ts()


def registered_domains():
    """已登记域列表（排序；给测试/文档用）。"""
    return sorted(DOMAIN_SPECS)


def domain_spec(domain):
    """取一份域登记（拷贝）；未登记返回空 dict。"""
    spec = DOMAIN_SPECS.get(str(domain or ""))
    return dict(spec) if spec else {}


def _legacy_domain(key):
    text = str(key or "")
    for prefix, domain in LEGACY_PREFIX_DOMAINS:
        if text.startswith(prefix):
            return domain
    return ""


def classify_key(key):
    """把一个 kv 键分类成登记域，并标出是否合规（不改名、不删除）。

    返回 dict(key, domain, domain_raw, compliant, reason)：
    - reason 为空表示合规；
    - legacy_no_colon / legacy_alias：老键，按前缀归到登记域，报告但不改；
    - unknown_domain：域未登记（未知域键）；
    - bad_shape / empty：形状非法。
    """
    text = str(key or "")
    out = {"key": text, "domain": "", "domain_raw": "", "compliant": False, "reason": ""}
    if not text:
        out["reason"] = "empty"
        return out
    parts = text.split(":")
    out["domain_raw"] = parts[0] if parts else ""
    if ":" not in text:
        out["domain"] = _legacy_domain(text)
        out["reason"] = "legacy_no_colon" if out["domain"] else "unknown_domain"
        return out
    if parts[0] in DOMAIN_SPECS and KEY_PATTERN.match(text):
        out["domain"] = parts[0]
        out["compliant"] = True
        return out
    legacy = _legacy_domain(text)
    if legacy:
        out["domain"] = legacy
        out["reason"] = "legacy_alias"
        return out
    out["reason"] = "bad_shape" if parts[0] in DOMAIN_SPECS else "unknown_domain"
    return out


def ttl_fresh(value, now_ts, default=None, zero_is_expired=False):
    """TTL 唯一判定：dict 带 until 且已过期 → default，否则原样返回。

    - 空值/非 dict → default；
    - until 为 0：zero_is_expired=False（current_action 语义）视为不过期，
      True（micro/date 语义）视为已过期；
    - 不删除键：过期只是读侧不认，清理留给 prune（Phase 20）。
    """
    if not isinstance(value, dict) or not value:
        return default
    try:
        until = float(value.get("until") or 0)
    except (TypeError, ValueError):
        return default
    if until <= 0:
        return default if zero_is_expired else value
    if until <= float(now_ts):
        return default
    return value


def _epoch_of_day(text):
    try:
        return _dt.datetime.strptime(str(text), "%Y-%m-%d").timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def should_log_health(now=None, interval=HEALTH_REPORT_INTERVAL):
    """启动/心跳限流：同一个进程里每 interval 秒最多打一次 INFO 摘要。"""
    now = float(now if now is not None else _now_ts())
    global _last_health_log_at
    with _HEALTH_LOCK:
        if now - _last_health_log_at < float(interval or 0):
            return False
        _last_health_log_at = now
        return True


class StateStore:
    """包在 AmoDB 外面的 kv 门面：读写、TTL、健康报告、dry-run 清理。

    db 只需要 kv_get/kv_set；kv_delete/kv_keys/kv_rows 存在时用存储层实现，
    不存在（测试替身）时健康报告自动退化为空，不抛错。
    """

    def __init__(self, db, now=None):
        self.db = db
        self._now = now

    # ---------- 基础读写 ----------
    def now(self):
        return float(self._now() if callable(self._now) else _now_ts())

    def get(self, key, default=None):
        getter = getattr(self.db, "kv_get", None)
        if not callable(getter):
            return default
        try:
            return getter(key, default)
        except Exception as exc:
            logger.debug("[amo] state_store.get(%r) 失败: %r", key, exc)
            return default

    def set(self, key, value):
        setter = getattr(self.db, "kv_set", None)
        if not callable(setter):
            raise TypeError("StateStore.set 需要 db.kv_set")
        setter(key, value)
        return value

    def delete(self, key):
        deleter = getattr(self.db, "kv_delete", None)
        if not callable(deleter):
            logger.debug("[amo] state_store.delete(%r)：db 不支持 kv_delete", key)
            return False
        return bool(deleter(key))

    def iter_prefix(self, prefix, limit=500):
        keys_fn = getattr(self.db, "kv_keys", None)
        if not callable(keys_fn):
            logger.debug("[amo] state_store.iter_prefix(%r)：db 不支持 kv_keys", prefix)
            return []
        keys = keys_fn(prefix, limit)
        return [(str(key), self.get(key)) for key in (keys or [])]

    def set_ttl(self, key, value, ttl_seconds, now=None, until=None):
        """写值并盖 until 时间戳；until 显式给出时优先（绝对时间）。"""
        if isinstance(value, dict):
            payload = dict(value)
        elif value is None:
            payload = {}
        else:
            raise TypeError("set_ttl 只接受 dict/None（标量请用 set）")
        deadline = (float(until) if until is not None
                    else float(now if now is not None else self.now()) + float(ttl_seconds or 0))
        payload["until"] = deadline
        self.set(key, payload)
        return payload

    def get_fresh(self, key, now=None, default=None, zero_is_expired=False):
        """读值并按 until 判过期；过期返回 default（键仍留在库里，交给 prune）。"""
        moment = float(now if now is not None else self.now())
        return ttl_fresh(self.get(key, None), moment, default=default,
                         zero_is_expired=zero_is_expired)

    # ---------- 健康报告 ----------
    def rows(self, prefix="", limit=10000):
        rows_fn = getattr(self.db, "kv_rows", None)
        if callable(rows_fn):
            try:
                return list(rows_fn(prefix, limit))
            except Exception as exc:
                logger.debug("[amo] state_store.rows(%r) 失败: %r", prefix, exc)
                return []
        keys_fn = getattr(self.db, "kv_keys", None)
        if not callable(keys_fn):
            return []
        try:
            keys = keys_fn(prefix, limit)
        except Exception as exc:
            logger.debug("[amo] state_store.rows(%r) 退化失败: %r", prefix, exc)
            return []
        return [{"key": str(key), "value": self.get(key), "updated": 0.0} for key in (keys or [])]

    def _scan(self, now, orphan_days, daily_overdue_days):
        rows = self.rows("", 10000)
        by_domain: dict[str, Any] = {}
        unknown_domains: dict[str, Any] = {}
        noncompliant = []
        expired = []
        stale_daily = []
        orphans = []
        for row in rows:
            key = str((row or {}).get("key") or "")
            if not key:
                continue
            value = row.get("value")
            updated = _as_float(row.get("updated"))
            info = classify_key(key)
            domain = info["domain"] or "unknown"
            by_domain[domain] = by_domain.get(domain, 0) + 1
            if info["reason"] == "unknown_domain":
                raw = info["domain_raw"] or "unknown"
                unknown_domains[raw] = unknown_domains.get(raw, 0) + 1
            if not info["compliant"]:
                noncompliant.append({"key": key, "domain": info["domain"] or "",
                                     "reason": info["reason"]})
            until = _until_of(value)
            if until and until <= now:
                expired.append(key)
            elif self._is_overdue_daily(key, value, now, daily_overdue_days):
                stale_daily.append(key)
            elif not until and updated and now - updated > float(orphan_days or 0) * 86400:
                orphans.append(key)
        return {
            "total_keys": len(rows),
            "by_domain": dict(sorted(by_domain.items())),
            "unknown_domains": dict(sorted(unknown_domains.items())),
            "noncompliant": noncompliant,
            "expired": sorted(expired),
            "stale_daily": sorted(stale_daily),
            "orphans": sorted(orphans),
        }

    @staticmethod
    def _is_overdue_daily(key, value, now, overdue_days):
        cutoff = float(now) - float(overdue_days or 0) * 86400
        match = DAY_IN_KEY.search(str(key or ""))
        day_text = match.group(1) if match else ""
        if not day_text and isinstance(value, dict):
            day_text = str(value.get("day") or value.get("date") or "")
        if not day_text:
            return False
        stamp = _epoch_of_day(day_text)
        return bool(stamp) and stamp < cutoff

    def _plan(self, scan, limit=200):
        candidates = sorted(set(scan["expired"]) | set(scan["stale_daily"]))
        return {
            "dry_run": True,
            "applied": False,
            "candidate_count": len(candidates),
            "candidates": candidates[:limit],
            "expired": scan["expired"][:limit],
            "stale_daily": scan["stale_daily"][:limit],
        }

    def kv_health(self, now=None, orphan_days=DEFAULT_ORPHAN_DAYS,
                  daily_overdue_days=DEFAULT_DAILY_OVERDUE_DAYS, limit=20):
        """只读健康报告（JSON 可序列化）：不删除任何键。"""
        moment = float(now if now is not None else self.now())
        scan = self._scan(moment, orphan_days, daily_overdue_days)
        return {
            "generated_at": moment,
            "orphan_days": int(orphan_days or 0),
            "daily_overdue_days": int(daily_overdue_days or 0),
            "total_keys": scan["total_keys"],
            "by_domain": scan["by_domain"],
            "unknown_domain_count": len(scan["unknown_domains"]),
            "unknown_domains": scan["unknown_domains"],
            "noncompliant_count": len(scan["noncompliant"]),
            "noncompliant_keys": scan["noncompliant"][:limit],
            "expired_count": len(scan["expired"]),
            "expired_keys": scan["expired"][:limit],
            "stale_daily_cache_count": len(scan["stale_daily"]),
            "stale_daily_cache_keys": scan["stale_daily"][:limit],
            "orphan_count": len(scan["orphans"]),
            "orphan_candidates": scan["orphans"][:limit],
            "prune": self._plan(scan, limit=limit),
        }

    def health_line(self, now=None):
        """一行 INFO 摘要（启动/心跳用）。"""
        report = self.kv_health(now=now)
        return ("kv_health total=%d domains=%d expired=%d stale_daily=%d "
                "unknown=%d noncompliant=%d orphan=%d prune_candidates=%d"
                % (report["total_keys"], len(report["by_domain"]),
                   report["expired_count"], report["stale_daily_cache_count"],
                   report["unknown_domain_count"], report["noncompliant_count"],
                   report["orphan_count"], report["prune"]["candidate_count"]))

    def prune(self, now=None, apply=False, confirm_token=None, limit=500):
        """清理计划：默认 dry-run（只返回计划，不删键）。

        apply=True 必须同时给 confirm_token=PRUNE_TOKEN，并且只应在
        测试临时库 / Phase 20 的显式运维入口调用；真实库本阶段只读。
        """
        moment = float(now if now is not None else self.now())
        scan = self._scan(moment, DEFAULT_ORPHAN_DAYS, DEFAULT_DAILY_OVERDUE_DAYS)
        plan = self._plan(scan, limit=limit)
        if not apply:
            return plan
        if confirm_token != PRUNE_TOKEN:
            raise ValueError("prune(apply=True) 需要 confirm_token=%r（Phase 20 才接线）" % PRUNE_TOKEN)
        deleted = 0
        for key in plan["candidates"]:
            if self.delete(key):
                deleted += 1
        plan.update(applied=True, dry_run=False, deleted=deleted)
        return plan


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _until_of(value):
    if not isinstance(value, dict):
        return 0.0
    return _as_float(value.get("until"))


def for_plugin(plugin, now=None):
    """取/建插件维度的 StateStore（plugin.state_store 优先）。"""
    store = getattr(plugin, "state_store", None)
    if isinstance(store, StateStore) and now is None:
        return store
    db = getattr(plugin, "db", plugin)
    return StateStore(db, now=now)


def attach(plugin, now=None):
    """在插件上挂一个 StateStore（幂等），返回它。"""
    store = StateStore(getattr(plugin, "db", plugin), now=now)
    try:
        plugin.state_store = store
    except Exception as exc:  # 只读桩/不可写对象：退化成临时 store
        logger.debug("[amo] state_store.attach 失败: %r", exc)
    return store


# ---------- 事务内的 kv SQL 助手（amo_shared 的 uid 合并用） ----------
def delete_row(cur, key):
    """事务内删一个 kv 键（调用方负责 commit）。"""
    cur.execute("DELETE FROM kv WHERE key=?", (str(key),))


def rename_row(cur, old_key, new_key):
    """事务内改键名（冲突时保留新键原值，调用方随后删旧键）。"""
    cur.execute("UPDATE OR IGNORE kv SET key=? WHERE key=?", (str(new_key), str(old_key)))


def put_row(cur, key, value):
    """事务内写一个 kv 键；序列化与 AmoDB.kv_set 保持一致。"""
    cur.execute("INSERT OR REPLACE INTO kv(key, value, updated) VALUES(?,?,?)",
                (str(key), json.dumps(value, ensure_ascii=False), _now_ts()))


def keys_of(cur):
    """事务内列出全部 kv 键。"""
    return [str(row[0]) for row in cur.execute("SELECT key FROM kv")]


def value_row(cur, key):
    """事务内读一个 kv 的 value，返回单元素元组（兼容旧 load(raw[0]) 写法）。"""
    row = cur.execute("SELECT value FROM kv WHERE key=?", (str(key),)).fetchone()
    return (row[0],) if row else None
