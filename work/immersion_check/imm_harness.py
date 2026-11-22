# -*- coding: utf-8 -*-
"""IMM 出戏验收 harness：假插件 + 真实回复链路（事实守卫/去重/语气池/B 开关）。

链路顺序与生产 amo_runtime_reply.on_decorating_result + amo_message._send_to_umo 一致：
  _guard_self_location -> apply_fact_guard(A + 语气池 + B pending)
  -> apply_narrative_repetition(去重/先答吃啥) -> apply_tone_guard(口头禅)
  -> [B 开启] apply_pending_tone_pick_async -> _send_to_umo(完整 send 阶段)
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.join(ROOT, "tests")
for _p in (ROOT, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_message  # noqa: E402
import amo_llm  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
import reply_fact_guard  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402

try:
    from astrbot.api.message_components import Plain as _Plain
except Exception:  # pragma: no cover
    _Plain = None


def _plain_text(chain):
    items = getattr(chain, "chain", chain) or []
    return "".join(str(getattr(item, "text", "") or "") for item in items)


class FakeDB:
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


class FakeWallet:
    def __init__(self, ledger=None):
        self._ledger = list(ledger or [])

    def ledger(self, limit=200):
        return list(self._ledger)[:limit]


class FakeState:
    def __init__(self, schedule_day, blocks, health, mood, relation):
        self.schedule = {schedule_day: [dict(b) for b in blocks]}
        self.health = dict(health or {})
        self.mood = dict(mood or {})
        self.relation = relation
        self.db = None

    def health_info(self):
        return dict(self.health)

    def current_activity(self):
        blocks = self.schedule.get(next(iter(self.schedule)), [])
        return dict(blocks[0]) if blocks else {}

    def snapshot(self):
        return {"activity": self.current_activity(), "dominant": "平静"}

    def tone_text(self, snap, relation=""):
        return ""


class FakeWardrobe:
    def __init__(self, home=True):
        self.home = bool(home)

    def home_outfit_guard(self, text):
        value = str(text or "")
        if not self.home:
            return value
        for word, repl in (("牛仔裤", "家居裤"), ("帆布鞋", "拖鞋"), ("运动鞋", "拖鞋"),
                           ("高跟鞋", "拖鞋"), ("靴子", "拖鞋"), ("小皮鞋", "拖鞋")):
            value = value.replace(word, repl)
        return re.sub(r"(?<!脱掉)(?<!换下)(?<!收好)外套", "居家服", value)


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        self.sent.append(_plain_text(chain))


class MockLLM:
    """B 选择器 mock：固定/轮换序号，可注入延迟/坏答案/超时。"""

    def __init__(self, mode="rotate", delay=0.0, answer=None, error=None):
        self.mode = mode
        self.delay = float(delay)
        self.answer = answer
        self.error = error
        self.calls = 0
        self.general_calls = 0

    async def chat(self, umo, system, prompt, limit=0):
        # 只有「语气选择器」这一条 B 链路走序号；其他 chat（地点重生成等）返回空，
        # 让生产守卫走确定性兜底，避免 mock 文本冒充模型自由文本。
        if "语气选择器" not in str(system) and "只选序号" not in str(system):
            self.general_calls += 1
            return ""
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.answer is not None:
            return self.answer
        if self.mode == "fixed":
            return '{"index": 1}'
        if self.mode == "rotate":
            return '{"index": %d}' % (1 + (self.calls % 5))
        if self.mode == "bad":
            return "我觉得第 3 条挺好的，你觉得呢"
        if self.mode == "oob":
            return '{"index": 99}'
        return '{"index": 0}'

    async def polish(self, umo, reply, tone, **kwargs):
        return reply

    async def persona(self, umo):
        return ""

    async def chat_raw(self, *a, **k):
        return ""


class ImmPlugin(amo_message.MessageMixin, amo_llm.LlmMixin):
    def __init__(self, world, llm=None):
        self.world = dict(world or {})
        day = self.world.get("day") or "2026-11-17"
        self.state = FakeState(day, self.world.get("blocks") or [], self.world.get("health") or {},
                               self.world.get("mood") or {}, self.world.get("relation") or "恋人")
        self.db = FakeDB(self.world.get("kv") or {})
        self.state.db = self.db
        self.wallet = FakeWallet(self.world.get("ledger") or [])
        self.relation = self.world.get("relation") or "恋人"
        self.wardrobe = FakeWardrobe(self.world.get("home_outfit", True))
        self.llm = llm
        self.context = FakeContext()
        self.segment_engine = None
        self.draw = None
        self.gift = None
        self.heartflow = None
        self._last_reply_at = {}
        self._recent_rows = []
        self._tone_pick_timeout = 1.5
        self.tone_llm_pick = bool(self.world.get("b_enabled"))
        self._cfg = dict(self.world.get("cfg") or {})

    # --- 被 send/decorate 阶段读取的最小接口 ---
    def cfg_int(self, key, default=0):
        return int(self._cfg.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self._cfg.get(key, default))

    def cfg_str(self, key, default=""):
        return str(self._cfg.get(key, default))

    def _segment(self, text):
        return [text] if text else []

    def _quote_allowed(self, *a, **k):
        return False

    def _remember_bot_message(self, umo, text):
        self._recent_rows.append({"role": "bot", "content": str(text or "")})

    def _remember_sent_reply(self, event, text):
        self._remember_bot_message("", text)

    async def _segment_wait(self, *a, **k):
        return None

    def _recent(self, umo, count=8):
        return self._recent_rows[-int(count or 8):][::-1]

    def _canonical_uid(self, event):
        return "1"

    def _is_talking_to_me(self, event):
        return True

    async def _maybe_own_offwork_sync(self, text):
        return None


def build_plugin(world, llm=None):
    plugin = ImmPlugin(world, llm=llm)
    pipeline.reset_tone_naturalness(plugin)
    return plugin


def _user_line(plugin, umo, text):
    plugin._recent_rows.append({"role": "user", "content": str(text or "")})


async def run_turn(plugin, user_text, candidate, now, umo, b_mode=False, activity=None):
    """跑完整链路，返回 {sent, violations, mechanical, mechanical_fixed, ms}."""
    import time
    started = time.perf_counter()
    activity = dict(activity or plugin.state.current_activity())
    text = str(candidate or "")
    # 1) 老地点守卫
    try:
        text = plugin._guard_self_location(text, activity)
    except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
        pass
    # 2) 事实守卫（A + 语气池，B 开启时记 pending）
    fixed_before, violations = "", []
    del _TONE_ACTIONS[:]
    try:
        _, fixed_before, violations, _ctx = pipeline._guard_once(text, plugin, activity, now)
        text = pipeline.apply_fact_guard(text, plugin, activity, now)
    except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
        pass
    tone_changed = bool(_TONE_ACTIONS and _TONE_ACTIONS[-1])
    # 3) 去重 / 先答吃啥
    try:
        text = pipeline.apply_narrative_repetition(text, plugin, user_text, umo, now)
    except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
        pass
    # 4) 口头禅去复读
    try:
        text = pipeline.apply_tone_guard(text, plugin, umo)
    except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
        pass
    # 5) B：消费 pending 让 LLM 选序号
    if b_mode:
        try:
            text = await pipeline.apply_pending_tone_pick_async(text, plugin)
        except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
            pass
    # 6) 完整 send 阶段（clean/人名/地点/居家服/隐私/约会/极端/过渡/交通/后台泄漏）
    # 2026-11-22 task-35：聊天轮的 source 必须是 chat。旧代码不传 source，_send_to_umo
    # 按 fail-closed 当 push 走 OutboundGateway 的主动频控/去重，第 2 轮起被 drop；
    # 而 sent 又取 context.sent[-1]，于是把上一轮的发送当成这一轮的发送（假复读）。
    sent = ""
    before = len(plugin.context.sent)
    try:
        await plugin._send_to_umo(umo, text, source="chat")
        if len(plugin.context.sent) > before:
            sent = plugin.context.sent[-1]
        else:
            # 被收件人/人格/技术词等发送阶段守卫丢弃或进 deferred：没有新的出站消息，
            # 用本轮处理后的文本做离线验收口径（旧代码这里会误取上一轮残留）。
            sent = text
    except Exception as exc:
        sent = text
        plugin._send_error = repr(exc)
    ms = (time.perf_counter() - started) * 1000.0
    mechanical, fixed_used = _mechanical(sent, violations, tone_changed)
    post = _post_check(plugin, sent, activity, now)
    _user_line(plugin, umo, user_text)
    if sent:
        plugin._remember_bot_message(umo, sent)
    return {"user": user_text, "candidate": str(candidate or ""), "sent": sent,
            "violations": violations, "fixed_before": fixed_before,
            "mechanical": mechanical, "fixed_used": fixed_used, "tone_changed": tone_changed, "ms": ms,
            "post_violations": post["violations"], "blacklist": post["blacklist"],
            "zone": post["zone"]}


def _post_check(plugin, sent, activity, now):
    """发送后复核：最终文本是否还和事实冲突 / 撞地点黑名单。"""
    violations, blacklist, zone = [], [], ""
    try:
        ctx = pipeline.fact_guard_context(plugin, activity, now)
        _fixed, violations = reply_fact_guard.guard(str(sent or ""), ctx)
        zone = str((ctx.get("activity") or {}).get("zone") or "")
    except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
        pass
    try:
        from amo_state_snapshot import blacklist_hit
        blacklist = list(blacklist_hit(str(sent or ""), zone))
    except Exception:
        blacklist = []
    return {"violations": violations, "blacklist": blacklist, "zone": zone}


async def run_proactive_turn(plugin, candidate, now, umo, b_mode=False, activity=None):
    """主动消息链路：事实守卫（可 B）→ 主动去重 → 完整 send 阶段。"""
    import time
    started = time.perf_counter()
    activity = dict(activity or plugin.state.current_activity())
    text = str(candidate or "")
    try:
        text = await pipeline.apply_fact_guard_async(text, plugin, activity, now)
    except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
        pass
    try:
        text = pipeline.apply_proactive_dedup(text, plugin, umo, now)
    except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
        pass
    sent = ""
    before = len(plugin.context.sent)
    try:
        await plugin._send_to_umo(umo, text)
        if len(plugin.context.sent) > before:
            sent = plugin.context.sent[-1]
        else:
            sent = text
    except Exception:
        sent = text
    ms = (time.perf_counter() - started) * 1000.0
    post = _post_check(plugin, sent, activity, now)
    if sent:
        plugin._remember_bot_message(umo, sent)
    return {"user": "", "candidate": str(candidate or ""), "sent": sent,
            "violations": [], "fixed_before": "", "mechanical": False,
            "fixed_used": [], "ms": ms, "post_violations": post["violations"],
            "blacklist": post["blacklist"], "zone": post["zone"]}


#: 记录语气池这一轮到底改了几条（apply_fact_guard 内部调用）
_TONE_ACTIONS: list[list[Any]] = []
_orig_naturalize = pipeline.naturalize_fact_rewrites


def _recording_naturalize(*args, **kwargs):
    result = _orig_naturalize(*args, **kwargs)
    try:
        _TONE_ACTIONS.append(list(result[1] or []))
    except Exception:
        _TONE_ACTIONS.append([])
    return result


pipeline.naturalize_fact_rewrites = _recording_naturalize


def _mechanical(sent, violations, tone_changed):
    """机械改写 = 有事实冲突、但这一轮语气池一条都没换（仍用固定改口句）。"""
    if not violations or tone_changed:
        return False, []
    used = []
    for item in violations or ():
        if isinstance(item, dict) and item.get("replacement"):
            used.append(str(item.get("replacement")))
    return True, used[:3]
