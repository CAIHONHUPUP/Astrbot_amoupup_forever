# -*- coding: utf-8 -*-
"""共享底座·用量：token 统计、价格、余额查询、UID 合并。从 amo_shared.py 按域拆出，旧名仍在门面。"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, TYPE_CHECKING
try:  # 可选依赖兜底（从 amo_shared 拆出）
    from .amo_shared_deps import (
    _clock_from_ts,
        _note_swallowed,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared_deps import (
        _clock_from_ts,
            _note_swallowed,
        )

try:  # 同包姐妹模块
    from .amo_shared_time import _now
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared_time import _now

# ---- 用量统计（读 AstrBot 的 data_v4.db provider_stats） ----
USAGE_PEAK_HOURS = ((9, 12), (14, 18))


USAGE_DEFAULT_PRICES = {
    "hit_peak": 0.04, "hit_idle": 0.02,
    "miss_peak": 2.0, "miss_idle": 1.0,
    "out_peak": 8.0, "out_idle": 4.0,
}


def usage_is_peak(ts: float) -> bool:
    """北京时间工作日 9-12、14-18 为高峰；周末全天算空闲（法定节假日未处理）。"""
    try:
        # 不能写死 +8：时区是可配的（clock.configure），服务器跑 UTC 时 +8 只是碰巧对
        dt = _clock_from_ts(ts)
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.usage_is_peak", _amo_exc)
        return False
    if dt.weekday() >= 5:
        return False
    return any(start <= dt.hour < end for start, end in USAGE_PEAK_HOURS)


def _usage_row_ts(start_time, created_at):
    try:
        value = float(start_time or 0)
        if value > 0:
            return value
    except Exception as _amo_exc:
        _note_swallowed("amo_shared._usage_row_ts", _amo_exc)
    try:
        dt = datetime.fromisoformat(str(created_at or "").strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception as _amo_exc:
        _note_swallowed("amo_shared._usage_row_ts", _amo_exc)
        return 0.0


def _empty_bucket():
    return {"calls": 0, "input_miss": 0, "input_cached": 0, "output": 0}


def summarize_token_usage(db_path: Any, start_ts: Any = None, end_ts: Any = None, model_filter: Any = None) -> dict:
    """按 provider_stats 汇总 token。start_ts/end_ts 为 epoch 秒，None 表示不限。"""
    usage = _empty_bucket()
    usage.update({"cache_rate": 0.0, "peak": _empty_bucket(), "idle": _empty_bucket(), "by_model": {}})
    if not db_path or not os.path.exists(db_path):
        usage["error"] = "找不到 AstrBot 数据库"
        return usage
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % str(db_path).replace("\\", "/"), uri=True)
    except Exception as exc:
        _note_swallowed("amo_shared.summarize_token_usage", exc)
        usage["error"] = "打开数据库失败: %s" % exc
        return usage
    try:
        rows = conn.execute(
            "SELECT provider_model, token_input_other, token_input_cached, token_output, "
            "start_time, created_at FROM provider_stats"
        ).fetchall()
    except Exception as exc:
        _note_swallowed("amo_shared.summarize_token_usage", exc)
        usage["error"] = "读取 provider_stats 失败: %s" % exc
        conn.close()
        return usage
    conn.close()

    for model, miss, cached, out, start_time, created_at in rows:
        model = str(model or "")
        if model_filter and model_filter not in model:
            continue
        ts = _usage_row_ts(start_time, created_at)
        if start_ts is not None and ts and ts < float(start_ts):
            continue
        if end_ts is not None and ts and ts >= float(end_ts):
            continue
        miss = int(miss or 0)
        cached = int(cached or 0)
        out = int(out or 0)
        usage["calls"] += 1
        usage["input_miss"] += miss
        usage["input_cached"] += cached
        usage["output"] += out
        bucket = usage["peak"] if usage_is_peak(ts) else usage["idle"]
        bucket["calls"] += 1
        bucket["input_miss"] += miss
        bucket["input_cached"] += cached
        bucket["output"] += out
        item = usage["by_model"].setdefault(model or "unknown", _empty_bucket())
        item["calls"] += 1
        item["input_miss"] += miss
        item["input_cached"] += cached
        item["output"] += out

    total_in = usage["input_miss"] + usage["input_cached"]
    usage["cache_rate"] = (usage["input_cached"] / total_in) if total_in else 0.0
    usage["by_model"] = [
        dict(model=key, **value) for key, value in
        sorted(usage["by_model"].items(), key=lambda kv: -(kv[1]["input_miss"] + kv[1]["input_cached"]))
    ]
    return usage


def usage_cost(usage: dict, prices: Any = None) -> float:
    """按空闲/高峰两档单价（元/百万 token）估算花费。"""
    table = dict(USAGE_DEFAULT_PRICES)
    if isinstance(prices, dict):
        for key, value in prices.items():
            try:
                table[key] = float(value)
            except (TypeError, ValueError):
                continue
    cost = 0.0
    for bucket, prefix in ((usage.get("peak") or {}, "peak"), (usage.get("idle") or {}, "idle")):
        cost += float(bucket.get("input_cached") or 0) * table["hit_" + prefix] / 1e6
        cost += float(bucket.get("input_miss") or 0) * table["miss_" + prefix] / 1e6
        cost += float(bucket.get("output") or 0) * table["out_" + prefix] / 1e6
    return round(cost, 6)


def fetch_provider_balance(api_key: str, base_url: str, kind: str, timeout: float = 8.0) -> dict:
    """尽力查询供应商余额；查不到就返回 {ok: False, error}，不抛异常。

    支持：DeepSeek（/user/balance）、SiliconFlow（/v1/user/info）、
    OpenAI 兼容中转（/v1/dashboard/billing/subscription + usage，one-api/new-api 常见）。
    """
    import urllib.request

    api_key = str(api_key or "").strip()
    base = str(base_url or "").rstrip("/")
    if not api_key or not base:
        return {"ok": False, "error": "缺密钥或地址"}

    def _request(url, headers=None):
        auth = dict(headers or {})
        auth.setdefault("Authorization", "Bearer " + api_key)
        auth.setdefault("Accept", "application/json")
        request = urllib.request.Request(url, headers=auth)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        if kind == "deepseek":
            payload = _request(base + "/user/balance", {"Accept": "application/json"})
            infos = payload.get("balance_infos") or []
            chosen = None
            for item in infos:
                if str(item.get("currency") or "").upper() == "CNY":
                    chosen = item
                    break
            if chosen is None and infos:
                chosen = infos[0]
            if not isinstance(chosen, dict):
                return {"ok": False, "error": "返回里没有余额信息"}

            def money(value):
                try:
                    return round(float(value), 2)
                except (TypeError, ValueError):
                    return None

            return {
                "ok": True,
                "available": bool(payload.get("is_available")),
                "currency": str(chosen.get("currency") or "CNY"),
                "amount": money(chosen.get("total_balance")),
                "granted": money(chosen.get("granted_balance")),
                "topped_up": money(chosen.get("topped_up_balance")),
            }
        if kind == "siliconflow":
            payload = _request(base + "/v1/user/info", {"Accept": "application/json"})
            data = payload.get("data") or {}
            amount = None
            for field in ("balance", "totalBalance"):
                raw = data.get(field)
                if raw is None:
                    continue
                try:
                    amount = round(float(raw), 2)
                    break
                except (TypeError, ValueError):
                    continue
            if amount is None:
                return {"ok": False, "error": "返回里没有余额字段"}
            return {"ok": True, "currency": "CNY", "amount": amount}
        # openai 兼容中转（one-api/new-api 常见）
        subscription = _request(base + "/v1/dashboard/billing/subscription")
        hard_limit = None
        try:
            hard_limit = float(subscription.get("hard_limit_usd"))
        except (TypeError, ValueError):
            hard_limit = None
        if hard_limit is None:
            return {"ok": False, "error": "返回里没有 hard_limit_usd"}
        today = _now()
        month_start = today.replace(day=1).strftime("%Y-%m-%d")
        end_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        used_cents = 0.0
        try:
            usage = _request(
                base + "/v1/dashboard/billing/usage?start_date=" + month_start + "&end_date=" + end_date
            )
            used_cents = float(usage.get("total_usage") or 0)
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.fetch_provider_balance", _amo_exc)
            used_cents = 0.0
        remaining = round(hard_limit - used_cents / 100.0, 2)
        return {
            "ok": True,
            "currency": "USD",
            "amount": max(0.0, remaining),
            "used": round(used_cents / 100.0, 2),
            "limit": round(hard_limit, 2),
        }
    except Exception as exc:
        _note_swallowed("amo_shared.fetch_provider_balance", exc)
        return {"ok": False, "error": str(exc)[:120]}


def fetch_deepseek_balance(api_key: str, base_url: str = "https://api.deepseek.com", timeout: float = 8.0) -> dict:
    """查 DeepSeek 余额；失败返回 {ok: False, error}，不抛异常。

    解析逻辑和 ``fetch_provider_balance(kind="deepseek")`` 完全一致，这里只保留
    一个"没配 key 就直说"的前置判断，避免两处各写一份、改一处漏一处。
    """
    api_key = str(api_key or "").strip()
    if not api_key:
        return {"ok": False, "error": "未配置 API Key"}
    return fetch_provider_balance(
        api_key, base_url or "https://api.deepseek.com", "deepseek", timeout
    )


def merge_uid_alias_data(conn: Any, alias_map: Any) -> dict:
    """把别名 uid 的档案/记忆/消息/关系/称呼并进主 uid（一次性、幂等）。

    返回统计 dict，调用方负责记 kv 标记，避免每次启动重复跑。
    """
    report = {"profiles": 0, "memories": 0, "messages": 0, "proactive_log": 0,
              "relations": 0, "kv": 0}
    if not isinstance(alias_map, dict) or not alias_map:
        return report
    cur = conn.cursor()

    def table_columns(name):
        try:
            return [row[1] for row in cur.execute("PRAGMA table_info(%s)" % name)]
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.merge_uid_alias_data.table_columns", _amo_exc)
            return []

    profile_cols = table_columns("profiles")
    if not profile_cols:
        return report

    def to_num(value, default=0.0):
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return default

    for alias_uid, canon_uid in list(alias_map.items()):
        alias_uid = str(alias_uid or "")
        canon_uid = str(canon_uid or "")
        if not alias_uid or not canon_uid or alias_uid == canon_uid:
            continue
        _merge_uid_profiles(cur, report, alias_uid, canon_uid, profile_cols, to_num)
        _merge_uid_messages(cur, report, alias_uid, canon_uid)
        _merge_uid_proactive(cur, report, alias_uid, canon_uid)
        _merge_uid_relations(cur, report, alias_uid, canon_uid)
        _merge_uid_kv(cur, report, alias_uid, canon_uid, to_num)

    try:
        conn.commit()
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)
    return report


def _merge_uid_profiles(cur, report, alias_uid, canon_uid, profile_cols, to_num):
    """档案合并：只有别名 → 改主键；两边都有 → 逐字段取并集/最大值后 INSERT OR REPLACE。"""
    cur.execute("SELECT * FROM profiles WHERE uid=?", (alias_uid,))
    alias_raw = cur.fetchone()
    cur.execute("SELECT * FROM profiles WHERE uid=?", (canon_uid,))
    canon_raw = cur.fetchone()

    def as_dict(row):
        if row is None:
            return None
        if hasattr(row, "keys"):
            return dict(row)
        return {col[0]: value for col, value in zip(cur.description, row)}

    alias_row = as_dict(alias_raw)
    canon_row = as_dict(canon_raw)
    if alias_row is not None and canon_row is None:
        # 只有别名档案：直接改主键，历史原样保留
        cur.execute("UPDATE profiles SET uid=? WHERE uid=?", (canon_uid, alias_uid))
        report["profiles"] += 1
    elif alias_row is not None and canon_row is not None:
        a = alias_row
        b = canon_row
        merged = dict(b)
        merged["uid"] = canon_uid
        for key in ("name", "birthday", "alias", "notes", "platform"):
            if key not in profile_cols:
                continue
            if key in ("alias", "notes"):
                parts = []
                for value in (b.get(key), a.get(key)):
                    text = str(value or "").strip()
                    if text and text not in parts:
                        parts.append(text)
                merged[key] = " | ".join(parts)
            else:
                merged[key] = b.get(key) or a.get(key) or ""
        if "relation" in profile_cols:
            canon_rel = str(b.get("relation") or "普通")
            alias_rel = str(a.get("relation") or "普通")
            merged["relation"] = canon_rel if canon_rel != "普通" else alias_rel
        for key in ("affection", "msg_count", "proactive_count", "is_admin"):
            if key in profile_cols:
                merged[key] = max(int(to_num(b.get(key))), int(to_num(a.get(key))))
        for key in ("last_seen", "last_proactive", "relation_at"):
            if key in profile_cols:
                merged[key] = max(to_num(b.get(key)), to_num(a.get(key)))
        if "first_seen" in profile_cols:
            values = [v for v in (to_num(b.get("first_seen")), to_num(a.get("first_seen"))) if v > 0]
            merged["first_seen"] = min(values) if values else None
        if "private_umo" in profile_cols:
            merged["private_umo"] = b.get("private_umo") or a.get("private_umo") or ""
        if "last_group" in profile_cols:
            merged["last_group"] = b.get("last_group") or a.get("last_group") or ""
        if "proactive_day" in profile_cols:
            merged["proactive_day"] = b.get("proactive_day") or a.get("proactive_day") or ""
        cols = [c for c in profile_cols if c in merged]
        cur.execute("DELETE FROM profiles WHERE uid=?", (alias_uid,))
        cur.execute(
            "INSERT OR REPLACE INTO profiles(%s) VALUES(%s)"
            % (",".join(cols), ",".join("?" * len(cols))),
            [merged.get(c) for c in cols],
        )
        report["profiles"] += 1


def _merge_uid_messages(cur, report, alias_uid, canon_uid):
    """消息改归属，并只对别名带来的那几条去重（同内容同秒只留最早 rowid）。"""
    # 消息：改归属 + 只对"别名带来的"那几条去重
    try:
        alias_msgs = [tuple(r) for r in cur.execute(
            "SELECT umo, role, content, created_at FROM messages WHERE uid=?", (alias_uid,))]
        cur.execute("UPDATE messages SET uid=? WHERE uid=?", (canon_uid, alias_uid))
        report["messages"] += cur.rowcount or 0
        for umo, role, content, created in alias_msgs:
            cur.execute(
                "DELETE FROM messages WHERE umo=? AND uid=? AND role=? AND content=? AND created_at=? "
                "AND rowid NOT IN (SELECT MIN(rowid) FROM messages WHERE umo=? AND uid=? AND role=? AND content=? AND created_at=?)",
                (umo, canon_uid, role, content, created, umo, canon_uid, role, content, created),
            )
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)


def _merge_uid_proactive(cur, report, alias_uid, canon_uid):
    """主动记录（proactive_log）改归属 + 去重。"""
    # 主动记录
    try:
        alias_pro = [tuple(r) for r in cur.execute(
            "SELECT kind, content, created FROM proactive_log WHERE uid=?", (alias_uid,))]
        cur.execute("UPDATE proactive_log SET uid=? WHERE uid=?", (canon_uid, alias_uid))
        report["proactive_log"] += cur.rowcount or 0
        for kind, content, created in alias_pro:
            cur.execute(
                "DELETE FROM proactive_log WHERE uid=? AND kind=? AND content=? AND created=? "
                "AND rowid NOT IN (SELECT MIN(rowid) FROM proactive_log WHERE uid=? AND kind=? AND content=? AND created=?)",
                (canon_uid, kind, content, created, canon_uid, kind, content, created),
            )
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)


def _merge_uid_relations(cur, report, alias_uid, canon_uid):
    """关系网：两列各换主 uid，按无序对去重、统一方向、自环删掉。"""
    # 关系网
    try:
        cur.execute("UPDATE OR IGNORE relation_network SET a_uid=? WHERE a_uid=?", (canon_uid, alias_uid))
        cur.execute("UPDATE OR IGNORE relation_network SET b_uid=? WHERE b_uid=?", (canon_uid, alias_uid))
        # OR IGNORE 遇到已存在的同一对关系会跳过，残留的别名行要显式删掉
        cur.execute("DELETE FROM relation_network WHERE a_uid=? OR b_uid=?", (alias_uid, alias_uid))
        # 两列各自 UPDATE 会把「a=friend,b=别名」变成「a=friend,b=主uid」，
        # 与另一条「a=主uid,b=friend」方向相反 → 同一条关系留两份。
        # 先按「无序对」去重（换方向时会撞唯一索引，必须先去重再换），再统一方向。
        cur.execute(
            "DELETE FROM relation_network WHERE rowid NOT IN (SELECT MIN(rowid) FROM relation_network "
            "GROUP BY MIN(a_uid, b_uid), MAX(a_uid, b_uid))"
        )
        cur.execute("UPDATE OR IGNORE relation_network SET a_uid=b_uid, b_uid=a_uid WHERE a_uid > b_uid")
        cur.execute("DELETE FROM relation_network WHERE a_uid=b_uid")
        cur.execute(
            "DELETE FROM relation_network WHERE rowid NOT IN "
            "(SELECT MIN(rowid) FROM relation_network GROUP BY a_uid, b_uid)"
        )
        report["relations"] += 1
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)


def _merge_uid_kv(cur, report, alias_uid, canon_uid, to_num):
    """kv：通道记录合并成一份，其余带别名的键改名。"""
    # kv：通道记录要合并成一份，其余带别名的键改名
    try:
        from .amo_state_store import delete_row as _kv_delete_row, keys_of as _kv_keys_of
        from .amo_state_store import put_row as _kv_put_row, rename_row as _kv_rename_row
        from .amo_state_store import value_row as _kv_value_row
    except ImportError:
        from amo_state_store import delete_row as _kv_delete_row, keys_of as _kv_keys_of
        from amo_state_store import put_row as _kv_put_row, rename_row as _kv_rename_row
        from amo_state_store import value_row as _kv_value_row
    try:
        old_key = "channels:%s" % alias_uid
        new_key = "channels:%s" % canon_uid
        old_ch = _kv_value_row(cur, old_key)
        new_ch = _kv_value_row(cur, new_key)

        def load(raw):
            try:
                return json.loads(raw[0] or "{}") if raw else {}
            except Exception as _amo_exc:
                _note_swallowed("amo_shared.merge_uid_alias_data.load", _amo_exc)
                return {}

        merged_ch = load(new_ch) if isinstance(load(new_ch), dict) else {}
        alias_ch = load(old_ch) if isinstance(load(old_ch), dict) else {}
        for platform, value in alias_ch.items():
            if not isinstance(value, dict):
                continue
            current = merged_ch.get(platform)
            if not isinstance(current, dict) or to_num(value.get("ts")) > to_num(current.get("ts")):
                merged_ch[platform] = value
        if merged_ch:
            _kv_rename_row(cur, old_key, new_key)
            _kv_delete_row(cur, old_key)
            _kv_put_row(cur, new_key, merged_ch)
            report["kv"] += 1
        else:
            _kv_delete_row(cur, old_key)
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)
    try:
        # 用 Python 侧的包含判断，避免 LIKE 里 '_' 被当成通配符误伤别的键
        for key in _kv_keys_of(cur):
            if alias_uid not in key:
                continue
            new = key.replace(alias_uid, canon_uid)
            if new == key:
                continue
            _kv_rename_row(cur, key, new)
            _kv_delete_row(cur, key)
            report["kv"] += 1
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)


