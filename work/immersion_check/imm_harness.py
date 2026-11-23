# -*- coding: utf-8 -*-
"""IMM 出戏验收 harness：假插件 + 真实回复链路（事实守卫/去重/语气池/B 开关）。

链路顺序与生产 amo_runtime_reply.on_decorating_result + amo_message._send_to_umo 一致：
  apply_fact_guard(地点/事实守卫 + 语气池 + B pending)
  -> apply_narrative_repetition(去重/先答吃啥) -> apply_tone_guard(口头禅)
  -> [B 开启] apply_pending_tone_pick_async -> _send_to_umo(完整 send 阶段)
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import sys
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.join(ROOT, "tests")
SIM = os.path.join(ROOT, "work", "sim")
for _p in (ROOT, TESTS, SIM):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_message  # noqa: E402
import amo_llm  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
import mock_provider as _mock_provider  # noqa: E402
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


#: repair 提示词里的问句 -> 探针意图（与 work/sim/sim_env._PROBE_OF 同口径）
_REPAIR_PROBE = (
    ("when_return", re.compile(r"几点|什么时候回|回不回来|回不回家|到家呀|什么时候到家")),
    ("where", re.compile(r"在哪|哪里|在不在家|到家了吗|到公司")),
    ("doing", re.compile(r"干嘛|做什么|忙什么|在忙|忙不忙|偷懒")),
    ("who", re.compile(r"跟谁|和谁|一个人|旁边有|有人吗|谁在一起")),
    ("eaten", re.compile(r"吃了吗|吃了没|吃饭了没|吃的什么|吃啥")),
)


def _probe_of(user_text):
    body = str(user_text or "")
    for name, pattern in _REPAIR_PROBE:
        if pattern.search(body):
            return name
    return ""


#: 情绪词 -> mock_provider 既有话题池（复用回复数据，不新造句子）。
#: 真模型重写时看得到「守卫改之前的那一版」，所以情绪不会写丢；mock 也必须这样。
_EMOTION_TOPIC = (
    ("想你", "miss"), ("想我", "miss"), ("爱你", "miss"),
    ("抱", "coquetry"), ("亲", "coquetry"), ("撒娇", "coquetry"),
    ("喜欢", "coquetry"), ("开心", "coquetry"), ("高兴", "coquetry"),
    ("累", "work_complain"), ("烦", "work_complain"), ("委屈", "work_complain"),
    ("难过", "work_complain"), ("难受", "work_complain"), ("心疼", "work_complain"),
)
#: 话题池里没有对应说法时 mock 自己拼的情绪变体（真模型也是这么写的，不是生产固定池）。
_EMOTION_VARIANTS = {
    "困": ("困了就早点睡。", "眼皮都打架了吧，快去睡。"),
    "怕": ("别怕，我在呢。",),
    "哭": ("别哭呀，我在这儿。",),
    "生气": ("别生气了。", "谁惹你了，跟我说。"),
    "郁闷": ("别郁闷了，我陪你待会儿。",),
    "孤单": ("我陪着你呢。",),
    "舍不得": ("我也舍不得。",),
    "心酸": ("听着有点心酸。",),
    "痛": ("还疼吗？",),
    "可爱": ("你最可爱了。",),
    "暖": ("心里暖暖的。",),
    "呜呜": ("别哭呀，抱抱。",),
}
#: 与验收口径同口径的情绪词表（run_imm.EMOTION_WORDS 的 harness 侧副本）。
_EMOTION_WORDS = (tuple(word for word, _topic in _EMOTION_TOPIC)
                  + tuple(_EMOTION_VARIANTS)
                  + ("想你", "难过", "委屈", "开心", "高兴", "累", "困", "烦", "怕", "哭",
                     "心疼", "喜欢", "爱你", "生气", "难受", "舍不得", "孤单", "心酸",
                     "暖", "抱", "亲", "可爱", "痛", "郁闷", "撒娇", "呜呜"))


def _prompt_value(body, prefix):
    """取提示词里某一行冒号后的内容（repair 提示词是「标签：内容」一行一条）。"""
    for line in str(body or "").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


class MockLLM:
    """B 选择器 mock：固定/轮换序号，可注入延迟/坏答案/超时。

    B2：chat 出口命中空/罐头会调一次模型（amo_reply_pipeline.repair_reply）。旧 mock
    的 chat_raw 恒返回空等于「模型永远不可用」，harness 就测不到新架构的出口修复，
    所以按 work/sim/sim_env.SimLLM 的做法只应答 repair 提示词（用 mock_provider 的
    回答 + 当前场景模拟真模型）；其它 chat/chat_raw 调用仍返回空（确定性兜底不变）。
    """

    def __init__(self, mode="rotate", delay=0.0, answer=None, error=None):
        self.mode = mode
        self.delay = float(delay)
        self.answer = answer
        self.error = error
        self.calls = 0
        self.general_calls = 0
        self.plugin = None
        #: 这一轮（同一个 item/plugin 生命周期）已经说过的句子：repair 不再重复。
        self.used = set()

    def _snapshot(self):
        try:
            return dict(self.plugin.state.current_activity() or {})
        except Exception:  # cleanup: 没有场景就按日常句回答（模拟模型读不到快照）
            return {}

    def _subject(self):
        """「我」的此刻状态/地点，用来拼场景变体（避免只有 3 句反复说）。"""
        snap = self._snapshot()
        verb = str(snap.get("verb") or snap.get("activity") or "").strip()
        place = str(snap.get("place") or "").strip()
        return verb, place

    def _plain_candidates(self, user, snap):
        """非情绪轮的候选（按「最像真模型」的顺序）：探针答案 / 场景句 / 日常池。

        顺序有意义：repair 的提示词要求「带上你自己的情绪和具体内容」，真模型会按此刻
        场景说一句具体的话；只从 3 句 daily 池里挑会被出口的宪法探针（罐头句/事实守卫）
        挡掉，最后 fallback 回原句——那才是 mock 保真度不足。
        """
        probe = _probe_of(user)
        answers, scene, daily = [], [], []
        if probe:
            meal_ok = str(snap.get("category") or "") == "meal"
            for index in range(5):
                answers.append(_mock_provider.clean_answer(
                    probe, snap, meal_ok, bool(snap.get("together")),
                    random.Random("cand:%s:%d" % (user, index))))
        daily.extend(_mock_provider.TOPIC_REPLIES["daily"])
        verb, place = self._subject()
        if verb:
            scene.append("%s呢，怎么啦？" % verb)
            scene.append("%s呢，你呢？" % verb)
        if place:
            scene.append("还在%s呢，怎么啦？" % place)
        cands = (answers + scene + daily) if probe else (scene + daily)
        return [str(item) for item in cands if str(item or "").strip()]

    def _emotion_candidates(self, word):
        """原句带情绪时的候选：复用 mock_provider 的话题池 + 变体，且必须含情绪词。"""
        cands = []
        topic = dict(_EMOTION_TOPIC).get(word)
        if topic:
            cands.extend(_mock_provider.TOPIC_REPLIES.get(topic) or ())
        cands.extend(_EMOTION_VARIANTS.get(word) or ())
        kept = [str(item) for item in cands
                if str(item or "").strip() and any(w in str(item) for w in _EMOTION_WORDS)]
        return kept or [str(item) for item in cands if str(item or "").strip()]

    def _sent_lines(self, umo):
        """这一轮已经发出去的 bot 话（近聊）：repair 不能把它们原样再说一遍。"""
        try:
            rows = self.plugin._recent(umo, 10) or []
        except Exception:  # cleanup: 读不到近聊就只用 mock 自己的记录
            rows = []
        return {str(row.get("content") or "").strip()
                for row in rows
                if str(row.get("role") or "") in ("bot", "assistant")}

    def _pick_unused(self, candidates, rng, banned=()):
        """按候选顺序挑一句本轮没说过的话；全说过就返回空，由调用方拼变体。"""
        taken = set(banned or ()) | self.used
        for item in candidates:
            if item and item not in taken:
                return item
        return ""

    def _repair_answer(self, prompt, umo=""):
        """只处理 chat 出口的 repair 提示词：模拟真模型——看得到原句所以保留情绪，
        并且不把这一轮已经说过的话再说一遍（mock 自己维护 used，不动验收阈值）。"""
        body = str(prompt or "")
        if "问题：" not in body or "只输出一句话" not in body:
            return ""
        user = _prompt_value(body, "他刚说的是：")
        if not user:
            return ""
        previous = _prompt_value(body, "守卫改之前的那一版：")
        snap = self._snapshot()
        rng = random.Random("repair:%s" % user)
        banned = self._sent_lines(umo)
        emotion = next((word for word in _EMOTION_WORDS if word in previous), "")
        if emotion:
            # 真模型重写时会接住原句的情绪（「我也想你。」这类），不会写丢。
            answer = self._pick_unused(self._emotion_candidates(emotion), rng, banned)
            if not answer:
                answer = self._pick_unused(
                    ["我也%s。" % emotion, "%s的时候记得靠我一点。" % emotion],
                    rng, banned)
        else:
            answer = self._pick_unused(self._plain_candidates(user, snap), rng, banned)
        if not answer:
            # 候选全说过（同一轮 repair 次数很多）时才拼一句场景变体，保证不为空。
            verb, place = self._subject()
            answer = "在%s呢。" % (verb or place or "忙")
        # 日程活动本身可能已带「在」（「在开会写周报」），模板再套一层会成「在在开会」。
        answer = str(answer or "").replace("在在", "在")
        self.used.add(answer)
        return answer

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

    async def chat_raw(self, umo="", system="", prompt="", *args, **kwargs):
        # 与 sim_env.SimLLM 同口径：只应答 repair 提示词，其余仍是空（模型不可用）。
        answer = self._repair_answer(prompt, umo=str(umo or ""))
        if answer:
            self.general_calls += 1
        return answer



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
    if llm is not None:
        try:
            llm.plugin = plugin   # repair mock 读当前场景（与 sim_env.SimLLM 一致）
        except Exception:  # cleanup: 只读 mock，不接受就按默认场景回答
            pass
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
    # 1) 事实守卫（3a-2b：地点/活动唯一守卫 reply_fact_guard + 语气池，B 开启时记 pending）
    fixed_before, violations = "", []
    del _TONE_ACTIONS[:]
    try:
        _, fixed_before, violations, _ctx = pipeline._guard_once(text, plugin, activity, now)
        text = pipeline.apply_fact_guard(text, plugin, activity, now)
    except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
        pass
    tone_changed = bool(_TONE_ACTIONS and _TONE_ACTIONS[-1])
    # 2) 去重 / 先答吃啥
    try:
        text = pipeline.apply_narrative_repetition(text, plugin, user_text, umo, now)
    except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
        pass
    # 3) 口头禅去复读
    try:
        text = pipeline.apply_tone_guard(text, plugin, umo)
    except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
        pass
    # 4) B：消费 pending 让 LLM 选序号
    if b_mode:
        try:
            text = await pipeline.apply_pending_tone_pick_async(text, plugin)
        except Exception:  # cleanup: harness 兜底，守卫失败不拦链路
            pass
    # 5) 完整 send 阶段（clean/人名/地点/居家服/隐私/约会/极端/过渡/交通/后台泄漏）
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
