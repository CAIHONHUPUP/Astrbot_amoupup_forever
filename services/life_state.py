# -*- coding: utf-8 -*-
"""跨插件 LifeStateSnapshot（v1）：能陪生成，能记/守护/定格只读。

为什么（task-27，来自 work/audit/cross_plugin.md P0-2）：

四个插件各自维护「此刻在哪 / 和谁 / 钱包多少 / 正在买什么 / 今天穿什么」，
于是出现同一时刻三套事实：

- 她说「卡里还有 4072」（能陪 kv shopping_prompt 里的旧快照），
  守护 shop:events 里 cash=3086.54（另一份旧快照）；
- 她说「在商场试开衫」，当天日程 09:00-11:00 是「在家歇着」。

这里把只读状态收敛成一份带 contract/version/source/generated_at 的快照：

- 生产者：能陪（本模块，唯一写者）；
- 消费者：守护/能记/定格只能读 life_state_snapshot()，禁止再各自存副本；
- 字段只增不改；某项拿不到给 None/{} 并记进 degraded，绝不抛异常；
- consistency 给出跨字段自检（例如 shopping 文案里的余额 != 钱包实时余额），
  消费者/面板据此标记「陈旧」，而不是继续把旧数字说出去。

契约测试：forever/tests/test_life_state_snapshot.py、
watch/tests/test_life_state_contract.py（消费端容错）。
"""
from __future__ import annotations

from typing import Any

import datetime as _dt
import re

CONTRACT_NAME = "amo-life-state"
CONTRACT_VERSION = 1
SOURCE = "forever"

#: v1 字段（消费者按这个列表判完整度；新增字段只追加，不改名/不删）
REQUIRED_FIELDS = (
    "contract", "version", "source", "generated_at", "day",
    "principal", "location", "current_action", "schedule",
    "date_session", "shopping", "wallet", "outfit", "mood", "media",
    "consistency", "degraded",
)

#: shopping_prompt.line 里被写死的数字（「价格 985 元」「卡里余额 4072 元」）
_PRICE_RE = re.compile(r"(?:价格|售价)\s*(\d+(?:\.\d+)?)\s*元")
_BALANCE_RE = re.compile(r"(?:余额|卡里|卡上)\D{0,6}(\d+(?:\.\d+)?)\s*元")
#: 同一价格/余额允许的误差（分）：写死的是整元文本，只做粗粒度陈旧检测
_CENTS_TOLERANCE = 100


def contract_descriptor():
    """契约自述：版本、字段、单位，给消费端/面板/测试用（纯常量，不读库）。"""
    return {
        "contract": CONTRACT_NAME,
        "version": CONTRACT_VERSION,
        "source": SOURCE,
        "fields": list(REQUIRED_FIELDS),
        "units": {"cents": "整数分", "generated_at": "epoch 秒", "day": "YYYY-MM-DD"},
        "readonly": True,
    }


def _now(plugin, now=None):
    if now is not None:
        try:
            return now if isinstance(now, _dt.datetime) else _dt.datetime.fromtimestamp(float(now))
        except (TypeError, ValueError):  # cleanup: 兜底/降级，保持原行为
            pass
    try:
        try:
            from .. import clock as _clock
        except ImportError:
            import clock as _clock
        return _clock.now()
    except Exception:
        return _dt.datetime.now()


def _ts(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class _Collector:
    """逐字段 try：任何一项失败只进 degraded，快照整体仍然可用。"""

    def __init__(self):
        self.degraded = []

    def take(self, field, producer, default):
        try:
            return producer()
        except Exception as exc:
            self.degraded.append({"field": field, "reason": "%s: %s" % (type(exc).__name__, exc)})
            return default


def _principal(plugin):
    """她和他之间的稳定身份：owner 私聊 umo/uid；没有就空（消费者按不可用处理）。"""
    umos = []
    getter = getattr(plugin, "_owner_umos", None)
    if callable(getter):
        getter_result = getter()
        if isinstance(getter_result, (list, tuple)):
            umos = [str(item) for item in getter_result if str(item or "").strip()]
    if not umos:
        try:
            rows = list(plugin.db.proactive_profiles(limit=1) or [])
        except Exception:
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ("private_umo", "last_group"):
                umo = str(row.get(key) or "").strip()
                if umo:
                    umos.append(umo)
            if umos:
                break
    umo = umos[0] if umos else ""
    uid = umo.rsplit(":", 1)[-1] if ":" in umo else umo
    platform = umo.split(":", 1)[0] if umo else ""
    scope = "group" if ":GroupMessage:" in umo else "private"
    return {"umo": umo, "uid": uid, "platform": platform, "scope": scope, "kind": "owner"}


def _location(plugin):
    """唯一场景源 amo_presence.current_scene（scene_ledger 钳制后的结果）。"""
    try:
        from .. import amo_presence as _presence
    except ImportError:
        import amo_presence as _presence
    scene = dict(_presence.current_scene(plugin) or {})
    scene["at_home"] = bool(_presence.at_home(scene))
    scene["in_shop"] = bool(getattr(_presence, "_in_shop")(scene))
    return scene


def _current_action(plugin):
    try:
        from .. import amo_current_action as _action
    except ImportError:
        import amo_current_action as _action
    return dict(_action.get_current_action(plugin) or {})


def _schedule(plugin, day):
    """当前日程块 + 取到它的原因 + 下一块；拿不到就是空（不猜）。"""
    out = {"current": {}, "reason": "", "next": {}, "day": day}
    state = getattr(plugin, "state", None)
    if state is None:
        return out
    info = None
    getter = getattr(state, "current_activity_info", None)
    if callable(getter):
        info = getter()
    if isinstance(info, tuple) and len(info) == 2:
        block, reason = info
        out["current"] = dict(block) if isinstance(block, dict) else {}
        out["reason"] = str(reason or "")
    elif isinstance(info, dict):
        out["current"] = dict(info)
    # 下一块：只读 schedule 表（同一天、开始分钟 > 现在）
    try:
        items = list(state.schedule.get(day) or [])
    except Exception:
        items = []
    current = out["current"]
    current_start = str(current.get("start") or "")[:5]
    later = [item for item in items if isinstance(item, dict)
             and str(item.get("start") or "")[:5] > current_start]
    if later:
        out["next"] = dict(later[0])
    return out


def _date_session(plugin, now, collector):
    """约会会话：canonical kv（date_session）优先，缺省时只读投影旧键。"""
    try:
        from .. import amo_date_session as _session
    except ImportError:
        import amo_date_session as _session
    data = dict(_session.load(plugin, now, settle=False) or {})
    canonical = {}
    try:
        canonical = dict(plugin.db.kv_get(_session.KV, {}) or {})
    except Exception:
        canonical = {}
    return {
        "canonical_present": bool(canonical.get("phase")),
        "phase": str(data.get("phase") or ""),
        "title": str(data.get("title") or ""),
        "place": str(data.get("place") or ""),
        "together": bool(data.get("together")),
        "line": str(data.get("line") or ""),
        "start_ts": _ts(data.get("start_ts")),
        "end_ts": _ts(data.get("end_ts")),
        "day": str(data.get("day") or ""),
        "schema_version": _int(data.get("schema_version")),
        "source": "date_session" if canonical.get("phase") else "legacy_projection",
    }


def _shopping(plugin, now_ts, wallet_balance_cents):
    """正在逛街试衣 pending：保留原文，但把写死的数字和实时钱包对齐并标记陈旧。"""
    try:
        from .. import amo_presence as _presence
    except ImportError:
        import amo_presence as _presence
    raw = dict(_presence.shopping_pending(plugin, now_ts) or {})
    line = str(raw.get("line") or "")
    summary = {"active": bool(line), "line": line,
               "until": _ts(raw.get("until")), "source": "shopping_prompt"}
    price_match = _PRICE_RE.search(line)
    balance_match = _BALANCE_RE.search(line)
    mentioned_price = _int(round(float(price_match.group(1)) * 100)) if price_match else 0
    mentioned_balance = _int(round(float(balance_match.group(1)) * 100)) if balance_match else 0
    summary["mentioned_price_cents"] = mentioned_price
    summary["mentioned_balance_cents"] = mentioned_balance
    summary["wallet_balance_cents"] = int(wallet_balance_cents or 0)
    stale = False
    if mentioned_balance and wallet_balance_cents:
        # 文案里是整元（cash // 100），和实时余额按整元对齐后再比，避免 0.x 元误报
        written_cents = (int(wallet_balance_cents) // 100) * 100
        stale = abs(written_cents - mentioned_balance) > _CENTS_TOLERANCE
    summary["line_stale"] = stale
    return summary


def _wallet(plugin, now):
    """钱包 + 本月消费（消费口径见 services/metrics.py，单一定义）。"""
    wallet = getattr(plugin, "wallet", None)
    out = {"balance_cents": 0, "withdrawable_cents": 0, "month": "",
           "month_consumption_cents": 0, "source": "wallet_ledger"}
    if wallet is None:
        return out
    out["balance_cents"] = _int(wallet.balance("cash"))
    out["withdrawable_cents"] = _int(wallet.balance("withdrawable"))
    try:
        from . import metrics as _metrics
    except ImportError:
        from services import metrics as _metrics
    month = _metrics.month_of(now)
    out["month"] = month
    out["month_consumption_cents"] = _int(_metrics.wallet_month_consumption_cents(wallet, month))
    return out


def _outfit(plugin, now):
    """今天白天穿搭（不含睡衣）：走 amo_fact_timeline.read_outfit 只读投影。"""
    try:
        from ..amo_fact_timeline import read_outfit
    except ImportError:
        from amo_fact_timeline import read_outfit
    day = now.date()
    text = str(read_outfit(plugin, day) or "").strip()
    return {"day": day.isoformat(), "text": text, "source": "wardrobe"}


def _mood(plugin):
    state = getattr(plugin, "state", None)
    out: dict[str, Any] = {"score": None, "words": []}
    if state is None:
        return out
    try:
        out["score"] = int(state.mood_score())
    except Exception:
        out["score"] = None
    try:
        out["words"] = [str(item) for item in (state.mood_words() or [])]
    except Exception:
        out["words"] = []
    return out


def _media(plugin, day):
    """今日照片：媒体账本（amo_media.photo_count）是唯一口径。"""
    out = {"photos_today": 0, "source": "media_ledger"}
    try:
        from .. import amo_media as _media_module
    except ImportError:
        import amo_media as _media_module
    getter = getattr(_media_module, "photo_count", None)
    if callable(getter):
        out["photos_today"] = _int(getter(plugin, day))
    return out


def _consistency(snapshot):
    """跨字段自检：只报「同一事实两个值」，不改数值。"""
    issues = []
    shopping = snapshot.get("shopping") or {}
    if shopping.get("line_stale"):
        issues.append({
            "code": "shopping_balance_stale",
            "detail": "shopping_prompt 写死余额 %s 分，钱包实时 %s 分"
                      % (shopping.get("mentioned_balance_cents"),
                         snapshot.get("wallet", {}).get("balance_cents", 0)),
        })
    scene = snapshot.get("location") or {}
    action = snapshot.get("current_action") or {}
    scene_place = str(scene.get("place") or "")
    action_place = str(action.get("place") or "")
    if scene_place and action_place and scene_place != action_place:
        issues.append({"code": "location_split",
                       "detail": "scene=%s current_action=%s" % (scene_place, action_place)})
    date_sess = snapshot.get("date_session") or {}
    if date_sess.get("together") and not date_sess.get("canonical_present"):
        issues.append({"code": "date_session_canonical_missing",
                       "detail": "旧键里有约会状态，canonical date_session 不存在"})
    return {"ok": not issues, "issues": issues}


def build_snapshot(plugin, now=None):
    """构建只读快照：不写 kv/DB/文件；任何字段失败只降级。

    返回 dict（字段见 REQUIRED_FIELDS）；调用方禁止修改后回写。
    """
    moment = _now(plugin, now)
    now_ts = _ts(moment.timestamp())
    day = moment.date().isoformat()
    collector = _Collector()
    principal = collector.take("principal", lambda: _principal(plugin),
                               {"umo": "", "uid": "", "platform": "", "scope": "private", "kind": ""})
    location = collector.take("location", lambda: _location(plugin), {})
    action = collector.take("current_action", lambda: _current_action(plugin), {})
    schedule = collector.take("schedule", lambda: _schedule(plugin, day),
                              {"current": {}, "reason": "", "next": {}, "day": day})
    date_session = collector.take("date_session", lambda: _date_session(plugin, moment, collector), {})
    wallet = collector.take("wallet", lambda: _wallet(plugin, moment),
                            {"balance_cents": 0, "withdrawable_cents": 0, "month": "",
                             "month_consumption_cents": 0, "source": "wallet_ledger"})
    wallet_balance = _int((wallet or {}).get("balance_cents"))
    shopping = collector.take("shopping", lambda: _shopping(plugin, now_ts, wallet_balance),
                              {"active": False, "line": "", "until": 0.0,
                               "line_stale": False})
    outfit = collector.take("outfit", lambda: _outfit(plugin, moment),
                            {"day": day, "text": "", "source": "wardrobe"})
    mood = collector.take("mood", lambda: _mood(plugin), {"score": None, "words": []})
    media = collector.take("media", lambda: _media(plugin, day),
                           {"photos_today": 0, "source": "media_ledger"})
    snapshot = {
        "contract": CONTRACT_NAME,
        "version": CONTRACT_VERSION,
        "source": SOURCE,
        "generated_at": now_ts,
        "day": day,
        "principal": principal,
        "location": location,
        "current_action": action,
        "schedule": schedule,
        "date_session": date_session,
        "shopping": shopping,
        "wallet": wallet,
        "outfit": outfit,
        "mood": mood,
        "media": media,
        "consistency": {"ok": True, "issues": []},
        "degraded": collector.degraded,
    }
    try:
        snapshot["consistency"] = _consistency(snapshot)
    except Exception as exc:  # 自检失败也不算快照失败
        snapshot["degraded"].append({"field": "consistency",
                                     "reason": "%s: %s" % (type(exc).__name__, exc)})
    return snapshot


def compact(snapshot, fields=()):
    """给日志/推送用的裁剪版：只留调用方需要的字段（契约不变形）。"""
    data = snapshot if isinstance(snapshot, dict) else {}
    keep = tuple(fields) or ("generated_at", "day", "location", "current_action",
                             "shopping", "wallet", "media")
    out = {"contract": CONTRACT_NAME, "version": CONTRACT_VERSION}
    for key in keep:
        if key in data:
            out[key] = data[key]
    return out
