# -*- coding: utf-8 -*-
"""task-32：收件人 / 角色反转守卫回归（2026-11-22 11:31 飞书真实 P0）。

线上原文（amo_data.db messages #2288，日志 amo_message:986 「[amo] 主动 -> ...」）：
    妈，我地铁上呢，中午回家吃，随便做点就行，我马上到。
收件人是老公，首句称呼却是「妈」，内容是老公对他妈报备的第一人称视角。

已核实来源（不是 proactive_log、不是延迟队列、不是旧发送函数）：
    watch social_tick -> social_ai.respond 生成「回 NPC 的那句 now」
    -> social_engine._apply 把 line 放进 out["push"]
    -> watch core/jobs.py 调 amo.request_push(text, "social")
    -> amo_feature_api.request_push(push_to_owner) -> amo_message._send_to_umo(source=push)
    -> messages #2288 / 日志 主动 ->
    kv 证据：external_push_ts:social=1795318312.505433、mood_applied:social_event:妈妈...

这里锁三件事：
1. 唯一守卫 amo_addressee_guard：首句称呼白名单 + 用户视角自述检测；
2. 接线 persona_guard / outbound / message / state 都只调用它，不写局部正则；
3. 生成层 prompt 有收件人锁（聊天 / 主动 / polish），家人只能当话题。
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (ROOT, HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_addressee_guard as guard  # noqa: E402
import amo_llm  # noqa: E402
import amo_llm_service  # noqa: E402
import amo_message as message  # noqa: E402
import amo_outbound as outbound  # noqa: E402
import amo_persona_guard as persona  # noqa: E402
import amo_prompt_registry as prompts  # noqa: E402
import amo_state_consistency as state  # noqa: E402

#: 2026-11-22 11:31:57 真实发到老公飞书的那一条。
REAL_1131 = "妈，我地铁上呢，中午回家吃，随便做点就行，我马上到。"
#: 合法句：家人只作话题，不能被误杀。
LEGIT_FAMILY_TOPIC = "我妈今天给我打电话了，说周末让我回家吃饭。"


class _DB:
    def __init__(self, profiles=None):
        self.kv = {}
        self.profiles = list(profiles or [])

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def proactive_profiles(self, limit=100):
        return [dict(row) for row in self.profiles[:int(limit or 100)]]

    def recent_messages(self, umo, limit=8):
        return []


class _Context:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))


class _State:
    def current_activity(self):
        return {"place": "家", "zone": "home"}


class _Plugin:
    """出站守卫用最小宿主：db / context / state / _recent。"""

    def __init__(self, profiles=None):
        self.db = _DB(profiles)
        self.context = _Context()
        self.state = _State()

    def _recent(self, umo, limit=8):
        return []

    def typing_seconds(self, text):
        return 0.0


def _owner_profile():
    return {
        "uid": "u1",
        "private_umo": "lark:FriendMessage:u1",
        "name": "family",
        "alias": "老公；亲爱的",
    }


class PureGuardTests(unittest.TestCase):
    def test_real_1131_sample_is_role_reversal_and_dropped(self):
        body, reason = guard.apply(REAL_1131)
        self.assertEqual("", body)
        self.assertEqual("addressee_role_reversal:妈", reason)

    def test_user_perspective_detected(self):
        self.assertTrue(guard.is_user_perspective(REAL_1131))
        self.assertTrue(guard.is_user_perspective("婆婆，我马上到。"))

    def test_legit_family_topic_untouched(self):
        body, reason = guard.apply(LEGIT_FAMILY_TOPIC)
        self.assertEqual(LEGIT_FAMILY_TOPIC, body)
        self.assertEqual("", reason)

    def test_husband_or_you_allowed(self):
        for text in ("老公，我在地铁上呢，中午回家吃。",
                     "你回来啦，饭在锅里。",
                     "亲爱的，我到楼下了。"):
            body, reason = guard.apply(text)
            self.assertEqual(text, body)
            self.assertEqual("", reason)

    def test_no_vocative_interjections_not_false_positive(self):
        for text in ("嗯，我在呢。", "好吧，你说了算。", "对了，晚上想吃什么？"):
            body, reason = guard.apply(text)
            self.assertEqual(text, body)
            self.assertEqual("", reason)

    def test_banned_vocative_without_user_view_rewritten_to_husband(self):
        body, reason = guard.apply("妈，今天天气不错。")
        self.assertEqual("老公，今天天气不错。", body)
        self.assertEqual("addressee_rewrite:妈->老公", reason)

    def test_roster_name_as_vocative_not_allowed(self):
        body, reason = guard.apply("柳如烟，下午一起去商场吗？", others={"柳如烟"})
        self.assertEqual("老公，下午一起去商场吗？", body)
        self.assertEqual("addressee_rewrite:柳如烟->老公", reason)

    def test_leading_action_narration_still_detected(self):
        body, reason = guard.apply("（笑了一下）妈，我马上到。")
        self.assertEqual("", body)
        self.assertEqual("addressee_role_reversal:妈", reason)


class GroupChatTests(unittest.TestCase):
    """群聊 umo：首句称呼只删不改，绝不把群聊串成对老公说的私聊。"""

    GROUP_UMO = "aiocqhttp:GroupMessage:123456"
    PRIVATE_UMO = "aiocqhttp:FriendMessage:123456"

    def test_group_friends_goodnight_strips_vocative(self):
        body, reason = guard.enforce(None, self.GROUP_UMO, "朋友们，晚安。", source="chat")
        self.assertEqual("晚安。", body)
        self.assertEqual("addressee_group_vocative:朋友们", reason)

    def test_group_roster_name_strips_vocative(self):
        body, reason = guard.enforce(
            None, self.GROUP_UMO, "柳如烟，你也在群里啊。", source="chat")
        self.assertEqual("你也在群里啊。", body)
        self.assertEqual("addressee_group_vocative:柳如烟", reason)

    def test_group_at_name_strips_vocative(self):
        body, reason = guard.enforce(
            None, self.GROUP_UMO, "@柳如烟，你也在群里啊。", source="chat")
        self.assertEqual("你也在群里啊。", body)
        self.assertEqual("addressee_group_vocative:柳如烟", reason)

    def test_group_vocative_only_dropped(self):
        body, reason = guard.enforce(None, self.GROUP_UMO, "朋友们，", source="chat")
        self.assertEqual("", body)
        self.assertEqual("addressee_group_vocative:朋友们", reason)

    def test_group_role_reversal_still_dropped(self):
        body, reason = guard.enforce(
            None, self.GROUP_UMO, "妈，我地铁上呢，中午回家吃，马上到。", source="chat")
        self.assertEqual("", body)
        self.assertEqual("addressee_role_reversal:妈", reason)

    def test_group_honorific_still_dropped(self):
        body, reason = guard.enforce(
            None, self.GROUP_UMO, "婆婆，我周末过去看您。", source="chat")
        self.assertEqual("", body)
        self.assertEqual("addressee_honorific:婆婆", reason)

    def test_private_same_inputs_still_rewritten_to_husband(self):
        for text, expected, reason in (
            ("朋友们，晚安。", "老公，晚安。", "addressee_rewrite:朋友们->老公"),
            ("柳如烟，你也在群里啊。", "老公，你也在群里啊。",
             "addressee_rewrite:柳如烟->老公"),
        ):
            body, got = guard.enforce(None, self.PRIVATE_UMO, text, source="chat")
            self.assertEqual(expected, body)
            self.assertEqual(reason, got)


class PersonaGuardWiringTests(unittest.TestCase):
    def test_prepare_outbound_drops_real_sample(self):
        plugin = _Plugin([_owner_profile()])
        body, reason = persona.prepare_outbound(
            plugin, "lark:FriendMessage:u1", REAL_1131, source="push")
        self.assertEqual("", body)
        self.assertEqual("addressee_role_reversal:妈", reason)

    def test_prepare_outbound_keeps_legit_family_topic(self):
        plugin = _Plugin([_owner_profile()])
        body, reason = persona.prepare_outbound(
            plugin, "lark:FriendMessage:u1", LEGIT_FAMILY_TOPIC, source="chat")
        self.assertEqual(LEGIT_FAMILY_TOPIC, body)
        self.assertEqual("", reason)

    def test_prepare_outbound_rewrites_wrong_vocative(self):
        plugin = _Plugin([_owner_profile()])
        body, reason = persona.prepare_outbound(
            plugin, "lark:FriendMessage:u1", "妈，今天天气不错。", source="push")
        self.assertEqual("老公，今天天气不错。", body)
        self.assertEqual("", reason)


class OutboundGatewayWiringTests(unittest.TestCase):
    def test_gateway_drops_real_sample_and_records_source(self):
        plugin = _Plugin([_owner_profile()])
        ok = asyncio.run(outbound.send(
            plugin, "lark:FriendMessage:u1", REAL_1131, source="push"))
        self.assertFalse(ok)
        self.assertEqual([], plugin.context.sent)
        rows = outbound.rows(plugin, "lark:FriendMessage:u1")
        self.assertTrue(rows)
        last = rows[-1]
        self.assertEqual("drop", last["decision"])
        self.assertEqual("push", last["source"])
        self.assertIn("addressee_role_reversal", last["reason"])

    def test_gateway_still_sends_legit_chat(self):
        plugin = _Plugin([_owner_profile()])
        ok = asyncio.run(outbound.send(
            plugin, "lark:FriendMessage:u1", LEGIT_FAMILY_TOPIC, source="chat"))
        self.assertTrue(ok)
        self.assertEqual(1, len(plugin.context.sent))


class StateAssemblyWiringTests(unittest.TestCase):
    def test_validate_assembly_never_returns_real_sample(self):
        plugin = _Plugin([_owner_profile()])
        out = state.validate_reply_assembly(
            REAL_1131, plugin, umo="", user_text="")
        # 判据不变：收件人/角色反转命中的原文绝不返回。
        self.assertNotIn("妈，我地铁", str(out))
        self.assertNotIn("地铁上", str(out))
        self.assertNotIn("妈", str(out))
        # B2：没有 chat 出口 repair（umo 空 = 非 chat）时绝不能交空串；改用此刻
        # 场景的状态句顶上（不是固定句池），保证「非 chat 绝不发空」。
        self.assertTrue(str(out).strip(), out)

    def test_validate_assembly_keeps_legit_family_topic(self):
        plugin = _Plugin([_owner_profile()])
        out = state.validate_reply_assembly(
            LEGIT_FAMILY_TOPIC, plugin, umo="", user_text="")
        self.assertIn("我妈今天", str(out))


class MessageSendWiringTests(unittest.TestCase):
    def test_send_to_umo_drops_real_sample(self):
        from test_cov2_message import FakeMix, FakePipeline  # noqa: E402

        plugin = FakeMix({})
        plugin._spawn = lambda coro: coro.close()
        with mock.patch.object(message, "_reply_pipeline", FakePipeline(REAL_1131)):
            ok = asyncio.run(plugin._send_to_umo(
                "lark:FriendMessage:u1", REAL_1131, source="push"))
        self.assertFalse(ok)

    def test_send_to_umo_keeps_legit_text(self):
        from test_cov2_message import FakeMix, FakePipeline  # noqa: E402

        plugin = FakeMix({})
        plugin._spawn = lambda coro: coro.close()
        plugin._segment = lambda text: [text]
        plugin._quote_allowed = lambda umo, group: False

        async def _segment_wait(*args, **kwargs):
            return 0.0

        async def _send_ok(*args, **kwargs):
            return True

        plugin._segment_wait = _segment_wait
        plugin._remember_bot_message = lambda umo, text: None
        with mock.patch.object(message, "_reply_pipeline", FakePipeline(LEGIT_FAMILY_TOPIC)), \
                mock.patch.object(message, "_gateway") as fake_gateway:
            fake_gateway.send = _send_ok
            ok = asyncio.run(plugin._send_to_umo(
                "lark:FriendMessage:u1", LEGIT_FAMILY_TOPIC, source="push"))
        self.assertTrue(ok)


class PromptLockTests(unittest.TestCase):
    def test_registry_has_single_addressee_lock(self):
        text = prompts.render("llm.rule.addressee")
        self.assertIn("收件人", text)
        self.assertIn("老公", text)
        self.assertIn("妈", text)
        self.assertIn("话题", text)
        self.assertIn("第一人称", text)

    def test_chat_prompt_renders_lock(self):
        source = inspect.getsource(amo_llm.LlmMixin._reply_system)
        self.assertIn("llm.rule.addressee", source)

    def test_proactive_prompt_renders_lock(self):
        source = inspect.getsource(amo_llm_service.LLMService.gen_proactive)
        self.assertIn("llm.rule.addressee", source)

    def test_polish_prompt_has_addressee_line(self):
        source = inspect.getsource(amo_llm_service.LLMService.polish)
        self.assertIn("收件人", source)
        self.assertIn("第一人称", source)


if __name__ == "__main__":
    unittest.main()
