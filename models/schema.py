# -*- coding: utf-8 -*-
"""数据模型与 CRUD：健康、生病记录、日程。纯函数，不依赖插件实例。"""
from __future__ import annotations

import time

HP_KEY = "health:hp"
SICK_KEY = "health:sick"
SICK_LOG_KEY = "health:sick_log"


def get_hp(db, default=100):
    try:
        return int(db.kv_get(HP_KEY, default) or default)
    except Exception:
        return int(default)


def update_hp(db, value):
    value = max(0, min(100, int(value)))
    db.kv_set(HP_KEY, value)
    return value


def get_sick(db):
    try:
        return bool(db.kv_get(SICK_KEY, False))
    except Exception:
        return False


def set_sick(db, sick, hours=0, reason=""):
    db.kv_set(SICK_KEY, bool(sick))
    db.kv_set("health:sick_since", time.time() if sick else 0)
    if hours:
        db.kv_set("health:sick_until", time.time() + 3600 * float(hours))
    if reason:
        add_sick_log(db, reason)
    return bool(sick)


def add_sick_log(db, reason, level="light"):
    rows = db.kv_get(SICK_LOG_KEY, []) or []
    rows.append({"at": time.time(), "reason": str(reason), "level": str(level)})
    db.kv_set(SICK_LOG_KEY, rows[-30:])
    return rows[-1]


def sick_log(db):
    return list(db.kv_get(SICK_LOG_KEY, []) or [])
