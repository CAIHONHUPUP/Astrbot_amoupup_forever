# -*- coding: utf-8 -*-
"""Phase 6：ReplyPipeline（amo_reply_pipeline）——顺序、when、去重、A/B 等价、反向验证。

A/B 的语义：tests/golden/reply_pipeline_ab.json 里的 trace/sent 是**改造前**代码
（见交付报告里的备份目录）跑同一批假插件得到的；这里用改造后的真实发送路径重跑一遍，
要求逐字节一致，连守卫调用顺序都一致。generate 阶段的 golden 同理，来自改造前真实
_claim_retry + _reply_postprocess + 补图块的输出。
"""
from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
import os
import re
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_reply_pipeline as pipeline  # noqa: E402
from amo_reply_pipeline import (  # noqa: E402
    PHASE_DECORATE,
    PHASE_GENERATE,
    PHASE_PROACTIVE_PRE,
    PHASE_SEND,
    Pipeline,
    ReplyContext,
    Stage,
    StopPipeline,
)

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "golden", "reply_pipeline_ab.json")
TRACE: list[str] = []
HOME = {"place": "家", "activity": "在家看剧", "category": "rest"}
MALL = {"place": "商场", "activity": "逛街", "category": "date"}
UMO = "aiocqhttp:FriendMessage:1"
_NOW = time.time()
DATE_KV = {"date_session": {"schema_version": 1, "phase": "active", "line": "和老公吃火锅",
                            "title": "约会", "place": "商场", "start_ts": _NOW - 600,
                            "end_ts": _NOW + 3600, "together": True}}

EXPECTED_SEND_ORDER = [
    "send.clean", "send.names", "send.zone_guard", "send.photo_claim", "send.wardrobe", "send.privacy",
    "send.status_dedup", "send.date_context", "send.immersion", "send.transition_advance",
    "send.transit_rewrite", "send.model_leak",
]
EXPECTED_PROACTIVE_ORDER = ["proactive_pre.consumption"]
EXPECTED_DECORATE_ORDER = [
    "decorate.strip_chat_format", "decorate.offwork_sync", "decorate.fact_guard",
    "decorate.attribution_alert", "decorate.gift_observe", "decorate.defensive_rewrite",
    "decorate.model_leak", "decorate.draw_after_reply", "decorate.polish_native",
    "decorate.brushoff", "decorate.food_question", "decorate.transit",
    "decorate.zone_guard", "decorate.date_guard", "decorate.mark_date_end", "decorate.trim",
]
EXPECTED_GENERATE_ORDER = [
    "generate.claim_retry", "generate.polish", "generate.trim", "generate.date_state",
    "generate.refusal", "generate.short_reply",
    "generate.consumption", "generate.group_guard", "generate.group_tease",
    "generate.photo_claim", "generate.photo_intent", "generate.photo_wants",
]

SEND_SCENARIOS = [
    {"id": "names", "text": "李婷约我明天吃饭，她说最近很忙", "umo": UMO,
     "activity": HOME, "wardrobe_home": True},
    {"id": "location", "text": "我在工位加班呢，公司今天好忙", "umo": UMO,
     "activity": HOME, "wardrobe_home": True},
    {"id": "date", "text": "我一个人在家吃饭呢，好无聊，你在哪？", "umo": UMO, "kv": DATE_KV,
     "activity": MALL, "wardrobe_home": False},
    {"id": "extreme", "text": "我真的不想活了，好累好累", "umo": UMO,
     "activity": HOME, "wardrobe_home": True},
    {"id": "photo_promise", "text": "等我拍好了发你看，咔嚓一下", "umo": UMO,
     "activity": HOME, "wardrobe_home": True},
    {"id": "privacy_wardrobe", "text": "我把聊天记录加密了，穿着牛仔裤和外套在沙发", "umo": UMO,
     "activity": HOME, "wardrobe_home": True},
    {"id": "model_leak", "text": "我是由深度求索开发的AI助手啊", "umo": UMO,
     "activity": HOME, "wardrobe_home": True},
    {"id": "group", "text": "老公我在家呢，@小王(QQ:12345678) 你们聊", "umo": "aiocqhttp:GroupMessage:2",
     "group": True, "activity": HOME, "wardrobe_home": True},
    {"id": "plain", "text": "好呀，那我们晚上吃火锅吧！", "umo": UMO,
     "activity": HOME, "wardrobe_home": False},
]
PROACTIVE_SCENARIOS = [
    {"id": "proactive_claim", "text": "老公我刚点了个外卖，好香啊。你今天忙不忙？",
     "umo": UMO, "profile": {"uid": "1", "name": "老公"}, "activity": HOME},
    {"id": "proactive_plain", "text": "今天风好大，窗户一直在响。你那边冷不冷？",
     "umo": UMO, "profile": {"uid": "1", "name": "老公"}, "activity": HOME},
]

#: 改造前跑真实 _claim_retry + _reply_postprocess + 补图块得到的逐字节结果
GOLDEN_GENERATE = {
    "claim": {"text": "今天在家煮了碗面，好香",
              "trace": ["polish", "trim", "date_state", "refusal", "short_reply",
                        "consumption"]},
    "photo_promise": {"text": "刚刚想拍照，没拍成，先不折腾啦。",
                      "trace": ["polish", "trim", "date_state", "refusal", "short_reply",
                                "consumption"]},
    "short_reply": {"text": "谢谢",
                    "trace": ["polish", "trim", "date_state", "refusal", "short_reply",
                              "consumption"]},
    "plain": {"text": "好呀，那晚上一起看剧。",
              "trace": ["polish", "trim", "date_state", "refusal", "short_reply",
                        "consumption"]},
}
GEN_INPUTS = {
    "claim": {"reply": "今天点了个外卖，好香", "user": "吃了吗"},
    "photo_promise": {"reply": "等我拍好了发你看，咔嚓一下", "user": "在干嘛"},
    "short_reply": {"reply": "谢谢", "user": "给你买了个东西"},
    "plain": {"reply": "好呀，那晚上一起看剧。", "user": "晚上干嘛"},
}
GEN_TRACE_GROUP = ["polish", "trim", "date_state", "refusal", "short_reply",
                   "consumption", "group_guard", "group_tease"]
STAGE_TO_LABEL = {
    "generate.claim_retry": "claim_retry",
    "generate.polish": "polish",
    "generate.trim": "trim",
    "generate.date_state": "date_state",
    "generate.refusal": "refusal",
    "generate.short_reply": "short_reply",
    "generate.consumption": "consumption",
    "generate.group_guard": "group_guard",
    "generate.photo_intent": "photo_intent",
}


# ---------------------------------------------------------------------------
# 假插件与工具
# ---------------------------------------------------------------------------


class FakeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, activity):
        self.activity = dict(activity or {})

    def snapshot(self):
        return {"activity": self.activity, "dominant": "平静"}

    def health_info(self):
        return {"status": "健康"}

    def current_activity(self):
        return self.activity


class FakeWardrobe:
    def __init__(self, home):
        self.home = bool(home)

    def home_outfit_guard(self, text):
        if not self.home:
            return text
        text = str(text or "")
        for word, repl in (("牛仔裤", "家居裤"), ("帆布鞋", "拖鞋"), ("运动鞋", "拖鞋"),
                           ("高跟鞋", "拖鞋"), ("靴子", "拖鞋"), ("小皮鞋", "拖鞋")):
            text = text.replace(word, repl)
        return re.sub(r"(?<!脱掉)(?<!换下)(?<!收好)外套", "居家服", text)


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        items = getattr(chain, "chain", chain)
        self.sent.append(getattr(items[0], "text", "") if items else "")


class FakeLLM:
    async def polish(self, umo, reply, tone, **kwargs):
        TRACE.append("polish")
        return reply

    async def chat(self, umo, system, prompt, limit=0):
        TRACE.append("claim_retry.chat")
        return ""


async def _noop_wait(*args, **kwargs):
    return None


def _load(mode="top"):
    """取一套插件模块；pkg 模式 = 生产加载方式（相对导入生效）。"""
    names = ("amo_message", "amo_proactive", "amo_llm", "amo_names", "amo_diary_guard",
             "amo_date_context", "amo_immersion_guard", "amo_transit", "amo_transition",
             "amo_fact_binder", "date_state", "short_reply", "refusal", "group_scene")
    if mode == "pkg":
        pkg = os.path.basename(ROOT)
        parent = os.path.dirname(ROOT)
        if not pkg.isidentifier():
            raise unittest.SkipTest("插件目录名不是合法包名，跳过包模式用例")
        if parent not in sys.path:
            sys.path.insert(0, parent)
        try:
            return {name: importlib.import_module(pkg + "." + name) for name in names}
        except Exception as exc:  # pragma: no cover - 环境不支持包模式
            raise unittest.SkipTest("包模式不可用: %r" % (exc,))
    return {name: importlib.import_module(name) for name in names}


def _golden(mode="top"):
    with open(GOLDEN_PATH, encoding="utf-8") as fh:
        return json.load(fh)[mode]


def _scenario(scenario_id, scenarios):
    for item in scenarios:
        if item["id"] == scenario_id:
            return dict(item)
    raise KeyError(scenario_id)


def _traced(orig, label):
    def wrapper(*args, **kwargs):
        TRACE.append(label)
        return orig(*args, **kwargs)
    return wrapper


class _Tracing:
    """临时给守卫函数套一层记录器（只记调用顺序，不改返回值）。"""

    SPECS = (("amo_names", "names", ("foreign_names", "sanitize")),
             ("amo_diary_guard", "privacy", ("sanitize_privacy",)),
             ("amo_date_context", "date", ("guard_reply",)),
             ("amo_immersion_guard", "immersion", ("sanitize_extreme", "fix_identity")),
             ("amo_transit", "transit", ("rewrite_sent_text",)),
             ("amo_transition", "transition", ("maybe_advance", "maybe_start_from_text")))

    def __init__(self, mods):
        self.mods = mods
        self.saved = []

    def __enter__(self):
        del TRACE[:]
        for key, label, funcs in self.SPECS:
            module = self.mods[key]
            for name in funcs:
                orig = getattr(module, name)
                self.saved.append((module, name, orig))
                setattr(module, name, _traced(orig, label + "." + name))
        fact = self.mods["amo_fact_binder"]
        orig_fact = fact.guard
        self.saved.append((fact, "guard", orig_fact))

        def fact_guard(reply, plugin, now=None, mode=""):
            TRACE.append("claim.guard")
            body = str(reply or "")
            if "点了个外卖" in body:
                return body.replace("点了个外卖", "在家煮了碗面"), True, "外卖"
            return orig_fact(reply, plugin, now, mode)
        fact.guard = fact_guard
        return self

    def __exit__(self, *exc):
        for module, name, orig in reversed(self.saved):
            setattr(module, name, orig)
        self.saved = []
        return False


def _send_plugin(mods, scenario):
    mixin = mods["amo_message"].MessageMixin
    plugin = mixin.__new__(mixin)
    plugin.db = FakeDB(scenario.get("kv"))
    plugin.state = FakeState(scenario.get("activity"))
    plugin.wardrobe = FakeWardrobe(scenario.get("wardrobe_home", False))
    plugin.llm = None
    plugin.context = FakeContext()
    plugin._last_reply_at = {}
    plugin.segment_engine = None
    plugin.cfg_int = lambda key, default=0: default
    plugin.cfg_bool = lambda key, default=False: default
    plugin._segment_wait = _noop_wait
    plugin._quote_allowed = lambda *a, **k: False
    plugin._remember_bot_message = lambda *a, **k: None
    real_zone = mixin._guard_zone_reply

    async def zone(text, umo="", **kwargs):
        TRACE.append("send.zone")
        return await real_zone(plugin, text, umo, **kwargs)
    plugin._guard_zone_reply = zone
    real_names = mixin._rewrite_without_foreign

    async def names(umo, text, dirty):
        TRACE.append("send.names")
        return await real_names(plugin, umo, text, dirty)
    plugin._rewrite_without_foreign = names
    _bind_claim(mods, plugin)
    return plugin


def _bind_claim(mods, plugin):
    real_claim = mods["amo_message"].MessageMixin._guard_consumption.__get__(plugin)

    def claim(text):
        TRACE.append("send.claim")
        return real_claim(text)
    plugin._guard_consumption = claim


def _send_result(mods, scenario):
    plugin = _send_plugin(mods, scenario)
    ok = asyncio.run(plugin._send_to_umo(scenario["umo"], scenario["text"],
                                         group=scenario.get("group", False)))
    return {"ok": bool(ok), "trace": list(TRACE), "sent": list(plugin.context.sent)}


def _proactive_plugin(mods, scenario):
    mixin = mods["amo_proactive"].ProactiveMixin
    plugin = mixin.__new__(mixin)
    plugin.db = FakeDB()
    plugin.state = FakeState(scenario.get("activity"))
    plugin.llm = None
    plugin.cfg_int = lambda key, default=0: default
    plugin.cfg_bool = lambda key, default=False: default
    plugin._proactive_candidates = lambda profile, primary: [primary]
    plugin._mark_bot_initiated = lambda *a, **k: None
    _bind_claim(mods, plugin)
    return plugin


def _proactive_result(mods, scenario):
    plugin = _proactive_plugin(mods, scenario)
    parts = []

    async def fake_send(umo, text, quote_id=None, group=False):
        parts.append(text)
        return True
    plugin._send_to_umo = fake_send
    asyncio.run(plugin._send_proactive_lines(scenario.get("profile", {}),
                                             scenario["umo"], scenario["text"]))
    return {"trace": list(TRACE), "sent": parts}


def _gen_class(mods):
    return type("FakeGenPlugin", (mods["amo_message"].MessageMixin, mods["amo_llm"].LlmMixin), {})


def _gen_plugin(mods):
    cls = _gen_class(mods)
    plugin = cls.__new__(cls)
    plugin.db = FakeDB()
    plugin.state = FakeState(HOME)
    plugin.draw = None
    plugin.gift = None
    plugin.llm = FakeLLM()
    plugin.cfg_int = lambda key, default=0: default
    plugin.cfg_bool = lambda key, default=False: default
    plugin._trim_reply = _traced(cls._trim_reply.__get__(plugin), "trim")
    plugin._guard_consumption = _traced(cls._guard_consumption.__get__(plugin), "consumption")

    def tease(*args, **kwargs):
        TRACE.append("group_tease")
    plugin._maybe_group_private_tease = tease
    return plugin


class _GenTracing:
    """generate 阶段：真实调用链 vs 登记表模型，都按同一套记录器抓顺序。"""

    def __init__(self, mods):
        self.mods = mods
        self.saved = []

    def __enter__(self):
        del TRACE[:]
        refuse = self.mods["refusal"]
        orig_refuse = refuse.should_refuse
        self.saved.append((refuse, "should_refuse", orig_refuse))

        def should_refuse(*args, **kwargs):
            TRACE.append("refusal")
            return orig_refuse(*args, **kwargs)
        refuse.should_refuse = should_refuse
        for key, name, label in (("date_state", "guard", "date_state"),
                                 ("short_reply", "guard", "short_reply"),
                                 ("group_scene", "guard_group_reply", "group_guard")):
            module = self.mods[key]
            orig = getattr(module, name)
            self.saved.append((module, name, orig))
            setattr(module, name, _traced(orig, label))
        fact = self.mods["amo_fact_binder"]
        orig_fact = fact.guard
        self.saved.append((fact, "guard", orig_fact))

        def fact_guard(reply, plugin, now=None, mode=""):
            body = str(reply or "")
            if "点了个外卖" in body:
                return body.replace("点了个外卖", "在家煮了碗面"), True, "外卖"
            return orig_fact(reply, plugin, now, mode)
        fact.guard = fact_guard
        return self

    def __exit__(self, *exc):
        for module, name, orig in reversed(self.saved):
            setattr(module, name, orig)
        self.saved = []
        return False


GEN_CTX = {"umo": UMO, "uid": "1", "scene": "private", "owner_name": "他", "name": "老公",
           "relation": "相恋", "persona": "", "state_text": "", "tone": "", "style": "",
           "consult": False, "cash_cents": 0, "silence_days": 0}


def _clean_trace(items):
    """_claim_retry 内部那次 llm.chat 只是实现细节，不算一道守卫。"""
    return [item for item in items if item != "claim_retry.chat"]


async def _legacy_generate(plugin, reply, user, group=False):
    """改造前 _compose_reply 的真实顺序（generate 阶段）。"""
    out = await plugin._claim_retry(reply, UMO, "", "")
    ctx = dict(GEN_CTX, scene="owner_group" if group else "private")
    out = await plugin._reply_postprocess(out, ctx, None, user, {"activity": HOME}, group)
    enforcer = getattr(getattr(plugin, "draw", None), "enforce_photo_claim", None)
    added = False
    if callable(enforcer):
        out, added = enforcer(None, out)
    if not added:
        out = plugin._enforce_photo_intent(None, out)
    return out


def _gen_ctx(plugin, user, group=False):
    return ReplyContext(plugin=plugin, umo=UMO, phase=PHASE_GENERATE, group=group,
                        activity=HOME, snap={"activity": HOME}, his_words=user,
                        scene="owner_group" if group else "private",
                        owner_name="他", relation="相恋", name="老公")


def _model_trace(result):
    return [STAGE_TO_LABEL[item] for item in result["applied"] if item in STAGE_TO_LABEL]


# ---------------------------------------------------------------------------
# 1) 登记表：顺序 / when / applied / stopped / 去重 / audit
# ---------------------------------------------------------------------------


class RegistryOrderTests(unittest.IsolatedAsyncioTestCase):
    def test_send_order_matches_legacy_code(self):
        got = [stage.id for stage in pipeline.PIPELINE.ordered(PHASE_SEND)]
        self.assertEqual(got, EXPECTED_SEND_ORDER)

    def test_proactive_order(self):
        got = [stage.id for stage in pipeline.PIPELINE.ordered(PHASE_PROACTIVE_PRE)]
        self.assertEqual(got, EXPECTED_PROACTIVE_ORDER)

    def test_decorate_order_matches_legacy_code(self):
        got = [stage.id for stage in pipeline.PIPELINE.ordered(PHASE_DECORATE)]
        self.assertEqual(got, EXPECTED_DECORATE_ORDER)

    def test_generate_order_matches_legacy_code(self):
        got = [stage.id for stage in pipeline.PIPELINE.ordered(PHASE_GENERATE)]
        self.assertEqual(got, EXPECTED_GENERATE_ORDER)

    async def test_when_filters_by_context(self):
        stages = [Stage("only.group", PHASE_SEND, 10, lambda text, ctx: text + "!", when=lambda ctx: ctx.group)]
        pipe = Pipeline(stages)
        private = await pipe.run("x", ReplyContext(phase=PHASE_SEND))
        group = await pipe.run("x", ReplyContext(phase=PHASE_SEND, group=True))
        self.assertEqual(private["applied"], [])
        self.assertEqual(private["text"], "x")
        self.assertEqual(group["applied"], ["only.group"])
        self.assertEqual(group["text"], "x!")

    async def test_real_group_guard_follows_ctx(self):
        mods = _load("top")
        with _GenTracing(mods):
            private_ctx = _gen_ctx(_gen_plugin(mods), "在干嘛", group=False)
            private = await pipeline.run("老公我在家呢", private_ctx)
            group_ctx = _gen_ctx(_gen_plugin(mods), "在干嘛", group=True)
            group = await pipeline.run("老公我在家呢", group_ctx)
        self.assertNotIn("generate.group_guard", private["applied"])
        self.assertIn("generate.group_guard", group["applied"])

    async def test_applied_and_changed_record_what_ran(self):
        stages = [Stage("a", PHASE_SEND, 10, lambda text, ctx: text + "A"),
                  Stage("b", PHASE_SEND, 20, lambda text, ctx: None),
                  Stage("c", PHASE_SEND, 30, lambda text, ctx: text)]
        result = await Pipeline(stages).run("x", ReplyContext(phase=PHASE_SEND))
        self.assertEqual(result["applied"], ["a", "b", "c"])
        self.assertEqual(result["changed"], ["a"])
        self.assertEqual(result["text"], "xA")
        self.assertEqual(result["notes"], [])

    async def test_none_return_keeps_text(self):
        stages = [Stage("noop", PHASE_SEND, 10, lambda text, ctx: None)]
        result = await Pipeline(stages).run("原文", ReplyContext(phase=PHASE_SEND))
        self.assertEqual(result["text"], "原文")
        self.assertEqual(result["changed"], [])

    async def test_stop_pipeline_sets_stopped(self):
        def stop(text, ctx):
            raise StopPipeline("到此为止")
        stages = [Stage("go", PHASE_SEND, 10, lambda text, ctx: text + "1"),
                  Stage("stop", PHASE_SEND, 20, stop),
                  Stage("after", PHASE_SEND, 30, lambda text, ctx: text + "3")]
        result = await Pipeline(stages).run("x", ReplyContext(phase=PHASE_SEND))
        self.assertTrue(result["stopped"])
        self.assertEqual(result["text"], "x1")
        self.assertEqual(result["applied"], ["go", "stop"])
        self.assertIn("到此为止", "".join(result["notes"]))

    async def test_same_stage_runs_once_per_context(self):
        calls = []

        def once(text, ctx):
            calls.append(text)
            return text + "!"
        stages = [Stage("s1", PHASE_SEND, 10, once)]
        pipe = Pipeline(stages)
        ctx = ReplyContext(phase=PHASE_SEND)
        first = await pipe.run("x", ctx)
        second = await pipe.run(first["text"], ctx)
        self.assertEqual(len(calls), 1)
        self.assertEqual(second["applied"], [])
        self.assertTrue(any("skip duplicate" in note for note in second["notes"]))

    async def test_same_target_runs_once_per_context(self):
        def marker(text, ctx):
            return text + "!"
        stages = [Stage("one", PHASE_SEND, 10, marker, target="shared.guard"),
                  Stage("two", PHASE_SEND, 20, marker, target="shared.guard")]
        result = await Pipeline(stages).run("x", ReplyContext(phase=PHASE_SEND))
        self.assertEqual(result["text"], "x!")
        self.assertEqual(result["applied"], ["one"])

    async def test_audit_reports_order_and_overlaps(self):
        report = pipeline.audit()
        self.assertEqual(report["order"][PHASE_SEND], EXPECTED_SEND_ORDER)
        self.assertEqual(report["duplicates"], [])
        targets = {item["target"]: item for item in report["overlaps"]}
        # 3a-2b：老 place_guard 包装已从登记表移除，且不许回流
        self.assertNotIn("amo_message._guard_self_location", targets)
        # 跨阶段重复仍要被审计出来（真实存在：地点黑名单在 decorate 和 send 各一次）
        self.assertIn("amo_message._guard_zone_reply", targets)
        self.assertFalse(targets["amo_message._guard_zone_reply"]["same_phase"])
        self.assertIn("decorate.zone_guard",
                      targets["amo_message._guard_zone_reply"]["stages"])
        self.assertIn("send.zone_guard",
                      targets["amo_message._guard_zone_reply"]["stages"])

    async def test_audit_flags_duplicates(self):
        stages = [Stage("dup", PHASE_SEND, 10, lambda text, ctx: None),
                  Stage("dup", PHASE_SEND, 20, lambda text, ctx: None)]
        report = pipeline.audit(Pipeline(stages))
        self.assertEqual(report["duplicates"], ["dup"])

    async def test_audit_flags_no_ctx_and_descriptors(self):
        report = pipeline.audit()
        self.assertIn("send.privacy", report["no_ctx"])
        self.assertIn("decorate.attribution_alert", report["descriptors"])
        self.assertIn("generate.photo_claim", report["descriptors"])

    async def test_audit_wired_phases(self):
        report = pipeline.audit()
        self.assertEqual(report["wired_phases"], [PHASE_SEND, PHASE_PROACTIVE_PRE])
        self.assertTrue(report["order"][PHASE_SEND])
        self.assertIn("decorate.fact_guard", report["unwired"])
        self.assertIn("generate.claim_retry", report["unwired"])


# ---------------------------------------------------------------------------
# 2) A/B 等价：改造前 golden vs 改造后真实发送路径（逐字节）
# ---------------------------------------------------------------------------


class SendPathABTests(unittest.TestCase):
    def _ab(self, scenario_id, mode="top"):
        mods = _load(mode)
        scenario = _scenario(scenario_id, SEND_SCENARIOS)
        golden = _golden(mode)["send:" + scenario_id]
        with _Tracing(mods):
            result = _send_result(mods, scenario)
        self.assertEqual(result["sent"], golden["sent"])
        self.assertEqual(result["trace"], golden["trace"])
        self.assertEqual(result["ok"], golden["ok"])

    def test_ab_names(self):
        self._ab("names")

    def test_ab_location(self):
        self._ab("location")

    def test_ab_date(self):
        self._ab("date")

    def test_ab_extreme(self):
        self._ab("extreme")

    def test_ab_photo_promise(self):
        self._ab("photo_promise")

    def test_ab_privacy_wardrobe(self):
        self._ab("privacy_wardrobe")

    def test_ab_model_leak(self):
        self._ab("model_leak")

    def test_ab_group(self):
        self._ab("group")

    def test_ab_plain(self):
        self._ab("plain")

    def test_ab_package_mode_all_scenarios(self):
        for scenario_id in [item["id"] for item in SEND_SCENARIOS]:
            with self.subTest(scenario=scenario_id):
                self._ab(scenario_id, "pkg")


class ProactivePathABTests(unittest.TestCase):
    def _ab(self, scenario_id, mode="top"):
        mods = _load(mode)
        scenario = _scenario(scenario_id, PROACTIVE_SCENARIOS)
        golden = _golden(mode)["proactive:" + scenario_id]
        with _Tracing(mods):
            result = _proactive_result(mods, scenario)
        self.assertEqual(result["sent"], golden["sent"])
        self.assertEqual(result["trace"], golden["trace"])

    def test_ab_proactive_claim(self):
        self._ab("proactive_claim")

    def test_ab_proactive_plain(self):
        self._ab("proactive_plain")

    def test_ab_proactive_package_mode(self):
        self._ab("proactive_claim", "pkg")


# ---------------------------------------------------------------------------
# 3) generate 阶段：登记表模型 vs 改造前真实调用链
# ---------------------------------------------------------------------------


class GeneratePhaseABTests(unittest.TestCase):
    def _ab(self, name, group=False):
        mods = _load("top")
        golden = GOLDEN_GENERATE[name]
        scenario = GEN_INPUTS[name]
        with _GenTracing(mods):
            legacy = asyncio.run(_legacy_generate(_gen_plugin(mods), scenario["reply"],
                                                  scenario["user"], group))
            legacy_trace = _clean_trace(TRACE)
            del TRACE[:]
            model = asyncio.run(pipeline.run(scenario["reply"],
                                             _gen_ctx(_gen_plugin(mods), scenario["user"], group)))
        if not group:
            self.assertEqual(legacy, golden["text"])
            self.assertEqual(legacy_trace, golden["trace"])
            expected = ["claim_retry"] + golden["trace"] + ["photo_intent"]
        else:
            expected = ["claim_retry"] + GEN_TRACE_GROUP[:-1] + ["photo_intent"]
        self.assertEqual(model["text"], legacy)
        self.assertEqual(_model_trace(model), expected)

    def test_ab_generate_photo_promise(self):
        self._ab("photo_promise")

    def test_ab_generate_claim(self):
        self._ab("claim")

    def test_ab_generate_short_reply(self):
        self._ab("short_reply")

    def test_ab_generate_plain(self):
        self._ab("plain")

    def test_generate_group_order_matches_legacy(self):
        mods = _load("top")
        with _GenTracing(mods):
            legacy = asyncio.run(_legacy_generate(_gen_plugin(mods), "老公我在家呢，群里别喊我",
                                                  "在干嘛", group=True))
            legacy_trace = _clean_trace(TRACE)
            del TRACE[:]
            model = asyncio.run(pipeline.run("老公我在家呢，群里别喊我",
                                             _gen_ctx(_gen_plugin(mods), "在干嘛", group=True)))
        self.assertEqual(legacy_trace, GEN_TRACE_GROUP)
        self.assertEqual(model["text"], legacy)
        self.assertEqual(_model_trace(model),
                         ["claim_retry"] + GEN_TRACE_GROUP[:-1] + ["photo_intent"])


# ---------------------------------------------------------------------------
# 4) 错误文本不影响发送 / 未标注 swallow 的阶段照旧抛
# ---------------------------------------------------------------------------


class ErrorResilienceTests(unittest.TestCase):
    def test_guard_exception_does_not_block_send(self):
        mods = _load("top")
        scenario = _scenario("plain", SEND_SCENARIOS)

        async def boom(*args, **kwargs):
            raise RuntimeError("守卫炸了")
        with _Tracing(mods):
            plugin = _send_plugin(mods, scenario)
            plugin._guard_zone_reply = boom
            ok = asyncio.run(plugin._send_to_umo(scenario["umo"], scenario["text"]))
            sent = list(plugin.context.sent)
        self.assertTrue(ok)
        self.assertEqual(sent, ["好呀，那我们晚上吃火锅吧！"])

    def test_unswallowed_exception_propagates(self):
        def boom(text, ctx):
            raise RuntimeError("没有标注 swallow")
        stages = [Stage("boom", PHASE_SEND, 10, boom)]
        with self.assertRaises(RuntimeError):
            asyncio.run(Pipeline(stages).run("x", ReplyContext(phase=PHASE_SEND)))

    def test_swallowed_stage_keeps_text(self):
        def boom(text, ctx):
            raise RuntimeError("吞掉")
        stages = [Stage("boom", PHASE_SEND, 10, boom, swallow="debug"),
                  Stage("after", PHASE_SEND, 20, lambda text, ctx: text + "!")]
        result = asyncio.run(Pipeline(stages).run("x", ReplyContext(phase=PHASE_SEND)))
        self.assertEqual(result["text"], "x!")
        self.assertEqual(result["applied"], ["boom", "after"])


# ---------------------------------------------------------------------------
# 5) 反向验证：调换顺序 / 删掉阶段 / 让同一守卫跑两次
# ---------------------------------------------------------------------------


def _send_stages():
    return [stage for stage in pipeline.PIPELINE.stages if stage.phase == PHASE_SEND]


def _other_stages():
    return [stage for stage in pipeline.PIPELINE.stages if stage.phase != PHASE_SEND]


def _run_pipeline(stages, scenario):
    mods = _load("top")
    with _Tracing(mods):
        plugin = _send_plugin(mods, scenario)
        ctx = pipeline.context_for_send(plugin, scenario["umo"])
        result = asyncio.run(Pipeline(stages).run(scenario["text"], ctx))
    return result


class ReverseVerificationTests(unittest.TestCase):
    def test_swap_two_stages_breaks_order(self):
        scenario = _scenario("plain", SEND_SCENARIOS)
        ok = _run_pipeline(_other_stages() + _send_stages(), scenario)
        self.assertEqual(ok["applied"], EXPECTED_SEND_ORDER)
        by_id = {stage.id: stage for stage in _send_stages()}
        first, second = by_id[EXPECTED_SEND_ORDER[0]], by_id[EXPECTED_SEND_ORDER[1]]
        swapped_stages = []
        for stage in _send_stages():
            if stage.id == first.id:
                swapped_stages.append(dataclasses.replace(stage, priority=second.priority))
            elif stage.id == second.id:
                swapped_stages.append(dataclasses.replace(stage, priority=first.priority))
            else:
                swapped_stages.append(stage)
        broken = _run_pipeline(_other_stages() + swapped_stages, scenario)
        self.assertNotEqual(broken["applied"], EXPECTED_SEND_ORDER)
        self.assertEqual(broken["applied"][:2], [EXPECTED_SEND_ORDER[1], EXPECTED_SEND_ORDER[0]])

    def test_remove_stage_breaks_golden(self):
        scenario = _scenario("extreme", SEND_SCENARIOS)
        golden = _golden("top")["send:extreme"]["sent"][0]
        full = _run_pipeline(_other_stages() + _send_stages(), scenario)
        self.assertEqual(full["text"], golden)
        without = [stage for stage in _send_stages() if stage.id != "send.immersion"]
        broken = _run_pipeline(_other_stages() + without, scenario)
        self.assertNotEqual(broken["text"], golden)
        self.assertIn("不想活", broken["text"])

    def test_duplicate_stage_would_apply_twice_without_dedup(self):
        def marker(text, ctx):
            return text + "!"

        def build(targets):
            return Pipeline([Stage("a", PHASE_SEND, 10, marker, target=targets[0]),
                             Stage("b", PHASE_SEND, 20, marker, target=targets[1])])
        deduped = asyncio.run(build(("guarded", "guarded")).run("x", ReplyContext(phase=PHASE_SEND)))
        self.assertEqual(deduped["text"], "x!")
        self.assertEqual(deduped["applied"], ["a"])
        blind = asyncio.run(build(("", "")).run("x", ReplyContext(phase=PHASE_SEND)))
        self.assertEqual(blind["text"], "x!!")
        self.assertNotEqual(blind["text"], deduped["text"])

    def test_duplicate_real_guard_keeps_golden(self):
        scenario = _scenario("extreme", SEND_SCENARIOS)
        golden = _golden("top")["send:extreme"]["sent"][0]
        target = {stage.id: stage for stage in _send_stages()}["send.immersion"]
        duplicate = Stage("send.immersion.dup", PHASE_SEND, 71, target.run, target=target.target)
        result = _run_pipeline(_other_stages() + _send_stages() + [duplicate], scenario)
        self.assertEqual(result["text"], golden)
        self.assertNotIn("send.immersion.dup", result["applied"])


class _DedupRecentPlugin:
    """只实现读近聊的最小插件（role=bot 的行按时间从旧到新）。"""

    def __init__(self, lines):
        self.rows = [{"role": "bot", "content": str(line)} for line in lines]

    def _recent(self, umo, count=8):
        return list(self.rows)[-int(count):]


class AnswerKeptAfterDedupTests(unittest.TestCase):
    """整句复读删句不能把对他的回答一起删掉（现场：只剩「你呢？」）。"""

    def setUp(self):
        pipeline.reset_repetition_memory()

    def test_whole_sentence_repeat_keeps_eaten_answer(self):
        plugin = _DedupRecentPlugin(["刚吃完饭，好撑。"])
        out = pipeline.apply_narrative_repetition(
            "刚吃完饭，好撑。你呢？", plugin, "吃了吗", "dedup:answer")
        self.assertIn("吃完", out)
        self.assertNotEqual(out.strip(), "你呢？")

    def test_segment_guard_keeps_answer_after_narrative(self):
        umo = "dedup:seg-answer"
        pipeline.reset_repetition_memory(umo)
        plugin = _DedupRecentPlugin(["刚吃完饭，好撑。"])
        fixed = pipeline.apply_narrative_repetition(
            "刚吃完饭，好撑。你呢？", plugin, "吃了吗", umo)
        self.assertIn("吃完", fixed)
        # send 阶段还会再跑一次分段去重：不能把刚保住的答案再删掉
        segment = pipeline.apply_segment_repeat_guard(fixed, plugin, umo)
        self.assertIn("吃完", segment)

    def test_whole_sentence_repeat_keeps_when_return_answer(self):
        plugin = _DedupRecentPlugin(["17:00差不多到家。"])
        raw = "17:00差不多到家。"
        question = "几点到家呀？"
        out = pipeline.apply_narrative_repetition(raw, plugin, question, "dedup:when")
        # 「答案优先于通用兜底」现在只在 apply_chat_safety 收口裁决一次：
        # 去重层可以先把状态复读压成兜底句，最后一道守卫按候选有没有答到点上还原。
        final = pipeline.apply_chat_safety(out, plugin, "dedup:when", question)
        self.assertIn("17:00", final)
        self.assertIn("到家", final)

    def test_emotion_repeat_without_answer_still_collapsed(self):
        plugin = _DedupRecentPlugin(["嗯嗯，我在家呢，想你"])
        out = pipeline.apply_narrative_repetition(
            "嗯嗯，我在家呢，想你", plugin, "想你", "dedup:emo")
        self.assertNotEqual(out, "嗯嗯，我在家呢，想你")
        self.assertIn("想你", out)


if __name__ == "__main__":
    unittest.main()

