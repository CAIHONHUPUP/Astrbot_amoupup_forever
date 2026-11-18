# -*- coding: utf-8 -*-
"""群聊场景与双轨人设（2026-11-15）。

配置：group_scenes，每行「群号=work_group」或 JSON 字典，支持
private / work_group / friend_group / stranger_group。
群聊 Prompt 走“社交面具”，发送层再做私密词硬拦截；主动消息只走私聊。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import json
import re

try:  # 提示词注册表（Phase 4）
    from . import amo_prompt_registry as _prompts
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_prompt_registry as _prompts

SCENES = ("private", "work_group", "friend_group", "stranger_group")
DEFAULT_GROUP_SCENE = "stranger_group"
PRIVATE_WORDS = ("房贷", "车贷", "交租", "肚子好饿", "例假", "生理期", "呜呜呜", "老公",
                 "没钱", "余额不足", "生病", "工资", "转账")
SCENE_LIMIT = {"work_group": 15, "friend_group": 20, "stranger_group": 20, "private": 0}
SAFE_REPLY = {"work_group": "收到，我先忙啦", "friend_group": "哈哈，最近在攒钱呢",
              "stranger_group": "谢谢，不太方便说~", "private": ""}
REPLACEMENTS = (
    ("呜呜呜", ""), ("房贷", "攒钱"), ("车贷", "攒钱"), ("交租", "攒钱"),
    ("肚子好饿", "有点饿"), ("例假", "有点不舒服"), ("生理期", "有点不舒服"),
    ("没钱", "手头紧"), ("余额不足", "手头紧"), ("生病", "不太舒服"),
    ("转账", "心意"), ("工资", "收入"),
)


def parse_scenes(raw):
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            return {}
        return {str(k).strip(): str(v).strip() for k, v in data.items()
                if str(v).strip() in SCENES}
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value in SCENES:
            out[key.strip()] = value
    return out


def scene_for_event(plugin, event):
    try:
        group_id = str(event.get_group_id() or "").strip()
    except Exception:
        group_id = ""
    if not group_id:
        return "private"
    mapping = parse_scenes(plugin.cfg_str("group_scenes", ""))
    if group_id in mapping:
        return mapping[group_id]
    try:
        name = str(event.get_group_name() or "").strip()
    except Exception:
        name = ""
    return mapping.get(name, DEFAULT_GROUP_SCENE)


def is_group_umo(umo):
    return ":GroupMessage:" in str(umo or "")


def scene_for_umo(plugin, umo):
    text = str(umo or "")
    if not is_group_umo(text):
        return "private"
    group_id = text.split(":GroupMessage:", 1)[1].split(":", 1)[0]
    return parse_scenes(plugin.cfg_str("group_scenes", "")).get(group_id, DEFAULT_GROUP_SCENE)


def proactive_allowed(plugin, umo):
    """主动消息只允许私聊或熟人/闺蜜群；工作群、陌生人群直接丢弃。"""
    scene = scene_for_umo(plugin, umo) if is_group_umo(umo) else "private"
    return scene in ("private", "friend_group")


def owner_nickname(plugin):
    return str(plugin.cfg_str("owner_nickname", "") or "").strip() or "他"


def prompt_block(scene, sender_name="群友", owner_name="他", is_owner=False, mentioned=False):
    scene = scene if scene in SCENES else DEFAULT_GROUP_SCENE
    if scene == "private":
        return _prompts.render("group.scene.private")
    scene_block = {"work_group": "group.scene.work",
                   "friend_group": "group.scene.friend"}.get(scene, "group.scene.stranger")
    parts = [
        _prompts.render(scene_block),
        _prompts.render("group.scene.roster", sender_name=sender_name, owner_name=owner_name),
    ]
    if is_owner:
        parts.append(_prompts.render("group.scene.owner", scene=scene))
    elif mentioned:
        parts.append(_prompts.render("group.scene.mentioned"))
    else:
        parts.append(_prompts.render("group.scene.silent"))
    return "\n".join(parts)


def guard_group_reply(text, scene, owner_name="他"):
    """发送层硬拦截：私密词替换；仍不干净就换成得体兜底；超长截断。"""
    body = str(text or "").strip()
    if scene == "private" or not body:
        return body, False
    changed = False
    for word, repl in REPLACEMENTS:
        if word in body:
            body = body.replace(word, repl)
            changed = True
    body = body.replace("老公", owner_name or "他")
    body = re.sub(r"[ \t]+", " ", body).strip(" ，,。")
    if any(word in body for word in PRIVATE_WORDS):
        body = SAFE_REPLY.get(scene) or "哈哈"
        changed = True
    limit = SCENE_LIMIT.get(scene, 20)
    if limit and len(body) > limit:
        cut = body[:limit]
        for sep in ("。", "！", "？", "~", "，"):
            if sep in cut:
                cut = cut.split(sep)[0]
                break
        body = cut or body[:limit]
        changed = True
    return body, changed


# ---- 三个拟真彩蛋（并入本模块，不再新增独立模块）----
# -*- coding: utf-8 -*-
"""最后三个拟真彩蛋（2026-11-15）：转账推拉、花钱隐瞒、选择性遗忘。

只做 Prompt 事实注入，不改钱包/记忆底层：
- transfer: 最近收到 520+ 转账且她手里有钱 → 想退回去
- purchase: 今天买了贵东西，被问“花了多少/账单” → 心虚想少报
- forgiveness: 记忆里有争吵/被凶，但今天他哄她/转账 → 可以嘴硬心软地原谅
"""

import datetime as _dt
import random

def _kv(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception:
        return default

def _recent(data, seconds, now):
    try:
        return now - float(data.get("ts") or 0) <= seconds
    except Exception:
        return False

def micro_prompt_block(plugin, text, memory_text="", now=None, rng=None):
    now = (now or _amo_clock.now()).timestamp()
    rng = rng or random
    body = str(text or "")
    parts = []
    transfer = _kv(plugin, "transfer:last", {}) or {}
    if isinstance(transfer, dict) and _recent(transfer, 15 * 60, now):
        try:
            amount = int(transfer.get("amount") or 0)
            balance = int(plugin.wallet.balance())
        except Exception:
            amount, balance = 0, 0
        if amount >= 52000 and balance >= 100000 and rng.random() < 0.15:
            parts.append("【转账推拉】老公给你发了 %.0f 元，但你想到他最近手头也紧，"
                         "你想退回去；可以说“心意我领啦，这钱你留着买点好吃的，我自己有钱~”。"
                         % (amount / 100.0))
    purchase = _kv(plugin, "last_purchase", {}) or {}
    if isinstance(purchase, dict) and _recent(purchase, 24 * 3600, now):
        try:
            price = int(purchase.get("price") or 0)
        except Exception:
            price = 0
        if price >= 30000 and any(word in body for word in ("花了多少", "花了多少钱", "账单", "买了什么", "花哪了")):
            parts.append("【花钱心虚】你今天买了%s，花了 %.0f 元，但怕老公说你，"
                         "想瞒着他，说只花了两百，或者说是打折买的；别主动全交代。"
                         % (str(purchase.get("name") or "东西"), price / 100.0))
    if memory_text and any(word in str(memory_text) for word in ("凶", "生气", "吵架", "委屈")):
        positive = _kv(plugin, "positive:last", 0)
        try:
            positive = float(positive or 0)
        except Exception:
            positive = 0
        if now - positive <= 6 * 3600 or any(word in body for word in ("乖", "哄", "对不起", "原谅")):
            parts.append("【选择性遗忘】你记得前两天他惹你生气，但看在他今天这么乖的份上，"
                         "可以嘴硬心软地说“哼，虽然你前两天惹我生气，但看在你今天这么乖的份上，我就原谅你啦~”；"
                         "不要主动翻旧账、不要越说越气。")
    return "\n".join(parts)