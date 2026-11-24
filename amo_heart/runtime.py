# -*- coding: utf-8 -*-
"""鱼之心运行时：载入 / 存回 / 回落 / 注入（IO 只在这里）。"""
from __future__ import annotations

import logging

logger = logging.getLogger("amo.amo_heart_runtime")

try:
    from ..clock import now_ts
except ImportError:
    from clock import now_ts

from .inject import block


def ensure(plugin):
    """取到插件的心；第一次用时从 kv 恢复，之后直接复用。"""
    heart = getattr(plugin, "heart", None)
    if heart is None:
        return None
    if not getattr(plugin, "_heart_loaded", False):
        try:
            saved = plugin.db.kv_get("heart_state", None)
            if isinstance(saved, dict):
                heart.state.update(saved)
        except Exception as exc:
            logger.debug("[amo_heart.runtime.ensure] optional step failed: %s", exc)
        plugin._heart_loaded = True
    return heart


def save(plugin, heart=None):
    heart = heart if heart is not None else getattr(plugin, "heart", None)
    if heart is None:
        return False
    try:
        plugin.db.kv_set("heart_state", dict(heart.state))
        return True
    except Exception:
        return False


def score_state(state):
    """九维合成的心情指数；不是九维均分。"""
    state = state or {}
    def delta(key, base):
        return float(state.get(key, base) or 0) - base
    raw = (50
           + delta("joy", 50) * 0.6
           + delta("attachment", 45) * 0.25
           + delta("intimacy", 40) * 0.25
           + delta("curiosity", 55) * 0.15
           - delta("stress", 15) * 0.5
           - delta("fatigue", 20) * 0.4)
    return max(0.0, min(100.0, raw))


def snapshot_day(plugin, day="", heart=None, force=False):
    """每天 22:00 后存一份九维快照，给七日趋势用；当天只写一次，之后冻结。"""
    heart = heart if heart is not None else ensure(plugin)
    if heart is None:
        return None
    import time as _time
    stamp = now_ts() if not day else None
    local = _time.localtime(stamp) if stamp is not None else None
    if not force and local is not None and int(local.tm_hour) < 22:
        return None
    day = str(day or _time.strftime("%Y-%m-%d", local if local is not None else _time.localtime()))
    key = "heart_daily:" + day
    if not force:
        try:
            existing = plugin.db.kv_get(key, None)
            if isinstance(existing, dict) and existing.get("score") is not None:
                return existing
        except Exception as exc:
            logger.debug("[amo_heart.runtime.snapshot_day] optional step failed: %s", exc)
    state = dict(getattr(heart, "state", {}) or {})
    dims = {name: round(float(state.get(name, 0) or 0), 1) for name in
            ("attachment", "curiosity", "reflection", "duty", "social",
             "fatigue", "intimacy", "stress", "joy")}
    data = {"day": day, "dims": dims, "score": round(score_state(dims), 1),
            "dominant": str(heart.dominant_mood() or ""),
            "updated": float(state.get("updated") or now_ts())}
    try:
        plugin.db.kv_set(key, data)
    except Exception:
        return None
    return data


def observe(plugin, text, minutes=0.0):
    heart = ensure(plugin)
    if heart is None:
        return None
    heart.observe(text or "", minutes)
    save(plugin, heart)
    snapshot_day(plugin, heart=heart)
    return heart


def tick(plugin, now=None):
    """距上次回落超过 30 分钟才按真实流逝时间回落一次。"""
    heart = ensure(plugin)
    if heart is None:
        return None
    now = now_ts() if now is None else float(now)
    try:
        elapsed = now - float(heart.state.get("updated") or 0)
    except (TypeError, ValueError):
        elapsed = 0.0
    if elapsed > 1800:
        heart.tick(elapsed / 60.0)
        heart.state["updated"] = now
        save(plugin, heart)
        snapshot_day(plugin, heart=heart)
    return heart


def prompt_block(plugin, thought=""):
    heart = ensure(plugin)
    if heart is None:
        return ""
    enabled = True
    cfg = getattr(plugin, "cfg_bool", None)
    if callable(cfg):
        try:
            enabled = bool(cfg("heart_enabled", True))
        except Exception:
            enabled = True
    if not enabled:
        return ""
    return block(heart.state, thought or getattr(heart, "thought", ""))
