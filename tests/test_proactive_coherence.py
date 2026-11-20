# -*- coding: utf-8 -*-
"""PR 主动消息时序/事实收口（2026-11-17 事故）。

15:11 面板测试照片静默进聊天；15:12 主动消息把「昨天」的事和此刻的反应混在
一句话里（"昨天刚洗完澡…笑死，出了身汗感觉烧退了一点"），用户看不懂。

这里锁住四件事：
1. 发过照片（含测试/手动）后 10 分钟内不主动开口；
2. 主动消息 prompt 带【现在：YYYY-MM-DD HH:MM】+ 时态规则；
3. 提「昨天/前天」必须和 memory/diary 的日期对得上，混写降级/不发；
4. 测试图带「测试图」语义，不占日常配额、不进主动上下文。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_proactive  # noqa: E402
import photo_share  # noqa: E402
from amo_message import MessageMixin  # noqa: E402
from amo_proactive import (  # noqa: E402
    ProactiveMixin,
    guard_proactive_time,
    proactive_facts,
    proactive_time_block,
)
from amo_shared import _now, now_ts  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402

SNAP = {"dominant": "开心", "activity": {"activity": "窝在沙发上追剧", "place": "家"},
        "weather_text": "晴，18℃"}
NOW = dt.datetime(2026, 11, 17, 15, 12)
RAW_BUG = ("老公，我昨天刚洗完澡裹着被子窝沙发上看了会儿综艺，笑死，"
           "出了身汗感觉烧退了一点。")


class FakeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeProactiveDB(FakeDB):
    """带照片分享记录 + 块日记的假库。"""

    def __init__(self, share_at=None, diaries=None):
        super().__init__()
        if share_at:
            self.kv[amo_proactive.PHOTO_LAST_SHARE_KEY] = {
                "at": share_at, "source": "test", "test": True}
        self.diaries = dict(diaries or {})

    def list_block_diaries(self, day):
        text = self.diaries.get(day)
        return [{"text": text}] if text else []

    def get_diary(self, day, hour):
        return ""


class _Plugin(ProactiveMixin):
    def __init__(self, db):
        self.db = db
        self.sent = []

    def _proactive_candidates(self, profile, primary):
        return [primary]

    async def _send_to_umo(self, umo, text, quote_id=None, group=False):
        self.sent.append(text)
        return True


class PhotoCooldownTests(unittest.TestCase):
    def _engine(self, db=None):
        engine = object.__new__(PhotoEngine)
        engine._last_sent_at = 0
        engine.plugin = type("P", (), {"db": db or FakeDB()})()
        return engine

    def test_note_sent_records_test_share(self):
        engine = self._engine()
        engine.note_sent(source="test", test=True, kind="daily", umo="u1")
        record = engine.last_share()
        self.assertEqual(record.get("source"), "test")
        self.assertTrue(record.get("test"))
        self.assertEqual(record.get("kind"), "daily")
        self.assertTrue(engine.photo_share_cooldown_active())

    def test_cooldown_expires_after_ten_minutes(self):
        engine = self._engine()
        engine.note_sent(source="test", test=True)
        at = float(engine.last_share()["at"])
        self.assertTrue(engine.photo_share_cooldown_active(now=at + 60))
        self.assertTrue(engine.photo_share_cooldown_active(now=at + 599))
        self.assertFalse(engine.photo_share_cooldown_active(now=at + 601))

    def test_memory_fallback_without_db(self):
        engine = object.__new__(PhotoEngine)
        engine._last_sent_at = 0
        engine.note_sent()  # 没有 plugin.db 也不能抛
        self.assertTrue(engine.photo_share_cooldown_active())


class PhotoProactiveGateTests(unittest.TestCase):
    def test_photo_within_ten_minutes_blocks(self):
        plugin = _Plugin(FakeProactiveDB(share_at=now_ts() - 60))
        self.assertTrue(plugin._photo_proactive_blocked())

    def test_photo_older_than_ten_minutes_allows(self):
        plugin = _Plugin(FakeProactiveDB(share_at=now_ts() - 601))
        self.assertFalse(plugin._photo_proactive_blocked())

    def test_proactive_send_is_blocked_after_photo(self):
        plugin = _Plugin(FakeProactiveDB(share_at=now_ts() - 30))
        sent = asyncio.run(plugin._send_proactive_lines({}, "umo", "在忙吗？"))
        self.assertEqual(sent, "")
        self.assertEqual(plugin.sent, [])

    def test_proactive_one_checks_photo_gate_before_generating(self):
        body = inspect.getsource(amo_proactive._proactive_one)
        self.assertIn("_photo_proactive_blocked", body)
        self.assertLess(body.index("_photo_proactive_blocked"), body.index("gen_proactive"))


class ProactiveTimePromptTests(unittest.TestCase):
    def test_facts_inject_now_and_tense_rules(self):
        text = proactive_facts(SNAP, 80, NOW)
        self.assertIn("【现在：2026-11-17 15:12】", text)
        self.assertIn("昨天是2026-11-16", text)
        self.assertIn("前天是2026-11-15", text)
        self.assertIn("时态规则", text)
        self.assertIn("不能写成刚刚/现在", text)
        self.assertIn("不要把过去和现在混在一起说", text)

    def test_now_block_is_the_first_thing_she_reads(self):
        self.assertTrue(proactive_time_block(NOW).startswith("【现在："))


class ProactiveTimeGuardTests(unittest.TestCase):
    def test_bug_text_is_degraded_and_reaction_removed(self):
        kept, decision = guard_proactive_time(RAW_BUG, {"昨天": True})
        self.assertEqual(decision, "degraded")
        self.assertIn("昨天", kept)
        self.assertNotIn("出了身汗", kept)
        self.assertNotIn("烧退", kept)

    def test_reverse_the_bug_text_would_have_shipped_without_guard(self):
        # 反向验证：事故原文确实把昨天和此刻混在一起，所以必须被校验改掉
        self.assertIn("昨天", RAW_BUG)
        self.assertIn("出了身汗", RAW_BUG)
        kept, _decision = guard_proactive_time(RAW_BUG, {"昨天": True})
        self.assertNotEqual(kept, RAW_BUG)

    def test_past_claim_without_diary_evidence_is_blocked(self):
        kept, decision = guard_proactive_time("昨天你带我去吃火锅了。", {"昨天": False})
        self.assertEqual((kept, decision), ("", "blocked"))

    def test_past_claim_with_evidence_passes(self):
        raw = "昨天你带我去吃火锅了，现在还想再去。"
        kept, decision = guard_proactive_time(raw, {"昨天": True})
        self.assertEqual((kept, decision), (raw, "ok"))

    def test_before_yesterday_uses_its_own_date(self):
        kept, decision = guard_proactive_time("前天你带我去吃火锅了。",
                                              {"昨天": True, "前天": False})
        self.assertEqual((kept, decision), ("", "blocked"))
        kept2, decision2 = guard_proactive_time("前天你带我去吃火锅了。", {"前天": True})
        self.assertEqual((kept2, decision2), ("前天你带我去吃火锅了。", "ok"))

    def test_mixed_tense_in_one_clause_is_rejected(self):
        kept, decision = guard_proactive_time("昨天这会儿我还在发烧。", {"昨天": True})
        self.assertEqual((kept, decision), ("", "blocked"))

    def test_current_only_text_untouched(self):
        raw = "现在有点饿，想你了。"
        self.assertEqual(guard_proactive_time(raw, {}), (raw, "ok"))

    def test_guard_reads_diary_date(self):
        yesterday = (_now() - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        plugin = _Plugin(FakeProactiveDB(diaries={yesterday: "昨天感冒，裹着被子看综艺。"}))
        text = asyncio.run(plugin._proactive_time_guard_text(
            {"uid": "u1"}, "昨天窝在沙发上看综艺，今天好多了。"))
        self.assertIn("昨天", text)
        self.assertIn("今天好多了", text)

    def test_guard_blocks_when_diary_has_no_yesterday(self):
        plugin = _Plugin(FakeProactiveDB())
        text = asyncio.run(plugin._proactive_time_guard_text(
            {"uid": "u1"}, "昨天你带我去吃火锅了。"))
        self.assertEqual(text, "")

    def test_guard_reads_memory_date(self):
        plugin = _Plugin(FakeProactiveDB())

        async def fake_recall(uid, label):
            return [], "[昨日] 他昨天带我去吃火锅"

        plugin._recall = fake_recall
        text = asyncio.run(plugin._proactive_time_guard_text(
            {"uid": "u1"}, "昨天你带我去吃火锅了。"))
        self.assertIn("昨天", text)


class _RecordingContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))


class _TestShareEngine(PhotoEngine):
    enabled = True


class _ShareNowEngine(PhotoEngine):
    enabled = True


class TestPhotoSharing(unittest.TestCase):
    def _engine(self):
        engine = object.__new__(_TestShareEngine)
        self.context = _RecordingContext()
        self.remembered = []
        self.bumped = []

        async def remember(umo, marker):
            self.remembered.append(marker)

        plugin = type("P", (), {"db": FakeDB(), "context": self.context,
                                "_remember_bot_media": staticmethod(remember)})()
        engine.plugin = plugin
        engine._stats = {"sent": 0, "failed": 0, "rolled": 0}
        engine._bump_count = lambda: self.bumped.append(1)
        engine._activity = lambda: {"activity": "在家", "place": "家"}
        engine._find_omni = lambda: object()
        engine._room_refs_enabled = lambda explicit=None: False
        engine._pick_selfie = lambda kind: False
        engine._prompt_for = lambda *args, **kwargs: "prompt"
        engine._cfg_bool = lambda key, default=False: False

        async def fake_generate(*args, **kwargs):
            return {"file_path": "x.png"}

        async def fake_localize(item):
            return item

        engine._generate = fake_generate
        engine._localize = fake_localize
        return engine

    def test_test_photo_has_marker_and_no_daily_share(self):
        engine = self._engine()
        ok = asyncio.run(engine._share("daily", "", {"uid": "u1"},
                                       target_umo="umo", test=True))
        self.assertTrue(ok)
        self.assertEqual(self.bumped, [])          # 不占每日配额
        self.assertEqual(self.remembered, [])      # 不进主动上下文
        chain = self.context.sent[-1][1].chain
        self.assertEqual(chain[0].text, photo_share.PHOTO_TEST_MARK)
        self.assertTrue(engine.last_share().get("test"))
        self.assertTrue(engine.photo_share_cooldown_active())

    def test_normal_photo_still_bumps_and_remembers(self):
        engine = self._engine()
        ok = asyncio.run(engine._share("daily", "", {"uid": "u1"},
                                       target_umo="umo"))
        self.assertTrue(ok)
        self.assertEqual(len(self.bumped), 1)
        self.assertIn("[我发的照片]", self.remembered[0])
        chain = self.context.sent[-1][1].chain
        self.assertEqual(len(chain), 1)            # 正常图不带测试说明
        self.assertFalse(engine.last_share().get("test"))

    def test_share_now_always_marks_test(self):
        engine = object.__new__(_ShareNowEngine)
        captured = {}

        async def fake_share(kind, scene, profile, **kwargs):
            captured.update(kwargs)
            return True

        engine._share = fake_share
        engine._busy = False
        engine._profiles = lambda: [{"uid": "u1", "relation": "相恋"}]
        engine._activity = lambda: {"place": "家"}
        engine._scene_for_kind = PhotoEngine._scene_for_kind
        engine._cfg_bool = lambda key, default=False: False
        message = asyncio.run(engine.share_now("daily"))
        self.assertTrue(captured.get("test"))
        self.assertIn("测试图", message)


class TestPhotoMemoryIsolation(unittest.TestCase):
    def _plugin(self):
        plugin = object.__new__(MessageMixin)
        chats, writes = [], []
        plugin._remember_bot_message = lambda umo, text: chats.append(text)

        async def fake_write(*args, **kwargs):
            writes.append(args)
            return True

        plugin._data_memory_write = fake_write
        return plugin, chats, writes

    def test_test_photo_marker_is_not_remembered(self):
        plugin, chats, writes = self._plugin()
        asyncio.run(plugin._remember_bot_media(
            "lark:FriendMessage:ou_x",
            "[我发的照片] 自拍，背景：客厅（测试图）"))
        self.assertEqual(chats, [])
        self.assertEqual(writes, [])

    def test_normal_photo_marker_still_remembered(self):
        plugin, chats, writes = self._plugin()
        asyncio.run(plugin._remember_bot_media(
            "lark:FriendMessage:ou_x", "[我发的照片] 自拍，背景：客厅"))
        self.assertEqual(len(chats), 1)
        self.assertEqual(len(writes), 1)


if __name__ == "__main__":
    unittest.main()
