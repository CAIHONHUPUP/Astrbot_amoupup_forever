# -*- coding: utf-8 -*-
"""照片发送：定时掷骰、主动发送、失败兜底（从 photo_engine 拆出）。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

try:  # 配置化骨架：失败话术等长文案放 infra/config/*.json
    from .infra.config import load_config
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from infra.config import load_config

import random
import time

from astrbot.api import logger

try:
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_observability import note as _note_swallowed

try:
    from .amo_shared import now_ts, quiet_now
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import now_ts, quiet_now

try:
    from .amo_image_common import MessageChain
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_image_common import MessageChain

try:
    from .photo_plan import (
    build_plan,
    pick_pose_for_plan,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_plan import (
            build_plan,
            pick_pose_for_plan,
        )

try:
    from .photo_scene import (
    matches_kind,
    scene_for,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_scene import (
            matches_kind,
            scene_for,
        )

try:  # 纯内核：失败话术/政策判定（域内只用标准库）
    from .domains.photo import failure_text as _failure_text
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.photo import failure_text as _failure_text

try:  # 纯内核：分享冷却时间窗（域内只用标准库）
    from .domains.photo import share_window as _share_window
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.photo import share_window as _share_window

try:  # 纯内核：失败/安全重试判定（域内只用标准库）
    from .domains.photo import share_fallback as _share_fallback
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.photo import share_fallback as _share_fallback

try:  # P22：房间参考图（默认关，A/B 用显式参数）
    from .photo_room import room_ref_for_plan
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_room import room_ref_for_plan

try:  # 提示词政策清洗（安全重试用）
    from .photo_view import sanitize_selfie_prompt
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_view import sanitize_selfie_prompt

try:  # 唯一在场/沉默/照片绑定策略（2026-11-22 架构收口）
    from . import amo_presence as _presence
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_presence as _presence

try:  # 单一媒体出口/账本（2026-11-22 task-23）：三条照片链路都从这里发
    from . import amo_media as _media
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_media as _media

try:  # 单 zone / 床句 / 房间家具 / 姿势发送前断言（task-23）
    from .photo_prompt import enforce_photo_media_prompt as _enforce_media_prompt
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_prompt import enforce_photo_media_prompt as _enforce_media_prompt

#: 阈值默认值（配置化：infra/config/amo_thresholds.json 可覆盖，代码里保留兜底）。
AMO_THRESHOLDS_DEFAULT = {"proactive_silence_seconds": 10 * 60,
                          "proactive_burst_guard_seconds": 90}


def _threshold(name: str, default: int) -> int:
    """读 infra/config/amo_thresholds.json 里的正整数阈值；坏值或缺失回退 default。"""
    data = load_config("amo_thresholds", AMO_THRESHOLDS_DEFAULT,
                       lambda value: isinstance(value, dict) and bool(value))
    try:
        value = int(data.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


#: 刚发过照片后，主动消息闭嘴的时长（与 amo_proactive 共用同一口径）。
PROACTIVE_SILENCE_SECONDS = _threshold("proactive_silence_seconds", 10 * 60)
#: 最近一次照片分享记录（持久化，重启后主动消息也能被挡住）。
PHOTO_LAST_SHARE_KEY = "photo_last_share"
#: 测试/手动分享的图片带这个标记：不占每日配额、不写进长期记忆。
PHOTO_TEST_MARK = "【测试图】"

#: 2026-11-17 失败语气池：只表达「没拍成/稍后再试/换角度」，
#: 不声称有图、不提内容政策/审核/API；同一会话不连续重复。
#: 配置化 pilot：正式文案在 infra/config/photo_failure_lines.json，下面这份是兜底——
#: JSON 缺失/损坏时行为与改造前逐字一致。
_is_failure_lines = _failure_text.is_failure_lines


_PHOTO_FAILURE_DEFAULT = (
    {"tone": "sweet", "text": "唔…这次没拍成，等我缓一下再给你拍，好不好？"},
    {"tone": "sweet", "text": "刚想弄给你看，结果没存上…等我一下下就好。"},
    {"tone": "playful", "text": "镜头今天跟我作对，刚想给你看就没了…别急嘛。"},
    {"tone": "playful", "text": "手滑了一下，什么都没留下来…再等我一会儿嘛。"},
    {"tone": "pout", "text": "没存下来，有点小郁闷。等我收拾一下再试一次。"},
    {"tone": "pout", "text": "刚才没拍好，不想给你看半成品，等我一下下。"},
    {"tone": "calm", "text": "今天状态不太对，拍出来不好看，晚点重新弄给你。"},
    {"tone": "calm", "text": "唔，没成功。等我换个角度再试一次，别催我啦。"},
)
PHOTO_FAILURE_LINES = tuple(load_config("photo_failure_lines", _PHOTO_FAILURE_DEFAULT, _is_failure_lines))
PHOTO_FAILURE_FALLBACK = PHOTO_FAILURE_LINES[0]["text"]

#: 内容政策失败的标记（底层节点 400 / 中转返回的 message）。
#: 内容政策失败的标记（配置化：infra/config/policy_failure_markers.json）。
_POLICY_FAILURE_DEFAULT = (
    "content_policy", "policy_violation", "content-policy", "content policy",
    "safety_violation", "safety policy", "内容政策", "内容审核", "违规内容",
)


_is_str_list = _failure_text.is_str_list


POLICY_FAILURE_MARKERS = tuple(load_config("policy_failure_markers", _POLICY_FAILURE_DEFAULT, _is_str_list))


def is_policy_failure(text):
    """这段生成失败信息是不是内容政策拦截（content_policy_violation）。"""
    return _failure_text.is_policy_failure(text, POLICY_FAILURE_MARKERS)


class _OmniResultRecorder:
    """包一层 Omni 调用：记录最后一次结果/异常，供 _share 判断政策拦截。

    _generate 只返回图片或 None，错误 message 会被吞掉；这里在调用边界留证。
    """

    def __init__(self, inner):
        self._inner = inner
        self.last_result = None
        self.last_error = ""

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def generate_images_for_plugin(self, **kwargs):
        try:
            result = await self._inner.generate_images_for_plugin(**kwargs)
        except Exception as exc:
            self.last_error = str(exc)
            raise
        self.last_result = result
        return result


def _recorder_reason(recorder):
    """从 recorder 取失败原因：优先异常，其次 result.message。"""
    return _failure_text.recorder_reason(recorder)


def photo_failure_pool(mood_score=None, relation=""):
    """按心情/关系挑语气池：低落偏委屈，开心偏俏皮，恋人偏甜。"""
    return _failure_text.failure_pool(PHOTO_FAILURE_LINES, mood_score, relation)


def pick_failure_line(mood_score=None, relation="", last="", rng=None):
    """选一条失败语气；避开上一条（同一会话不连续重复）。"""
    return _failure_text.pick_failure_line(PHOTO_FAILURE_LINES, mood_score, relation, last, rng)


class PhotoShareMixin:
    """把照片发出去：掷骰、生成、发送、失败提示。"""

    if TYPE_CHECKING:  # 宿主属性/方法由 photo_engine / PhotoPromptMixin 提供
        plugin: Any
        enabled: Any
        debug: Any
        _busy: Any
        _last_check: Any
        _stats: Any
        _spawn: Any
        _activity: Any
        _cfg_bool: Any
        _cfg_int: Any
        _cfg_str: Any
        _profiles: Any
        _target_umo: Any
        _candidates: Any
        _roll_key: Any
        _bump_count: Any
        _generate: Any
        _localize: Any
        _image_component: Any
        _find_omni: Any
        _pick_selfie: Any
        _selfie_ready: Any
        _selfie_prompt_for: Any
        _prompt_for: Any
        cleanup_cache: Any
        invalidate_activity: Any
        today_count: Any

    def on_schedule_changed(self, day="", version=0, reason="schedule_changed"):
        """日程变更事件：生图下一次会重新读 current_activity；这里留可验收日志。"""
        # P2-4：日程变了，丢掉出图链路的「此刻日程」分钟缓存，下一张重新读。
        try:
            self.invalidate_activity()
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.on_schedule_changed", _amo_exc)
        logger.info("[PHOTO_DEBUG] reason=schedule_changed day=%s version=%s source=%s",
                    day or "-", version or "-", reason or "-")

    async def tick(self):
        try:
            self.cleanup_cache()
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.tick", _amo_exc)
        if not self.enabled or self._busy:
            logger.debug("[PHOTO] tick skip reason=%s",
                         "busy" if self._busy else "photo_enable=false")
            return
        now = time.monotonic()
        if now - self._last_check < 20:
            return
        # 这一轮她已经自己发过自拍（LLM 工具或照片请求）→ 别再补一张
        if self.proactive_blocked():
            logger.debug("[PHOTO] tick skip reason=llm_handled")
            return
        self._last_check = now
        try:
            if quiet_now(self._cfg_str("photo_quiet_hours", "23:00-07:00")):
                logger.debug("[PHOTO] tick skip reason=quiet_hours")
                return
            cap = max(0, self._cfg_int("photo_daily_cap", 4))
            if self.today_count() >= cap:
                logger.debug("[PHOTO] tick skip reason=daily_cap count=%s", cap)
                return
            profiles = self._profiles()
            if not profiles:
                logger.debug("[PHOTO] tick skip reason=no_owner_profile")
                return
            # 唯一在场策略：沉默 6 小时 / 逛街 pending 但不在商场 → 自动照片整轮不发。
            try:
                blocked = not _presence.can_photo(self.plugin, profiles[0], now=now_ts())
            except Exception as _amo_exc:
                _note_swallowed("photo_share.PhotoShareMixin.tick.presence", _amo_exc)
                blocked = False
            if blocked:
                logger.debug("[PHOTO] tick skip reason=presence_gate")
                return
            for kind, scene, prob, key in self._candidates():
                roll_key = self._roll_key(kind, key)
                try:
                    if self.plugin.db.kv_get(roll_key, 0):
                        continue
                    self.plugin.db.kv_set(roll_key, 1)
                except Exception as _amo_exc:
                    _note_swallowed("photo_engine.PhotoEngine.tick", _amo_exc)
                    continue
                self._stats["rolled"] += 1
                if random.random() * 100 >= prob:
                    if self.debug:
                        logger.info("[amo] 照片：%s 掷骰没中（%d%%）" % (kind, prob))
                    continue
                profile = random.choice(profiles)
                self._busy = True
                # 生图要几十秒，不能卡在 30 秒一轮的 tick 里；发完再放开
                if self._spawn(self._run_share(kind, scene, profile)) is None:
                    self._busy = False
                return
        except Exception as exc:
            logger.debug("[amo] 照片任务失败: %s" % exc)

    def note_sent(self, source="auto", test=False, kind="", umo=""):
        """记一次「刚发过照片」：图片引擎、自动补图和主动消息共用这一个冷却。"""
        self._last_sent_at = time.monotonic()
        record = {"at": now_ts(), "source": str(source or "auto"),
                  "test": bool(test), "kind": str(kind or ""), "umo": str(umo or "")}
        try:
            db = getattr(self.plugin, "db", None)
            if db is not None and callable(getattr(db, "kv_set", None)):
                db.kv_set(PHOTO_LAST_SHARE_KEY, record)
        except Exception as exc:
            _note_swallowed("photo_share.PhotoShareMixin.note_sent", exc)

    def last_share(self):
        """最近一次照片分享记录（没有就空 dict）；只读，不触发冷却。"""
        try:
            db = getattr(self.plugin, "db", None)
            record = db.kv_get(PHOTO_LAST_SHARE_KEY, {}) if db is not None else {}
        except Exception as exc:
            _note_swallowed("photo_share.PhotoShareMixin.last_share", exc)
            record = {}
        return record if isinstance(record, dict) else {}

    def photo_share_cooldown_active(self, seconds=PROACTIVE_SILENCE_SECONDS, now=None):
        """刚分享过照片（含测试/手动）→ 这段时间内不许主动开口。"""
        seconds = max(0, int(seconds))
        record = self.last_share()
        try:
            at = float(record.get("at") or 0)
        except (TypeError, ValueError):
            at = 0.0
        stamp = float(now if now is not None else now_ts()) if at else time.monotonic()
        last = at if at else getattr(self, "_last_sent_at", 0)
        # 没 db / note_sent 还没落库（单测）时用内存时间戳兜底
        return _share_window.within(last, stamp, seconds)

    def sent_recently(self, seconds=300):
        return _share_window.within(getattr(self, "_last_sent_at", 0), time.monotonic(), seconds)

    def proactive_blocked(self):
        """定时分享要不要停：这一轮已处理过照片，或刚发过一张。"""
        return self.suppressed() or self.sent_recently()

    def suppressed(self):
        """这一轮是不是已经有照片在发了（10 分钟内不再定时补发）。"""
        return getattr(self, "_suppress_until", 0) > time.monotonic()

    def note_llm_handled(self, seconds=600):
        """她已经在对话里自己处理了拍照：10 分钟内不再由定时器主动补发。"""
        self._suppress_until = time.monotonic() + max(0, int(seconds))

    async def _run_share(self, kind, scene, profile):
        try:
            if not self.enabled:
                return
            cap = max(0, self._cfg_int("photo_daily_cap", 4))
            if self.today_count() >= cap:
                return
            ok = await self._share(kind, scene, profile)
            if ok:
                self.note_sent()
            if not ok:
                await self._notify_failure(self._target_umo(profile), "share_failed",
                                           profile=profile)
        finally:
            self._busy = False

    # ---------- 失败状态 / 回复守卫 ----------

    def _photo_turn_state(self):
        store = getattr(self, "_photo_turn_store", None)
        if not isinstance(store, dict):
            store = {}
            self._photo_turn_store = store
        return store

    def _photo_pending_set(self):
        pending = getattr(self, "_photo_pending", None)
        if not isinstance(pending, set):
            pending = set()
            self._photo_pending = pending
        return pending

    def _mark_photo_pending(self, umo=""):
        if umo:
            self._photo_pending_set().add(str(umo))

    def _clear_photo_pending(self, umo=""):
        self._photo_pending_set().discard(str(umo or ""))

    def note_photo_ok(self, umo=""):
        """记一次「图真的发出来了」，回复守卫据此放行有图的话。"""
        if not umo:
            return
        self._photo_turn_state()[str(umo)] = {"ok": time.monotonic(), "fail": 0.0,
                                              "reason": ""}

    def note_photo_fail(self, umo="", reason=""):
        """记一次「这次没图」；回复守卫据此删掉声称有图的句子。"""
        if not umo:
            return
        self._photo_turn_state()[str(umo)] = {"ok": 0.0, "fail": time.monotonic(),
                                              "reason": str(reason or "")[:160]}

    def photo_guard_state(self, umo="", ok_window=120, fail_window=600):
        """给回复守卫判断：ok / pending / failed / idle。

        顺带收编「模型直接调外部 generate_selfie」：它自己发图不会走 amo 的
        MediaGateway，这里按它写在 omni._selfie_turn_store 的证据补一条
        external_selfie 账本（占 cap + 写 memory），这样「发过的图」聊天/记忆查得到。
        """
        umo = str(umo or "")
        now = time.monotonic()
        try:
            finder = getattr(self, "_find_omni", None)
            omni = finder() if callable(finder) else None
            if umo and _media.reconcile_external(self.plugin, umo, omni):
                logger.info("[PHOTO] 外部 generate_selfie 已补记媒体账本（umo=%s）", umo[:48])
                return "ok"
        except Exception as _amo_exc:
            _note_swallowed("photo_share.PhotoShareMixin.photo_guard_state.external", _amo_exc)
        record = self._photo_turn_state().get(umo) or {}
        fail_at = float(record.get("fail") or 0)
        if fail_at and now - fail_at < max(0, int(fail_window)):
            return "failed"
        if umo and umo in self._photo_pending_set():
            return "pending"
        ok_at = float(record.get("ok") or 0)
        if ok_at and now - ok_at < max(0, int(ok_window)):
            return "ok"
        if self.sent_recently(max(0, int(ok_window))):
            return "ok"
        # 模型直接调生图插件的 generate_selfie（不走 amo 的 send_photo）：
        # 它成功下发后会在 Omni 实例上按 umo 记一笔，这里认这个证据。
        try:
            finder = getattr(self, "_find_omni", None)
            omni = finder() if callable(finder) else None
            getter = getattr(omni, "_selfie_turn_store", None)
            last_ok = getter().get(umo) if callable(getter) else None
            if last_ok is not None and now - float(last_ok) < max(0, int(ok_window)):
                return "ok"
        except Exception as _amo_exc:
            _note_swallowed("photo_share.PhotoShareMixin.photo_guard_state", _amo_exc)
        return "idle"

    def failure_line(self, umo="", relation=""):
        """选一条温柔失败话术；同一会话不连续重复。"""
        mood = None
        try:
            mood = float(self.plugin.state.mood_score())
        except Exception:
            mood = None
        store = getattr(self, "_failure_line_last", None)
        if not isinstance(store, dict):
            store = {}
            self._failure_line_last = store
        key = str(umo or "")
        line = pick_failure_line(mood, relation, store.get(key, ""))
        store[key] = line
        return line

    async def _notify_failure(self, umo, reason="", profile=None):
        """按日程分享失败时补一句温柔人话。

        节流只保留统一出口那一层（push_to_owner 的 5 分钟窗口 + outbound 去重），
        这里不再自己记 10 分钟：多套节流互相打架时，失败话术会时灵时不灵。
        """
        if not umo or MessageChain is None:
            return
        try:
            relation = str((profile or {}).get("relation") or "") if isinstance(profile, dict) else ""
            line = _media.sanitize_failure_text(self.failure_line(umo, relation))
            logger.warning("[PHOTO] failure_notice=1 reason=%s", str(reason or "")[:80])
            try:
                from astrbot.api.message_components import Plain
            except Exception:
                from astrbot.api.event.components import Plain
            push = getattr(self.plugin, "push_to_owner", None)
            if callable(push):
                await push(line)
            else:
                await self.plugin.context.send_message(umo, MessageChain([Plain(text=line)]))
        except Exception as exc:
            logger.warning("[PHOTO] failure_notice send failed: %s", exc)

    async def _send(self, umo, item, caption=""):
        """发图：唯一入口是 amo_media.send_photo（MediaGateway）。

        Gateway 内完成：caption 媒体标记剥离 → 内容签名/事件去重 → 每日配额 →
        事件占位 → 统一出站出口（_send_media_to_umo，保留 task-16 的 source=photo）
        → 记一条账本 + 占 cap + 写 memory。caption 里的「[我发的照片]」在
        gateway 里再剥一次，任何出口都不会把占位符当人话发出去。
        """
        if not umo:
            return False
        component = self._image_component(item)
        if component is None:
            return False
        meta = getattr(self, "_photo_send_meta", None) or {}
        binding = getattr(self, "_photo_event_binding", None) or {}
        if binding.get("resend"):
            origin = "resend"
            claim_event = False
        else:
            origin = str(meta.get("origin") or "manual")
            claim_event = bool(meta.get("claim_event", origin == "auto"))
        event_id = str(meta.get("event_id") or binding.get("event_id") or "")
        scene = meta.get("scene") if isinstance(meta.get("scene"), dict) else {}
        test = bool(meta.get("test"))

        async def _transport():
            """宿主没有 _send_media_to_umo（单测/旧宿主）时的直接发送。"""
            parts = []
            caption_text = str(caption or "")
            try:
                try:
                    from .amo_message import strip_media_markers as _strip
                except ImportError:
                    from amo_message import strip_media_markers as _strip
                caption_text = _strip(caption_text)
            except Exception:  # cleanup: 兜底/降级，保持原行为
                pass
            if caption_text:
                try:
                    from astrbot.api.message_components import Plain
                except Exception:
                    try:
                        from astrbot.api.event.components import Plain
                    except Exception:
                        Plain = None
                if Plain is not None:
                    parts.append(Plain(text=str(caption_text)))
            parts.append(component)
            await self.plugin.context.send_message(umo, MessageChain(parts))

        bump = None
        try:
            if not callable(getattr(getattr(self.plugin, "photo", None), "_bump_count", None)):
                bump = getattr(self, "_bump_count", None)
        except Exception:
            bump = None
        try:
            result = await _media.send_photo(
                self.plugin, umo, item,
                components=[component],
                caption=caption,
                origin=origin,
                kind=str(meta.get("kind") or "photo"),
                event_id=event_id,
                scene=scene,
                pose=meta.get("pose"),
                outfit=str(meta.get("outfit") or ""),
                test=test,
                claim_event=claim_event,
                dedupe=meta.get("dedupe"),
                enforce_cap=meta.get("enforce_cap"),
                count=meta.get("count"),
                transport=_transport,
                bump=bump,
            )
        except Exception as exc:
            logger.warning("[amo] 照片发送失败: %s" % exc)
            return False
        if not result.get("ok"):
            if str(result.get("reason") or "") not in ("dup_event", "dup_signature",
                                                       "daily_cap", "no_component",
                                                       "send_blocked"):
                logger.warning("[amo] 照片未发出：%s", result.get("reason"))
            return False
        try:
            store = getattr(self, "_last_sent", None)
            if not isinstance(store, dict):
                store = {}
                self._last_sent = store
            store[str(umo)] = item
        except Exception as exc:
            _note_swallowed("photo_share._send.remember", exc)
        return True

    async def resend_last(self, umo):
        """他要「发给我看看」时，把最近发过的那张原样再发一次，不重新生图。

        这是他明确要的，不算自动分享：source=chat 绕过 90 秒主动去重，
        也不占用事件配额（同一 event_id 第一张的额度已经用掉）。
        """
        store = getattr(self, "_last_sent", None)
        item = (store or {}).get(str(umo or ""))
        if not item:
            return False
        old = getattr(self, "_photo_event_binding", None)
        self._photo_event_binding = {"event_id": "", "resend": True}
        try:
            return await self._send(str(umo), item)
        finally:
            self._photo_event_binding = old

    async def _share(self, kind, scene, profile, target_umo="", lock_scene=True,
                     room_refs=None, test=False):
        activity = self._activity()
        omni = self._find_omni()
        if omni is None:
            logger.warning("[amo] 没有我能永远定格你，照片发不出去。")
            return False
        umo = str(target_umo or "") or self._target_umo(profile)
        # 唯一在场策略：照片必须绑定当前事件（沉默 6 小时 / 逛街 pending 但不在商场 → 不发；
        # pending 且在商场 → 场景强制成商场/试衣间，不能让家里自拍配商场文案）。
        try:
            binding = _presence.photo_binding(self.plugin, profile, kind=kind,
                                              manual=bool(test), now=now_ts())
        except Exception as _amo_exc:
            _note_swallowed("photo_share.PhotoShareMixin._share.presence", _amo_exc)
            binding = {"ok": True, "reason": "", "scene": "", "event_id": "", "shopping": False}
        if not binding.get("ok"):
            logger.info("[PHOTO] 照片未绑定当前事件：%s（kind=%s）",
                        binding.get("reason"), kind)
            return False
        if binding.get("scene"):
            scene = str(binding["scene"])
        self._photo_event_binding = binding
        # 一次生图先把“日程 → zone → 姿势/守卫”定型；提示词 / 参考图 / 锁 / 调试
        # 全程共用这一份计划——整条链路只解析一次（2026-11-16 Phase 9）
        try:
            plan = build_plan(activity, kind=kind, mode="photo", scene=str(scene or ""))
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._share.plan", _amo_exc)
            plan = None
        # P22：房间参考图（2026-11-17 起默认开，读 photo_room_refs；显式 room_refs=False 可关）
        if plan is not None and self._room_refs_enabled(room_refs):
            self._attach_room_ref(plan)
        selfie = self._pick_selfie(kind)
        if selfie and not self._selfie_ready(omni):
            selfie = False
        # 自拍：姿势由场景规则决定（居家才可能用姿势库），公共场合不会随机到床姿
        pose = None
        pose_text = ""
        if selfie and plan is not None:
            library = getattr(getattr(self, "plugin", None), "pose", None)
            pose, pose_text = pick_pose_for_plan(plan, library)
        prompt = (self._selfie_prompt_for(kind, scene, activity, pose=pose, plan=plan,
                                          pose_text=pose_text) if selfie
                  else self._prompt_for(kind, scene, activity, plan=plan))
        # 2026-11-22 task-23：生成前先断言「单 zone + 床句/家具/姿势对齐」。
        # 不通过就不生成、不发（宁可发失败话术，也不发地铁+床/浴室+客厅那种互斥图）。
        prompt, _prompt_ok, _prompt_reasons = _enforce_media_prompt(
            prompt, plan=plan, pose=pose, activity=activity)
        if not _prompt_ok:
            logger.warning("[PHOTO] prompt 单场景断言不通过，本次不发：%s",
                           "；".join(_prompt_reasons) or "-")
            self._stats["failed"] += 1
            self.note_photo_fail(umo, "prompt_conflict:" + ",".join(_prompt_reasons))
            return False
        if self._cfg_bool("photo_dry_run", False):
            logger.info("[amo] 照片（dry-run）%s：%s" % ("自拍" if selfie else "街景", prompt))
            return False
        started = time.monotonic()
        self._mark_photo_pending(umo)
        first_recorder = _OmniResultRecorder(omni)
        failure_reason = ""
        item = None
        try:
            item = await self._generate(first_recorder, prompt, selfie, pose=pose,
                                        plan=plan if lock_scene else None,
                                        room_refs=room_refs)
            failure_reason = _recorder_reason(first_recorder)
            if _share_fallback.retry_decision(item, selfie):
                # 自拍失败绝不能退回空镜（会出空房间+一只手）。命中 content_policy
                # 时用去身体化的安全 prompt 重试一次；其它失败也走安全版，仍失败就这次不发。
                policy_hit = is_policy_failure(failure_reason)
                if policy_hit:
                    self._stats["policy_fail"] = self._stats.get("policy_fail", 0) + 1
                    logger.warning("[PHOTO_POLICY] policy_fail=1 stage=first umo=%s reason=%s",
                                   umo[:48], failure_reason[:120])
                try:
                    retry_prompt = self._selfie_prompt_for(
                        kind, scene, activity, pose=None,
                        plan=plan if lock_scene else None, safe=True)
                except Exception:
                    retry_prompt = sanitize_selfie_prompt(prompt, safe=True)
                self._stats.setdefault("policy_fallback_ok", 0)
                self._stats.setdefault("policy_fallback_fail", 0)
                self._stats["policy_fallback"] = self._stats.get("policy_fallback", 0) + 1
                logger.warning("[PHOTO_POLICY] fallback=1 policy=%s umo=%s prompt=%s",
                               "1" if policy_hit else "0", umo[:48], retry_prompt[:160])
                retry_recorder = _OmniResultRecorder(omni)
                item = await self._generate(retry_recorder, retry_prompt, True, pose=None,
                                            plan=plan if lock_scene else None,
                                            room_refs=room_refs)
                stat = _share_fallback.retry_stat(item)
                self._stats[stat] = self._stats.get(stat, 0) + 1
                if item:
                    logger.info("[PHOTO_POLICY] fallback_ok=1 policy=%s umo=%s",
                                _share_fallback.policy_flag(policy_hit), umo[:48])
                else:
                    failure_reason = _share_fallback.merged_failure_reason(
                        failure_reason, _recorder_reason(retry_recorder))
                    logger.warning("[PHOTO_POLICY] fallback_fail=1 policy=%s umo=%s reason=%s",
                                   _share_fallback.policy_flag(policy_hit), umo[:48],
                                   failure_reason[:120])
        finally:
            self._clear_photo_pending(umo)
        if not item and selfie:
            logger.warning("[amo] 自拍重试仍失败，本次不发照片（空镜兜底已禁用）")
            self._stats["failed"] += 1
            self.note_photo_fail(umo, failure_reason or "selfie_failed")
            return False
        if not item:
            self._stats["failed"] += 1
            self.note_photo_fail(umo, failure_reason or "generate_failed")
            return False
        localized = await self._localize(item)
        if localized is not item:
            item = localized
        logger.info("[amo] 照片耗时 %.1fs（生成+本地化，%s）" % (
            time.monotonic() - started, "自拍" if selfie else "街景"))
        # 发送元数据（账本/去重/配额都在 amo_media.MediaGateway 里用）：事件占位从
        # _share 挪到 gateway，避免 photo_share 和 MediaGateway 各 claim 一次。
        _binding = self._photo_event_binding or {}
        outfit_meta = ""
        try:
            outfit_meta = (self._selfie_outfit_text(str(getattr(plan, "zone", "") or ""))
                           if selfie else self.outfit_today())
        except Exception as _amo_exc:
            _note_swallowed("photo_share.PhotoShareMixin._share.outfit_meta", _amo_exc)
            outfit_meta = ""
        self._photo_send_meta = {
            "origin": "test" if test else "auto",
            "kind": str(kind or "photo"),
            "event_id": str(_binding.get("event_id") or ""),
            "scene": {
                "event_id": str(_binding.get("event_id") or ""),
                "zone": str(getattr(plan, "zone", "") or ""),
                "place": str(getattr(plan, "where", "") or ""),
            },
            "pose": pose,
            "outfit": outfit_meta,
            "test": bool(test),
            "claim_event": not test,
            "dedupe": not test,
            "enforce_cap": not test,
            "count": not test,
        }
        # 2026-11-22 task-25：发送前按场景账本复核一次——生图几十秒，期间日程可能已经切走
        # （14:00 在商场拍完、14:05 已经回到家）。复核不过就这次不发，绝不发错场景的图。
        if not test and lock_scene:
            try:
                try:
                    from .amo_scene_ledger import verify_photo_scene as _verify_photo
                except ImportError:
                    from amo_scene_ledger import verify_photo_scene as _verify_photo
                _scene_check = _verify_photo(
                    self.plugin,
                    expected_zone=str(getattr(plan, "base_zone", "") or ""),
                    expected_event_id=str((self._photo_event_binding or {}).get("event_id") or ""),
                    now=now_ts(),
                )
            except Exception as _amo_exc:
                _note_swallowed("photo_share.PhotoShareMixin._share.verify_scene", _amo_exc)
                _scene_check = {"ok": True, "reasons": []}
            if not _scene_check.get("ok"):
                logger.info("[PHOTO] 生图期间场景已变化，本次不发：%s",
                            "；".join(_scene_check.get("reasons") or []) or "-")
                self._stats["failed"] += 1
                self._photo_send_meta = None
                return False
        try:
            sent_ok = await self._send(umo, item, caption=PHOTO_TEST_MARK if test else "")
        finally:
            self._photo_send_meta = None
        if not sent_ok:
            self._stats["failed"] += 1
            return False
        if test:
            # 测试/手动图不算日常分享：不占每日配额，也不写进「我今天发了照片」的长期记忆
            logger.info("[amo] 测试照片已发（%s，不计入日常分享）", kind)
        self._stats["sent"] += 1
        self.note_photo_ok(umo)
        self.note_sent(source="test" if test else "auto", test=test, kind=kind, umo=umo)
        # 占 cap / 写 memory / 记 scene_id+pose+hash+event_id 账本都在 MediaGateway 里完成
        # （2026-11-22 task-23：以前只在这条链路 note_sent + remember，工具链路 0 记录）。
        logger.info("[amo] 照片已分享（%s/%s）：%s" % (kind, "自拍" if selfie else "街景", prompt))
        return True

    def _room_refs_enabled(self, explicit=None):
        """房间参考图开关：显式参数优先，否则读 photo_room_refs（默认开）。

        P22 当初默认关，A/B 后用户选了「默认带房间参考图」，所以缺省值是 True；
        显式 room_refs=False 仍然可以回到旧行为（A/B / 排查用）。
        """
        if explicit is not None:
            return bool(explicit)
        try:
            return bool(self._cfg_bool("photo_room_refs", True))
        except Exception:
            return True

    def _attach_room_ref(self, plan):
        """把当前房间参考图写进计划；没有图就保持不带（不阻塞出图）。

        ``room_ref_checked`` 只用来让 _generate 不再重复找一次图（空镜路径）。
        """
        plan.room_ref_checked = True
        try:
            housing = getattr(self.plugin, "housing", None)
            ref = room_ref_for_plan(plan, housing)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._attach_room_ref", _amo_exc)
            return ""
        if not ref:
            logger.debug("[amo] 没有可用的房间参考图，本次不带（不阻塞出图）")
            return ""
        plan.room_ref = ref
        policy = dict(getattr(plan, "refs_policy", None) or {})
        policy["room"] = True
        plan.refs_policy = policy
        return ref

    @staticmethod
    def _scene_for_kind(kind, place="", activity=None):
        """测试/手动发图时按类别给明确场景，避免“旅游”沿用当前在家的地点。

        自动发照片那条链路不传 ``scene``，地点完全由日程决定；这里是面板
        「发一张旅游照/逛街照」用的：类别和当前日程对不上时，给该类别的固定场景。
        """
        kind = str(kind or "daily")
        data = dict(activity) if isinstance(activity, dict) else {}
        if place and not data.get("place"):
            data["place"] = place
        if kind in ("travel", "shopping", "weekend") and not matches_kind(kind, data):
            return scene_for({}, kind)
        return scene_for(data, kind)

    async def share_now(self, kind="daily", umo=""):
        """面板「测试」/``/amo photo test``：无视概率，立刻按类别发一张。"""
        if not self.enabled:
            return "照片功能没开（photo_enable=false）。"
        if self._busy:
            return "还在画上一张，等它出来。"
        profiles = self._profiles()
        if not profiles:
            return "没有可发送的会话（先和他聊一句）。"
        activity = self._activity()
        kind = str(kind or "daily").strip() or "daily"
        scene = self._scene_for_kind(kind, str(activity.get("place") or ""), activity)
        if self._cfg_bool("photo_dry_run", False):
            if kind == "outfit":
                prompt = self._selfie_prompt_for(kind, scene, activity)
            else:
                prompt = self._prompt_for(kind, scene, activity)
            logger.info("[amo] 照片（dry-run）%s：%s" % (kind, prompt))
            return "🧪 dry-run：%s" % prompt
        self._busy = True
        try:
            # 手动「发一张旅游照/逛街照」且和当前日程对不上时，是刻意覆盖场景，别再用日程锁
            lock_scene = True
            if kind in ("travel", "shopping", "weekend"):
                lock_scene = bool(matches_kind(kind, activity))
            ok = await self._share(kind, scene, random.choice(profiles), target_umo=umo,
                                   lock_scene=lock_scene, test=True)
        finally:
            self._busy = False
        return "✅ 发了一张（%s，测试图）" % kind if ok else "❌ 没发出去，看日志"
