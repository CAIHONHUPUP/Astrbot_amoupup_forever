# -*- coding: utf-8 -*-
"""隔离模拟器环境（2026-11-21，只读测试）。

目标：在不碰生产库/生产配置的前提下，用「假插件 + 真实回复链路」跑日常对话。

隔离手段
--------
1. 状态：FakeState/FakeDB/FakeContext 全在内存里，没有任何 sqlite 连接；
2. 时钟：调用生产自带的 clock.freeze()（测试钩子）把「现在」固定到本轮时刻，
   于是所有真实守卫读到的 now/today 都是被控值；
3. 回复链路：直接调用生产的 RuntimeReplyMixin / MessageMixin / LlmMixin 实例方法
   （_deco_soft_guards -> _deco_rewrite_reply -> _deco_text_cleanup -> apply_chat_safety
   -> _send_to_umo），不复制、不修改生产代码；
4. 模型：SimLLM 是可复现的模拟 provider（不联网、不读 provider 配置）。

与生产的差异（报告里必须写明）
------------------------------
- AstrBot 事件循环/分段引擎/TTS/生图/记忆插件没有接：segment_engine=None，draw/gift=None，
  _send_to_umo 直接把整段当作一条消息；
- 地点黑名单的「模型重生成」分支用空返回（SimLLM.chat 返回 ""），等价于重生成失败，
  因此走确定性 force_rewrite 兜底；
- 没有真实模型语义，只能测「确定性守卫 + 上下文一致性」。
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.join(ROOT, "tests")
for _p in (ROOT, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import clock  # noqa: E402
import amo_llm  # noqa: E402
import amo_message  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
import amo_state_consistency as state_consistency  # noqa: E402
import amo_state_snapshot as snap_mod  # noqa: E402
import place_guard  # noqa: E402
import reply_fact_guard  # noqa: E402
from amo_runtime_reply import RuntimeReplyMixin  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402

try:
    from astrbot.api.message_components import Plain as _Plain
except Exception:  # pragma: no cover - stub 缺 Plain 时用最小替身
    class _Plain:  # type: ignore
        def __init__(self, text=""):
            self.text = str(text or "")


#: 模拟对话使用的固定日期（clock.freeze 之后 today_str() 就是它）
SIM_DAY = "2026-11-17"


def _minutes(hhmm):
    hour, minute = str(hhmm).split(":")[:2]
    return int(hour) * 60 + int(minute)


def ts_of(hhmm, day=SIM_DAY):
    """'08:30' -> 该日（本地时区）的时间戳，用于 clock.freeze。"""
    base = _dt.datetime.strptime(day, "%Y-%m-%d")
    return (base + _dt.timedelta(minutes=_minutes(hhmm))).timestamp()


@contextlib.contextmanager
def frozen_clock(hhmm, day=SIM_DAY):
    clock.freeze(ts_of(hhmm, day))
    try:
        yield clock.now()
    finally:
        clock.unfreeze()


class FakeDB:
    """只读 kv + 写内存；不接 sqlite。"""

    def __init__(self, kv=None):
        self.kv = dict(kv or {})
        self.writes = []

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value
        self.writes.append(key)

    def get_profile(self, uid):
        return {"uid": uid, "name": "老公", "relation": "恋人", "alias": "老公"}

    # 少数只读视图会顺手问日程/日记；给空实现，避免抛异常掩盖真实路径
    def get_schedule(self, day):
        return []

    def get_block_diaries(self, day):
        return []


class FakeWallet:
    def ledger(self, limit=200):
        return []


class FakeState:
    """日程驱动的「此刻」状态；优先级与生产 current_activity_info 一致（covering > 最近块）。"""

    def __init__(self, blocks, health=None, mood=None):
        self.schedule = {SIM_DAY: [dict(item) for item in (blocks or [])]}
        self.health = dict(health or {"status": "健康", "sick": False})
        self.mood = dict(mood or {"平静": 60})
        self.relation = "恋人"
        self.db = None

    def _minute_now(self):
        moment = clock.now()
        return moment.hour * 60 + moment.minute

    def current_activity_info(self):
        items = [item for item in (self.schedule.get(clock.today_str()) or []) if item]
        current = self._minute_now()
        covering = [item for item in items
                    if _minutes(item.get("start", "00:00")) <= current < _minutes(item.get("end", "24:00"))]
        if covering:
            chosen = max(covering, key=lambda item: _minutes(item.get("start", "00:00")))
            return chosen, "covering"
        past = [item for item in items if _minutes(item.get("start", "00:00")) <= current]
        if past:
            return past[-1], "stale_slot"
        if items:
            return items[0], "yesterday_same_hour"
        return {"activity": "休息", "place": "家", "zone": "home", "category": "rest"}, "no_schedule"

    def current_activity(self):
        return self.current_activity_info()[0]

    def snapshot(self):
        return {"activity": self.current_activity(),
                "health": dict(self.health),
                "dominant": "平静"}

    def health_info(self):
        return dict(self.health)

    def tone_text(self, snap, relation=""):
        return ""


class FakeWardrobe:
    """居家服守卫的最小替身（不做改写，只保证接口在）。"""

    def home_outfit_guard(self, text):
        return str(text or "")


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        items = getattr(chain, "chain", chain) or []
        self.sent.append("".join(str(getattr(item, "text", "") or "") for item in items))


class FakeEvent:
    def __init__(self, text, umo="大肥鱼:FriendMessage:302578963"):
        self.message_str = str(text or "")
        self.unified_msg_origin = umo
        self.message_obj = type("MsgObj", (), {"message_id": "sim-1"})()

    def is_private_chat(self):
        return True

    def get_extra(self, key, default=None):
        return default


class FakeResult:
    def __init__(self, text=""):
        self.chain = [_Plain(text)]


class SimLLM:
    """可复现的模拟 provider：只回答语气选择器，其余返回空（= 模型不可用）。"""

    def __init__(self):
        self.calls = 0
        self.general_calls = 0
        self.answers = []

    async def chat(self, umo, system, prompt, limit=0):
        system = str(system or "")
        if "语气选择器" in system or "只选序号" in system:
            self.calls += 1
            return '{"index": 1}'
        self.general_calls += 1
        self.answers.append(str(prompt or "")[:80])
        return ""

    async def polish(self, umo, reply, tone, **kwargs):
        return reply

    async def persona(self, umo):
        return ""

    async def chat_raw(self, *args, **kwargs):
        return ""


class SimPlugin(amo_message.MessageMixin, amo_llm.LlmMixin):
    # 真实生产方法（RuntimeReplyMixin 上的发送前链路），只借用函数本体
    _deco_soft_guards = RuntimeReplyMixin._deco_soft_guards
    _deco_rewrite_reply = RuntimeReplyMixin._deco_rewrite_reply
    _deco_text_cleanup = RuntimeReplyMixin._deco_text_cleanup
    _align_arrival = RuntimeReplyMixin._align_arrival
    _align_transit_word = RuntimeReplyMixin._align_transit_word
    _align_swim_where = RuntimeReplyMixin._align_swim_where
    _align_footwear = RuntimeReplyMixin._align_footwear

    def __init__(self, blocks, kv=None, health=None, cfg=None):
        self.state = FakeState(blocks, health=health)
        self.db = FakeDB(kv)
        self.state.db = self.db
        self.wallet = FakeWallet()
        self.relation = "恋人"
        self.wardrobe = FakeWardrobe()
        self.llm = SimLLM()
        self.context = FakeContext()
        self.segment_engine = None
        self.draw = None
        self.gift = None
        self.heartflow = None
        self.micro_events = None
        self._last_reply_at = {}
        self._recent_rows = []
        self._tone_pick_timeout = 1.5
        self.tone_llm_pick = False
        self._cfg = dict(cfg or {})

    # --- 发送/守卫读取的最小接口 ---
    def cfg_int(self, key, default=0):
        return int(self._cfg.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self._cfg.get(key, default))

    def cfg_str(self, key, default=""):
        return str(self._cfg.get(key, default))

    def _segment(self, text):
        return [text] if text else []

    def _quote_allowed(self, *args, **kwargs):
        return False

    def _remember_bot_message(self, umo, text):
        self._recent_rows.append({"role": "bot", "content": str(text or "")})

    def _remember_sent_reply(self, event, text):
        self._remember_bot_message("", text)

    async def _segment_wait(self, *args, **kwargs):
        return None

    def _recent(self, umo, count=8):
        return list(self._recent_rows[-int(count or 8):])[::-1]

    def _canonical_uid(self, event):
        return "1"

    def _is_talking_to_me(self, event):
        return True

    # 生产里这两个是同步调用（amo_runtime_reply._deco_soft_guards），必须用 def
    def _maybe_own_offwork_sync(self, text):
        return None

    def _maybe_own_desk_sync(self, text):
        return None


def build_plugin(blocks, kv=None, health=None, cfg=None):
    plugin = SimPlugin(blocks, kv=kv, health=health, cfg=cfg)
    pipeline.reset_tone_naturalness(plugin)
    return plugin


def _join_new(sent, start):
    return "".join(sent[start:]) if len(sent) > start else ""


async def run_turn(plugin, user_text, candidate, hhmm, umo="大肥鱼:FriendMessage:302578963"):
    """跑生产顺序的完整链路，返回最终发出去的文本与守卫痕迹。

    顺序 = amo_runtime_reply.on_decorating_result 的真实顺序
    （_deco_soft_guards -> _deco_rewrite_reply -> _deco_text_cleanup -> apply_chat_safety）
    + amo_message._send_to_umo（send 阶段登记表）。
    """
    event = FakeEvent(user_text, umo)
    result = FakeResult()
    # 真实聊天两轮之间远大于视图缓存 TTL(2s)；回放时每轮显式失效，
    # 避免把「缓存里的旧 now」当成生产行为（缓存的真实缺陷另见 _trace / context_checks）。
    try:
        from amo_diary_guard import invalidate_narrative_view
        invalidate_narrative_view(plugin)
    except Exception:  # cleanup: 视图失效失败不影响仿真
        pass
    plugin._recent_rows.append({"role": "user", "content": str(user_text or "")})
    sent_start = len(plugin.context.sent)
    out = {"user": user_text, "candidate": str(candidate or ""), "sent": "",
           "violations_before": [], "violations_after": [], "guard_error": ""}
    with frozen_clock(hhmm) as now:
        activity = dict(plugin.state.current_activity() or {})
        try:
            ctx = pipeline.fact_guard_context(plugin, activity, now)
            _fixed, before = reply_fact_guard.guard(str(candidate or ""), ctx)
            out["violations_before"] = before
        except Exception as exc:  # 观测用，不影响链路
            out["guard_error"] += "ctx:%r;" % (exc,)
            ctx = {"activity": activity, "now": now}
        text = str(candidate or "")
        try:
            text = plugin._deco_soft_guards(event, text)
            text = plugin._deco_rewrite_reply(event, result, text)
            text = await plugin._deco_text_cleanup(event, text)
            try:
                history = " ".join(str(row.get("content") or "") for row in plugin._recent(umo, 6)
                                   if isinstance(row, dict))
            except Exception:  # cleanup: 历史读不到按空
                history = ""
            text = pipeline.apply_chat_safety(text, plugin, umo, user_text, history_text=history)
        except Exception as exc:
            out["guard_error"] += "deco:%r;" % (exc,)
        try:
            # 这是「聊天回复」的发送阶段：生产里聊天走 source=chat（_send_now 与
            # immersion_check/imm_harness 都显式传 chat）。不传会 fail-closed 成 push，
            # 出口守卫按主动消息口径把「写代码」这类系统词整条丢弃，聊天轮就发空
            # （task-36 G20/t2 的对照组空回复就是这个口径差造成的）。
            await plugin._send_to_umo(umo, text, source="chat")
        except Exception as exc:
            out["guard_error"] += "send:%r;" % (exc,)
            plugin.context.sent.append(text)
        out["sent"] = _join_new(plugin.context.sent, sent_start)
        out["delivered"] = bool(out["sent"])
        try:
            _after_fixed, after = reply_fact_guard.guard(out["sent"], ctx)
            out["violations_after"] = after
        except Exception as exc:
            out["guard_error"] += "after:%r;" % (exc,)
        try:
            zone = str((ctx.get("activity") or {}).get("zone") or "")
            out["blacklist_hit"] = list(snap_mod.blacklist_hit(out["sent"], zone))
        except Exception:  # cleanup: 黑名单检查失败按未命中
            out["blacklist_hit"] = []
        out["now"] = now.strftime("%H:%M")
        out["activity"] = activity
        out["snapshot_text"] = snap_mod.snapshot_text(snap_mod.build_snapshot(plugin, now))
    return out
