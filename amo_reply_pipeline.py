# -*- coding: utf-8 -*-
"""ReplyPipeline：回复守卫的统一有序登记表（Phase 6）。

为什么要这个模块
----------------
回复在发出去之前要过十来道守卫，它们原来散在三个地方，顺序只能靠读代码
猜，还有几道在同一条回复上跑了两遍。这里把「真实调用顺序」抄成一份登记表，
让顺序可以被读、被审计、被测试；守卫本身（名字校验、地点黑名单、约会守卫、
过渡守卫、消费校验……）一个都没重写，仍然是原来那些函数，只是改成由流水线
按登记表调用。

四个阶段（phase）= 代码里真实存在的四条路径
-------------------------------------------
顺序不是设计出来的，是从改造前的调用点抄下来的：

1. generate（生成后、装饰前）—— amo_llm._compose_reply：
   _claim_retry → _reply_postprocess：polish → _trim_reply →
   date_state.guard → refusal.should_refuse/guard →
   _guard_consumption →
   group_scene.guard_group_reply → _maybe_group_private_tease；
   回到 _compose_reply 之后是**副作用阶段**：draw.enforce_photo_claim →
   _enforce_photo_intent → draw.send_photo_now（会真的补一张图，
   所以单独建模、保持原位置，不并入纯文本阶段）。

2. decorate（on_decorating_result）—— amo_runtime.on_decorating_result：
   strip_chat_format → _deco_soft_guards（_maybe_own_offwork_sync →
   apply_fact_guard → 归属串台告警）→ _deco_rewrite_reply
   （gift 观察 → 防御话术/AI 自曝 → 后台信息泄漏 → draw.maybe_after_reply）→
   _deco_polish_native（polish_native 开关）→ _deco_text_cleanup
   （strip_brushoff → _strip_repeated_food_question → _transit_rewrite →
   _guard_zone_reply → amo_date_context.guard_reply → mark_reply_date_end →
   _trim_reply）。

3. send（真正发出去那条路）—— amo_message._send_to_umo（改造前顺序）：
   clean_reply → 白名单外人名（重写/清洗）→ _guard_zone_reply →
   wardrobe.home_outfit_guard → sanitize_privacy →
   amo_date_context.guard_reply → fix_identity(sanitize_extreme(text)) →
   maybe_advance/maybe_start_from_text（**副作用：推进过渡状态**）→
   amo_transit.rewrite_sent_text → looks_like_model_leak。

4. proactive_pre（主动消息**分段前**那一步）—— amo_proactive._send_proactive_lines：
   _guard_consumption（消费校验）在按句号切分之前跑；切分之后每一段还会各自
   过一遍 send。

接线现状（本阶段只把两条路真正接到 run()）
------------------------------------------
- send：amo_message._send_to_umo 唯一的守卫入口，wired。
- proactive_pre：amo_proactive._send_proactive_lines 的消费校验，wired。
- generate / decorate：只登记、不接管（wired=False）——这两个阶段的复合块里夹着
  LLM 重写、发消息、落库等副作用，动它们的调用点风险远大于收益；登记表先把顺序
  和重复显性化，接管留给后续阶段。run() 仍然可以按 phase 执行它们（测试就是这么
  做等价验证的）。

去重
----
同一条回复上，同一个 stage id 只会执行一次；同一个 target（底层守卫函数）
也只会执行一次（ctx.stage_set / ctx.target_set 打标）。跨阶段重复由 audit()
列出来——例如 _trim_reply 在 generate 和 decorate 里各出现一次，
_guard_zone_reply 在 send 和 decorate 里各出现一次。

地点改口只有 reply_fact_guard 一个生产者（3a-2b 起）
---------------------------------------------------
place_guard 只提供地点词表与判据（match_location / allowed_groups / spoken_place），
不再改写回复；generate 阶段也不再单独对齐地点，出站统一由 decorate / proactive 的
apply_fact_guard 兜一次（send 阶段保留 zone 黑名单）。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import inspect
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, TYPE_CHECKING

try:  # 整句级复读守卫（去重/短句兜底，纯文本无副作用）
    from .domains.reply import repetition_guard as _repeat_guard
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.reply import repetition_guard as _repeat_guard  # type: ignore

try:  # 统一兜底注册表（task-18）：兜底句唯一来源
    from .domains.reply import repetition_guard as _fallbacks
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.reply import repetition_guard as _fallbacks

try:  # B 方案选择器：模块只做异步选择，候选/轮换仍在本文件
    from . import amo_tone_pick as _tone_pick
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            import amo_tone_pick as _tone_pick
        except Exception:  # pragma: no cover - 兜底
            _tone_pick = None

try:  # 包模式优先，按文件直接加载时退回绝对导入
    from .amo_observability import note as _note_swallowed
    from .amo_shared import (
        _now,
        clean_reply,
        logger,
        looks_like_model_leak,
        now_ts,
        strip_chat_format,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_observability import note as _note_swallowed
        from amo_shared import (
            _now,
            clean_reply,
            logger,
            looks_like_model_leak,
            now_ts,
            strip_chat_format,
        )


try:  # 纯内核：流水线数据结构（域内只用标准库）
    from .domains.reply.frame import (CHANNEL_PRIVATE, PHASE_SEND, ReplyContext,
                                      Stage, StopPipeline)
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.reply.frame import (CHANNEL_PRIVATE, PHASE_SEND, ReplyContext,
                                         Stage, StopPipeline)

try:  # 纯内核：语气候选（域内只用标准库 + note）
    from .domains.reply.tone import (
        ACTIVITY_TONE, MEAL_TONE, PLACE_TONE, RECOVERY_TONE, TONE_POOLS, _TONE_FALLBACK,
        _activity_tone, _changed_segment, _natural_clause, _pick_tone_variant,
        _today_chat, _tone_candidates, _tone_group, _tone_key, _tone_mood,
        _tone_relation, _tone_scene, _tone_state,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.reply.tone import (
            ACTIVITY_TONE, MEAL_TONE, PLACE_TONE, RECOVERY_TONE, TONE_POOLS, _TONE_FALLBACK,
            _activity_tone, _changed_segment, _natural_clause, _pick_tone_variant,
            _today_chat, _tone_candidates, _tone_group, _tone_key, _tone_mood,
            _tone_relation, _tone_scene, _tone_state,
        )

PHASE_GENERATE = "generate"
PHASE_DECORATE = "decorate"
PHASE_PROACTIVE_PRE = "proactive_pre"
PHASES = (PHASE_GENERATE, PHASE_DECORATE, PHASE_SEND, PHASE_PROACTIVE_PRE)

CHANNEL_GROUP = "group"
CHANNEL_PROACTIVE = "proactive"








def _phase_index(phase: str) -> int:
    try:
        return PHASES.index(phase)
    except ValueError:
        return len(PHASES)


def _cfg_int(plugin: Any, key: str, default: int) -> int:
    getter = getattr(plugin, "cfg_int", None)
    if not callable(getter):
        return int(default)
    return int(getter(key, default))


class Pipeline:
    """有序阶段表。run(text, ctx) 只执行 ctx.phase 那一组阶段。"""

    def __init__(self, stages: Optional[Sequence[Stage]] = None):
        self.stages: List[Stage] = list(DEFAULT_STAGES if stages is None else stages)

    def ordered(self, phase: Optional[str] = None) -> List[Stage]:
        chosen = [(index, stage) for index, stage in enumerate(self.stages)
                  if phase is None or stage.phase == phase]
        chosen.sort(key=lambda pair: (_phase_index(pair[1].phase), pair[1].priority, pair[0]))
        return [stage for _index, stage in chosen]

    async def run(self, text: Any, ctx: ReplyContext) -> Dict[str, Any]:
        value = text
        applied: List[str] = []
        changed: List[str] = []
        stopped = False
        for stage in self.ordered(ctx.phase):
            if stage.run is None:
                continue
            if ctx.marked(stage.id, stage.target):
                ctx.note("skip duplicate: %s" % stage.id)
                continue
            if stage.when is not None and not _when_ok(stage, ctx):
                continue
            ctx.mark(stage.id, stage.target)
            applied.append(stage.id)
            ctx.trace.append(stage.id)
            try:
                outcome = stage.run(value, ctx)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
            except StopPipeline as exc:
                stopped = True
                ctx.note("stopped by %s: %s" % (stage.id, exc))
                break
            except Exception as exc:  # 只有显式标注 swallow 的阶段才吞
                if stage.swallow == "note":
                    _note_swallowed(stage.note_scope or stage.id, exc)
                    continue
                if stage.swallow == "debug":
                    if stage.log_msg:
                        logger.debug(stage.log_msg, exc)
                    else:
                        logger.debug("[amo] 守卫阶段 %s 失败: %s", stage.id, exc)
                    continue
                raise
            if outcome is None or not stage.replace:
                continue
            if outcome != value:
                value = outcome
                changed.append(stage.id)
        return {"text": value, "applied": applied, "changed": changed,
                "stopped": stopped, "notes": list(ctx.notes), "phase": ctx.phase}

    def audit(self) -> Dict[str, Any]:
        return _audit(self.stages)


def _when_ok(stage: Stage, ctx: ReplyContext) -> bool:
    try:
        return bool(stage.when(ctx))  # type: ignore[misc]
    except Exception as exc:  # when 出问题只影响这一道，不该拖垮发送
        logger.debug("[amo] 阶段 %s 的 when 判定失败: %s", stage.id, exc)
        ctx.note("when failed: %s" % stage.id)
        return False


# ---------------------------------------------------------------------------
# send：amo_message._send_to_umo（顺序与改造前逐行一致）
# ---------------------------------------------------------------------------


def _send_clean(text, ctx):
    return clean_reply(text, _cfg_int(ctx.plugin, "max_reply_chars", 220))


async def _send_names(text, ctx):
    try:
        from .amo_names import foreign_names as _foreign_names, sanitize as _name_sanitize
    except ImportError:
        from amo_names import foreign_names as _foreign_names, sanitize as _name_sanitize
    dirty = _foreign_names(text)
    if not dirty:
        return None
    fixed = await ctx.plugin._rewrite_without_foreign(ctx.umo, text, dirty)
    result = fixed or _name_sanitize(text)
    logger.info("[amo] 白名单外人名已拦截: %s", dirty)
    return result


async def _send_zone(text, ctx):
    guarded = await ctx.plugin._guard_zone_reply(text, ctx.umo)
    if guarded and guarded != text:
        return guarded
    return None


def _photo_guard_state(plugin, umo):
    """问照片引擎：现在是 ok / pending / failed / idle。"""
    photo = getattr(plugin, "photo", None)
    getter = getattr(photo, "photo_guard_state", None)
    if callable(getter):
        try:
            return str(getter(umo) or "idle")
        except Exception as exc:
            logger.debug("[amo] 照片状态读取失败: %s", exc)
    draw = getattr(plugin, "draw", None)
    if umo and umo in getattr(draw, "_inflight", set()):
        return "pending"
    return "idle"


def _send_photo_claim(text, ctx):
    """失败/无图时，删掉「图有点糊 / 凑合看 / 拍好了」这类声称有图的句子。"""
    try:
        try:
            from .photo_view import (looks_like_broken_photo_claim,
                                     looks_like_photo_claim, strip_photo_claim)
        except ImportError:
            from photo_view import (looks_like_broken_photo_claim,
                                    looks_like_photo_claim, strip_photo_claim)
        body = str(text or "")
        if not body or not looks_like_photo_claim(body):
            return None
        state = _photo_guard_state(ctx.plugin, ctx.umo)
        if state == "ok":
            return None
        if state == "idle" and not looks_like_broken_photo_claim(body):
            # 没有任何照片链路证据时，别误伤普通聊天里的「拍好啦」；
            # 只拦「图糊/凑合看」这种明确翻车的句子。
            return None
        fixed = strip_photo_claim(body)
        if not fixed:
            fallback = "唔…这次没拍成，等我缓一下再给你拍，好不好？"
            photo = getattr(ctx.plugin, "photo", None)
            picker = getattr(photo, "failure_line", None)
            try:
                fixed = picker(ctx.umo) if callable(picker) else fallback
            except Exception:
                fixed = fallback
        logger.info("[PHOTO_GUARD] claim_rewritten=1 state=%s text=%s",
                    state, body[:60])
        return fixed
    except Exception as exc:
        _note_swallowed("amo_reply_pipeline.send_photo_claim", exc)
        return None


def _send_wardrobe(text, ctx):
    try:
        wardrobe = getattr(ctx.plugin, "wardrobe", None)
        if wardrobe is not None and hasattr(wardrobe, "home_outfit_guard"):
            return wardrobe.home_outfit_guard(text)
    except Exception as exc:
        _note_swallowed("amo_reply_pipeline.send_wardrobe", exc)
    return None


def _send_privacy(text, ctx):
    # 与改造前一致：这里只有相对导入，没有绝对导入兜底。
    # 顶层 sys.path 载入（单测的老模式）会 ImportError，被 send.privacy 的
    # swallow="note" 吞掉——这是既有行为，Phase 6 不改（改了就是行为漂移）。
    from .amo_diary_guard import sanitize_privacy
    return sanitize_privacy(text)


def _send_date_context(text, ctx):
    # 同上：只有相对导入，顶层载入模式下这道守卫会被跳过，保持原样。
    from .amo_date_context import guard_reply as _guard_date
    return _guard_date(text, ctx.plugin)


def _send_immersion(text, ctx):
    try:
        from .amo_immersion_guard import fix_identity, sanitize_extreme
    except ImportError:
        from amo_immersion_guard import fix_identity, sanitize_extreme
    return fix_identity(sanitize_extreme(text))


def _send_transition(text, ctx):
    try:
        try:
            from .amo_transition import maybe_advance as _maybe_advance, maybe_start_from_text as _maybe_transition_text
        except ImportError:
            from amo_transition import maybe_advance as _maybe_advance, maybe_start_from_text as _maybe_transition_text
        _maybe_advance(ctx.plugin, text)
        started = _maybe_transition_text(ctx.plugin, text, source="bot")
        if started:
            logger.info("[amo] 她的话开启过渡：%s->%s", started.get("kind"), started.get("to"))
    except Exception as exc:
        logger.debug("[amo] 她的过渡意图识别失败: %s", exc)
    return None


def _send_transit(text, ctx):
    try:
        try:
            from .amo_transit import rewrite_sent_text as _transit_rewrite
        except ImportError:
            from amo_transit import rewrite_sent_text as _transit_rewrite
        return _transit_rewrite(ctx.plugin, text)
    except Exception as exc:
        logger.debug("[amo] 过渡地点守卫失败: %s", exc)
    return None


def _model_leak_deflection(plugin=None, umo=""):
    """挑一句最近没说过的挡话；全说过就按轮换用下一条。"""
    used = set(_repeat_guard.recent_reference(plugin, umo, DEDUP_WINDOW))
    return _fallbacks.pick("leak", "leak:" + str(umo or ""), used)


def _send_model_leak(text, ctx):
    if looks_like_model_leak(text):
        return _model_leak_deflection(ctx.plugin, ctx.umo)
    return None


# ---------------------------------------------------------------------------
# proactive_pre：amo_proactive._send_proactive_lines（分段前）
# ---------------------------------------------------------------------------


def _pre_claim(text, ctx):
    guard = getattr(ctx.plugin, "_guard_consumption", None)
    if callable(guard):
        return str(guard(text) or text)
    return None


# ---------------------------------------------------------------------------
# generate：amo_llm._compose_reply / _reply_postprocess（只登记，不接管）
# ---------------------------------------------------------------------------


async def _gen_claim_retry(text, ctx):
    retry = getattr(ctx.plugin, "_claim_retry", None)
    if not callable(retry):
        return None
    return await retry(text, ctx.umo, ctx.system, ctx.prompt)


async def _gen_polish(text, ctx):
    llm = getattr(ctx.plugin, "llm", None)
    if llm is None:
        return None
    return await llm.polish(
        ctx.umo, text, ctx.tone, state_text=ctx.state_text, relation=ctx.relation,
        name=ctx.name, persona=ctx.persona, style=ctx.style, consult=ctx.consult,
    )


def _gen_trim(text, ctx):
    return ctx.plugin._trim_reply(text)


def _gen_date_state(text, ctx):
    try:
        from .date_state import guard as _date_guard
    except ImportError:
        from date_state import guard as _date_guard
    result = _date_guard(text, ctx.plugin)
    if result != text:
        logger.info("[DATE_GUARD] 约会后失忆回复已拦截")
    return result


def _gen_refusal(text, ctx):
    try:
        import refusal
    except ImportError:  # pragma: no cover - 顶层总能导入
        return None
    snap = ctx.snap or ctx.activity or {}
    hour = ctx.hour if ctx.hour is not None else int(_now().hour)
    refuse, reason = refusal.should_refuse(
        snap, ctx.his_words, ctx.cash_cents, hour, ctx.silence_days)
    if not refuse:
        return None
    result = refusal.guard(text, snap, ctx.his_words, ctx.cash_cents, hour, ctx.silence_days)
    if refusal.is_refusal_reply(result):
        try:
            ctx.plugin.db.kv_set("refusal:last", {"ts": now_ts(), "reason": reason})
        except Exception as exc:  # cleanup：落库失败不影响回复
            logger.debug("[amo] 拒绝原因落库失败: %s", exc)
    return result


def _gen_consumption(text, ctx):
    return ctx.plugin._guard_consumption(text)


def _gen_group(text, ctx):
    try:
        import group_scene
    except ImportError:  # pragma: no cover - 顶层总能导入
        return None
    result, changed = group_scene.guard_group_reply(text, ctx.scene, ctx.owner_name)
    if changed:
        logger.info("[GROUP_GUARD] 群聊私密/超长回复已拦截重写")
    return result


def _gen_photo_intent(text, ctx):
    return ctx.plugin._enforce_photo_intent(ctx.event, text)


# ---------------------------------------------------------------------------
# decorate：amo_runtime.on_decorating_result（只登记，不接管）
# ---------------------------------------------------------------------------


def _dec_strip_chat_format(text, ctx):
    return strip_chat_format(text)


def _dec_offwork(text, ctx):
    ctx.plugin._maybe_own_offwork_sync(text)
    ctx.plugin._maybe_own_desk_sync(text)
    return None


def _dec_fact_guard(text, ctx):
    """decorate 的事实/地点守卫（唯一守卫：reply_fact_guard）。"""
    place = ctx.place
    activity = ctx.activity or ({"place": place} if place else None)
    return apply_fact_guard(text, ctx.plugin, activity)


def _dec_gift_observe(text, ctx):
    gift = getattr(ctx.plugin, "gift", None)
    if gift is not None:
        try:
            gift.observe_reply(ctx.event, text)
        except Exception as exc:
            logger.debug("[amo] 礼物心愿捕获失败: %s", exc)
    return None


def _dec_model_leak(text, ctx):
    if looks_like_model_leak(text):
        return _model_leak_deflection(ctx.plugin, ctx.umo)
    return None


def _dec_draw(text, ctx):
    draw = getattr(ctx.plugin, "draw", None)
    if draw is not None:
        try:
            draw.maybe_after_reply(ctx.event, text)
        except Exception as exc:
            logger.warning("[amo] 画图判断失败: %s", exc)
    return None


def _dec_brushoff(text, ctx):
    try:
        from .brushoff import strip_brushoff
    except ImportError:
        from brushoff import strip_brushoff
    result, brush = strip_brushoff(text, ctx.his_words)
    if brush:
        logger.debug("[amo] 去掉赶人的话: %s", brush)
    return result


def _dec_food_question(text, ctx):
    return ctx.plugin._strip_repeated_food_question(text, ctx.his_words, ctx.last_bot_line)


def _dec_transit(text, ctx):
    try:
        try:
            from .amo_transit import rewrite_sent_text as _transit_rewrite
        except ImportError:
            from amo_transit import rewrite_sent_text as _transit_rewrite
        return _transit_rewrite(ctx.plugin, text)
    except Exception as exc:
        _note_swallowed("amo_reply_pipeline.decorate_transit", exc)
    return None


async def _dec_zone(text, ctx):
    guarded = await ctx.plugin._guard_zone_reply(text, ctx.umo, user_text=ctx.his_words)
    if guarded and guarded != text:
        logger.info("[amo] 地点黑名单重写: %s -> %s", text[:40], guarded[:40])
        return guarded
    return None


def _dec_date_guard(text, ctx):
    try:
        from .amo_date_context import guard_reply as _guard_date_reply
    except ImportError:
        from amo_date_context import guard_reply as _guard_date_reply
    return _guard_date_reply(text, ctx.plugin)


def _dec_mark_date_end(text, ctx):
    try:
        from .amo_date_context import mark_reply_date_end as _mark_date_end
    except ImportError:
        from amo_date_context import mark_reply_date_end as _mark_date_end
    _mark_date_end(ctx.plugin, text)
    return None


def _dec_trim(text, ctx):
    return ctx.plugin._trim_reply(text)


# ---------------------------------------------------------------------------
# 回复事实守卫（2026-11-17）：日程/状态对不上时兜底改写
#
# 只借用流水线的「调用位置」：地点老守卫先跑，新守卫在它后面兜底，
# 不新增 stage id（A/B golden 记录的调用顺序保持不变）。
# ---------------------------------------------------------------------------

_LEGACY_TRIP_KEYS = ("wander_trip", "travel", "follow_offer")
_LEGACY_TRIP_ON_ROAD = ("outbound", "traveling", "returning")


def _legacy_date_info(db, moment):
    """老库只有 date_now 时的只读约会视图：窗口内算在一起。"""
    empty = {"active": False, "together": False}
    if db is None:
        return empty
    try:
        current = db.kv_get("date_now", {}) or {}
        if not isinstance(current, dict) or not current.get("line"):
            return empty
        together = float(current.get("until") or 0) > moment.timestamp()
    except Exception as exc:
        logger.debug("[reply_guard] 读取旧约会键失败: %s", exc)
        return empty
    return {"active": together, "together": together}


def _legacy_trip_info(db, moment):
    """老库（wander_trip/travel/follow_offer）的只读旅游状态。"""
    if db is None:
        return {}
    for key in _LEGACY_TRIP_KEYS:
        try:
            data = db.kv_get(key, {}) or {}
        except Exception as exc:
            logger.debug("[reply_guard] 读取旧旅游键 %s 失败: %s", key, exc)
            continue
        if not isinstance(data, dict) or not data:
            continue
        phase = str(data.get("phase") or "")
        if phase in _LEGACY_TRIP_ON_ROAD:
            return {"phase": phase, "active": True,
                    "hotel": phase in ("outbound", "traveling")}
        if phase in ("done", "cancelled", "canceled"):
            continue
        start = str(data.get("start_day") or data.get("start") or "")
        try:
            days = int(data.get("days") or 0)
        except (TypeError, ValueError):
            days = 0
        if not start or days <= 0:
            continue
        try:
            start_day = _dt.date.fromisoformat(start)
        except ValueError:
            continue
        today = moment.date()
        if start_day <= today < start_day + _dt.timedelta(days=days):
            return {"phase": phase or "approved", "active": True, "hotel": True}
    return {}




def _current_activity(plugin):
    """日程快照里的当前活动（视图读不到时的兜底，缺什么就传空，不猜）。"""
    state = getattr(plugin, "state", None)
    if state is None:
        return {}
    try:
        return dict(state.current_activity() or {})
    except Exception as exc:  # cleanup: 读不到当前日程就不给活动
        logger.debug("[reply_guard] 读取当前日程失败: %s", exc)
        return {}


def fact_guard_context(plugin, activity=None, now=None):
    """收集 reply_fact_guard 需要的只读上下文：事实只读唯一视图（F1）。

    「当天到底发生了什么」（地点/活动/吃饭/健康/陪伴/宵夜/夜间）不再各自读日程/账本/
    健康，统一来自 amo_diary_guard.day_view（amo_fact_timeline.build_day_view）；
    date/trip/today_chat 是会话状态（kv/进程内），不属于四本账，仍走原只读路径。
    """
    moment = now if now is not None else _now()
    context: dict[str, Any] = {"now": moment, "activity": {}, "health": {}, "nearby": []}
    view = None
    try:
        try:
            from .amo_diary_guard import narrative_ctx, narrative_view
        except ImportError:
            from amo_diary_guard import narrative_ctx, narrative_view
        # N1：真实时钟路径复用生成上下文已经建好的当天视图（显式 now 不缓存）。
        view = narrative_view(plugin, moment, cache=(now is None))
        context = narrative_ctx(view, plugin=plugin, now=moment, activity=activity)
    except Exception as exc:  # cleanup: 视图读不到就退化成旧上下文
        logger.debug("[reply_guard] 事实视图读取失败: %s", exc)
    if not context.get("activity") and not (view or {}).get("events"):
        # 只有视图完全没有事实（老库/极简 fake）才退回日程快照；真实路径只读视图
        context["activity"] = _current_activity(plugin)
    # 视图有事件、但 now 落在两个日程块之间的空档（例如早上第一块 09:00 之前）时
    # 也没有 current：同样补位，否则事实守卫拿着空 activity 空转，动作矛盾会漏到
    # 出站（task-36 G46/t1）。补位只读 state.current_activity（和 prompt 快照同一个
    # 「此刻」），不读账本、不改事实。
    if not context.get("activity"):
        context["activity"] = _current_activity(plugin)
    # 约会/旅游只读 kv，不走 amo_date_session.load / amo_trip.load：
    # 那两个会自动迁移/收尾（会落库），守卫在发送前不该有副作用。
    # 新键优先；没有 phase 时只读回退到老键（同样不迁移、不落库）。
    db = getattr(plugin, "db", None)
    date = {}
    try:
        data = (db.kv_get("date_session", {}) or {}) if db is not None else {}
        phase = str(data.get("phase") or "") if isinstance(data, dict) else ""
        if phase:
            date = {"active": phase in ("active", "paused"),
                    "together": bool(data.get("together"))}
        else:
            date = _legacy_date_info(db, moment)
    except Exception as exc:
        logger.debug("[reply_guard] 读取约会状态失败: %s", exc)
    trip = {}
    try:
        data = (db.kv_get("trip", {}) or {}) if db is not None else {}
        phase = str(data.get("phase") or "") if isinstance(data, dict) else ""
        if phase:
            trip = {"phase": phase,
                    "active": phase in ("outbound", "traveling", "returning"),
                    "hotel": phase in ("outbound", "traveling")}
        else:
            trip = _legacy_trip_info(db, moment)
    except Exception as exc:
        logger.debug("[reply_guard] 读取旅游状态失败: %s", exc)
    context["date"] = date
    context["trip"] = trip
    context["today_chat"] = _today_chat(plugin, moment)
    return context



# ---------------------------------------------------------------------------
# 事实守卫「改口」自然度（N2）：固定句 -> 语气池
#
# reply_fact_guard 只负责「哪句冲突、改成什么意思」，改口的措辞太机械（每条都是
# 「就是有点累」「刚才在手机上聊了几句」）。这一层只换语气、不加事实：按心情/
# 关系/场景从 5-8 条说法里挑一条，不同轮换、不连续重复；第 0 条保留旧固定句，
# 旧 golden 与确定性断言不受影响。只改冲突的那一小句，前后情绪句原样保留。
# ---------------------------------------------------------------------------

#: 离线生成说明（A 方案）：先让 LLM 为每个规则/场景生成 20-30 条同义语气，
#: 再用规则筛掉带数字/金额/新人名/新地点/后台词的候选，人工保留 8 条进代码。
#: 运行时只做确定性轮换，不调用 LLM。
TONE_POOL_CURATION_NOTE = (
    "离线生成：每条规则/场景让模型写 20-30 条同义语气，要求只改语气、不加事实；"
    "再用规则筛掉带数字、金额、新人名、新地点、后台词的候选，人工保留 5-8 条。"
)

#: 规则 -> 场景组 -> 5-8 条说法（第 0 条 = 旧固定句；离线筛选后保留 8 条）

#: 生病时被改掉的「我好了」：只换语气，仍然是「还没好」的意思

#: 吃饭冲突：按原固定句/改动片段分组（同义改写，不新增「吃了什么」）

#: 活动冲突：键是去掉「在」之后的动作；按前缀决定要不要补「在」

#: 地点冲突：整句模板（{p}=原前缀 {t}=正确地点 {s}=原后缀），8 条/场景。
#: 模板不把「还/就」直接拼在「我在」后面（否则会出现「我在还在公司」）。

#: plugin 没有可写属性时的兜底轮换状态（进程内，测试可 reset）


def reset_tone_naturalness(plugin=None):
    """清空语气轮换状态（测试/重启语义用）。"""
    if plugin is None:
        _TONE_FALLBACK.clear()
        return
    try:
        setattr(plugin, "_fact_tone_state", {})
    except Exception as exc:  # cleanup: 极简 fake 没有属性也不影响主流程
        logger.debug("[reply_guard] 重置语气轮换失败: %s", exc)
























def naturalize_fact_rewrites(text, violations, plugin=None, activity=None,
                             now=None, seed=""):
    """把事实守卫的固定改口句换成语气池说法；只动冲突小句。

    返回 (新文本, 轮换记录)；任何一步失败都保留守卫原文，绝不拦发送。
    """
    value = str(text or "")
    actions: list[Any] = []
    if not value or not violations:
        return value, actions
    cursor = 0
    moment = now or _now()
    for item in violations or ():
        if not isinstance(item, dict):
            continue
        rule = str(item.get("rule") or "")
        sentence = str(item.get("sentence") or "")
        replacement = str(item.get("replacement") or "")
        if not rule or not replacement:
            continue
        position = value.find(replacement, cursor)
        if position < 0:
            position = value.find(replacement)
        if position < 0:
            continue
        prefix, middle, suffix = _changed_segment(sentence, replacement)
        item_variants = item.get("variants") or ()
        if not middle and not (rule == "place" and len(item_variants) >= 2):
            cursor = position + len(replacement)
            continue
        seed_text = "%s|%s|%s|%s" % (seed, rule, moment, value[:position])
        new_clause = _natural_clause(rule, prefix, middle, suffix, sentence,
                                     replacement, plugin, activity, seed_text,
                                     variants=item_variants)
        if not new_clause or new_clause == replacement:
            cursor = position + len(replacement)
            continue
        value = value[:position] + new_clause + value[position + len(replacement):]
        cursor = position + len(new_clause)
        item["natural"] = new_clause
        actions.append({"rule": rule, "before": replacement, "after": new_clause,
                        "middle": middle})
    return value, actions


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# B 方案开关与同步桥（选择器实现拆到 amo_tone_pick.py，控制 pipeline 体量）
# ---------------------------------------------------------------------------

TONE_LLM_PICK_ENABLED = False
TONE_LLM_PICK_TIMEOUT = 1.5
TONE_LLM_PICK_MAX_CANDIDATES = 8
TONE_LLM_STATS: Dict[str, int] = {
    "calls": 0, "picked": 0, "fallback": 0, "timeout": 0,
    "invalid": 0, "error": 0, "no_llm": 0, "fact_fallback": 0,
}


def reset_tone_llm_pick_stats():
    """清空 B 方案统计（测试/诊断用）。"""
    for key in list(TONE_LLM_STATS):
        TONE_LLM_STATS[key] = 0


def tone_llm_pick_stats():
    """B 方案只读统计：调用/选中/回退/超时/非法/无 LLM。"""
    return dict(TONE_LLM_STATS)


def set_tone_llm_pick(enabled):
    """显式开关 B（测试/运维命令用）；默认 False。"""
    global TONE_LLM_PICK_ENABLED
    TONE_LLM_PICK_ENABLED = bool(enabled)
    return TONE_LLM_PICK_ENABLED


def _env_truthy(name):
    return str(os.environ.get(name, "")).strip().lower() in (
        "1", "true", "yes", "on", "enable", "enabled")


def tone_llm_pick_enabled(plugin=None):
    """B 是否启用：模块开关 / plugin 属性 / 配置键 / 环境变量。"""
    if TONE_LLM_PICK_ENABLED:
        return True
    attr = getattr(plugin, "tone_llm_pick", None) if plugin is not None else None
    if isinstance(attr, bool):
        return attr
    cfg = getattr(plugin, "cfg_bool", None) if plugin is not None else None
    if callable(cfg):
        try:
            if bool(cfg("tone_llm_pick", False)):
                return True
        except Exception as exc:  # cleanup: 配置读不到就继续看环境变量
            logger.debug("[reply_guard] B 开关读取失败: %s", exc)
    return _env_truthy("AMO_TONE_LLM_PICK")


def _tone_llm_timeout(plugin):
    value = getattr(plugin, "tone_llm_pick_timeout", TONE_LLM_PICK_TIMEOUT)
    try:
        return max(0.05, min(5.0, float(value)))
    except (TypeError, ValueError):
        return TONE_LLM_PICK_TIMEOUT


def _guard_once(text, plugin, activity=None, now=None):
    """同步守卫一次：返回 (原文, 守卫改写, violations, context)。"""
    value = str(text or "")
    if not value:
        return value, value, [], {}
    try:
        try:
            from . import reply_fact_guard as _fact_guard
        except ImportError:
            import reply_fact_guard as _fact_guard
    except Exception as exc:
        logger.debug("[reply_guard] 模块加载失败: %s", exc)
        return value, value, [], {}
    try:
        context = fact_guard_context(plugin, activity, now)
        fixed, violations = _fact_guard.guard(value, context)
    except Exception as exc:
        logger.debug("[reply_guard] 守卫执行失败: %s", exc)
        return value, value, [], {}
    return value, fixed, violations, context


def _naturalize_safe(fixed, violations, plugin, activity, now):
    """A 方案的安全入口：任何异常都返回守卫原文。"""
    try:
        naturalized, _actions = naturalize_fact_rewrites(
            fixed, violations, plugin=plugin, activity=activity, now=now)
        return naturalized or fixed
    except Exception as exc:  # cleanup: 语气池失败就退回守卫固定句
        logger.debug("[reply_guard] 语气轮换失败: %s", exc)
        return fixed


def apply_fact_guard(text, plugin, activity=None, now=None):
    """回复发送前跑一次「不矛盾」守卫；日志只打 debug，失败绝不拦发送。"""
    value, fixed, violations, context = _guard_once(text, plugin, activity, now)
    if not value:
        return value
    for item in violations:
        logger.debug("[reply_guard] rule=%s said=%s -> %s | %s",
                     item.get("rule"), item.get("sentence"),
                     item.get("replacement"), item.get("reason"))
    naturalized = fixed
    tone_actions = []
    try:
        naturalized, tone_actions = naturalize_fact_rewrites(
            fixed, violations, plugin=plugin, activity=activity, now=now)
    except Exception as exc:  # cleanup: 语气池失败就退回守卫固定句
        logger.debug("[reply_guard] 语气轮换失败: %s", exc)
    for item in tone_actions:
        logger.debug("[reply_guard] tone rule=%s %s -> %s",
                     item.get("rule"), item.get("before"), item.get("after"))
    if violations and tone_llm_pick_enabled(plugin):
        try:
            for item in violations:
                if isinstance(item, dict) and not item.get("natural"):
                    item["natural"] = str(item.get("replacement") or "")
            setattr(plugin, "_tone_pick_pending", {
                "text": naturalized or fixed, "violations": violations,
                "context": context, "activity": activity, "now": now})
        except Exception as exc:  # cleanup: 不能记 pending 就只走 A
            logger.debug("[reply_guard] B pending 记录失败: %s", exc)
    return naturalized or fixed or value


async def apply_fact_guard_async(text, plugin, activity=None, now=None):
    """可开关 B：默认走 A；开关打开且命中冲突时让 LLM 在候选里选序号。"""
    if _tone_pick is None:
        value, fixed, violations, _context = _guard_once(
            text, plugin, activity, now)
        return _naturalize_safe(fixed, violations, plugin, activity, now) \
            if value else value
    return await _tone_pick.apply_fact_guard_async(
        text, plugin, activity, now)


async def apply_pending_tone_pick_async(text, plugin):
    """消费同步守卫留下的冲突记录：让 LLM 在候选里选，替换 A 的确定性说法。"""
    if _tone_pick is None:
        return str(text or "")
    return await _tone_pick.apply_pending_tone_pick_async(text, plugin)


if _tone_pick is not None:
    _llm_pick_index = _tone_pick._llm_pick_index
    _llm_naturalize = _tone_pick._llm_naturalize

def apply_tone_guard(text, plugin=None, umo="", record=True):
    """口头禅去逐字复读（在 apply_fact_guard 之后跑）；失败只记 debug，绝不拦发送。"""
    value = str(text or "")
    if not value.strip():
        return value
    try:
        try:
            from . import tone_guard as _tone_guard
        except ImportError:
            import tone_guard as _tone_guard
        fixed, actions = _tone_guard.guard(
            value, plugin=plugin, umo=str(umo or ""), record=bool(record))
    except Exception as exc:
        logger.debug("[tone_guard] 守卫执行失败: %s", exc)
        return value
    for item in actions or ():
        logger.debug("[tone_guard] %s phrase=%s reason=%s: %s -> %s",
                     item.get("action"), item.get("phrase"), item.get("reason"),
                     item.get("before"), item.get("after"))
    return fixed or value


def tone_guard_prompt_rule():
    """提示词侧的【口头禅】规则（文案在 tone_guard；这里只做可失败的安全转发）。"""
    try:
        try:
            from .tone_guard import prompt_rule as _prompt_rule
        except ImportError:
            from tone_guard import prompt_rule as _prompt_rule
        return str(_prompt_rule() or "")
    except Exception as exc:
        logger.debug("[tone_guard] 提示词规则读取失败: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# 跨轮/跨段状态去重 + 问吃啥先答（在事实守卫之后加的一层，不改守卫逻辑）
# ---------------------------------------------------------------------------
#
# 现场：她先主动说「药拿好了，等会儿打车回去」，他回「你中午吃啥」，她又把
# 「我这边拿了药，正等车回去呢」说了一遍，还切成 4 段各说一次。
# 这一层只做两件事：同一状态（药/等车/打车/在医院/回家）最多说一句；他问吃啥
# 就先答吃了什么（事实来自唯一视图），状态句往后放且不重复。

#: 同一件事的说法（药/车/医院/回家）；一句话命中多组只算一次
PROACTIVE_STATUS_SECONDS = 30 * 60
FOOD_QUESTION_RE = re.compile(
    r"(吃啥|吃什么|吃点什么|吃的什么|午饭|中午吃|晚饭|晚上吃|吃了没|吃了吗|吃饭了没)")
#: 他问的答案类型 -> 回复里什么算答到了（整句复读删句时用来保留答案）
_ANSWER_RULES = (
    (FOOD_QUESTION_RE, re.compile(r"吃|饭|面|粥|外卖|饿|饱")),
    (re.compile(r"在哪|哪里|哪呢|在家吗|到公司|到哪|出门了"),
     re.compile(r"在家|在公司|在路|在商场|在餐厅|在办公室|在街上|在景区|在酒店|"
                r"在医院|在健身房|在学校|这边|到了|到家|出门|外面|地铁|公交|车上|"
                r"家|公司|商场|餐厅|医院")),
    (re.compile(r"几点|什么时候|多久|回不回来"),
     re.compile(r"\d|马上|快了|晚点|一会儿|下班|到家|回去"
                r"|在家|已经在家|这就回|接着回|这就来")),
    (re.compile(r"干嘛|做什么|忙什么|干什么|偷懒"),
     re.compile(r"上班|加班|开会|睡|休息|吃|做饭|逛|追剧|看|写|改|收拾|玩|练|"
                r"健身|刷|忙|瘫|窝|躺|做")),
    (re.compile(r"跟谁|谁在一起|一个人|旁边有人|有人吗"),
     re.compile(r"自己|一个人|没人|跟|和|我们|俩|同事|朋友|他|她|妈|爸")),
)
#: 本轮他问的话：(轮次, 原话)；分段/发送去重靠它判断整句复读有没有把答案删掉
_TURN_USER_TEXT: Dict[str, Any] = {}
#: 本轮「原始候选」：(轮次, 进入去重层时的回复)。
#: 只给 apply_chat_safety 最后一道守卫用：候选已经答到点上，就不许被通用兜底句覆盖。
_TURN_SOURCE: Dict[str, Any] = {}
#: 本轮「修复句候选」：(轮次, repair_reply 采用的句子)。
#: repair 在 exit 段之后、send 段之前跑；不换候选的话 validate/dedupe 的
#: 复检会拿旧候选把修复句盖掉（analyst-m G23 实测）。
_TURN_REPAIRED: Dict[str, Any] = {}
#: 通用兜底句：她这一轮其实没答上来时用的占位话。整条回复归一化后等于其中一句，就算兜底。
#: 「候选优先于兜底」是数据发出的单一优先级，不再让各守卫各写一份「保留原句」补丁。
#: 句子本身只在 domains.reply.repetition_guard.FALLBACKS["generic"] 一处维护。
_GENERIC_BODIES = _fallbacks.generic_bodies()
#: 每个会话最近发出去的状态（跨轮去重依据；只在内存，重启后靠 _recent 兜底）
#: 当前这一轮回复的批次号（分段去重只在同一轮内生效，避免误删跨轮正常消息）


def reset_repetition_memory(umo=""):
    """清掉整句去重/状态去重的进程内记忆（验收 harness 每个样本独立会话用）。"""
    reset_status_memory(umo)
    _repeat_guard.reset(umo)
    if umo:
        _TURN_USER_TEXT.pop(str(umo), None)
        _TURN_SOURCE.pop(str(umo), None)
        _TURN_REPAIRED.pop(str(umo), None)
    else:
        _TURN_USER_TEXT.clear()
        _TURN_SOURCE.clear()
        _TURN_REPAIRED.clear()
    reset_semantic_recheck_stats()












try:  # 状态去重内核：2026-11-18 搬进 domains/reply/status_text.py
    from .domains.reply.status_text import (  # noqa: F401  re-export
        STATUS_GROUPS, STATUS_MEMORY_LIMIT, _ACTIVE_TURN, _SENTENCE_SPLIT_RE,
        _STATUS_MEMORY, _TURN_SEGMENTS, _TURN_SEQ, _drop_repeated_status,
        _limit_status_sentences, _remember_status, _status_said, begin_status_turn,
        reset_status_memory, status_tokens,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.reply.status_text import (  # noqa: F401  re-export
            STATUS_GROUPS, STATUS_MEMORY_LIMIT, _ACTIVE_TURN, _SENTENCE_SPLIT_RE,
            _STATUS_MEMORY, _TURN_SEGMENTS, _TURN_SEQ, _drop_repeated_status,
            _limit_status_sentences, _remember_status, _status_said, begin_status_turn,
            reset_status_memory, status_tokens,
        )

#: 整句级去重：实现在 domains.reply.repetition_guard，这里只保留分段记忆
DEDUP_WINDOW = _repeat_guard.DEDUP_WINDOW
#: 同一轮里已经发出去的分段（归一化后的整句序列，分段去重只看本轮）






def _answers_question(text, question):
    """「他问的这类问题」，这段话里有没有给出对应答案。"""
    body = str(text or "")
    ask = str(question or "")
    if not body.strip() or not ask.strip():
        return False
    for pattern, answer in _ANSWER_RULES:
        if pattern.search(ask) and answer.search(body):
            return True
    return False


#: 删句后以这些词开头，说明前面被删掉的是前提/条件，只剩结论（线上：
#: 「等你病好了，再谈收拾我这件事」→「再谈收拾我这件事」）。
_ORPHAN_CONCLUSION_RE = re.compile(r"^(再(谈|说|聊|看|讲|试|想)|那就|这才|才能|才说)")


def _has_orphan_conclusion(text):
    for part in re.split(r"(?<=[。！？!?~～\n])", str(text or "")):
        body = part.strip().lstrip("，,、 ")
        if body and _ORPHAN_CONCLUSION_RE.match(body):
            return True
    return False


def _keep_answer(original, fixed, question, dropped):
    """整句复读删句后答案被删没了，就保留原句：答到问题优先于「不复读」。

    只在他问的是可判定的答案（吃了没/在哪/几点回/干嘛/跟谁）时生效；
    「想你」这类没有答案的复读仍然照旧删/换短句。
    """
    if not dropped:
        return fixed
    if _has_orphan_conclusion(fixed) and not _has_orphan_conclusion(original):
        return original
    if _answers_question(fixed, question):
        return fixed
    if _answers_question(original, question):
        return original
    return fixed


def _is_generic_fallback(text):
    """整条回复是不是通用兜底句（归一化后等于注册表 FALLBACKS["generic"] 里的一句）。"""
    body = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(text or ""))
    return bool(body) and body in _GENERIC_BODIES


def _answered_intent(original, user_text):
    """原始候选有没有答到他这一句问的意图（先按问句守卫的答案词表，再按通用答案规则）。"""
    body = str(original or "")
    ask = str(user_text or "")
    if not body.strip() or not ask.strip():
        return False
    try:
        from .amo_question_guard import _answered as _q_answered
        from .amo_question_guard import _category as _q_category
    except ImportError:
        from amo_question_guard import _answered as _q_answered
        from amo_question_guard import _category as _q_category
    try:
        category = _q_category(ask)
        if category and _q_answered(category, body):
            return True
    except Exception as exc:  # cleanup: 判不出来就退回通用答案规则
        logger.debug("[amo] chat_safety.intent 失败: %s", exc)
    # 问句守卫的答案词表偏窄（when 类只认「到家/路上/马上/…」），判不出来时
    # 仍按通用答案规则再看一遍，别把「其实答到了」的候选判成没答到。
    return _answers_question(body, ask)


# 1b-1（2026-11-22）：原 _prefer_answered_candidate 已删除。它在 apply_chat_safety 里
# 裸还原整个候选，和 amo_rewrite_safety.recover（带兜底/多池门限）以及
# final_semantic_recheck（带安全/事实约束）职责重叠；三层叠加会导致同一候选一轮
# 被还原多次。现在只保留后两层：recover 负责“现行是兜底/多池”，final recheck 负责
# “候选答到、改完答不到”，前者带安全改写、后者带事实约束。
def _restore_partial_candidate(text, umo, user_text):
    """候选被守卫删掉一段、只剩残句时还原（1b-1：并入单一出口，不再单开一层）。"""
    key = str(umo or "").strip()
    entry = _TURN_SOURCE.get(key)
    if not entry:
        return text
    turn, original = entry
    if turn != _ACTIVE_TURN.get(key):
        return text
    original = str(original or "").strip()
    if not original or not _answered_intent(original, user_text):
        return text
    left = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(text or ""))
    right = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", original)
    if len(left) < 6 or left == right or left not in right:
        return text
    if _answers_question(text, user_text):
        return text
    return original


def _turn_question(umo):
    """这一轮他问的话；跨轮/跨段的旧问题不再参与保留答案判断。"""
    key = str(umo or "").strip()
    entry = _TURN_USER_TEXT.get(key)
    if not entry:
        return ""
    turn, question = entry
    return str(question or "") if turn == _ACTIVE_TURN.get(key) else ""


# ---------------------------------------------------------------------------
# 唯一后置语义复检（task-29，2026-11-22）
#
# 现场（审计 chat_coherence.md 3.2/3.4）：唯一会判「有没有回答他最后一句」的
# grounding_critic 在 polish 里（生成期），而 _align_arrival / _align_swim_where /
# state_consistency.rewrite / question_guard.answer 都在它之后整句删除/替换；
# 换完之后没有任何一层再问「这还是不是对他那句话的回答」，于是真实模型层把
# 「记得呀，那天你穿白衬衫…」换成「我还在路上呢，快到了。」这类通用句。
#
# 这里给所有会整句删除/替换的守卫加同一个收口：改完再判一次（判据在
# amo_grounding_critic.post_rewrite_recheck），原候选答到、改完答不到 → 保留候选。
# 各守卫不再各写一份「保留原句」白名单。
# ---------------------------------------------------------------------------

#: 后置语义复检埋点（验收口径「守卫改写后语义复检失败 = 0」看这里）：
#: calls=过闸且确实动了文本的次数，restored=还原候选次数，blocked=因安全/事实拒绝还原次数。
_RECHECK_STATS: Dict[str, int] = {"calls": 0, "restored": 0, "blocked": 0}
#: 现行文本是事实守卫产物时的识别词：消费守卫把「买了」改成「还没买」，
#: 这种事实改写优先于语义还原（不能为了答上问题就把假消费还原回来）。
_SAFETY_REPLY_HINTS = ("还没买",)


def reset_semantic_recheck_stats():
    """清空后置语义复检埋点（验收 harness 每轮独立统计用）。"""
    for key in _RECHECK_STATS:
        _RECHECK_STATS[key] = 0


def semantic_recheck_stats():
    """后置语义复检指标：calls / restored / blocked。"""
    return dict(_RECHECK_STATS)


def _is_any_fallback(text):
    """整条回复是不是某句通用兜底（通用/状态复读/短句池，不只 generic 一池）。"""
    try:
        try:
            from .amo_grounding_critic import is_fallback as _shared_fallback
        except ImportError:
            from amo_grounding_critic import is_fallback as _shared_fallback
        return bool(_shared_fallback(text))
    except Exception as exc:  # cleanup: 判不出来就退回通用池
        logger.debug("[amo] chat_safety.fallback 失败: %s", exc)
        return _is_generic_fallback(text)


def _turn_candidate(umo):
    """本轮候选；只认本轮（跨轮候选不许盖这一轮）。

    repair 采用修复句后优先返回修复句（_TURN_REPAIRED）：send 段的复检/候选优先
    必须以修复句为准，不能再用旧候选把修复结果盖掉（analyst-m G23）。
    """
    key = str(umo or "").strip()
    active = _ACTIVE_TURN.get(key)
    repaired = _TURN_REPAIRED.get(key)
    if repaired is not None:
        turn, fresh = repaired
        if turn == active:
            return str(fresh or "")
        _TURN_REPAIRED.pop(key, None)
    entry = _TURN_SOURCE.get(key)
    if not entry:
        return ""
    turn, original = entry
    if turn != active:
        return ""
    return str(original or "")


def mark_turn_repaired(plugin, umo, text):
    """repair 成功后把本轮候选同步成修复句（候选 store + 本轮候选表）。

    - _turn_candidate → 修复句：validate 的 _post_recheck / chat_safety 复检不再还原旧候选；
    - amo_rewrite_safety.remember_candidate → 修复句：recover_rewrite_damage 也不会用旧候选。
    """
    key = str(umo or "").strip()
    body = str(text or "").strip()
    if not key or not body:
        return
    turn = _ACTIVE_TURN.get(key)
    if turn is not None:
        _TURN_REPAIRED[key] = (turn, body)
    try:
        try:
            from .amo_rewrite_safety import remember_candidate as _remember
        except ImportError:
            from amo_rewrite_safety import remember_candidate as _remember
        _remember(plugin, key, body)
    except Exception as _amo_exc:  # cleanup: 登记失败不影响发送，复检会退回旧候选
        _note_swallowed("amo_reply_pipeline.mark_turn_repaired", _amo_exc)


def _media_clean(text):
    """出站前剥掉媒体占位符（候选里可能带 [我发的照片]，永远不许进人话）。"""
    body = str(text or "")
    try:
        try:
            from .amo_message import strip_media_markers as _strip_media
        except ImportError:
            from amo_message import strip_media_markers as _strip_media
        return str(_strip_media(body) or "").strip()
    except Exception as exc:  # cleanup: 剥不掉就按原文，validate 层还会再剥一次
        logger.debug("[amo] 候选还原剥离媒体标记失败: %s", exc)
        return body.strip()


def _restore_candidate(original, plugin=None):
    """要回候选前的出站卫生：媒体标记剥离 + 消费事实守卫重跑。

    「候选优先于语义」不等于「候选优先于事实」：没记账却说买了 N 元的候选，
    还原前先过一遍消费守卫，别为了答上问题把假消费发出去。
    """
    body = _media_clean(original)
    try:
        try:
            from .amo_purchase_guard import rewrite as _purchase_guard
        except ImportError:
            from amo_purchase_guard import rewrite as _purchase_guard
        body = str(_purchase_guard(body, plugin) or body).strip()
    except Exception as exc:  # cleanup: 消费守卫失败就不额外改
        logger.debug("[amo] 候选还原消费守卫失败: %s", exc)
    return body


def _unsafe_candidate(candidate):
    """候选本身带模型自曝/后台泄漏/防御话术时不许还原（安全优先于语义）。"""
    body = str(candidate or "")
    if not body.strip():
        return True
    try:
        try:
            from .amo_shared_text import (looks_like_ai_admission,
                                          looks_like_defensive_reply)
        except ImportError:
            from amo_shared_text import (looks_like_ai_admission,
                                         looks_like_defensive_reply)
        if looks_like_ai_admission(body) or looks_like_defensive_reply(body):
            return True
    except Exception as exc:  # cleanup: 安全判据不可用就只用模型泄漏判据
        logger.debug("[amo] 候选安全判据失败: %s", exc)
    try:
        return bool(looks_like_model_leak(body))
    except Exception:  # cleanup: 判不出来不阻断
        return False


def _protected_current(text):
    """现行文本是安全挡话/消费事实守卫的产物时，不许被候选覆盖。"""
    body = str(text or "").strip()
    if not body:
        return False
    if any(hint in body for hint in _SAFETY_REPLY_HINTS):
        return True
    try:
        try:
            from .amo_grounding_critic import is_blocking_fallback as _blocking
        except ImportError:
            from amo_grounding_critic import is_blocking_fallback as _blocking
        return bool(_blocking(body))
    except Exception:  # cleanup: 判据不可用就不额外拦
        return False


def _recheck_once(original, text, user_text):
    """跑一次纯判定（不做出站卫生），返回判完的文本。"""
    try:
        try:
            from .amo_grounding_critic import post_rewrite_recheck as _recheck
        except ImportError:
            from amo_grounding_critic import post_rewrite_recheck as _recheck
        return str(_recheck(
            original, text, user_text,
            answered=lambda body: _answered_intent(body, user_text)))
    except Exception as exc:  # cleanup: 复检失败就保持改写结果（不能把回复弄丢）
        logger.debug("[amo] 后置语义复检失败: %s", exc)
        return str(text or "")


# ---------------------------------------------------------------------------
# 救援链：一个 runner + 两张 profile 表 + 两个薄壳（M2）
# ---------------------------------------------------------------------------
# final_semantic_recheck（整条，recheck）与 apply_segment_repeat_guard（分段，segment）
# 共享同一个 runner，差别只在阶段表。纯判据仍是 amo_grounding_critic.post_rewrite_recheck
# 的独立函数（judge 阶段只调用它，判据一行不动）。
#
# 契约（同 amo_state_consistency._run_assembly）：_stage_xxx(ctx) -> str | None；
# 原地写 ctx.text / ctx.fixed；返回非空 reason = 立即短路（只表示「为什么停」）。
# 空串不是 stop：整段都是复读时这一段就是空串（send 按空段跳过），不算短路。
# 本链**不调模型、不产出固定句**：repair-owned 的修复句只可能被原样保留（它在
# post_rewrite_recheck 里同时是候选与现行），绝不会被换短句、也不会多调一次模型。


@dataclass
class _RescueCtx:
    """一次复检/分段救援的上下文（stages 只写自己的字段）。"""

    plugin: Any = None
    umo: Any = ""
    user_text: Any = ""
    profile: str = "recheck"
    text: Any = ""
    current: str = ""
    candidate: str = ""
    question: str = ""
    fixed: str = ""
    safe: str = ""
    refs: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def _stage_judge(ctx):
    """R1 recheck：纯判据判一次（没变就原样返回，不计数）。"""
    if not ctx.candidate.strip():
        return "no_candidate"  # SR1：候选为空 → 原样返回，连统计都不动
    # 本轮他真正问的那句以登记表为准：send 阶段传进来的 user_text 可能已经过期
    # （扫近聊取到的是更早的 user 行），用它复检会把「其实答到了」的回复判成没答到
    # （task-36 G09/t5：「忙什么呢」被记成上一轮的「路上堵死了」）。
    ctx.question = _turn_question(ctx.umo) or str(ctx.user_text or "")
    ctx.fixed = _recheck_once(ctx.candidate, ctx.current, ctx.question)
    if ctx.fixed == ctx.current:
        ctx.text = ctx.fixed  # SR2：判完没变
        return "unchanged"
    _RECHECK_STATS["calls"] += 1
    return None


def _stage_protect(ctx):
    """R2 recheck：现行是安全挡话/事实守卫产物时不许被候选覆盖（SR3）。"""
    if _protected_current(ctx.current):
        _RECHECK_STATS["blocked"] += 1
        logger.info("[amo] 后置复检拒绝还原（安全挡话/事实守卫）: %s", ctx.current[:40])
        return "protected"
    return None


def _stage_restore(ctx):
    """R3 recheck：候选出站卫生（媒体占位符剥离 + 消费事实守卫重跑）。"""
    ctx.safe = _restore_candidate(ctx.candidate, ctx.plugin)
    return None


def _stage_unsafe(ctx):
    """R4 recheck：候选本身带自曝/后台泄漏/防御话术就不许还原（SR4）。"""
    if _unsafe_candidate(ctx.safe):
        _RECHECK_STATS["blocked"] += 1
        logger.info("[amo] 后置复检拒绝还原（候选不安全）: %s", ctx.safe[:40])
        return "unsafe"
    return None


def _stage_rejudge(ctx):
    """R5 recheck：卫生改写过的候选再判一次；仍答不到就不还原（SR5/SR6）。"""
    fixed = ctx.fixed
    if ctx.safe != ctx.candidate:
        retry = _recheck_once(ctx.safe, ctx.current, ctx.question)
        if retry == ctx.current:
            _RECHECK_STATS["blocked"] += 1
            return "recheck_failed"
        fixed = retry
    _RECHECK_STATS["restored"] += 1
    logger.info("[amo] 改写后语义复检：还原候选 %s -> %s", ctx.current[:40], fixed[:40])
    ctx.text = fixed
    return "restored"


def _stage_refs(ctx):
    """S1 segment：参考文本 = 最近几条她自己说过的话 + 本轮前面已发出去的段。"""
    if not ctx.current.strip():
        return "empty"  # SR7：空输入原样返回（不读近聊、不动分段记忆）
    key = str(ctx.umo or "").strip()
    earlier = (list(_TURN_SEGMENTS.get(key) or ())
               if _ACTIVE_TURN.get(key) is not None else [])
    ctx.refs = _repeat_guard.recent_reference(ctx.plugin, ctx.umo, DEDUP_WINDOW) + earlier
    return None


def _stage_dedupe(ctx):
    """S2 segment：整句复读删句；整段都是复读就删成空串（空串不是 stop）。"""
    ctx.fixed, ctx.dropped = _repeat_guard.dedup_or_fallback(ctx.current, ctx.umo, ctx.refs)
    return None


def _stage_keep_answer(ctx):
    """S3 segment：他这一问的答案落在这一段里就保住（答案优先于不复读）。"""
    ctx.fixed = _keep_answer(ctx.current, ctx.fixed, _turn_question(ctx.umo), ctx.dropped)
    for item in ctx.dropped:
        logger.debug("[dedup] 分段复读已删掉: %s", item[:40])
    return None


def _stage_remember(ctx):
    """S4 segment：这一段记进本轮分段记忆（只有 push 逐段路径走，跨轮语义不变）。"""
    key = str(ctx.umo or "").strip()
    rows = _TURN_SEGMENTS.setdefault(key, [])
    body = _repeat_guard.compare_body(ctx.fixed)
    if body:
        rows.append(body)
        del rows[:-STATUS_MEMORY_LIMIT]
    ctx.text = ctx.fixed
    return None


#: 阶段实现表（名字 -> 函数）。判据/异常策略全在函数内，表只负责顺序。
_STAGES = {
    "judge": _stage_judge,
    "protect": _stage_protect,
    "restore": _stage_restore,
    "unsafe": _stage_unsafe,
    "rejudge": _stage_rejudge,
    "refs": _stage_refs,
    "dedupe": _stage_dedupe,
    "keep_answer": _stage_keep_answer,
    "remember": _stage_remember,
}

#: 两张阶段表（唯一事实源）。顺序即真实行为，不许「优化」：
#: - recheck：判 → 保护 → 卫生 → 不安全 → 再判（与 task-29 的直写顺序逐条对应）；
#: - segment：参考文本 → 删复读 → 保答案 → 记本轮段。
#: 不注册进 amo_reply_pipeline.PIPELINE：golden 顺序锁死，M2 不占全局 stage 位。
_CHAINS = {
    "recheck": ("judge", "protect", "restore", "unsafe", "rejudge"),
    "segment": ("refs", "dedupe", "keep_answer", "remember"),
}


def _run_rescue(text, ctx, profile):
    """唯一 runner：按 profile 查表逐步，首个非空 reason 立即短路。

    recheck 的输入/输出都是整条回复（候选为空时把入参**原样**返回，连 str() 都
    不加强转）；segment 的输入/输出都是单段文本（空输入原样返回、整段复读返回
    空串，由 send 阶段按空段跳过）。
    """
    ctx.profile = profile
    ctx.current = str(text or "")
    ctx.text = text if profile == "recheck" else ctx.current
    for name in _CHAINS[profile]:
        if _STAGES[name](ctx):
            break
    return ctx.text


def final_semantic_recheck(text, plugin=None, umo="", user_text="", original=None):
    """唯一后置语义复检：会整句删除/替换的守卫跑完后统一过这一道（task-29）。

    调用点（顺序即真实装饰链）：
    - apply_narrative_repetition：整句/状态去重之后；
    - apply_chat_safety：收口，覆盖其前的 _align_arrival/_align_swim_where 和其中
      的 state_consistency.rewrite / question_guard.answer；
    - amo_state_consistency.rewrite / validate_reply_assembly：自身出口再保一道。

    判定在 amo_grounding_critic.post_rewrite_recheck：原候选答到他这一句、改完后
    答不到（或整条只剩通用兜底句）→ 保留候选。这里只负责取本轮候选 + 出站卫生：
    媒体占位符剥离、消费事实守卫重跑、模型自曝/后台泄漏/安全挡话不还原。
    B2：本函数**不产出固定句**（固定句池已停产出），改写结果为空就原样返回空串，
    由调用方决定（chat 交出口 repair，非 chat 用原文兜底）。
    M2：本函数只是 _CHAINS["recheck"] 的薄壳（统计/日志/候选契约不变）。
    """
    candidate = str(original or "") if original is not None else _turn_candidate(umo)
    ctx = _RescueCtx(plugin=plugin, umo=umo, user_text=user_text,
                     candidate=candidate)
    return _run_rescue(text, ctx, "recheck")


def complete_deleted_answer(text, plugin=None, umo="", user_text="", candidate=""):
    """整句删除/替换后出口补答：他这一问的答案被删掉时把候选要回来。

    问句守卫是「答到点上」的唯一判据；删句层（分段去重/场景剥离）不该为了补答再
    复制一份模板，统一从这里进。candidate 传删句前的正文：它答到了就把答案原样
    要回来（答案优先于不复读）。B1：都没答到时返回**空串**（不再用状态模板填一句）；
    调用方自己决定是否用原文兜底——chat 走出口 repair，非 chat 必须保留原文。
    """
    try:
        try:
            from .amo_question_guard import answer as _question_answer
        except ImportError:
            from amo_question_guard import answer as _question_answer
        return _question_answer(_turn_question(umo) or str(user_text or ""), plugin,
                                text, candidate=str(candidate or ""))
    except Exception as exc:  # cleanup: 补答失败保持删句结果，绝不影响出站
        logger.debug("[amo] 出口问句补答失败: %s", exc)
        return text


def _answer_food_first(text, plugin, user_text, now=None):
    """他问「吃啥/吃什么」：先用事实视图答吃了什么，再补状态。"""
    question = str(user_text or "")
    if not question or not FOOD_QUESTION_RE.search(question):
        return text
    head = _SENTENCE_SPLIT_RE.split(str(text or ""), 1)[0]
    if any(word in head for word in ("吃", "饭", "面", "饺子", "粥", "外卖", "煮")):
        return text
    try:
        try:
            from .amo_diary_guard import food_answer
        except ImportError:
            from amo_diary_guard import food_answer
        answer = str(food_answer(plugin, now) or "").strip()
    except Exception as exc:  # cleanup: 答不出吃啥就不强塞
        logger.debug("[dedup] 吃饭事实读取失败: %s", exc)
        answer = ""
    if not answer:
        return text
    body = str(text or "").lstrip()
    if answer.endswith(("。", "！", "？", "~", "～")):
        return answer + body
    return answer + "。" + body


def recover_rewrite_damage(current, plugin, umo="", user_text=""):
    """候选优先出口（task-40）：完整候选被固定兜底/多池拼装/语法破损覆盖时还原。

    判定与安全改写在 amo_rewrite_safety + amo_persona_guard.sanitize_candidate；
    只有存在本轮候选（装饰链入口登记）且他这一轮确实有输入时才生效，主动消息
    没有 user_text 不会走到这里。
    """
    if not str(user_text or "").strip():
        return current
    try:
        try:
            from .amo_rewrite_safety import recover as _recover
            from .amo_persona_guard import guard_candidate as _guard_candidate
            from .amo_persona_guard import sanitize_candidate as _sanitize
        except ImportError:
            from amo_rewrite_safety import recover as _recover
            from amo_persona_guard import guard_candidate as _guard_candidate
            from amo_persona_guard import sanitize_candidate as _sanitize
    except Exception as exc:  # cleanup: 候选优先层不可用就保持现行文本
        logger.debug("[amo] 候选优先层不可用: %s", exc)
        return current
    try:
        return _recover(
            plugin, umo, current,
            lambda value: _sanitize(plugin, umo, value, str(user_text or "")),
            guard=lambda value: _guard_candidate(plugin, umo, value, str(user_text or "")),
            source="chat", user_text=str(user_text or ""))
    except Exception as exc:  # cleanup: 还原失败绝不把回复弄丢
        logger.debug("[amo] 候选优先还原失败: %s", exc)
        return current


def apply_chat_safety(text, plugin, umo="", user_text="", history_text=""):
    """聊天回复的「最后一道确定性守卫」（2026-11-21 审计：聊天不走 send 阶段）。

    只跑确定性改写，不调模型：极端/暴力词 → 身份 → 人名 → 消费 → 反催问。
    B1 出口契约：守卫把回复删空时返回**空串**（不再用「or 原文」把删掉的内容
    捞回来），空串由 chat 出口 repair（amo_reply_pipeline.repair_reply）重生成；
    修复失败才回退「guarded or raw」。非 chat 来源不调用本函数。
    """
    value = str(text or "")
    # 本轮他真正问的那句以登记表为准（apply_narrative_repetition 已登记）：调用方
    # 传进来的 user_text 在 send/复检路径上可能是上一轮的旧问句，用它补答会把这一轮
    # 改成上一轮的答案（task-36 回归：send 阶段扫近聊取到更早的 user 行）。
    user_text = _turn_question(umo) or str(user_text or "")
    if not value.strip():
        # 空串进、空串出：不再用问句模板补一句固定话（B1），交出口 repair 重生成。
        return ""
    try:
        try:
            from .amo_share_ledger import mark_reacted as _mark_reacted
        except ImportError:
            from amo_share_ledger import mark_reacted as _mark_reacted
        if user_text:
            _mark_reacted(plugin)
    except Exception as exc:
        logger.debug("[amo] chat_safety.share_reacted 失败: %s", exc)
    # 单一优先级（全链路只在这里裁决一次）：这一轮原始候选已经答到他的问题，
    # 先把候选恢复回来；后面的确定性改写都在候选上做，通用兜底句不许再盖掉它。
    # 候选没答到，就保持上游守卫给的结果（该改的照改）。
    try:
        try:
            from .amo_immersion_guard import fix_identity, sanitize_extreme
        except ImportError:
            from amo_immersion_guard import fix_identity, sanitize_extreme
        value = fix_identity(sanitize_extreme(value))
    except Exception as exc:
        logger.debug("[amo] chat_safety.immersion 失败: %s", exc)
    try:
        try:
            from .amo_names import sanitize as _names_sanitize
        except ImportError:
            from amo_names import sanitize as _names_sanitize
        value = _names_sanitize(value) or value
    except Exception as exc:
        logger.debug("[amo] chat_safety.names 失败: %s", exc)
    try:
        try:
            from .amo_purchase_guard import rewrite as _purchase_guard
        except ImportError:
            from amo_purchase_guard import rewrite as _purchase_guard
        value = _purchase_guard(value, plugin)
    except Exception as exc:
        logger.debug("[amo] chat_safety.purchase 失败: %s", exc)
    try:
        try:
            from .amo_anti_nag import rewrite as _anti_nag
        except ImportError:
            from amo_anti_nag import rewrite as _anti_nag
        value = _anti_nag(value, user_text)
        if not str(value or "").strip():
            # 催问句是整条：删空 = 本轮不发。绝不能让函数末尾的「原文兜底」
            # 把催问句捞回来（task-3b C1 的空串哨兵契约）。
            return ""
    except Exception as exc:
        logger.debug("[amo] chat_safety.anti_nag 失败: %s", exc)
    try:
        try:
            from .amo_companion_guard import rewrite as _companion_guard
        except ImportError:
            from amo_companion_guard import rewrite as _companion_guard
        try:
            _activity = plugin.state.current_activity() or {}
        except Exception:
            _activity = {}
        value = _companion_guard(value, _activity)
    except Exception as exc:
        logger.debug("[amo] chat_safety.companion 失败: %s", exc)
    try:
        try:
            from .amo_marriage_guard import rewrite as _marriage_guard
        except ImportError:
            from amo_marriage_guard import rewrite as _marriage_guard
        value = _marriage_guard(value)
    except Exception as exc:
        logger.debug("[amo] chat_safety.marriage 失败: %s", exc)
    try:
        try:
            from .amo_voice_guard import clean as _voice_clean
            from .amo_persona_store import active_pack as _active_pack
        except ImportError:
            from amo_voice_guard import clean as _voice_clean
            from amo_persona_store import active_pack as _active_pack
        _public = "Group" in str(umo or "")
        _cleaned = _voice_clean(value, _active_pack(plugin), public=_public)
        if _cleaned.strip():
            value = _cleaned
    except Exception as exc:
        logger.debug("[amo] chat_safety.voice 失败: %s", exc)
    try:
        try:
            from .amo_state_consistency import rewrite as _state_rewrite
        except ImportError:
            from amo_state_consistency import rewrite as _state_rewrite
        _context = (str(user_text or "") + "\n" + str(history_text or "")).strip()
        value = _state_rewrite(value, plugin, _context, umo=str(umo or ""))
        try:
            try:
                from .amo_role_guard import rewrite as _role_rewrite
            except ImportError:
                from amo_role_guard import rewrite as _role_rewrite
            value = _role_rewrite(value, str(user_text or ""))
        except Exception as exc:
            logger.debug("[amo] chat_safety.role 失败: %s", exc)
    except Exception as exc:
        logger.debug("[amo] chat_safety.state 失败: %s", exc)
    try:
        try:
            from .amo_question_guard import answer as _question_answer
        except ImportError:
            from amo_question_guard import answer as _question_answer
        # B1：问句守卫不再套模板，答非所问时返回空串。本轮候选已经答到这类问题
        # （严格判据/通用答案规则任一命中）时，候选优先；当前文本也答不到就保持
        # 空串往出口走（repair 重生成，失败回退 raw），而不是盖一句固定状态话。
        _candidate = _media_clean(_turn_candidate(umo))
        _candidate_answers = bool(_candidate) and _answered_intent(_candidate, user_text)
        # 「在哪」已在正文里给出此刻地点（场景守卫刚补的「我在面馆呢」）就算答到：
        # 地点词表覆盖不到的场景地名，不许被问句守卫当成没答而删空。
        _where_answered = False
        try:
            from .amo_question_guard import _category as _q_category
        except ImportError:
            from amo_question_guard import _category as _q_category
        try:
            _current_place = str(
                (plugin.state.current_activity() or {}).get("place") or "").strip()
        except Exception:
            _current_place = ""
        if _current_place and _q_category(str(user_text or "")) == "where":
            _where_answered = _current_place in str(value or "")
        if not _candidate_answers and not _where_answered:
            value = _question_answer(
                str(user_text or ""), plugin, value, candidate=_candidate)
    except Exception as exc:
        logger.debug("[amo] chat_safety.question 失败: %s", exc)
    # 唯一后置语义复检（task-29）收口：上面所有整句删除/替换（含其前的
    # _align_arrival/_align_swim_where、其中的 state_consistency.rewrite 与
    # question_guard.answer）都跑完了，再判一次「还答不答得上他这一句」。
    value = final_semantic_recheck(value, plugin, umo, user_text)
    # task-40：全链路跑完后，完整候选被固定兜底/多池拼装/语法破损覆盖时还原候选。
    value = recover_rewrite_damage(value, plugin, umo, user_text)
    # 1b-1：候选被局部删除（只剩残句）的还原并入同一出口，保证「答到优先」。
    value = _restore_partial_candidate(value, umo, user_text)
    # 自然度埋点（只判不改）：句尾判词/零语气词/模板句，供 1a/N2 评估。
    try:
        try:
            from .amo_persona_guard import note_naturalness as _note_nat
        except ImportError:
            from amo_persona_guard import note_naturalness as _note_nat
        _note_nat(value)
    except Exception as _amo_exc:  # cleanup: 埋点失败绝不影响发送
        logger.debug("[amo] naturalness note failed: %s", _amo_exc)
    # B1：删空就是删空（不再用「or 原文」复活被守卫删掉的句子），空串交出口 repair。
    return value.strip()


def apply_narrative_repetition(text, plugin, user_text="", umo="", now=None):
    """整条回复的第一道「重复/答非所问」层（事实守卫之后、分段之前）。

    1) 他问吃啥：第一句先答吃了什么（事实只读唯一视图）；
    2) 最近 3 条 bot 消息说过的状态（药/等车/打车/在医院/回家），同一状态最多
       留 1 句；全删空时**返回空串**（B1：不再压成「还是老样子。」），由 chat
       出口的生成式修复重生成；非 chat 来源不会走到这里（它们用原文兜底）。
    """
    value = str(text or "")
    if not value.strip():
        return value
    turn = begin_status_turn(umo)
    _TURN_USER_TEXT[str(umo or "").strip()] = (turn, str(user_text or ""))
    # 记下「进入去重层时的原始候选」：后面去重/地点/问句守卫都可能把整条换掉，
    # 唯一优先级在 apply_chat_safety 里裁决（候选答到点上就还原）。
    _TURN_SOURCE[str(umo or "").strip()] = (turn, value)
    value = _answer_food_first(value, plugin, user_text, now)
    if FOOD_QUESTION_RE.search(str(user_text or "")):
        value = _limit_status_sentences(value, 1)
    said = _status_said(umo, now, count=3)
    for line in _repeat_guard.recent_bot_texts(plugin, umo, 5):
        said.update(status_tokens(line))
    fixed, _dropped = _drop_repeated_status(value, said)
    # 整句级复读：和最近 3-5 条 bot 消息相同/≥0.9 相似的句子直接删；整条重复换短句。
    # 但删句不能把对他的回答一起删掉（「刚吃完饭，好撑。你呢？」不能只剩「你呢？」）。
    # B1：状态句全删空就保持空串，不再「or 原文」把复读捞回来。
    before_dedup = fixed
    refs = _repeat_guard.recent_reference(plugin, umo, DEDUP_WINDOW)
    fixed, repeated = _repeat_guard.dedup_or_fallback(before_dedup, umo, refs)
    fixed = _keep_answer(before_dedup, fixed, user_text, repeated)
    for item in repeated:
        logger.debug("[dedup] 整句复读已去掉: %s", item[:40])
    # 完成动作一致性：刚说「洗漱过了」就别在回复里又说「才想起来洗漱」（2026-11-17）。
    try:
        try:
            from .done_action_guard import done_actions as _done_actions
            from .done_action_guard import filter_contradictions as _drop_contradictions
        except ImportError:
            from done_action_guard import done_actions as _done_actions
            from done_action_guard import filter_contradictions as _drop_contradictions
        _done = _done_actions(_repeat_guard.recent_bot_texts(plugin, umo, 12))
        if _done:
            _guarded = _drop_contradictions(fixed, _done).strip()
            if _guarded:
                fixed = _guarded
    except Exception as _amo_exc:
        logger.debug("[dedup] 完成动作一致性检查失败: %s", _amo_exc)
    # 整句/状态去重是「整句删除」守卫：删完再判一次「还答不答得上他这一句」。
    # 本轮候选真答到了就还原候选（答案优先于不复读）；否则空串继续往出口走，
    # 交给 repair 重生成（B1：不再用 status_repeat 整条压住）。
    return final_semantic_recheck(fixed, plugin, umo, user_text, original=value)


def apply_proactive_dedup(text, plugin, umo="", now=None):
    """主动消息：同一个状态 30 分钟内不重复主动发（全删空就这一轮不发）。"""
    value = str(text or "")
    if not value.strip():
        return value
    turn = begin_status_turn(umo)
    # 主动消息没有「他在问什么」，别让上一轮聊天的问题参与保留答案
    _TURN_USER_TEXT[str(umo or "").strip()] = (turn, "")
    said = _status_said(umo, now, count=None, seconds=PROACTIVE_STATUS_SECONDS)
    fixed, _dropped = _drop_repeated_status(value, said)
    refs = _repeat_guard.recent_reference(plugin, umo, DEDUP_WINDOW)
    fixed, repeated = _repeat_guard.dedup_or_fallback(fixed or "", umo, refs)
    for item in repeated:
        logger.debug("[dedup] 主动消息整句复读已去掉: %s", item[:40])
    return fixed if fixed.strip() else ""


def apply_segment_dedup(text, plugin, umo="", now=None):
    """分段发送前：后段和同一轮前面的段重复同一状态，就整段删掉。

    返回空串表示这一段整段都是复读，send 阶段会跳过发送（_send_to_umo 见空返回）。
    """
    value = str(text or "")
    if not value.strip():
        return value
    turn = _ACTIVE_TURN.get(str(umo or "").strip())
    if turn is None:  # 不是我们这一轮发出去的（别的插件/测试直连），不动
        return value
    question = _turn_question(umo)
    said = _status_said(umo, now, count=None, turn=turn)
    fixed, _dropped = _drop_repeated_status(value, said)
    if not fixed.strip():
        # 整段被状态去重删空时，如果他问的答案正好在这段里，保留它
        if _dropped and question and _answers_question(value, question):
            fixed = value
        else:
            return ""
    before_dedup = fixed
    refs = _repeat_guard.recent_reference(plugin, umo, DEDUP_WINDOW)
    fixed, _repeated = _repeat_guard.dedup_or_fallback(before_dedup, umo, refs)
    fixed = _keep_answer(before_dedup, fixed, question, _repeated)
    _remember_status(umo, fixed, now, turn=turn)
    return fixed


def apply_segment_repeat_guard(text, plugin=None, umo=""):
    """分段发送前：和最近 3-5 条 bot 消息 / 本轮前面的段重复就删句。

    返回空串 = 这一段整段都是复读，**不再换一句短的**（B2：固定句池已停产出）：
    send 阶段按空段跳过（amo_outbound.send 遇空直接不发送），不会发一条空消息。
    他这一问的答案落在这一段里时仍由 _keep_answer 保住（答案优先于不复读）。
    M2：本函数只是 _CHAINS["segment"] 的薄壳（_TURN_SEGMENTS 副作用与「空段=空串」
    契约不变；repair-owned 的修复句只会在 keep_answer 里被原样保住）。
    """
    ctx = _RescueCtx(plugin=plugin, umo=umo)
    return _run_rescue(text, ctx, "segment")


def _send_status_dedup(text, ctx):
    return apply_segment_dedup(text, ctx.plugin, ctx.umo)


# ---------------------------------------------------------------------------
# 3C：统一「生成式修复」hook —— 守卫把回复变成空/罐头句时，调一次模型重生成
#
# 唯一接线点：chat 出站 async 阶段（amo_runtime_reply.on_decorating_result），
# apply_chat_safety 之后。口径：
#   * 只修 chat 出站：空回复 / 罐头句（固定句池造出来的模板话）/ 多池串味；
#     非 chat 来源（主动/推送）一律不修；
#   * 修 = 调一次 chat_raw（REPAIR_TIMEOUT 秒）；结果过
#     amo_persona_guard.prepare_generated_line（禁语整句重写 + addressee_guard.apply
#     默认参数），仍是空/罐头/含禁语就判失败；
#   * 失败 fail-open 到「guarded or raw」：绝不把回复吞掉；
#   * 一次出站最多一次模型调用（repair 内部自己判定，调用方不再预判）。
# ---------------------------------------------------------------------------

#: 总开关：True = 空/罐头回复交给模型重生成（用户 2026-11-23 要求默认开）。
REPAIR_ENABLED = True
#: 修复超时（秒）：命中才调，最长等这么久，超时保持现有结果。
REPAIR_TIMEOUT = 8.0
#: 修复的系统提示：只要一句她会说的话，不要解释。
_REPAIR_SYSTEM = "你就是这个角色本人，只输出一句她会说的话，不要解释。"
#: 修出来的句子超过这个长度就不像「一句话」，不采用（fail-open）。
_REPAIR_MAX_CHARS = 120
#: 「包含」匹配只对够长的罐头句成立：太短的碎片（「笑死我了」）包含匹配会误伤正常回复。
_CANNED_CONTAINS_MIN = 6
#: 修复计数（进程内观测，不驱动逻辑）：ok=采用新句 / fail=调了模型但不可用 / skip=命中但没调。
_REPAIR_COUNTER: Dict[str, int] = {}
#: 懒加载的罐头句识别集（规范化 body）：只用于识别，不产出任何句子。
_CANNED_BODIES: Optional[frozenset] = None

#: 括号动作旁白：判罐头句只看「说出口的部分」（动作-only 兜底句 = 动作 + 一句台词）。
_ACTION_PAREN = re.compile(r"[（(][^（）()]*[）)]")

#: 固定句池之外的已知罐头句（识别用，不产出；来源见 tasks/3b/3c 施工图）。
#: B3 已删 persona/grounding 的 safe_reply 产出；这些旧句仍留在识别集里，
#: 模型偶然写出同样的模板话照样会被判罐头、交回 repair。
_CANNED_EXTRA_TEXTS = (
    # 旧 persona_guard.safe_reply（复检两次失败时的安全句，B3 已删）
    "我在呢，先别急，慢慢跟我说。",
    "我刚说过的呀，就是刚才跟你提的那个，你别逗我。",
    "我记混了，你再说一遍？",
    # 旧 grounding_critic.safe_reply（同一契约的旧实现，B3 已删产出）
    "然后啊，刚才那个还没讲完呢，后面还有更好笑的。",
    "你刚问的这个，我一下没答好，你再说一遍？",
    "换谁都得气，我在听着。",
    "我看到了，等我缓一下再细看。",
    "我在听，你接着说。",
    # short_reply 旧句（task-3b C1 已删产出，识别集保留）
    "哇！谢谢老公！爱你！",
    "好呀~",
    "没问题啦！",
    "你敷衍我是吧？那别聊了。",
    "你就回我一个嗯？哼，我不理你了！",
)

#: 用固定样例渲染注册表里的问句模板（question_guard 的槽位在注册表里没有字面量）。
_QUESTION_SAMPLES: tuple[tuple[str, dict[str, str]], ...] = (
    ("question.where", {"place": "外面"}),
    ("question.where", {"place": "公司"}),
    ("question.doing", {"doing": "忙点事"}),
    ("question.who", {"who": "朋友"}),
    ("question.who", {"who": "妈妈"}),
    ("question.when_route", {"place": "外面", "end": "晚点"}),
    ("question.alone", {}),
    ("question.when_home", {}),
    ("question.when_late", {}),
    ("question.eaten", {}),
    ("question.not_eaten", {}),
)


def _bump_repair(name):
    _REPAIR_COUNTER[name] = _REPAIR_COUNTER.get(name, 0) + 1


def repair_stats():
    """生成式修复计数快照（进程内；观测用，不驱动逻辑）。"""
    return dict(_REPAIR_COUNTER)


def reset_repair_stats():
    """清空修复计数（测试与回放用）。"""
    _REPAIR_COUNTER.clear()


def _compare(text):
    """规范化正文（只留汉字/字母/数字），与注册表的 compare_body 同口径。"""
    try:
        return _fallbacks.compare_body(text)
    except Exception:  # cleanup: 注册表异常就本地归一化
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(text or ""))


def _canned_bodies():
    """懒加载的罐头句识别集：注册表识别集 + 问句模板样例 + 旧安全句/旧句。"""
    global _CANNED_BODIES
    if _CANNED_BODIES is not None:
        return _CANNED_BODIES
    bodies = set()
    try:
        bodies.update(_fallbacks.fixed_rewrite_bodies())
    except Exception as _amo_exc:  # cleanup: 注册表不可用就只靠固定句清单
        _note_swallowed("amo_reply_pipeline.repair.canned.registry", _amo_exc)
    for key, fields in _QUESTION_SAMPLES:
        try:
            # B2：产出 API 已停用（sentence 恒返回空串），识别集直接读池数据渲染
            # 模板样例——只进比对集合，永远不会被发出去。
            pool = _fallbacks.fallback_pool(key)
            template = str(pool[0]) if pool else ""
            if template:
                try:
                    template = template.format(**fields)
                except (KeyError, IndexError, ValueError):
                    template = str(pool[0])
                bodies.add(_compare(template))
        except Exception as _amo_exc:  # cleanup: 单个模板坏了不影响整体
            _note_swallowed("amo_reply_pipeline.repair.canned.question", _amo_exc)
    bodies.update(_compare(item) for item in _CANNED_EXTRA_TEXTS)
    _CANNED_BODIES = frozenset(body for body in bodies if body)
    return _CANNED_BODIES


def is_canned_reply(text):
    """这条回复是不是空/罐头句（守卫造出来的模板话）；只判不改、绝不抛。

    a) 空/纯空白 → True；
    b) repetition_guard.is_fixed_rewrite → True（固定兜底/改写句的唯一识别集）；
    c) 去掉括号动作旁白后，正文等于/包含已知罐头句（旧安全句、question_guard
       模板样例、动作台词、short_reply 旧句）→ True。
    """
    body = str(text or "")
    if not body.strip():
        return True
    try:
        if _fallbacks.is_fixed_rewrite(body):
            return True
    except Exception as _amo_exc:  # cleanup: 识别集异常就当没命中
        _note_swallowed("amo_reply_pipeline.repair.canned.fixed", _amo_exc)
    normalized = _compare(_ACTION_PAREN.sub("", body))
    if not normalized:
        return True
    try:
        canned = _canned_bodies()
    except Exception as _amo_exc:  # cleanup: 识别集不可用就只按固定句判
        _note_swallowed("amo_reply_pipeline.repair.canned", _amo_exc)
        return False
    if normalized in canned:
        return True
    return any(len(item) >= _CANNED_CONTAINS_MIN and item in normalized
               for item in canned)


def repair_failure(guarded):
    """这条回复要修的理由（"" = 不用修）：empty / canned / repeat（多池串味）。"""
    body = str(guarded or "")
    if not body.strip():
        return "empty"
    if is_canned_reply(body):
        return "canned"
    try:
        if _fallbacks.is_multi_pool(body):
            return "repeat"
    except Exception as _amo_exc:  # cleanup: 判据异常就当不是串味
        _note_swallowed("amo_reply_pipeline.repair.multi_pool", _amo_exc)
    return ""


def needs_repair(guarded):
    """runtime 用：这条要不要交给模型修（同一份纯判据，用来跳过禁语重生成）。"""
    try:
        return bool(repair_failure(guarded))
    except Exception as _amo_exc:  # cleanup: 判据异常不修，保持原行为
        _note_swallowed("amo_reply_pipeline.repair.needs", _amo_exc)
        return False


#: 修复句采用前的宪法探针（3b-C 补洞）：这些守卫会改口 = 这句与账本/此刻场景矛盾。
_REPAIR_GUARD_POINTS = {
    "purchase": "模型补出来的句子在说自己买了东西/花了钱，但账本里没有这笔消费",
    "fact": "模型补出来的句子和此刻的地点/事实对不上",
}


def _repair_guard_reason(fresh, plugin):
    """采用修复句前再跑 purchase + fact 守卫（analyst-m probe_repair_acceptance）。

    只判「守卫会不会改这句」：改了说明这句和账本/此刻场景矛盾，整句不采用
    （fail-open 到 guarded or raw），绝不把守卫改过的版本发出去。
    """
    body = str(fresh or "").strip()
    if not body:
        return "empty"
    try:
        try:
            from .amo_purchase_guard import rewrite as _purchase_rewrite
        except ImportError:
            from amo_purchase_guard import rewrite as _purchase_rewrite
        fixed = str(_purchase_rewrite(body, plugin) or "").strip()
        if not fixed or fixed != body:
            return "purchase"
    except Exception as _amo_exc:  # cleanup: 探针异常不阻断（fail-open 到原行为）
        _note_swallowed("amo_reply_pipeline.repair.guard.purchase", _amo_exc)
    # apply_fact_guard 命中冲突时会记 _tone_pick_pending（B 方案待选）；探针不该留下
    # 这个副作用，跑完恢复原值（原来没有就清掉）。
    pending = getattr(plugin, "_tone_pick_pending", None)
    try:
        fixed = str(apply_fact_guard(body, plugin) or "").strip()
    except Exception as _amo_exc:  # cleanup: 探针异常不阻断
        _note_swallowed("amo_reply_pipeline.repair.guard.fact", _amo_exc)
        fixed = body
    finally:
        try:
            plugin._tone_pick_pending = pending if pending is not None else None
        except Exception as _amo_exc:  # cleanup: 插件禁止写属性就放弃恢复
            _note_swallowed("amo_reply_pipeline.repair.guard.pending", _amo_exc)
    if not fixed:
        return "fact"
    if fixed != body:
        # 只把「改口」判不安全：守卫在原文外面补词（「看电影呢」→「一起看电影呢」）
        # 仍是同一事实，可以发；把内容换掉（地点/吃饭/同伴/金额）才不采用。
        old_body = _compare(body)
        new_body = _compare(fixed)
        if not old_body or old_body not in new_body:
            return "fact"
    return ""


_REPAIR_POINTS = {
    "empty": "上一版被守卫删空了，一句能发的话都没剩下",
    "canned": "上一版是固定模板句（罐头句），不是真的在接他的话",
    "repeat": "上一版是把几句兜底台词拼在一起",
}


def _repair_prompt(reason, raw, guarded, user_text="", history=""):
    """修复指令：带上他的原话/最近上下文/上一版，只要一句真人话。"""
    parts = []
    if str(user_text or "").strip():
        parts.append("他刚说的是：%s" % str(user_text).strip()[:100])
    context = re.sub(r"\s+", " ", str(history or "")).strip()
    if context:
        parts.append("最近聊的：%s" % context[-160:])
    if str(raw or "").strip():
        parts.append("守卫改之前的那一版：%s" % str(raw).strip()[:160])
    if str(guarded or "").strip():
        parts.append("现在准备发的是：%s" % str(guarded).strip()[:160])
    parts.append("问题：%s。" % _REPAIR_POINTS.get(str(reason), "上一版不能用"))
    parts.append("请用第一人称「我」，接住他这句话接着往下说，带上你自己的情绪和具体内容；"
                 "不要用固定模板句/客服话术，不要说第三人称自称（「你美丽的老婆」「你老婆」），"
                 "不要旁白和解释，只输出一句话。")
    return "\n".join(parts)


def _repair_fallback(raw, guarded):
    """失败绝不吞回复：能发 guarded 就发 guarded，否则退回「洗过一遍旁白」的原话。

    直接退回 raw 会把守卫刚删掉的动作旁白又带回来（回放 narration_after 0→2），
    所以 raw 至少先过 strip_action_narration；仍为空才真的不发。
    """
    text = str(guarded or "")
    if text.strip():
        return text
    text = str(raw or "")
    try:
        try:
            from .amo_persona_guard import strip_action_narration as _strip_narration
        except ImportError:
            from amo_persona_guard import strip_action_narration as _strip_narration
        text = str(_strip_narration(text) or "")
    except Exception:  # cleanup: 剥离失败就用原话，至少不吞回复
        text = str(raw or "")
    return text.strip()


async def repair_reply(raw, guarded, plugin, umo="", user_text="", history="",
                       source="chat"):
    """空/罐头回复交给模型重生成一句；失败 fail-open 到「guarded or raw」。

    只处理 chat 出站（source 不是 chat → 原样返回 guarded）；REPAIR_ENABLED=False、
    没有可用 LLM、或这条不是空/罐头/串味时都 **不调模型**、原样返回（开关关 =
    行为零变化）。同一次出站最多一次模型调用：needs_repair() 命中后 runtime 会
    跳过 regenerate_forbidden。

    3b-C 安全边界：
    - guarded 已是安全产物（_protected_current：还没买/leak 挡话）→ 直接跳过；
    - 采用前再过 purchase_guard + apply_fact_guard：被改口 = 与账本/此刻场景矛盾，
      整句不采用（fail_guard，回退 guarded or raw）；
    - 采用后 mark_turn_repaired：send 段复检/候选优先以修复句为准，不再用旧候选覆盖。
    """
    body = str(guarded or "")
    # 安全产物（「还没买」/leak 挡话/角色挡话）跳过：修了也会被 send 段改回，
    # 还可能把 raw 里的假事实重新带回来 → 不调模型、不二次改写。
    if _protected_current(body):
        _bump_repair("skip_protected")
        return body
    reason = repair_failure(body)
    if not reason:
        return body                           # 正常句：不计数、不调模型
    if str(source or "").strip().lower() not in ("", "chat"):
        _bump_repair("skip")                  # 非 chat 来源（主动/推送）不修
        return body
    llm = getattr(plugin, "llm", None)
    if (not REPAIR_ENABLED or llm is None
            or not callable(getattr(llm, "chat_raw", None))):
        _bump_repair("skip")                  # 开关关/没有可用模型：零调用
        return body
    try:
        try:
            from .amo_persona_guard import forbidden_hits, prepare_generated_line
        except ImportError:
            from amo_persona_guard import forbidden_hits, prepare_generated_line
    except Exception as _amo_exc:  # cleanup: 守卫不可用就不冒风险重生成
        _bump_repair("fail")
        _note_swallowed("amo_reply_pipeline.repair_reply.import", _amo_exc)
        return _repair_fallback(raw, body)
    try:
        fresh = await asyncio.wait_for(
            llm.chat_raw(str(umo or ""), _REPAIR_SYSTEM,
                         _repair_prompt(reason, raw, body, user_text, history)),
            timeout=REPAIR_TIMEOUT)
    except Exception as _amo_exc:  # cleanup: 超时/模型异常 fail-open 到现有文本
        _bump_repair("fail")
        _note_swallowed("amo_reply_pipeline.repair_reply", _amo_exc)
        return _repair_fallback(raw, body)
    fresh = str(fresh or "").strip()
    if fresh and len(fresh) <= _REPAIR_MAX_CHARS:
        try:
            fresh = str(prepare_generated_line(fresh) or "").strip()
        except Exception as _amo_exc:  # cleanup: 确定性层异常就当模型没给
            _note_swallowed("amo_reply_pipeline.repair_reply.clean", _amo_exc)
            fresh = ""
    else:
        fresh = ""
    # 仍是空/罐头/含禁语 → 不采用，交回「guarded or raw」。
    if (not fresh or is_canned_reply(fresh) or forbidden_hits(fresh)):
        _bump_repair("fail")
        return _repair_fallback(raw, body)
    # 3b-C：生成句还要过 purchase/fact 守卫；被改口 = 与账本/场景矛盾 → 不采用。
    guard_reason = _repair_guard_reason(fresh, plugin)
    if guard_reason:
        logger.info("[amo] 修复句未过宪法守卫（%s：%s），不采用: %s",
                    guard_reason, _REPAIR_GUARD_POINTS.get(guard_reason, ""), fresh[:60])
        _bump_repair("fail_guard")
        return _repair_fallback(raw, body)
    _bump_repair("ok")
    # send 段复检/候选优先改用修复句，不再拿旧候选把它盖掉（analyst-m G23）。
    mark_turn_repaired(plugin, umo, fresh)
    logger.info("[amo] 空/罐头回复已重生成 reason=%s text=%s", reason, fresh[:80])
    return fresh


# ---------------------------------------------------------------------------
# 登记表：顺序 = 改造前代码里的真实调用顺序
# ---------------------------------------------------------------------------


def default_stages() -> List[Stage]:
    return [
        # ---- generate（wired=False：调用点仍在 amo_llm）----
        Stage("generate.claim_retry", PHASE_GENERATE, 10, _gen_claim_retry, wired=False,
              target="amo_llm._claim_retry",
              ctx_keys=("plugin", "umo", "system", "prompt"),
              note="fact_binder 消费校验的 LLM 重试（调用点在 _compose_reply）"),
        Stage("generate.polish", PHASE_GENERATE, 20, _gen_polish, wired=False,
              target="amo_llm.polish", note="LLM 润色",
              ctx_keys=("plugin", "umo", "tone", "state_text", "relation", "name",
                        "persona", "style", "consult")),
        Stage("generate.trim", PHASE_GENERATE, 30, _gen_trim, wired=False,
              target="amo_llm._trim_reply", ctx_keys=("plugin",)),
        Stage("generate.date_state", PHASE_GENERATE, 40, _gen_date_state, wired=False,
              target="date_state.guard", ctx_keys=("plugin",)),
        Stage("generate.refusal", PHASE_GENERATE, 50, _gen_refusal, wired=False,
              side_effect=True, target="refusal.guard",
              ctx_keys=("plugin", "snap", "activity", "his_words", "cash_cents", "hour", "silence_days")),
        Stage("generate.consumption", PHASE_GENERATE, 80, _gen_consumption, wired=False,
              target="amo_message._guard_consumption", ctx_keys=("plugin",)),
        Stage("generate.group_guard", PHASE_GENERATE, 90, _gen_group, wired=False,
              when=lambda ctx: bool(ctx.group), target="group_scene.guard_group_reply",
              ctx_keys=("scene", "owner_name")),
        Stage("generate.group_tease", PHASE_GENERATE, 95, None, wired=False, side_effect=True,
              when=lambda ctx: bool(ctx.group),
              target="amo_llm._maybe_group_private_tease", note="群里敷衍完私聊补一条（发消息）"),
        Stage("generate.photo_claim", PHASE_GENERATE, 98, None, wired=False, side_effect=True,
              target="draw_engine.enforce_photo_claim", note="副作用：真的补图"),
        Stage("generate.photo_intent", PHASE_GENERATE, 99, _gen_photo_intent, wired=False,
              side_effect=True, target="amo_llm._enforce_photo_intent",
              ctx_keys=("plugin", "event"), note="副作用：命中拍照意图补一张，发不出就删承诺"),
        Stage("generate.photo_wants", PHASE_GENERATE, 100, None, wired=False, side_effect=True,
              target="draw_engine.send_photo_now", note="副作用：他明确要图时补一张"),

        # ---- decorate（wired=False：调用点仍在 amo_runtime）----
        Stage("decorate.strip_chat_format", PHASE_DECORATE, 5, _dec_strip_chat_format, wired=False,
              target="amo_shared.strip_chat_format"),
        Stage("decorate.offwork_sync", PHASE_DECORATE, 10, _dec_offwork, wired=False,
              when=lambda ctx: not ctx.group, side_effect=True,
              target="amo_schedule._maybe_own_offwork_sync", ctx_keys=("plugin",)),
        Stage("decorate.fact_guard", PHASE_DECORATE, 20, _dec_fact_guard, wired=False,
              target="amo_reply_pipeline.apply_fact_guard", ctx_keys=("plugin", "place"),
              note="地点/事实唯一守卫（真实调用点在 amo_runtime_reply 装饰阶段）"),
        Stage("decorate.attribution_alert", PHASE_DECORATE, 30, None, wired=False,
              side_effect=True, target="amo_shared.attribution_leak",
              note="归属串台只告警不拦（复合块）"),
        Stage("decorate.gift_observe", PHASE_DECORATE, 40, _dec_gift_observe, wired=False,
              side_effect=True, target="gift_engine.observe_reply", ctx_keys=("plugin", "event")),
        Stage("decorate.defensive_rewrite", PHASE_DECORATE, 50, None, wired=False,
              side_effect=True, target="amo_shared.looks_like_defensive_reply",
              note="防御话术/AI 自曝改写（复合块，会改 result.chain）"),
        Stage("decorate.model_leak", PHASE_DECORATE, 60, _dec_model_leak, wired=False,
              target="amo_shared.looks_like_model_leak"),
        Stage("decorate.draw_after_reply", PHASE_DECORATE, 70, _dec_draw, wired=False,
              side_effect=True, target="draw_engine.maybe_after_reply", ctx_keys=("plugin", "event")),
        Stage("decorate.polish_native", PHASE_DECORATE, 80, None, wired=False,
              when=lambda ctx: bool(getattr(ctx.plugin, "cfg_bool", lambda *a, **k: False)("polish_native", False)),
              target="amo_llm.polish", note="polish_native 开关打开时的原生润色"),
        Stage("decorate.brushoff", PHASE_DECORATE, 90, _dec_brushoff, wired=False,
              target="brushoff.strip_brushoff", ctx_keys=("his_words",)),
        Stage("decorate.food_question", PHASE_DECORATE, 95, _dec_food_question, wired=False,
              target="amo_message._strip_repeated_food_question",
              ctx_keys=("plugin", "his_words", "last_bot_line")),
        Stage("decorate.transit", PHASE_DECORATE, 100, _dec_transit, wired=False,
              target="amo_transit.rewrite_sent_text", ctx_keys=("plugin",)),
        Stage("decorate.zone_guard", PHASE_DECORATE, 110, _dec_zone, wired=False,
              target="amo_message._guard_zone_reply", ctx_keys=("plugin", "umo", "his_words")),
        Stage("decorate.date_guard", PHASE_DECORATE, 120, _dec_date_guard, wired=False,
              target="amo_date_context.guard_reply", ctx_keys=("plugin",)),
        Stage("decorate.mark_date_end", PHASE_DECORATE, 130, _dec_mark_date_end, wired=False,
              side_effect=True, target="amo_date_context.mark_reply_date_end", ctx_keys=("plugin",)),
        Stage("decorate.trim", PHASE_DECORATE, 140, _dec_trim, wired=False,
              target="amo_llm._trim_reply", ctx_keys=("plugin",)),

        # ---- send（wired=True：amo_message._send_to_umo 唯一入口）----
        Stage("send.clean", PHASE_SEND, 10, _send_clean,
              target="amo_shared.clean_reply", ctx_keys=("plugin",)),
        Stage("send.names", PHASE_SEND, 20, _send_names, swallow="debug",
              log_msg="[amo] 人名校验失败: %s",
              target="amo_names.foreign_names", ctx_keys=("plugin", "umo")),
        Stage("send.zone_guard", PHASE_SEND, 30, _send_zone, swallow="debug",
              log_msg="[amo] 发送前地点校验失败: %s",
              target="amo_message._guard_zone_reply", ctx_keys=("plugin", "umo")),
        Stage("send.photo_claim", PHASE_SEND, 35, _send_photo_claim, swallow="debug",
              log_msg="[amo] 发送前照片声称校验失败: %s",
              target="photo_view.strip_photo_claim", ctx_keys=("plugin", "umo"),
              note="失败/无图时不许声称有图或图糊"),
        Stage("send.wardrobe", PHASE_SEND, 40, _send_wardrobe,
              target="wardrobe.home_outfit_guard", ctx_keys=("plugin",)),
        Stage("send.privacy", PHASE_SEND, 50, _send_privacy, swallow="note",
              note_scope="amo_reply_pipeline.send_privacy",
              target="amo_diary_guard.sanitize_privacy"),
        Stage("send.status_dedup", PHASE_SEND, 55, _send_status_dedup,
              target="amo_reply_pipeline.apply_segment_dedup",
              ctx_keys=("plugin", "umo"),
              note="分段发送前：后段重复同一状态就整段不发（跨段不复读）"),
        Stage("send.date_context", PHASE_SEND, 60, _send_date_context, swallow="debug",
              log_msg="[amo] 发送前约会校验失败: %s",
              target="amo_date_context.guard_reply", ctx_keys=("plugin",)),
        Stage("send.immersion", PHASE_SEND, 70, _send_immersion,
              target="amo_immersion_guard.sanitize_extreme"),
        Stage("send.transition_advance", PHASE_SEND, 80, _send_transition,
              replace=False, side_effect=True, target="amo_transition.maybe_advance",
              ctx_keys=("plugin",), note="副作用：她的话可能推进过渡状态"),
        Stage("send.transit_rewrite", PHASE_SEND, 90, _send_transit, swallow="debug",
              log_msg="[amo] 过渡地点守卫失败: %s",
              target="amo_transit.rewrite_sent_text", ctx_keys=("plugin",)),
        Stage("send.model_leak", PHASE_SEND, 100, _send_model_leak,
              target="amo_shared.looks_like_model_leak"),

        # ---- proactive_pre（wired=True：amo_proactive._send_proactive_lines）----
        Stage("proactive_pre.consumption", PHASE_PROACTIVE_PRE, 10, _pre_claim,
              swallow="debug", log_msg="[amo] 主动消息消费校验失败: %s",
              target="amo_message._guard_consumption", ctx_keys=("plugin",)),
    ]


DEFAULT_STAGES: List[Stage] = default_stages()
PIPELINE = Pipeline(DEFAULT_STAGES)


def context_for_send(plugin: Any, umo: str, group: bool = False) -> ReplyContext:
    """_send_to_umo 的上下文：发送侧只读 umo/群聊标记，别的不碰。"""
    return ReplyContext(
        plugin=plugin, umo=str(umo or ""), phase=PHASE_SEND,
        channel=CHANNEL_GROUP if group else CHANNEL_PRIVATE, group=bool(group),
    )


def context_for_proactive(plugin: Any, umo: str, profile: Optional[Dict[str, Any]] = None) -> ReplyContext:
    """主动消息分段前的上下文（消费校验在切句之前跑）。"""
    return ReplyContext(
        plugin=plugin, umo=str(umo or ""), phase=PHASE_PROACTIVE_PRE,
        channel=CHANNEL_PROACTIVE, group=False, profile=dict(profile or {}),
    )


async def run(text: Any, ctx: ReplyContext) -> Dict[str, Any]:
    """全局流水线入口：按 ctx.phase 跑对应的有序阶段。"""
    return await PIPELINE.run(text, ctx)


def register(stage: Stage) -> None:
    """往全局登记表里加一道阶段（测试/扩展用；生产顺序请改 default_stages）。"""
    PIPELINE.stages.append(stage)


def audit(pipeline: Optional[Pipeline] = None) -> Dict[str, Any]:
    """审计登记表：重复 id / 同一守卫跨阶段重复 / 纯文本阶段 / 描述阶段。"""
    return _audit((pipeline or PIPELINE).stages)


def _audit(stages: Sequence[Stage]) -> Dict[str, Any]:
    order: Dict[str, List[str]] = {phase: [] for phase in PHASES}
    seen: Dict[str, int] = {}
    targets: Dict[str, List[str]] = {}
    target_phase: Dict[str, Set[str]] = {}
    no_ctx: List[str] = []
    unconditional: List[str] = []
    descriptors: List[str] = []
    side_effects: List[str] = []
    unwired: List[str] = []
    duplicates: List[str] = []
    for stage in sorted(stages, key=lambda item: (_phase_index(item.phase), item.priority)):
        order.setdefault(stage.phase, []).append(stage.id)
        seen[stage.id] = seen.get(stage.id, 0) + 1
        if seen[stage.id] == 2:
            duplicates.append(stage.id)
        if stage.target:
            targets.setdefault(stage.target, []).append(stage.id)
            target_phase.setdefault(stage.target, set()).add(stage.phase)
        if not stage.ctx_keys:
            no_ctx.append(stage.id)
        if stage.when is None:
            unconditional.append(stage.id)
        if stage.run is None:
            descriptors.append(stage.id)
        if stage.side_effect:
            side_effects.append(stage.id)
        if not stage.wired:
            unwired.append(stage.id)
    overlaps = []
    for target, ids in sorted(targets.items()):
        if len(ids) > 1:
            phases = target_phase.get(target, set())
            overlaps.append({"target": target, "stages": ids,
                             "same_phase": len(phases) == 1,
                             "phases": sorted(phases)})
    return {
        "order": order,
        "duplicates": duplicates,
        "overlaps": overlaps,
        "no_ctx": no_ctx,
        "unconditional": unconditional,
        "descriptors": descriptors,
        "side_effects": side_effects,
        "unwired": unwired,
        "wired_phases": [PHASE_SEND, PHASE_PROACTIVE_PRE],
        "phases": list(PHASES),
        "total": len(stages),
    }
