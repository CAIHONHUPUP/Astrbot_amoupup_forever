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
   short_reply.guard → _guard_self_location → _guard_consumption →
   group_scene.guard_group_reply → _maybe_group_private_tease；
   回到 _compose_reply 之后是**副作用阶段**：draw.enforce_photo_claim →
   _enforce_photo_intent → draw.send_photo_now（会真的补一张图，
   所以单独建模、保持原位置，不并入纯文本阶段）。

2. decorate（on_decorating_result）—— amo_runtime.on_decorating_result：
   strip_chat_format → _deco_soft_guards（_maybe_own_offwork_sync →
   _guard_self_location → 归属串台告警）→ _deco_rewrite_reply
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
列出来——例如 _guard_self_location 在 generate 和 decorate 里各出现一次，
_guard_zone_reply 在 send 和 decorate 里各出现一次。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import inspect
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, TYPE_CHECKING

try:  # 整句级复读守卫（去重/短句兜底，纯文本无副作用）
    from . import amo_repetition_guard as _repeat_guard
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_repetition_guard as _repeat_guard  # type: ignore

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
    return _repeat_guard.pick_variant("leak:" + str(umo or ""),
                                      _repeat_guard.MODEL_LEAK_DEFLECTIONS, used)


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


def _gen_short(text, ctx):
    try:
        import short_reply
    except ImportError:  # pragma: no cover - 顶层总能导入
        return None
    return short_reply.guard(text, ctx.snap or {}, ctx.his_words)


def _gen_self_location(text, ctx):
    return ctx.plugin._guard_self_location(text, ctx.activity or {})


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


def _dec_self_location(text, ctx):
    place = ctx.place
    value = ctx.plugin._guard_self_location(text, place)
    activity = ctx.activity or ({"place": place} if place else None)
    return apply_fact_guard(value, ctx.plugin, activity)


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
#: 状态去重删空时，这些说明句子里有明确答案（时间/地点/吃饭/是否），保留原句
_ANSWER_CUE_RE = re.compile(
    r"\d{1,2}[:：]\d{2}|在家|在公司|在路|到了|到家|不在|没有|吃饭|吃完饭|好的|是的")
#: 他问的答案类型 -> 回复里什么算答到了（整句复读删句时用来保留答案）
_ANSWER_RULES = (
    (FOOD_QUESTION_RE, re.compile(r"吃|饭|面|粥|外卖|饿|饱")),
    (re.compile(r"在哪|哪里|哪呢|在家吗|到公司|到哪|出门了"),
     re.compile(r"在家|在公司|在路|在商场|在餐厅|在办公室|在街上|在景区|在酒店|"
                r"在医院|在健身房|在学校|这边|到了|到家|出门|外面|地铁|公交|车上|"
                r"家|公司|商场|餐厅|医院")),
    (re.compile(r"几点|什么时候|多久|回不回来"),
     re.compile(r"\d|马上|快了|晚点|一会儿|下班|到家|回去")),
    (re.compile(r"干嘛|做什么|忙什么|干什么|偷懒"),
     re.compile(r"上班|加班|开会|睡|休息|吃|做饭|逛|追剧|看|写|改|收拾|玩|练|"
                r"健身|刷|忙|瘫|窝|躺|做")),
    (re.compile(r"跟谁|谁在一起|一个人|旁边有人|有人吗"),
     re.compile(r"自己|一个人|没人|跟|和|我们|俩|同事|朋友|他|她|妈|爸")),
)
#: 本轮他问的话：(轮次, 原话)；分段/发送去重靠它判断整句复读有没有把答案删掉
_TURN_USER_TEXT: Dict[str, Any] = {}
#: 每个会话最近发出去的状态（跨轮去重依据；只在内存，重启后靠 _recent 兜底）
#: 当前这一轮回复的批次号（分段去重只在同一轮内生效，避免误删跨轮正常消息）


def reset_repetition_memory(umo=""):
    """清掉整句去重/状态去重的进程内记忆（验收 harness 每个样本独立会话用）。"""
    reset_status_memory(umo)
    _repeat_guard.reset(umo)
    if umo:
        _TURN_USER_TEXT.pop(str(umo), None)
    else:
        _TURN_USER_TEXT.clear()












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

#: 整句级去重：实现在 amo_repetition_guard，这里只保留分段记忆
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


def _keep_answer(original, fixed, question, dropped):
    """整句复读删句后答案被删没了，就保留原句：答到问题优先于「不复读」。

    只在他问的是可判定的答案（吃了没/在哪/几点回/干嘛/跟谁）时生效；
    「想你」这类没有答案的复读仍然照旧删/换短句。
    """
    if not dropped:
        return fixed
    if _answers_question(fixed, question):
        return fixed
    if _answers_question(original, question):
        return original
    return fixed


def _turn_question(umo):
    """这一轮他问的话；跨轮/跨段的旧问题不再参与保留答案判断。"""
    key = str(umo or "").strip()
    entry = _TURN_USER_TEXT.get(key)
    if not entry:
        return ""
    turn, question = entry
    return str(question or "") if turn == _ACTIVE_TURN.get(key) else ""


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


def apply_chat_safety(text, plugin, umo="", user_text="", history_text=""):
    """聊天回复的「最后一道确定性守卫」（2026-11-21 审计：聊天不走 send 阶段）。

    只跑确定性改写，不调模型：极端/暴力词 → 身份 → 人名 → 消费 → 反催问。
    """
    value = str(text or "")
    if not value.strip():
        try:
            try:
                from .amo_question_guard import answer as _empty_answer
            except ImportError:
                from amo_question_guard import answer as _empty_answer
            value = _empty_answer(str(user_text or ""), plugin, "")
        except Exception as exc:
            logger.debug("[amo] chat_safety.empty 失败: %s", exc)
        return value
    try:
        try:
            from .amo_share_ledger import mark_reacted as _mark_reacted
        except ImportError:
            from amo_share_ledger import mark_reacted as _mark_reacted
        if user_text:
            _mark_reacted(plugin)
    except Exception as exc:
        logger.debug("[amo] chat_safety.share_reacted 失败: %s", exc)
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
        value = _state_rewrite(value, plugin, _context)
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
        value = _question_answer(str(user_text or ""), plugin, value)
    except Exception as exc:
        logger.debug("[amo] chat_safety.question 失败: %s", exc)
    return value.strip() or str(text or "")


def apply_narrative_repetition(text, plugin, user_text="", umo="", now=None):
    """整条回复的第一道「重复/答非所问」层（事实守卫之后、分段之前）。

    1) 他问吃啥：第一句先答吃了什么（事实只读唯一视图）；
    2) 最近 3 条 bot 消息说过的状态（药/等车/打车/在医院/回家），同一状态最多
       留 1 句；全删空时压成「还是老样子」，不整句复读。
    """
    value = str(text or "")
    if not value.strip():
        return value
    turn = begin_status_turn(umo)
    _TURN_USER_TEXT[str(umo or "").strip()] = (turn, str(user_text or ""))
    value = _answer_food_first(value, plugin, user_text, now)
    if FOOD_QUESTION_RE.search(str(user_text or "")):
        value = _limit_status_sentences(value, 1)
    said = _status_said(umo, now, count=3)
    for line in _repeat_guard.recent_bot_texts(plugin, umo, 5):
        said.update(status_tokens(line))
    fixed, dropped = _drop_repeated_status(value, said)
    if dropped and not fixed.strip():
        # 有明确答案（时间/地点/吃饭/是否）时保留原回答；纯状态重复才用短句
        fixed = value if _ANSWER_CUE_RE.search(value) else "还是老样子。"
    # 整句级复读：和最近 3-5 条 bot 消息相同/≥0.9 相似的句子直接删；整条重复换短句。
    # 但删句不能把对他的回答一起删掉（「刚吃完饭，好撑。你呢？」不能只剩「你呢？」）。
    before_dedup = fixed or value
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
            _guarded = _drop_contradictions(fixed or value, _done).strip()
            if _guarded:
                fixed = _guarded
    except Exception as _amo_exc:
        logger.debug("[dedup] 完成动作一致性检查失败: %s", _amo_exc)
    return fixed or value


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
    """分段发送前：和最近 3-5 条 bot 消息 / 本轮前面的段重复就换短的（返回非空）。"""
    value = str(text or "")
    if not value.strip():
        return value
    key = str(umo or "").strip()
    earlier = (list(_TURN_SEGMENTS.get(key) or ())
               if _ACTIVE_TURN.get(key) is not None else [])
    refs = _repeat_guard.recent_reference(plugin, umo, DEDUP_WINDOW) + earlier
    fixed, dropped = _repeat_guard.dedup_or_fallback(value, umo, refs)
    fixed = _keep_answer(value, fixed, _turn_question(umo), dropped)
    for item in dropped:
        logger.debug("[dedup] 分段复读已换掉: %s", item[:40])
    rows = _TURN_SEGMENTS.setdefault(key, [])
    body = _repeat_guard.compare_body(fixed)
    if body:
        rows.append(body)
        del rows[:-STATUS_MEMORY_LIMIT]
    return fixed or value


def _send_status_dedup(text, ctx):
    return apply_segment_dedup(text, ctx.plugin, ctx.umo)


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
        Stage("generate.short_reply", PHASE_GENERATE, 60, _gen_short, wired=False,
              target="short_reply.guard", ctx_keys=("snap", "his_words")),
        Stage("generate.self_location", PHASE_GENERATE, 70, _gen_self_location, wired=False,
              target="amo_message._guard_self_location", ctx_keys=("plugin", "activity")),
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
        Stage("decorate.self_location", PHASE_DECORATE, 20, _dec_self_location, wired=False,
              target="amo_message._guard_self_location", ctx_keys=("plugin", "place")),
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
