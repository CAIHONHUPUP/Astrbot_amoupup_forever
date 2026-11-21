# -*- coding: utf-8 -*-
"""人格档案与进化状态（2026-11-21）。

- 每个 profile 一套 pack.json（基础人设，只读）+ state.json（进化增量）；
- active_profile 只记录当前用哪套，切换不覆盖、不删状态；
- 切走不后台学习，切回继续用原 state；
- 进化只写 state：corpus_evolved / weights / candidates；
- 共享关系记忆不在这里，由记忆库按 bot_id 保留。
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Any

try:
    from . import clock as _amo_clock
except ImportError:
    import clock as _amo_clock

try:
    from .amo_persona_pack import DEFAULT_PACK_PATH, default_pack, validate
except ImportError:
    from amo_persona_pack import DEFAULT_PACK_PATH, default_pack, validate

DEFAULT_ID = "gentle-playful"
MAX_STATE_CORPUS = 100
MAX_SNAPSHOTS = 10


def _root(plugin) -> str:
    try:
        db_path = str(getattr(getattr(plugin, "db", None), "path", "") or "")
        base = os.path.dirname(os.path.abspath(db_path)) if db_path else ""
    except Exception:
        base = ""
    if not base:
        return ""
    return os.path.join(base, "persona_profiles")


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def _profile_dir(plugin, profile_id):
    root = _root(plugin)
    if not root:
        return ""
    return os.path.join(root, str(profile_id or DEFAULT_ID))


def ensure_profile(plugin, profile_id=DEFAULT_ID) -> str:
    folder = _profile_dir(plugin, profile_id)
    if not folder:
        return ""
    os.makedirs(folder, exist_ok=True)
    pack_path = os.path.join(folder, "pack.json")
    if not os.path.isfile(pack_path):
        if str(profile_id) == DEFAULT_ID and os.path.isfile(DEFAULT_PACK_PATH):
            shutil.copy2(DEFAULT_PACK_PATH, pack_path)
        else:
            _write_json(pack_path, default_pack())
    state_path = os.path.join(folder, "state.json")
    if not os.path.isfile(state_path):
        _write_json(state_path, {"version": 1, "corpus_evolved": [], "weights": {},
                                 "candidates": [], "snapshots": []})
    return folder


def profiles(plugin) -> list[str]:
    root = _root(plugin)
    if not root:
        return [DEFAULT_ID]
    ensure_profile(plugin, DEFAULT_ID)
    try:
        names = [name for name in os.listdir(root)
                 if os.path.isdir(os.path.join(root, name))
                 and os.path.isfile(os.path.join(root, name, "pack.json"))]
    except Exception:
        names = []
    return names or [DEFAULT_ID]


def active_id(plugin) -> str:
    try:
        value = str(plugin.db.kv_get("persona_active", DEFAULT_ID) or DEFAULT_ID)
    except Exception:
        value = DEFAULT_ID
    if value in profiles(plugin):
        return value
    return DEFAULT_ID


def import_pack(plugin, data) -> dict:
    """导入人设包：id 冲突拒绝，不自动切换。"""
    try:
        validate(data)
    except Exception as exc:
        return {"ok": False, "reason": "invalid", "error": str(exc)[:120]}
    pid = str(data.get("id") or "").strip()
    if not pid or pid in profiles(plugin):
        return {"ok": False, "reason": "exists", "profiles": profiles(plugin)}
    folder = ensure_profile(plugin, pid)
    if not folder:
        return {"ok": False, "reason": "no_data_dir"}
    _write_json(os.path.join(folder, "pack.json"), data)
    return {"ok": True, "id": pid}


def switch(plugin, profile_id) -> dict:
    pid = str(profile_id or "").strip()
    if pid not in profiles(plugin):
        return {"ok": False, "reason": "not_found", "profiles": profiles(plugin)}
    ensure_profile(plugin, pid)
    try:
        plugin.db.kv_set("persona_active", pid)
    except Exception as exc:
        return {"ok": False, "reason": "save_failed", "error": str(exc)[:80]}
    return {"ok": True, "active": pid}


def load_pack(plugin, profile_id="") -> dict:
    pid = str(profile_id or active_id(plugin))
    folder = ensure_profile(plugin, pid)
    data = _read_json(os.path.join(folder, "pack.json"), {})
    try:
        validate(data)
        return data
    except Exception:
        return default_pack()


def load_state(plugin, profile_id="") -> dict:
    pid = str(profile_id or active_id(plugin))
    folder = ensure_profile(plugin, pid)
    state = _read_json(os.path.join(folder, "state.json"), {})
    state.setdefault("version", 1)
    state.setdefault("corpus_evolved", [])
    state.setdefault("weights", {})
    state.setdefault("candidates", [])
    state.setdefault("snapshots", [])
    return state


def save_state(plugin, profile_id, state) -> bool:
    pid = str(profile_id or active_id(plugin))
    folder = ensure_profile(plugin, pid)
    if not folder:
        return False
    try:
        _write_json(os.path.join(folder, "state.json"), state)
        return True
    except Exception:
        return False


def snapshot(plugin, profile_id="", reason="") -> str:
    pid = str(profile_id or active_id(plugin))
    state = load_state(plugin, pid)
    snaps = list(state.get("snapshots") or [])
    name = "%d-%d.json" % (int(_amo_clock.now_ts()), len(snaps))
    folder = ensure_profile(plugin, pid)
    snap_dir = os.path.join(folder, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    _write_json(os.path.join(snap_dir, name), state)
    snaps.append({"file": name, "at": int(_amo_clock.now_ts()), "reason": str(reason or "")[:40]})
    state["snapshots"] = snaps[-MAX_SNAPSHOTS:]
    save_state(plugin, pid, state)
    return name


def effective_pack(plugin, profile_id="") -> dict:
    """基础包 + 已批准的进化语料（不改基础卡）。"""
    base = load_pack(plugin, profile_id)
    state = load_state(plugin, profile_id)
    corpus = list(base.get("corpus") or [])
    for row in state.get("corpus_evolved") or []:
        if isinstance(row, dict) and str(row.get("text") or "").strip():
            corpus.append(row)
    base["corpus"] = corpus[:MAX_STATE_CORPUS]
    return base


def active_pack(plugin) -> dict:
    return effective_pack(plugin, active_id(plugin))


def add_candidate(plugin, text, scene="", mood="", profile_id="") -> dict:
    body = str(text or "").strip()[:80]
    if not body:
        return {"ok": False, "reason": "empty"}
    pid = str(profile_id or active_id(plugin))
    state = load_state(plugin, pid)
    rows = list(state.get("candidates") or [])
    if any(str(row.get("text") or "") == body for row in rows):
        return {"ok": True, "duplicate": True}
    snapshot(plugin, pid, "before_candidate")
    rows.append({"text": body, "scene": str(scene or "general"),
                 "mood": str(mood or ""), "at": int(_amo_clock.now_ts())})
    state["candidates"] = rows[-20:]
    save_state(plugin, pid, state)
    return {"ok": True, "candidates": len(state["candidates"])}


def approve_candidate(plugin, index=0, profile_id="") -> dict:
    pid = str(profile_id or active_id(plugin))
    state = load_state(plugin, pid)
    rows = list(state.get("candidates") or [])
    try:
        row = rows.pop(int(index))
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "reason": "not_found"}
    snapshot(plugin, pid, "before_approve")
    evolved = list(state.get("corpus_evolved") or [])
    evolved.append(row)
    state["corpus_evolved"] = evolved[-MAX_STATE_CORPUS:]
    state["candidates"] = rows
    save_state(plugin, pid, state)
    return {"ok": True, "text": row.get("text"), "total": len(evolved)}
