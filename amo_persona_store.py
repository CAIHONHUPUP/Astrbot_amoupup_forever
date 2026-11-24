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
import re
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


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def ensure_profile(plugin, profile_id=DEFAULT_ID) -> str:
    pid = str(profile_id or DEFAULT_ID)
    if not _SAFE_ID.fullmatch(pid):
        return ""
    folder = _profile_dir(plugin, pid)
    if not folder:
        return ""
    root = os.path.abspath(_root(plugin))
    if os.path.commonpath([root, os.path.abspath(folder)]) != root:
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
    if not pid or not _SAFE_ID.fullmatch(pid):
        return {"ok": False, "reason": "invalid", "error": "id 只能字母数字_-，最长 32"}
    if pid in profiles(plugin):
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
    # 2026-11-24 A 阶段：旧 state.json 没有这些字段时自动补默认（不覆盖已有值）。
    state.setdefault("pending_review", None)
    state.setdefault("style_pool", [])
    state.setdefault("style_pending", [])
    state.setdefault("style_archive", [])
    state.setdefault("last_used", {})
    state.setdefault("used_seq", 0)
    state.setdefault("evolve_stats", {})
    state.setdefault("evolve_log", [])
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


_PACK_CACHE: dict = {}


def _file_stamp(path):
    try:
        stat = os.stat(path)
        return (int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        return (0, 0)


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
    pid = active_id(plugin)
    folder = ensure_profile(plugin, pid)
    key = (folder, pid)
    stamp = (_file_stamp(os.path.join(folder, "pack.json")),
             _file_stamp(os.path.join(folder, "state.json")))
    cached = _PACK_CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    data = effective_pack(plugin, pid)
    _PACK_CACHE[key] = (stamp, data)
    return data


def add_candidate(plugin, text, scene="", mood="", profile_id="") -> dict:
    """手动塞一条（/人设 候选加）。

    2026-11-24 起和自动学习合并：都先进「待通过」，只有一条审批流水线。
    """
    body = str(text or "").strip()[:80]
    if not body:
        return {"ok": False, "reason": "empty"}
    pid = str(profile_id or active_id(plugin))
    state = load_state(plugin, pid)
    rows = list(state.get("style_pending") or [])
    if any(str(row.get("text") or "") == body for row in rows):
        return {"ok": True, "duplicate": True}
    snapshot(plugin, pid, "before_candidate")
    rows.append({"text": body, "scene": str(scene or "general"),
                 "mood": str(mood or ""), "at": int(_amo_clock.now_ts()),
                 "intent": "", "score": 1.0, "last_used": 0,
                 "decayed_days": 0, "source": "manual"})
    state["style_pending"] = rows[-20:]
    save_state(plugin, pid, state)
    return {"ok": True, "candidates": len(state["style_pending"])}


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

def mark_used(plugin, texts, profile_id="") -> bool:
    """记 last_used（语料防连续复用）；顺手给池里被抽中的句子 +1 分。"""
    pid = str(profile_id or active_id(plugin))
    state = load_state(plugin, pid)
    now = int(_amo_clock.now_ts())
    used = dict(state.get("last_used") or {})
    wanted = {str(text or "") for text in (texts or []) if str(text or "")}
    if not wanted:
        return False
    for text in wanted:
        used[text] = now
    if len(used) > 200:
        for text, _at in sorted(used.items(), key=lambda item: item[1])[:len(used) - 200]:
            used.pop(text, None)
    state["last_used"] = used
    try:
        seq = int(state.get("used_seq") or 0) + 1
    except (TypeError, ValueError):
        seq = 1
    state["used_seq"] = seq
    for row in state.get("style_pool") or []:
        if isinstance(row, dict) and str(row.get("text") or "") in wanted:
            try:
                row["score"] = float(row.get("score", 1.0) or 1.0) + 1.0
            except (TypeError, ValueError):
                row["score"] = 2.0
            row["last_used"] = now
            # 同一秒连续几轮也不会并列：用递增序号轮换，避免高分句霸屏。
            row["used_seq"] = seq
    return save_state(plugin, pid, state)


def restore_snapshot(plugin, name="", profile_id="") -> dict:
    """恢复到指定快照；name 为空时取最新一份。返回 {"ok", "file"}。"""
    pid = str(profile_id or active_id(plugin))
    state = load_state(plugin, pid)
    snaps = [row for row in (state.get("snapshots") or []) if isinstance(row, dict)]
    chosen = None
    target = str(name or "")
    if target:
        chosen = next((row for row in reversed(snaps) if str(row.get("file") or "") == target), None)
    elif snaps:
        chosen = snaps[-1]
    if not chosen:
        return {"ok": False, "reason": "no_snapshot"}
    folder = ensure_profile(plugin, pid)
    path = os.path.join(folder, "snapshots", str(chosen.get("file") or ""))
    data = _read_json(path, {})
    if not isinstance(data, dict) or not data:
        return {"ok": False, "reason": "snapshot_missing"}
    if not save_state(plugin, pid, data):
        return {"ok": False, "reason": "save_failed"}
    return {"ok": True, "file": str(chosen.get("file") or "")}
