# -*- coding: utf-8 -*-
"""amo_shared 纯函数回归：工具、文本判定、用量统计。

这些函数几乎所有链路都会用到（防抖、分段、心情、面板），
之前只有个别用例顺带覆盖，单独锁住行为，改实现时不容易悄悄跑偏。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_shared  # noqa: E402
from amo_shared import (  # noqa: E402
    _clock_configure,
    clamp,
    clean_reply,
    day_meta,
    dialect_for_city,
    extract_persona_name,
    has_question,
    in_minute_range,
    json_extract,
    json_extract_list,
    lark_quote_kwargs,
    looks_like_ai_admission,
    looks_like_defensive_reply,
    looks_like_model_leak,
    md5_int,
    other_platform_bot_lines,
    overlap,
    parse_hhmm,
    quiet_now,
    segment_text,
    sentiment,
    strip_chat_format,
    summarize_token_usage,
    to_float,
    to_int,
    tokens,
    truncate,
    typing_delay_ms,
    uniq,
    usage_cost,
    usage_is_peak,
)


class ScalarUtilTests(unittest.TestCase):
    def test_clamp(self) -> None:
        self.assertEqual(clamp(150), 100.0)
        self.assertEqual(clamp(-5), 0.0)
        self.assertEqual(clamp(50), 50.0)
        self.assertEqual(clamp("abc", 3, 9), 3.0)

    def test_to_int_and_float(self) -> None:
        self.assertEqual(to_int("3.9"), 3)
        self.assertEqual(to_int(None), 0)
        self.assertEqual(to_int("x", 7), 7)
        self.assertEqual(to_float("1.5"), 1.5)
        self.assertEqual(to_float("x", 2.5), 2.5)

    def test_truncate(self) -> None:
        self.assertEqual(truncate("a\nb", 10), "a b")
        self.assertEqual(truncate("abcdef", 3), "abc…")

    def test_md5_int_stable(self) -> None:
        self.assertEqual(md5_int("东莞"), md5_int("东莞"))
        self.assertNotEqual(md5_int("东莞"), md5_int("贵阳"))

    def test_uniq_drops_falsy(self) -> None:
        self.assertEqual(uniq([1, 1, 0, 2, ""]), [1, 2])

    def test_parse_hhmm(self) -> None:
        self.assertEqual(parse_hhmm("08:30"), 510)
        self.assertEqual(parse_hhmm("bad", 5), 5)

    def test_in_minute_range(self) -> None:
        self.assertTrue(in_minute_range(10, 5, 20))
        self.assertFalse(in_minute_range(25, 5, 20))
        self.assertTrue(in_minute_range(23, 20, 5))   # 跨零点
        self.assertTrue(in_minute_range(3, 20, 5))
        self.assertTrue(in_minute_range(10, 10, 10))  # start == end 视为全天

    def test_quiet_now_bad_spec_is_false(self) -> None:
        self.assertFalse(quiet_now("这不是时间"))


class TextAnalysisTests(unittest.TestCase):
    def test_has_question(self) -> None:
        self.assertTrue(has_question("你吃了吗"))
        self.assertTrue(has_question("what?"))
        self.assertFalse(has_question("好的"))

    def test_sentiment_counts(self) -> None:
        self.assertEqual(sentiment("")["score"], 0.0)
        good = sentiment("今天很开心，谢谢你")
        self.assertGreaterEqual(good["pos"], 2)
        bad = sentiment("好烦，真讨厌")
        self.assertGreaterEqual(bad["neg"], 2)
        self.assertTrue(sentiment("你这个废物")["insult"])

    def test_tokens_include_bigrams(self) -> None:
        got = tokens("你好世界")
        self.assertIn("你好", got)
        self.assertIn("世界", got)

    def test_overlap(self) -> None:
        self.assertAlmostEqual(overlap({"a", "b"}, {"b", "c"}), 0.5)
        self.assertEqual(overlap(set(), {"a"}), 0.0)


class JsonExtractTests(unittest.TestCase):
    def test_plain_object(self) -> None:
        self.assertEqual(json_extract('{"a": 1}'), {"a": 1})

    def test_code_fence_and_trailing_comma(self) -> None:
        self.assertEqual(json_extract('```json\n{"a": 1,}\n```'), {"a": 1})

    def test_nested_and_string_braces(self) -> None:
        text = '前缀 {"a": {"b": "}"}, "c": 2} 后缀'
        payload = json_extract(text)
        self.assertEqual(payload["a"]["b"], "}")
        self.assertEqual(payload["c"], 2)

    def test_list_helper(self) -> None:
        rows = json_extract_list('[{"a": 1}, {"b": 2}]')
        self.assertEqual(len(rows), 2)
        self.assertEqual(json_extract_list('{"a": 1}'), [{"a": 1}])
        self.assertEqual(json_extract_list("不是 json"), [])

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(json_extract("{"))
        self.assertIsNone(json_extract(""))


class ReplyCleanupTests(unittest.TestCase):
    def test_strip_chat_format(self) -> None:
        text = strip_chat_format("@张三(QQ:1234567) 在吗")
        self.assertIn("张三", text)
        self.assertNotIn("QQ", text)
        self.assertNotIn("1234567", text)

    def test_clean_reply_strips_prefix_and_fence(self) -> None:
        self.assertEqual(clean_reply("Amo：你好呀"), "你好呀")
        self.assertEqual(clean_reply('"带引号"'), "带引号")
        self.assertEqual(clean_reply("```\n代码\n```"), "")

    def test_ai_admission(self) -> None:
        self.assertTrue(looks_like_ai_admission("我只是个机器人"))
        self.assertFalse(looks_like_ai_admission("今天天气不错"))

    def test_defensive_reply(self) -> None:
        self.assertTrue(looks_like_defensive_reply("你才是机器人"))
        self.assertFalse(looks_like_defensive_reply("我在写代码"))

    def test_model_leak(self) -> None:
        self.assertTrue(looks_like_model_leak("我是用 deepseek 回的"))
        self.assertFalse(looks_like_model_leak("今天吃了面"))
        # 长回复不算泄漏（可能是在正经讨论）
        self.assertFalse(looks_like_model_leak("deepseek " + "很长的说明" * 20))

    def test_typing_delay_bounds(self) -> None:
        self.assertGreaterEqual(typing_delay_ms(""), 200)
        self.assertLessEqual(typing_delay_ms("很长" * 500, cap=3000), 3600)
        self.assertGreater(typing_delay_ms("好" * 50), typing_delay_ms("好"))


class SegmentTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(segment_text(""), [])
        self.assertEqual(segment_text("   "), [])

    def test_short_text_one_piece(self) -> None:
        self.assertEqual(segment_text("在吗"), ["在吗"])

    def test_long_text_respects_max_segments(self) -> None:
        text = "。".join(["今天上班好累啊想早点回家躺着"] * 8)
        pieces = segment_text(text, max_segments=3, max_len=20)
        # 段数受 max_segments 限制；为保住句子完整，合并后的段可以超过 max_len
        self.assertLessEqual(len(pieces), 3)
        self.assertEqual("".join(pieces).count("今天上班好累啊想早点回家躺着"), 8)

    def test_paired_symbols_not_split(self) -> None:
        pieces = segment_text("她说「今天不回来了」，我有点失落。", min_len=1, max_len=12)
        joined = "".join(pieces)
        self.assertIn("「今天不回来了」", joined)

    def test_regex_mode_falls_back(self) -> None:
        pieces = segment_text("一句。两句。", mode="regex", regex_pattern="(?<=。)", max_segments=4)
        self.assertGreaterEqual(len(pieces), 1)


class DayMetaTests(unittest.TestCase):
    def setUp(self) -> None:
        _clock_configure("Asia/Shanghai")

    def test_holiday(self) -> None:
        info = day_meta("2026-10-01")
        self.assertEqual(info["holiday"], "国庆")
        self.assertTrue(info["rest"])
        self.assertGreaterEqual(info["holiday_days"], 1)

    def test_solar_term(self) -> None:
        self.assertEqual(day_meta("2026-11-07")["solar_term"], "立冬")

    def test_weekend_label(self) -> None:
        info = day_meta("2026-11-08")  # 周日
        self.assertTrue(info["weekend"])
        self.assertEqual(info["label"], "周末")

    def test_weekday(self) -> None:
        info = day_meta("2026-11-10")  # 周二
        self.assertEqual(info["weekday"], "二")
        self.assertFalse(info["rest"])

    def test_bad_date_falls_back_to_today(self) -> None:
        self.assertTrue(day_meta("不是日期")["date"])


class CrossPlatformAndQuoteTests(unittest.TestCase):
    def test_other_platform_bot_lines(self) -> None:
        rows = [
            {"role": "bot", "content": "我在便利店买饭", "umo": "lark:FriendMessage:ou_x"},
            {"role": "bot", "content": "本平台说的", "umo": "qq:FriendMessage:1"},
            {"role": "user", "content": "他说的", "umo": "lark:FriendMessage:ou_x"},
            {"role": "bot", "content": "按平台兜底", "platform": "qq"},
        ]
        lines = other_platform_bot_lines(rows, "qq:FriendMessage:1", limit=3, platform="qq")
        self.assertEqual(lines, ["我在便利店买饭"])

    def test_other_platform_keeps_tail(self) -> None:
        rows = [{"role": "bot", "content": "第%d句" % i, "umo": "a"} for i in range(5)]
        self.assertEqual(other_platform_bot_lines(rows, "b", limit=2), ["第3句", "第4句"])

    def test_lark_private_uses_open_id(self) -> None:
        kwargs = lark_quote_kwargs(True, "m1", "ou_user", "oc_chat", True)
        self.assertEqual(kwargs["receive_id"], "ou_user")
        self.assertEqual(kwargs["receive_id_type"], "open_id")
        self.assertNotIn("reply_message_id", kwargs)

    def test_lark_group_quote(self) -> None:
        kwargs = lark_quote_kwargs(False, "m1", "ou_user", "oc_chat", True)
        self.assertEqual(kwargs["reply_message_id"], "m1")

    def test_lark_group_no_quote(self) -> None:
        kwargs = lark_quote_kwargs(False, "m1", "", "oc_chat", False)
        self.assertEqual(kwargs["receive_id"], "oc_chat")

    def test_lark_without_ids_falls_back(self) -> None:
        self.assertEqual(lark_quote_kwargs(False, "", "", "", False), {})


class MiscSharedTests(unittest.TestCase):
    def test_dialect_for_city(self) -> None:
        self.assertEqual(dialect_for_city("东莞"), "广东话")
        self.assertEqual(dialect_for_city("贵阳"), "贵州话")
        self.assertEqual(dialect_for_city(""), "")
        self.assertEqual(dialect_for_city("火星"), "")

    def test_extract_persona_name(self) -> None:
        self.assertEqual(extract_persona_name({"name": "大肥鱼"}), "大肥鱼")
        self.assertEqual(extract_persona_name({"name": "default", "prompt": "你的名字叫阿珍。"}), "阿珍")
        self.assertEqual(extract_persona_name(None), "")

    def test_schedule_category_mood_is_dict(self) -> None:
        table = amo_shared.schedule_category_mood()
        self.assertIsInstance(table, dict)


class UsageTests(unittest.TestCase):
    def setUp(self) -> None:
        _clock_configure("Asia/Shanghai")

    def _ts(self, y, m, d, h) -> float:
        return datetime(y, m, d, h, tzinfo=timezone(timedelta(hours=8))).timestamp()

    def test_peak_hours(self) -> None:
        self.assertTrue(usage_is_peak(self._ts(2026, 11, 10, 10)))   # 周二 10 点
        self.assertFalse(usage_is_peak(self._ts(2026, 11, 10, 12)))  # 午休
        self.assertFalse(usage_is_peak(self._ts(2026, 11, 7, 10)))   # 周六

    def test_usage_cost(self) -> None:
        usage = {
            "peak": {"input_cached": 1e6, "input_miss": 1e6, "output": 1e6},
            "idle": {"input_cached": 1e6, "input_miss": 1e6, "output": 1e6},
        }
        self.assertAlmostEqual(usage_cost(usage), 10.04 + 5.02, places=4)
        cheap = usage_cost(usage, {"out_peak": 0, "out_idle": 0})
        self.assertAlmostEqual(cheap, 1e6 * 0.04 / 1e6 + 1e6 * 2.0 / 1e6
                              + 1e6 * 0.02 / 1e6 + 1e6 * 1.0 / 1e6)

    def test_summarize_missing_db(self) -> None:
        usage = summarize_token_usage(os.path.join(ROOT, "不存在的库.db"))
        self.assertIn("error", usage)

    def test_summarize_rows(self) -> None:
        path = os.path.join(ROOT, ".test-tmp", "usage-%d.db" % int(time.time() * 1000))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE provider_stats (provider_model TEXT, token_input_other INTEGER, "
            "token_input_cached INTEGER, token_output INTEGER, start_time REAL, created_at REAL)"
        )
        rows = [
            ("deepseek-chat", 1000, 500, 200, self._ts(2026, 11, 10, 10), 0),
            ("deepseek-chat", 200, 100, 50, self._ts(2026, 11, 7, 10), 0),
            ("bge-m3", 300, 0, 0, self._ts(2026, 11, 10, 10), 0),
        ]
        conn.executemany("INSERT INTO provider_stats VALUES (?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()

        usage = summarize_token_usage(path)
        self.assertEqual(usage["calls"], 3)
        self.assertEqual(usage["input_miss"], 1500)
        self.assertEqual(usage["input_cached"], 600)
        self.assertEqual(usage["output"], 250)
        self.assertEqual([item["model"] for item in usage["by_model"]][0], "deepseek-chat")

        filtered = summarize_token_usage(path, model_filter="bge")
        self.assertEqual(filtered["calls"], 1)
        windowed = summarize_token_usage(path, start_ts=self._ts(2026, 11, 9, 0))
        self.assertEqual(windowed["calls"], 2)


if __name__ == "__main__":
    unittest.main()
