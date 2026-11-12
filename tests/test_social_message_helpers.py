# -*- coding: utf-8 -*-
"""社交/消息里的判定小工具：uid 归一、管理员、情绪惯性、是否在对她说话。

这些函数看着短，但每条消息都会走；判错就是"把两个人的记忆混一起"或"群里随便一句都回"。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_db import AmoDB  # noqa: E402
from amo_message import MessageMixin  # noqa: E402
from amo_shared import now_ts  # noqa: E402
from amo_social import SocialMixin  # noqa: E402


class FakeEvent:
    def __init__(self, **kwargs):
        self._private = kwargs.get("private", False)
        self._sender_id = kwargs.get("sender_id", "1")
        self._at = kwargs.get("at", False)
        for key, value in kwargs.items():
            if key in ("private", "sender_id", "at"):
                continue
            setattr(self, key, value)

    def is_private_chat(self):
        return self._private

    def get_sender_id(self):
        return self._sender_id

    @property
    def is_at_or_wake_command(self):
        return self._at

    def __getattr__(self, name):
        raise AttributeError(name)


class FakeState:
    def __init__(self):
        self.nudged = []

    def nudge(self, **kwargs):
        self.nudged.append(kwargs)


class FakePlugin:
    def __init__(self, tmp, **cfg):
        self.config = {"uid_merge_enabled": True}
        self.config.update(cfg)
        self.db = AmoDB(tmp)
        self.db.init()
        self.admin_uids = set()
        self._disturb_until = {}
        self._disturb_last = {}
        self._disturb_cooldown = {}
        self._disturb_reset_at = {}
        self._last_affect = {}
        self.state = FakeState()
        self.heartflow = None
        self.context = None
        for name, owner in (
            ("_uid_alias_mapping", SocialMixin), ("_canonical_uid", SocialMixin),
            ("_is_admin", SocialMixin), ("_tone_with_continuity", SocialMixin),
            ("_disturb_category", SocialMixin), ("_disturb_active", SocialMixin),
            ("_apply_disturbance", SocialMixin), ("_is_talking_to_me", MessageMixin),
        ):
            setattr(self, name, types.MethodType(getattr(owner, name), self))

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="amo-msg-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.plugin = FakePlugin(self.tmp)

    def tearDown(self) -> None:
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class AliasTests(Base):
    def test_chained_alias_resolved(self) -> None:
        self.plugin.config["uid_alias_map"] = json.dumps({"a": "b", "b": "c", "x": "x"})
        mapping = self.plugin._uid_alias_mapping()
        self.assertEqual(mapping.get("a"), "c")
        self.assertNotIn("x", mapping)

    def test_uid_map_file_merges(self) -> None:
        path = os.path.join(os.path.dirname(self.plugin.db.path), "uid_map.json")
        with open(path, "w", encoding="utf-8-sig") as fh:
            json.dump({"ou_x": "302578963"}, fh)
        mapping = self.plugin._uid_alias_mapping()
        self.assertEqual(mapping["ou_x"], "302578963")

    def test_canonical_uid(self) -> None:
        self.plugin.config["uid_alias_map"] = json.dumps({"ou_x": "302578963"})
        self.assertEqual(self.plugin._canonical_uid(FakeEvent(sender_id="ou_x")), "302578963")
        self.assertEqual(self.plugin._canonical_uid(FakeEvent(sender_id="other")), "other")

    def test_canonical_uid_disabled(self) -> None:
        self.plugin.config["uid_merge_enabled"] = False
        self.plugin.config["uid_alias_map"] = json.dumps({"ou_x": "302578963"})
        self.assertEqual(self.plugin._canonical_uid(FakeEvent(sender_id="ou_x")), "ou_x")

    def test_canonical_uid_bad_event(self) -> None:
        class Boom:
            def get_sender_id(self):
                raise RuntimeError("没有 sender")

        self.assertEqual(self.plugin._canonical_uid(Boom()), "")


class AdminTests(Base):
    def test_admin_uid(self) -> None:
        self.plugin.admin_uids = {"302578963"}
        self.assertTrue(self.plugin._is_admin(FakeEvent(sender_id="302578963")))

    def test_admin_from_config_and_cached(self) -> None:
        class Context:
            calls = 0

            def get_config(self, umo=None):
                Context.calls += 1
                return {"admins_id": "1, 2"}

        self.plugin.context = Context()
        event = FakeEvent(sender_id="2", unified_msg_origin="qq:1")
        self.assertTrue(self.plugin._is_admin(event))
        self.assertTrue(self.plugin._is_admin(event))
        self.assertEqual(Context.calls, 1, "同一会话 60 秒内只读一次配置")

    def test_admin_config_error_is_false(self) -> None:
        class Boom:
            def get_config(self, umo=None):
                raise RuntimeError("配置挂了")

        self.plugin.context = Boom()
        self.assertFalse(self.plugin._is_admin(FakeEvent(sender_id="1", unified_msg_origin="qq:1")))


class ToneTests(Base):
    def test_disturb_active_adds_warning(self) -> None:
        self.plugin._disturb_until["u"] = now_ts() + 100
        tone = self.plugin._tone_with_continuity("u", "语气自然")
        self.assertIn("不要一秒变甜蜜", tone)

    def test_previous_anger_keeps_continuity(self) -> None:
        self.plugin._last_affect["u"] = (now_ts() - 60, "你现在很烦躁")
        tone = self.plugin._tone_with_continuity("u", "语气自然")
        self.assertIn("慢慢缓和", tone)

    def test_old_affect_expires(self) -> None:
        self.plugin._last_affect["u"] = (now_ts() - 5000, "你现在很烦躁")
        self.assertNotIn("慢慢缓和", self.plugin._tone_with_continuity("u", "语气自然"))

    def test_disturb_category(self) -> None:
        self.assertTrue(self.plugin._disturb_category({"activity": {"category": "sleep"}}))
        self.assertTrue(self.plugin._disturb_category({"activity": {"category": "work"}}))
        self.assertFalse(self.plugin._disturb_category({"activity": {"category": "meal"}}))

    def test_apply_disturbance(self) -> None:
        self.plugin._apply_disturbance("u")
        self.assertGreater(self.plugin._disturb_until["u"], now_ts())
        self.assertTrue(self.plugin.state.nudged)
        self.assertIn("生气", self.plugin.state.nudged[0])


class TalkingToMeTests(Base):
    def test_private_is_for_me(self) -> None:
        self.assertTrue(self.plugin._is_talking_to_me(FakeEvent(private=True)))

    def test_private_check_error_counts_as_private(self) -> None:
        class Boom:
            def is_private_chat(self):
                raise RuntimeError("拿不到会话类型")

        self.assertTrue(self.plugin._is_talking_to_me(Boom()))

    def test_heartflow_target_bot(self) -> None:
        self.assertTrue(self.plugin._is_talking_to_me(FakeEvent(heartflow_target="bot")))

    def test_interject_is_not_for_me(self) -> None:
        self.assertFalse(self.plugin._is_talking_to_me(
            FakeEvent(heartflow_mode_hint="interject")))

    def test_at_command(self) -> None:
        self.assertTrue(self.plugin._is_talking_to_me(FakeEvent(at=True)))

    def test_heartflow_addresses_bot(self) -> None:
        class Heartflow:
            def _addresses_bot(self, text):
                return "小鱼" in text

        self.plugin.heartflow = Heartflow()
        self.assertTrue(self.plugin._is_talking_to_me(FakeEvent(message_str="小鱼在吗")))
        self.assertFalse(self.plugin._is_talking_to_me(FakeEvent(message_str="今天好热")))


if __name__ == "__main__":
    unittest.main()
