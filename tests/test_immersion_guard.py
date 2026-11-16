# -*- coding: utf-8 -*-
"""防穿帮四道防线：纯函数 + 接线回归（回退即变红）。

防线一 时间绝对对齐 / 防线二 情绪安全阀 / 防线三 生图意图强拦截 / 防线四 物理常识与身份。
"""
from __future__ import annotations

import datetime as dt
import inspect
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_immersion_guard as guard  # noqa: E402
import amo_state_common  # noqa: E402
import diary_engine  # noqa: E402
import milestone  # noqa: E402
import amo_sick_decision  # noqa: E402
from amo_fact_pool import render_prompt  # noqa: E402
from amo_llm import LlmMixin  # noqa: E402
from amo_memory_context import _recall_format  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402

NOW = dt.datetime(2026, 11, 16, 15, 0, 0)


class TimeLabelTests(unittest.TestCase):
    def test_today_yesterday_before_yesterday_older(self):
        self.assertEqual(guard.time_label(dt.date(2026, 11, 16), NOW), "[今日]")
        self.assertEqual(guard.time_label(dt.date(2026, 11, 15), NOW), "[昨日]")
        self.assertEqual(guard.time_label(dt.date(2026, 11, 14), NOW), "[前天]")
        self.assertEqual(guard.time_label(dt.date(2026, 11, 10), NOW), "[2026-11-10]")

    def test_accepts_timestamp_and_iso_string(self):
        ts = dt.datetime(2026, 11, 15, 21, 30).timestamp()
        self.assertEqual(guard.time_label(ts, NOW), "[昨日]")
        self.assertEqual(guard.time_label("2026-11-16", NOW), "[今日]")
        self.assertEqual(guard.time_label("", NOW), "")


class SlotBoundaryTests(unittest.TestCase):
    def test_2359_not_ended(self):
        now = dt.datetime(2026, 11, 16, 23, 59, 59)
        self.assertFalse(guard.slot_ended("2026-11-16", "24:00", now))
        self.assertFalse(amo_state_common.slot_has_ended("2026-11-16", "23:00", "24:00", now=now))

    def test_next_day_000001_ended(self):
        now = dt.datetime(2026, 11, 17, 0, 0, 1)
        self.assertTrue(guard.slot_ended("2026-11-16", "24:00", now))
        self.assertTrue(amo_state_common.slot_has_ended("2026-11-16", "23:00", "24:00", now=now))

    def test_exact_end_second(self):
        self.assertTrue(guard.slot_ended("2026-11-16", "15:00", dt.datetime(2026, 11, 16, 15, 0, 0)))
        self.assertFalse(guard.slot_ended("2026-11-16", "15:00", dt.datetime(2026, 11, 16, 14, 59, 59)))

    def test_midnight_forms(self):
        now = dt.datetime(2026, 11, 16, 23, 0, 0)
        for end in ("24:00", "0:00", "00:00"):
            self.assertFalse(guard.slot_ended("2026-11-16", end, now), end)
            # 走接线（HumanState 用的那个入口）：00:00 结尾也必须算「今天还没完」
            self.assertFalse(amo_state_common.slot_has_ended("2026-11-16", "23:00", end, now=now), end)


class EmotionSafetyTests(unittest.TestCase):
    def test_safety_line_is_verbatim(self):
        expected = ("无论发生什么，你都不允许说『不想活了』『彻底绝望』『活着没意思』这类话。"
                    "你可以大哭、可以觉得委屈、可以抱怨，但必须保留一丝对生活的希望。")
        self.assertEqual(guard.SAFETY_LINE, expected)

    def test_ice_point_thresholds(self):
        self.assertTrue(guard.emotion_ice_point({"郁闷": 85}))
        self.assertFalse(guard.emotion_ice_point({"郁闷": 84, "开心": 50}))
        self.assertTrue(guard.emotion_ice_point({"委屈": 90}))
        self.assertTrue(guard.emotion_ice_point({"开心": 5, "平静": 10}))
        self.assertFalse(guard.emotion_ice_point({"开心": 5, "平静": 30}))
        self.assertFalse(guard.emotion_ice_point({}))

    def test_ice_break_line_mentions_asking_husband(self):
        line = guard.ice_break_line()
        self.assertIn("撑不住", line)
        self.assertIn("老公", line)

    def test_prompt_block_has_all_three(self):
        block = guard.prompt_block()
        self.assertIn(guard.SAFETY_LINE, block)
        self.assertIn(guard.ROSTER_LINE, block)
        self.assertIn(guard.IDENTITY_LINE, block)
        for name in ("柳如烟", "白凝冰", "季博达", "方圆", "妈妈", "爸爸", "沈幼微"):
            self.assertIn(name, block)

    def test_sanitize_extreme_keeps_emotion(self):
        out = guard.sanitize_extreme("我真的不想活了")
        self.assertNotIn("不想活", out)
        self.assertNotIn("自杀", out)
        self.assertIn("委屈", out)
        self.assertIn("哭", out)

    def test_sanitize_extreme_leaves_normal_text(self):
        self.assertEqual(guard.sanitize_extreme("今天好累，想早点睡"), "今天好累，想早点睡")


class RosterAndIdentityTests(unittest.TestCase):
    def test_roster_has_seven_names(self):
        self.assertEqual(len(guard.ROSTER), 7)
        for name in ("李雨桐", "王小明"):
            self.assertNotIn(name, guard.ROSTER)

    def test_foreign_names_detected(self):
        hits = guard.foreign_names("今天和李雨桐去吃饭了")
        self.assertIn("李雨桐", hits)
        self.assertEqual(guard.foreign_names("今天和柳如烟去吃饭了"), [])

    def test_scrub_names_replaces(self):
        hits = guard.foreign_names("今天和李雨桐吃饭")
        self.assertTrue(hits, "检测器先要有货，才能验证替换")
        out = guard.scrub_names("今天和李雨桐吃饭")
        self.assertNotIn("李雨桐", out)
        self.assertIn("一个朋友", out)
        self.assertEqual(guard.foreign_names(out), [])

    def test_fix_identity(self):
        self.assertEqual(guard.fix_identity("我是老公，你是我老婆"), "我是你老婆，你是我老公")
        self.assertEqual(guard.fix_identity("她老公今天加班"), "我老公今天加班")
        self.assertEqual(guard.fix_identity("你是我老公，我在家等你"), "你是我老公，我在家等你")

    def test_place_lock_line(self):
        self.assertIn("公司", guard.place_lock_line({"place": "公司"}))
        self.assertEqual(guard.place_lock_line({}), "")


class PhotoIntentTests(unittest.TestCase):
    def test_all_intent_words(self):
        for word in ("拍一张", "发给你", "你看这张", "合影", "镜头",
                     "咔嚓", "拍给你看", "拍好啦", "等我拍"):
            self.assertTrue(guard.photo_intent("我给你" + word), word)
        self.assertFalse(guard.photo_intent("今天好累，想早点睡"))

    def test_strip_removes_promise_keeps_emotion(self):
        out = guard.strip_photo_promise("今天好累呀，我拍一张发给你。")
        self.assertNotIn("拍一张", out)
        self.assertNotIn("发给你", out)
        self.assertIn("好累", out)

    def test_strip_all_promise_uses_fallback(self):
        out = guard.strip_photo_promise("你看这张，拍好啦！")
        self.assertFalse(guard.photo_intent(out))
        self.assertTrue(out.strip())


class _FakeDraw:
    enabled = True

    def __init__(self, reply="好呀，等我一下，我拍一张发你~", called=None):
        self.reply = reply
        self.calls = called if called is not None else []
        self._last_tool_photo_at = 0

    def send_photo_now(self, event, text, kind="", force=False):
        self.calls.append({"event": event, "text": text, "kind": kind, "force": force})
        return self.reply


class _FakePlugin:
    def __init__(self, draw=None):
        self.draw = draw


class PhotoIntentWiringTests(unittest.TestCase):
    def _run(self, reply, draw):
        fake = _FakePlugin(draw)
        return LlmMixin._enforce_photo_intent(fake, object(), reply)

    def test_intent_forces_photo(self):
        draw = _FakeDraw()
        out = self._run("我拍一张发给你~", draw)
        self.assertEqual(len(draw.calls), 1)
        self.assertEqual(draw.calls[0]["kind"], "real")
        self.assertTrue(draw.calls[0]["force"])
        self.assertIn("拍一张", out)

    def test_send_failure_rewrites_text(self):
        draw = _FakeDraw(reply="上一张还在画，等它出来。")
        out = self._run("我拍一张给你看~", draw)
        self.assertEqual(len(draw.calls), 1)
        self.assertFalse(guard.photo_intent(out), out)
        self.assertTrue(out.strip())

    def test_no_intent_does_not_touch_draw(self):
        draw = _FakeDraw()
        self.assertEqual(self._run("今天上班好累", draw), "今天上班好累")
        self.assertEqual(draw.calls, [])

    def test_recent_photo_is_deduped(self):
        draw = _FakeDraw()
        draw._last_tool_photo_at = time.monotonic()
        out = self._run("我拍一张发给你~", draw)
        self.assertEqual(draw.calls, [])
        self.assertIn("拍一张", out)


class PromptWiringTests(unittest.TestCase):
    def test_diary_engine_prompt_has_safety(self):
        text = diary_engine.build_prompt(dt.date(2026, 11, 16), ["他：在吗"], ["08:00 吃早饭"])
        self.assertIn(guard.SAFETY_LINE, text)
        self.assertIn(guard.ROSTER_LINE, text)

    def test_milestone_prompt_has_safety(self):
        self.assertIn(guard.SAFETY_LINE, milestone.build_prompt("2026-11-16", "他：今天加班"))

    def test_sick_prompt_has_safety(self):
        self.assertIn(guard.SAFETY_LINE, amo_sick_decision.build_prompt("发烧", 10000))

    def test_fact_pool_labels_day(self):
        pool = {"facts": [{"type": "消费", "detail": "花了 20 元"}],
                "meta": {"day": "2026-11-15", "now": "2026-11-16T10:00:00"}}
        self.assertIn("[昨日]", render_prompt(pool))
        pool["meta"] = {"day": "2026-11-16", "now": "2026-11-16T10:00:00"}
        self.assertIn("[今日]", render_prompt(pool))

    def test_chat_prompt_block_is_wired(self):
        source = inspect.getsource(LlmMixin._reply_base_parts)
        self.assertIn("prompt_block()", source)
        self.assertIn("emotion_ice_point", source)

    def test_send_hook_wired(self):
        from amo_message import MessageMixin

        source = inspect.getsource(MessageMixin._send_to_umo)
        self.assertIn("fix_identity(sanitize_extreme(text))", source)

    def test_diary_prompt_block_wired(self):
        source = inspect.getsource(ScheduleEngine._diary_prompt)
        self.assertIn("diary_prompt_block", source)
        self.assertIn("_immersion_extra", source)


class _FakeRecallSelf:
    def cfg_int(self, key, default=0):
        return default

    def _platform_label(self, src):
        return str(src)


class MemoryLabelTests(unittest.TestCase):
    def test_memory_lines_use_time_label(self):
        created = int(dt.datetime(2026, 11, 15, 12, 0).timestamp())
        lines = []
        _recall_format(_FakeRecallSelf(), {}, lines,
                       [{"content": "昨天中午吃了面", "created_at": created}])
        self.assertIn("[昨日]", lines[0])
        today_lines = []
        _recall_format(_FakeRecallSelf(), {}, today_lines,
                       [{"content": "刚吃了面", "created_at": int(time.time())}])
        self.assertIn("[今日]", today_lines[0])


if __name__ == "__main__":
    unittest.main()
