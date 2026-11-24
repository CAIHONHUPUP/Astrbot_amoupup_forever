# -*- coding: utf-8 -*-
"""她自己的例句池（docs/persona_self_evolve.md L1，2026-11-24 A 阶段）。

A 阶段只做「采集 → 过滤 → 入池 → 注入 → 面板可见」：
- 只学私聊、只学聊天回复（非主动/系统/照片语音）、发送成功才暂存；
- 下一轮按他的反应判定正/负/中性，正向才入池；
- 固定 33 条例句池永远是兜底，空池不空窗；
- 只入池、不写 candidates（B1 才写）；自动通过默认关（B2 才做）。

所有判定都是规则，不新增每轮 LLM 调用。
"""
from __future__ import annotations

import difflib
import re
import time

try:
    from . import clock as _clock
    from . import amo_persona_pack as _pack
    from . import amo_persona_store as _store
except ImportError:
    if not __package__:  # pragma: no cover - 直接按文件加载
        import sys
        sys.path.insert(0, __file__.rsplit("\\", 1)[0])
    import clock as _clock
    import amo_persona_pack as _pack
    import amo_persona_store as _store

#: docs §5：正向/负向词表（只做规则，不调模型）。
POSITIVE_WORDS = ("哈哈", "笑死", "可爱", "喜欢你", "亲", "抱", "乖", "好呀", "嗯嗯", "真棒")
NEGATIVE_WORDS = ("滚", "烦", "闭嘴", "无语", "随便", "别说了", "换一个", "重说", "不听")
COMMAND_WORDS = ("换一个", "重说", "别", "不要")
#: AI 腔 / 技术词（docs §6.2）。
TECH_WORDS = ("作为", "首先", "总之", "服务器", "模型", "接口", "系统提示", "人工智能",
              "语言模型", "token", "prompt")
#: 极端负向情绪：这种时候说的狠话不学。
EXTREME_MOODS = ("生气", "崩溃", "绝望", "愤怒", "伤心")
EXTREME_SCORE = 20
MEDIA_KINDS = ("photo", "image", "voice", "audio", "video", "media", "file", "record")
MEDIA_MARKS = ("[图片]", "[语音]", "[视频]", "[文件]", "[CQ:", "<img", "http://", "https://")
#: 2026-11-24：动作括号 `（…）` 是她说话的一部分，不算模板；只挡【】这类后台格式。
TEMPLATE_MARKS = ("【", "】")
PENDING_TTL = 600          # 10 分钟没反应 = 冷场，不学
RECENT_WINDOW = 1800       # 30 分钟内抽过的句子排到最后，防连续复用
PER_INTENT_DEFAULT = 6
TOTAL_CAP_DEFAULT = 30
DECAY_DAYS_DEFAULT = 30
DECAY_RATE = 0.98
ARCHIVE_SCORE = 0.5
MAX_LOG = 10
MAX_ARCHIVE = 100
_HAN = re.compile(r"[\u4e00-\u9fff]")


def _cfg_bool(plugin, key, default=False):
    try:
        return bool(plugin.cfg_bool(key, default))
    except Exception:
        return bool(default)


def _cfg_int(plugin, key, default):
    try:
        return int(plugin.cfg_int(key, default))
    except Exception:
        return int(default)


def auto_evolve_enabled(plugin) -> bool:
    """总开关（默认关；A 阶段观察采集质量时在面板/配置里手动打开）。"""
    return _cfg_bool(plugin, "persona_auto_evolve", False)


def auto_approve_enabled(plugin) -> bool:
    """自动通过（默认关；B2 才允许打开，代码只提供开关）。"""
    return _cfg_bool(plugin, "persona_auto_approve", False)


def _now() -> int:
    return int(_clock.now_ts())


def _day_key(ts) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(float(ts or 0)))


def han_count(text) -> int:
    return len(_HAN.findall(str(text or "")))


def normalize_text(text) -> str:
    """去标点/空白，只留汉字和字母数字，用于相似度。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(text or ""))


#: 动作描写括号（「（扒了口饭，看着手机）嗯，去吧。」）——只学她嘴里说的那句。
_NARRATION = re.compile(r"[（(][^（()）\n]{1,80}[)）]")
#: 纯语气字：整句都是这些，就不值得学。
_FILLER = set("嗯哦啊呀哈呃唔诶欸吧呢嘛啦")


def spoken_text(text) -> str:
    """去掉动作/旁白括号，只留她说出口的部分。"""
    body = _NARRATION.sub("", str(text or ""))
    return re.sub(r"\s+", " ", body).strip()


def similarity(a, b) -> float:
    left, right = normalize_text(a), normalize_text(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def is_private_umo(umo, group=False) -> bool:
    """群聊/私聊判定：优先看 umo 的消息类型段，再看调用方显式 group。"""
    if group:
        return False
    body = str(umo or "")
    if not body:
        return False
    lowered = body.lower()
    if "groupmessage" in lowered or "group_message" in lowered or "group:" in lowered:
        return False
    if "friendmessage" in lowered or "privatemessage" in lowered:
        return True
    return "group" not in lowered


def is_media_kind(kind, text="") -> bool:
    value = str(kind or "").lower()
    if any(word in value for word in MEDIA_KINDS):
        return True
    body = str(text or "")
    return any(mark in body for mark in MEDIA_MARKS)


def current_scene_mood(plugin) -> dict:
    """取真实 scene（日程 zone）/ mood / mood_score；取不到就留空。"""
    scene = ""
    mood = ""
    score = 50
    state = getattr(plugin, "state", None)
    try:
        activity = state.current_activity() if state is not None else {}
        if isinstance(activity, dict):
            scene = str(activity.get("zone") or activity.get("place") or "")
    except Exception:
        scene = ""
    try:
        mood = str(state.dominant_mood() or "") if state is not None else ""
    except Exception:
        mood = ""
    try:
        score = int(state.mood_score()) if state is not None else 50
    except Exception:
        score = 50
    return {"scene": scene, "mood": mood, "mood_score": score}


def _stats(plugin, state):
    stats = state.setdefault("evolve_stats", {})
    today = _day_key(_now())
    if stats.get("day") != today:
        stats["day"] = today
        stats["added"] = 0
        stats["positive"] = 0
        stats["negative"] = 0
        stats["neutral"] = 0
    return stats


def today_added(plugin) -> int:
    state = _store.load_state(plugin)
    return int(_stats(plugin, state).get("added") or 0)


def record_chat_success(plugin, event, text) -> dict:
    """聊天回复真正交给发送链后调用（on_decorating_result / _send_now 两条路）。"""
    umo = str(getattr(event, "unified_msg_origin", "") or "")
    try:
        group = not bool(event.is_private_chat())
    except Exception:
        group = False
    return record_pending(plugin, text, umo, source="chat", kind="", group=group)


def record_pending(plugin, text, umo, source="chat", kind="", group=False) -> dict:
    """发送成功后暂存一条待判定回复（只留最新一条）。

    只有「私聊 + 聊天回复 + 非媒体 + 自动进化开着」才会写；发送失败不会走到这里。
    """
    body = str(text or "").strip()
    if not body or not auto_evolve_enabled(plugin):
        return {"ok": False, "reason": "disabled"}
    if group or not is_private_umo(umo, group=group):
        return {"ok": False, "reason": "not_private"}
    if str(source or "") != "chat":
        return {"ok": False, "reason": "not_chat"}
    if is_media_kind(kind, body):
        return {"ok": False, "reason": "media"}
    info = current_scene_mood(plugin)
    state = _store.load_state(plugin)
    state["pending_review"] = {
        "text": body[:220],
        "scene": info["scene"],
        "mood": info["mood"],
        "mood_score": info["mood_score"],
        "at": _now(),
        "umo": str(umo or ""),
    }
    _store.save_state(plugin, _store.active_id(plugin), state)
    return {"ok": True, "text": body[:80]}


def judge_reaction(pending, user_text, now=None) -> str:
    """规则判定他的反应：positive / negative / neutral / timeout。"""
    if not isinstance(pending, dict):
        return "neutral"
    now = _now() if now is None else int(now)
    try:
        age = now - int(pending.get("at") or 0)
    except (TypeError, ValueError):
        age = PENDING_TTL + 1
    if age > PENDING_TTL:
        return "timeout"
    body = normalize_text(user_text)
    if not body:
        return "neutral"
    if any(word in body for word in NEGATIVE_WORDS):
        return "negative"
    if any(word in body for word in POSITIVE_WORDS):
        return "positive"
    # 1-2 个字敷衍（哦/嗯/。）：不学。
    if han_count(body) <= 2:
        return "negative"
    # 继续话题：>=4 个汉字、不是命令句。
    if han_count(body) >= 4 and not any(word in body for word in COMMAND_WORDS):
        return "positive"
    return "neutral"


def _forbidden(plugin) -> list:
    try:
        card = (_store.load_pack(plugin) or {}).get("card") or {}
        return [str(item) for item in (card.get("forbidden") or []) if str(item)]
    except Exception:
        return []


def _pool_texts(state) -> list:
    rows = list(state.get("style_pool") or []) + list(state.get("style_archive") or [])
    return [str(row.get("text") or "") for row in rows if isinstance(row, dict)]


def _recent_user_texts(plugin, umo) -> list:
    try:
        rows = plugin._recent(umo, 10) or []
    except Exception:
        return []
    out = []
    for row in rows:
        if isinstance(row, dict) and str(row.get("role") or "") == "user":
            out.append(str(row.get("content") or ""))
    return out


def _too_similar(text, others) -> bool:
    for other in others:
        if other and similarity(text, other) >= 0.8:
            return True
    return False


def filter_reason(plugin, state, text, scene, mood, mood_score, umo="") -> str:
    """入池前过滤器；返回拒绝原因（空串 = 通过）。docs §6。"""
    body = spoken_text(text)
    count = han_count(body)
    # 下限 3：她平时一句话就 3-6 个字；上限按「说出口的部分」算，
    # 动作描写再长也不算超长。纯语气（嗯嗯/啊啊）仍然不收。
    if count < 3 or count > 40:
        return "length"
    if all(char in _FILLER for char in body if _HAN.match(char)):
        return "length"
    if any(word in body for word in _forbidden(plugin)):
        return "forbidden"
    lowered = body.lower()
    if any(word.lower() in lowered for word in TECH_WORDS):
        return "tech"
    if any(mark in body for mark in TEMPLATE_MARKS) or re.search(r"\d\s*[、.．]", body):
        return "template"
    if any(word in str(mood or "") for word in EXTREME_MOODS):
        return "extreme_mood"
    try:
        if int(mood_score or 50) <= EXTREME_SCORE:
            return "extreme_mood"
    except (TypeError, ValueError):
        mood_score = 50
    others = _pool_texts(state)
    others.extend(str(row.get("text") or "") for row in _pack.load_style_examples()
                  if isinstance(row, dict))
    others.extend(_recent_user_texts(plugin, umo))
    if _too_similar(body, others):
        return "similar"
    stats = _stats(plugin, state)
    cap = max(1, _cfg_int(plugin, "persona_evolve_daily_cap", 3))
    if int(stats.get("added") or 0) >= cap:
        return "daily_cap"
    return ""


def _queue_archive(state, rows):
    archive = list(state.get("style_archive") or [])
    archive.extend(rows)
    state["style_archive"] = archive[-MAX_ARCHIVE:]


def trim_pool(plugin, state):
    """每意图 ≤6、总 ≤30；超出的先挑分数低/最久没用的进 archive。"""
    per_intent = max(1, _cfg_int(plugin, "persona_style_pool_per_intent", PER_INTENT_DEFAULT))
    total_cap = TOTAL_CAP_DEFAULT
    pool = [row for row in (state.get("style_pool") or []) if isinstance(row, dict)]
    dropped = []

    def _score(row):
        try:
            return float(row.get("score", 1.0) or 1.0)
        except (TypeError, ValueError):
            return 1.0

    kept = []
    for intent in {str(row.get("intent") or "daily") for row in pool}:
        same = [row for row in pool if str(row.get("intent") or "daily") == intent]
        same.sort(key=lambda row: (-_score(row), int(row.get("last_used") or 0), int(row.get("at") or 0)))
        kept.extend(same[:per_intent])
        dropped.extend(same[per_intent:])
    kept.sort(key=lambda row: (-_score(row), int(row.get("at") or 0)))
    if len(kept) > total_cap:
        dropped.extend(kept[total_cap:])
        kept = kept[:total_cap]
    state["style_pool"] = kept
    if dropped:
        _queue_archive(state, dropped)
    return len(dropped)


def decay_pool(state, now=None, decay_days=DECAY_DAYS_DEFAULT) -> int:
    """30 天没被抽中后按天 ×0.98；低于 0.5 进 archive（不删，可恢复）。"""
    now = _now() if now is None else int(now)
    try:
        decay_days = max(1, int(decay_days or DECAY_DAYS_DEFAULT))
    except (TypeError, ValueError):
        decay_days = DECAY_DAYS_DEFAULT
    pool = [row for row in (state.get("style_pool") or []) if isinstance(row, dict)]
    kept, archived = [], []
    for row in pool:
        try:
            base = int(row.get("last_used") or row.get("at") or now)
            total_days = max(0, int((now - base) / 86400))
            applied = max(0, int(row.get("decayed_days") or 0))
            new_days = max(0, total_days - decay_days - applied)
            if new_days:
                row["score"] = float(row.get("score", 1.0) or 1.0) * (DECAY_RATE ** new_days)
                row["decayed_days"] = applied + new_days
        except (TypeError, ValueError):
            row["score"] = 1.0
        try:
            score = float(row.get("score", 1.0) or 1.0)
        except (TypeError, ValueError):
            score = 1.0
        if score < ARCHIVE_SCORE:
            archived.append(row)
        else:
            kept.append(row)
    state["style_pool"] = kept
    if archived:
        _queue_archive(state, archived)
    return len(archived)


def add_to_pool(plugin, text, scene="", mood="", mood_score=50, source="auto", state=None,
                pending=False) -> dict:
    """过滤通过后入池；写前 snapshot。A 阶段不写 candidates。

    2026-11-24：聊天里可以带动作，**库里只存她说出口的那句**——动作是场景，不是语气。
    """
    body = spoken_text(text)
    if not body:
        return {"ok": False, "reason": "empty"}
    state = state if isinstance(state, dict) else _store.load_state(plugin)
    reason = filter_reason(plugin, state, body, scene, mood, mood_score)
    if reason:
        stats = _stats(plugin, state)
        stats["rejected"] = int(stats.get("rejected") or 0) + 1
        _store.save_state(plugin, _store.active_id(plugin), state)
        return {"ok": False, "reason": reason}
    if any(str(row.get("text") or "") == body for row in state.get("style_pool") or []
           if isinstance(row, dict)):
        return {"ok": False, "reason": "duplicate"}
    _store.snapshot(plugin, _store.active_id(plugin), "before_style_pool")
    # snapshot() 写的是磁盘 state；把它的 snapshots 元数据并回本地状态，别被下面的 save 覆盖。
    try:
        state["snapshots"] = list((_store.load_state(plugin) or {}).get("snapshots") or [])
    except Exception:
        state["snapshots"] = list(state.get("snapshots") or [])
    now = _now()
    row = {
        "text": body,
        "intent": _pack.style_intent(body),
        "scene": str(scene or "general"),
        "mood": str(mood or ""),
        "score": 1.0,
        "at": now,
        "last_used": 0,
        "decayed_days": 0,
        "source": str(source or "auto"),
    }
    if pending:
        state.setdefault("style_pending", []).append(row)
    else:
        state.setdefault("style_pool", []).append(row)
    trim_pool(plugin, state)
    stats = _stats(plugin, state)
    stats["added"] = int(stats.get("added") or 0) + 1
    log = list(state.get("evolve_log") or [])
    log.append({"text": body, "scene": row["scene"], "mood": row["mood"], "intent": row["intent"],
                "at": now, "source": row["source"]})
    state["evolve_log"] = log[-MAX_LOG:]
    _store.save_state(plugin, _store.active_id(plugin), state)
    return {"ok": True, "text": body, "pool": len(state["style_pool"])}


def review_pending(plugin, user_text, group=False) -> dict:
    """下一轮进来先判上一轮反应；正向且过滤通过才入池。"""
    if group:
        return {"ok": False, "reason": "group"}
    state = _store.load_state(plugin)
    if not auto_evolve_enabled(plugin):
        if state.get("pending_review") is not None:
            state["pending_review"] = None
            _store.save_state(plugin, _store.active_id(plugin), state)
        return {"ok": False, "reason": "disabled"}
    pending = state.get("pending_review")
    if not isinstance(pending, dict):
        return {"ok": False, "reason": "empty"}
    state["pending_review"] = None
    verdict = judge_reaction(pending, user_text)
    stats = _stats(plugin, state)
    if verdict != "positive":
        stats["negative" if verdict == "negative" else "neutral"] = int(
            stats.get("negative" if verdict == "negative" else "neutral") or 0) + 1
        _store.save_state(plugin, _store.active_id(plugin), state)
        return {"ok": False, "reason": verdict}
    stats["positive"] = int(stats.get("positive") or 0) + 1
    # 自动通过开着时直接进池；默认关：先进「待通过」等人点
    out = add_to_pool(plugin, pending.get("text"), scene=pending.get("scene"),
                      mood=pending.get("mood"), mood_score=pending.get("mood_score"),
                      source="auto", state=state,
                      pending=not auto_approve_enabled(plugin))
    _store.save_state(plugin, _store.active_id(plugin), state)
    return out


def learned_style_samples(plugin, user_text, limit=2, mark=True) -> list:
    """优先她自己的池（同意图最高分 2 条）；空池回落固定 33 条。

    mark=False 只给面板预览看，不写 last_used / 不加分。
    """
    limit = max(1, int(limit or 2))
    state = _store.load_state(plugin)
    archived = decay_pool(state, decay_days=_cfg_int(plugin, "persona_style_decay_days",
                                                      DECAY_DAYS_DEFAULT))
    if archived:
        _store.save_state(plugin, _store.active_id(plugin), state)
    intent = _pack.style_intent(user_text)
    rows = [row for row in (state.get("style_pool") or [])
            if isinstance(row, dict) and str(row.get("text") or "")
            and str(row.get("intent") or "daily") == intent]
    now = _now()

    def _pick_key(row):
        used = int(row.get("last_used") or 0)
        seq = int(row.get("used_seq") or 0)
        score = -float(row.get("score", 1.0) or 1.0)
        at = int(row.get("at") or 0)
        if used and now - used < RECENT_WINDOW:
            # 最近抽过：按使用序号严格轮换，避免高分句一直霸屏。
            return (1, seq, score, at)
        # 没抽过/很久没抽：分数高的优先。
        return (0, score, used, at)

    rows.sort(key=_pick_key)
    picked = [{"text": str(row.get("text") or ""), "source": "pool"} for row in rows[:limit]]
    if picked:
        if mark:
            _store.mark_used(plugin, [item["text"] for item in picked])
        return picked
    fixed = _pack.style_samples(user_text, limit=limit)
    return [{"text": str(text), "source": "fixed"} for text in fixed if text]


def learned_style_block(plugin, user_text, limit=2, used_out=None) -> str:
    """system prompt 末尾的「她以前这样说过」块；总增量 ≤120 字。"""
    picked = learned_style_samples(plugin, user_text, limit=limit)
    if isinstance(used_out, list):
        used_out.extend(str(item.get("text") or "") for item in picked)
    if not picked:
        return ""
    prefix = "【你以前这样说过（只学语气，不要逐字重复）】"
    lines = []
    used = len(prefix)
    for item in picked:
        text = spoken_text(str(item.get("text") or ""))[:40]
        if not text:
            continue
        cost = len(text) + 3  # "- " + 换行
        if used + cost > 118:
            break
        lines.append("- " + text)
        used += cost
    return (prefix + "\n" + "\n".join(lines)) if lines else ""


def recent_auto(plugin, limit=5) -> list:
    state = _store.load_state(plugin)
    rows = [row for row in (state.get("evolve_log") or []) if isinstance(row, dict)]
    return rows[-max(1, int(limit or 5)):][::-1]


def edit_pool_entry(plugin, index=0, text="") -> dict:
    """改一条自动入池的句子（面板「改」）；index 与 recent_auto 同序（0=最新）。"""
    body = spoken_text(text)
    state = _store.load_state(plugin)
    log = [row for row in (state.get("evolve_log") or []) if isinstance(row, dict)]
    try:
        row = log[-1 - int(index)]
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "reason": "empty"}
    old = str(row.get("text") or "")
    try:
        at = int(row.get("at") or 0)
    except (TypeError, ValueError):
        at = 0
    if not body:
        return {"ok": False, "reason": "empty_text"}
    count = han_count(spoken_text(body))
    if count < 3 or count > 40:
        return {"ok": False, "reason": "length"}
    if any(word in body for word in _forbidden(plugin)):
        return {"ok": False, "reason": "forbidden"}
    if any(word in body.lower() for word in TECH_WORDS):
        return {"ok": False, "reason": "tech"}
    if any(mark in body for mark in TEMPLATE_MARKS):
        return {"ok": False, "reason": "template"}
    _store.snapshot(plugin, _store.active_id(plugin), "before_edit_pool")
    try:
        state["snapshots"] = list((_store.load_state(plugin) or {}).get("snapshots") or [])
    except Exception:
        state["snapshots"] = list(state.get("snapshots") or [])
    for key in ("style_pending", "style_pool"):
        for item in state.get(key) or []:
            if (isinstance(item, dict) and str(item.get("text") or "") == old
                    and int(item.get("at") or 0) == at):
                item["text"] = body
                item["intent"] = _pack.style_intent(body)
                break
        else:
            continue
        break
    row["text"] = body
    _store.save_state(plugin, _store.active_id(plugin), state)
    return {"ok": True, "text": body}


def approve_pending(plugin, index=0) -> dict:
    """把一条待通过的句子正式收进例句池（面板「通过」）；写前 snapshot。"""
    state = _store.load_state(plugin)
    rows = [row for row in (state.get("style_pending") or []) if isinstance(row, dict)]
    try:
        row = rows[-1 - int(index)]
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "reason": "empty"}
    text = str(row.get("text") or "")
    _store.snapshot(plugin, _store.active_id(plugin), "before_approve_pending")
    try:
        state["snapshots"] = list((_store.load_state(plugin) or {}).get("snapshots") or [])
    except Exception:
        state["snapshots"] = list(state.get("snapshots") or [])
    state["style_pending"] = [item for item in (state.get("style_pending") or [])
                               if not (isinstance(item, dict)
                                       and str(item.get("text") or "") == text
                                       and int(item.get("at") or 0) == int(row.get("at") or 0))]
    approved = dict(row)
    approved["intent"] = _pack.style_intent(text)
    approved["source"] = str(approved.get("source") or "manual")
    state.setdefault("style_pool", []).append(approved)
    trim_pool(plugin, state)
    _store.save_state(plugin, _store.active_id(plugin), state)
    return {"ok": True, "text": text}


def undo_last_auto(plugin, index=0) -> dict:
    """撤销最近一条自动入池（默认最后一条）；写前 snapshot。"""
    state = _store.load_state(plugin)
    log = [row for row in (state.get("evolve_log") or []) if isinstance(row, dict)]
    try:
        row = log.pop(-1 - int(index))
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "reason": "empty"}
    _store.snapshot(plugin, _store.active_id(plugin), "before_undo_auto")
    try:
        state["snapshots"] = list((_store.load_state(plugin) or {}).get("snapshots") or [])
    except Exception:
        state["snapshots"] = list(state.get("snapshots") or [])
    text = str(row.get("text") or "")
    at = int(row.get("at") or 0)
    for key in ("style_pending", "style_pool"):
        state[key] = [item for item in (state.get(key) or [])
                      if not (isinstance(item, dict) and str(item.get("text") or "") == text
                              and int(item.get("at") or 0) == at)]
    state["evolve_log"] = log
    stats = _stats(plugin, state)
    stats["added"] = max(0, int(stats.get("added") or 0) - 1)
    _store.save_state(plugin, _store.active_id(plugin), state)
    return {"ok": True, "text": text}


def _rows_with_log_index(state, key) -> list:
    """面板两侧的列表：倒序显示，并带上它在 evolve_log 里的位置（撤销/编辑按这个走）。"""
    log = [row for row in (state.get("evolve_log") or []) if isinstance(row, dict)]
    out = []
    for row in reversed(list(state.get(key) or [])):
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["log_index"] = next(
            (len(log) - 1 - pos for pos, cand in enumerate(reversed(log))
             if str(cand.get("text") or "") == str(row.get("text") or "")
             and int(cand.get("at") or 0) == int(row.get("at") or 0)), -1)
        out.append(item)
    return out


def status(plugin) -> dict:
    state = _store.load_state(plugin)
    return {
        "auto_evolve": auto_evolve_enabled(plugin),
        "auto_approve": auto_approve_enabled(plugin),
        "today_added": int(_stats(plugin, state).get("added") or 0),
        "pool_count": len([row for row in (state.get("style_pool") or []) if isinstance(row, dict)]),
        "archive_count": len(state.get("style_archive") or []),
        "pending_review": state.get("pending_review") if isinstance(state.get("pending_review"), dict) else None,
        "recent_auto": recent_auto(plugin, 5),
        "pending_pool": _rows_with_log_index(state, "style_pending"),
        "pool_rows": _rows_with_log_index(state, "style_pool"),
    }
