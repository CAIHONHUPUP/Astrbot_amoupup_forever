# -*- coding: utf-8 -*-
"""约会状态注入（2026-11-15）：他赴约后，她就“看得见”他。

- 约会中：最高事实“他就在你身边” + 感官代偿 + 括号动作 + 环境渲染 + 防失忆；
- 刚碰面：要求生成碰面动作，不许干巴巴说“欢迎”；
- 刚结束：约会结束、他不在身边（同城不同住、各回各家）；她一个人回家，只能手机/视频联系，日记重点回味。
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

import logging
import re

try:  # 提示词注册表（Phase 4）
    from . import amo_prompt_registry as _prompts
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_prompt_registry as _prompts

logger = logging.getLogger("amo")

try:
    from .clock import now as _clock_now
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from clock import now as _clock_now

try:  # 约会会话单一源（Phase 2）
    from . import amo_date_session as _ds
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_date_session as _ds

MEET_WINDOW = 10 * 60

_FORBIDDEN = (
    "我看不到你", "看不到你", "你在哪", "你在哪里", "你是不是不在家",
    "我一个人在家吃饭", "一个人逛街", "我一个人吃饭", "我自己一个人",
)

_SCENES = (
    (("火锅", "烤肉", "自助", "烧烤"),
     "店里很吵，锅底咕嘟咕嘟地翻，辣味和油烟一直往鼻子里钻，碗筷碰得叮当响"),
    (("电影", "影院"),
     "电影院里灯刚关，大屏幕的光亮起来，周围很安静，偶尔有人小声说话"),
    (("商场", "逛街", "买衣服", "购物"),
     "商场里人很多，背景音乐和店铺的灯光混在一起，试衣间外排着队"),
    (("咖啡", "奶茶", "甜品"),
     "店里放着轻音乐，咖啡机在响，杯子和托盘碰在桌上"),
    (("公园", "散步", "景区", "游乐"),
     "外面风不大，路边有树叶的响声和远处的人声，天色刚刚好"),
    (("街", "路", "车站"),
     "路边有车流和风声，偶尔有行人从旁边走过"),
)


def _kv(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception:
        return default


def _now(now=None):
    return now or _clock_now()


def _active(plugin, now=None):
    return _ds.active(plugin, now)


def _home(plugin, now=None):
    """约会结束，她一个人回到家；同城不同住、各回各家（读会话单一源）。"""
    return _ds.home(plugin, now)


def _title(plugin, now=None):
    data = _ds.load(plugin, now)
    return str(data.get("title") or "约会")


END_WORDS = ("回家吧", "我们回家", "回家啦", "一起回家", "走回家", "吃好了", "吃完了",
             "吃饱了", "太晚了", "该回去了", "回去吧", "走吧回去", "结账吧")


def finish_date_now(plugin, now=None, reason="user_done"):
    """约会收尾：把正在进行的约会日程截断到此刻，切到「刚到家」（一个人）。"""
    now = now or _now()
    day = now.strftime("%Y-%m-%d")
    now_min = now.hour * 60 + now.minute
    _ds.finish(plugin, now, reason=reason, title=_title(plugin, now))
    try:
        state: Any = getattr(plugin, "state", None)
        current = list((getattr(state, "schedule", {}) or {}).get(day) or [])
        patched = []
        for row in current:
            row = dict(row)
            try:
                begin = int(str(row.get("start") or "0:00").split(":")[0]) * 60 + int(str(row.get("start") or "0:00").split(":")[1])
                finish = int(str(row.get("end") or "0:00").split(":")[0]) * 60 + int(str(row.get("end") or "0:00").split(":")[1])
            except Exception:
                patched.append(row)
                continue
            if str(row.get("category")) in ("social", "date") and begin <= now_min < finish:
                row["end"] = now.strftime("%H:%M")
            patched.append(row)
        if patched != current:
            apply = getattr(state, "apply_schedule", None)
            if callable(apply):
                def _mutate(rows):
                    out = []
                    for row in rows:
                        row = dict(row) if isinstance(row, dict) else row
                        if not isinstance(row, dict):
                            out.append(row)
                            continue
                        try:
                            begin = (int(str(row.get("start") or "0:00").split(":")[0]) * 60
                                     + int(str(row.get("start") or "0:00").split(":")[1]))
                            finish = (int(str(row.get("end") or "0:00").split(":")[0]) * 60
                                      + int(str(row.get("end") or "0:00").split(":")[1]))
                        except Exception:
                            out.append(row)
                            continue
                        if str(row.get("category")) in ("social", "date") \
                                and begin <= now_min < finish:
                            row["end"] = now.strftime("%H:%M")
                        out.append(row)
                    return out
                apply(day, _mutate, source="date", reason="约会结束截断")
            else:
                state.commit_schedule_change(day, patched, old_items=current,
                                             source="date", reason="约会结束截断")
    except Exception as exc:
        logger.debug("[amo_date_context.finish_date_now] optional step failed: %s", exc)
    # task-30：收尾事实的地点从场景账本取，不能硬说「现在你在家」——约会结束她可能
    # 直接回公司/医院，旧文案会在 21:00 写一句和日程冲突的「你在家」。
    _after_place = ""
    try:
        try:
            from .amo_scene_ledger import scene_at as _scene_at
        except ImportError:
            from amo_scene_ledger import scene_at as _scene_at
        _after_place = str((_scene_at(plugin, now) or {}).get("place") or "")
    except Exception as exc:  # cleanup：账本读不到就用中性说法
        logger.debug("[amo] 约会收尾场景读取失败: %r", exc)
        _after_place = ""
    home_line = ("今天的约会已经结束，你们各自回到日常；%s他不在你身边，"
                 "只能手机聊天。" % (("现在你在" + _after_place + "，") if _after_place else ""))
    try:
        plugin.set_date_fact(home_line, now.timestamp() + 6 * 3600)
    except Exception:
        try:
            from .amo_state_store import for_plugin as _state_store_for
        except ImportError:
            from amo_state_store import for_plugin as _state_store_for
        _state_store_for(plugin).set_ttl("date_fact", {"line": home_line}, 0,
                                        until=now.timestamp() + 6 * 3600)
    # 兼容旧调用方/测试桩：set_date_home 现在也走会话（同一次收尾，幂等）
    home_setter = getattr(plugin, "set_date_home", None)
    if callable(home_setter):
        try:
            home_setter("约会", now.timestamp())
        except Exception as exc:  # cleanup：兼容失败不影响会话已经收尾
            logger.debug("[amo] 约会回家投影失败: %r", exc)
    _end_line = "时间不早了，吃饱喝足了，该回去了；约会结束，他不在你身边，你一个人回家。"
    try:
        try:
            from . import amo_household as _hh
        except ImportError:
            import amo_household as _hh
        _override = _hh.wording("date_ending", plugin=plugin)
        if _override:
            _end_line = "时间不早了，吃饱喝足了，该回去了；" + _override
    except Exception as exc:  # cleanup: 居住状态读不到就用 apat 原文
        logger.debug("[amo] 约会收尾居住文案读取失败: %r", exc)
    _ds.request_end(plugin, _end_line, reason=reason, ttl=600, sent=True, now=now)
    return reason


DATE_END_CUES = ("该回家了", "我们回家", "回家吧", "该回去了", "一起回家", "太晚了", "回去吧", "不早了")


def mark_reply_date_end(plugin, text, now=None):
    """她自己说「该回家了」时记一个 pending 收尾，等他回「好/走吧」再真正结束。"""
    body = str(text or "")
    if not any(cue in body for cue in DATE_END_CUES):
        return False
    if "不回家" in body or "别回家" in body or "先不回去" in body:
        return False
    now = now or _now()
    if not _ds.is_dating(plugin, now):
        return False
    _ds.request_end(plugin, body[:120], reason="bot_proposed", ttl=600, sent=True, now=now)
    return True


def maybe_finish_date(plugin, text, now=None):
    """他说「回家吧/吃好了」，或她刚提过「该回家了」而他回短肯定词时收尾约会。"""
    now = now or _now()
    if not _ds.is_dating(plugin, now):
        return ""
    body = str(text or "")
    if any(word in body for word in END_WORDS):
        return finish_date_now(plugin, now)
    compact = re.sub(r"[\s，。！？!?、~～…,.]+", "", body)
    if compact in ("好", "好啊", "好呀", "好的", "行", "行啊", "嗯", "嗯嗯", "可以", "ok", "OK",
                  "走吧", "走呗", "回家", "回家吧", "回去吧", "回吧", "那就回", "听你的"):
        ending = _ds.ending(plugin, now) or {}
        if ending.get("sent"):
            return finish_date_now(plugin, now, reason="agreed")
    return ""


def scene_line(text):
    body = str(text or "")
    for words, line in _SCENES:
        if any(word in body for word in words):
            return line
    return "周围有吃饭的人声、碗筷声和店里的音乐，空气里是热乎的饭菜味"


def prompt_block(plugin, now=None):
    now = _now(now)
    active = _active(plugin, now)
    if active:
        line = str(active.get("line") or "").strip()
        scene = scene_line(line)
        try:
            since = float(active.get("since") or active.get("start_ts") or 0)
        except Exception:
            since = 0
        meet = bool(since) and (now.timestamp() - since) <= MEET_WINDOW
        meet_title = ""
        outfit = ""
        state_line = ""
        if meet:
            info = _kv(plugin, "date_meet", {}) or {}
            meet_title = str(info.get("title") or line or "约会")
            try:
                wardrobe = getattr(plugin, "wardrobe", None)
                worn = wardrobe.current() if wardrobe else None
                outfit = str((worn or {}).get("text") or "") if isinstance(worn, dict) else str(worn or "")
            except Exception:
                outfit = ""
            try:
                hunger = int(plugin.get_hunger())
                state_line = "很饿，肚子在叫" if hunger < 35 else ("有点饿" if hunger < 60 else "")
            except Exception:
                state_line = ""
            try:
                words = str(plugin.state.mood_words() or "").strip()
                if words:
                    state_line = (state_line + "；" + words) if state_line else words
            except Exception as exc:  # cleanup: 情绪词拿不到就不写状态，不影响开场
                logger.debug("[amo_date_context.prompt_block] optional step failed: %s", exc)
        return _prompts.render("date.active", line=line, scene=scene, meet=meet,
                               meet_title=meet_title, outfit=outfit, state_line=state_line)
    if _ds.paused(plugin, now):
        return _prompts.render("date.pause")
    ending = _ds.ending(plugin, now)
    if ending:
        return _prompts.render("date.ending", fact=str(ending.get("fact") or ""))
    if _home(plugin, now):
        text = _prompts.render("date.home")
        try:
            try:
                from . import amo_household as _hh
            except ImportError:
                import amo_household as _hh
            _override = _hh.wording("date_ending", plugin=plugin)
            if _override:
                text = text + "\n【居住·最高优先级】" + _override
        except Exception as exc:  # cleanup: 居住状态读不到就用原块
            logger.debug("[amo] 约会结束居住文案读取失败: %r", exc)
        return text
    return ""


def guard_reply(text, plugin, now=None):
    """约会中把“看不到你/一个人在家”这类破沉浸的话改成现场动作。"""
    body = str(text or "")
    if not body or not _active(plugin, now):
        return body
    for phrase in _FORBIDDEN:
        if phrase not in body:
            continue
        if "看不到" in phrase:
            body = body.replace(phrase, "（伸手在你面前晃了晃）你不就在这儿嘛")
        elif "一个人" in phrase:
            body = body.replace(phrase, "（挽着你的胳膊）我们明明在一起呀")
        elif "你在哪" in phrase or "在哪里" in phrase:
            body = body.replace(phrase, "（探头看你）你不就在我面前嘛")
        else:
            body = body.replace(phrase, "（往你身边靠了靠）你就在这儿呀")
    return body
