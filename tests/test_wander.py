from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_source  # noqa: E402

from wander import (  # noqa: E402
    ask_limit,
    assess,
    detect_follow_invite,
    detect_follow_refuse,
    follow_agree_text,
    follow_block,
    follow_offer_text,
    follow_reject_text,
    detect_agreement,
    detect_question,
    detect_refusal,
    offer_block,
    offer_text,
    pick_destination,
    plan_text,
    solo_agree_text,
    solo_block,
    solo_refuse_text,
    trip_days,
    want_travel,
)


class FakeRng:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


class QuestionTests(unittest.TestCase):
    def test_ask(self) -> None:
        for text in (
            "你最近想出去玩吗",
            "想不想出去玩",
            "要不要出去旅游",
            "你想去旅游吗",
            "最近想出去走走吗",
            "宝贝，你要不要出去玩",
        ):
            self.assertTrue(detect_question(text), text)

    def test_not_ask(self) -> None:
        for text in (
            "我明天想出去玩",
            "出去玩吧",
            "今天好累",
            "你吃饭了吗",
            "周末我们去旅游",
        ):
            self.assertFalse(detect_question(text), text)


class FollowTextTests(unittest.TestCase):
    def test_offer(self) -> None:
        for value in (0.0, 0.3, 0.7, 0.99):
            text = follow_offer_text("清远", FakeRng(value))
            self.assertTrue(text)

    def test_agree_reject(self) -> None:
        self.assertTrue(follow_agree_text("清远", FakeRng(0.0)))
        self.assertTrue(follow_reject_text("清远", FakeRng(0.0)))

    def test_invite_detection(self) -> None:
        for text in ("你过来吧", "来陪我", "过来找我", "你过来呀", "过来吧宝贝"):
            self.assertTrue(detect_follow_invite(text), text)
        for text in ("我过去吧", "我过来吧", "好好上班", "吃饭吧", "你忙"):
            self.assertFalse(detect_follow_invite(text), text)

    def test_refuse_detection(self) -> None:
        for text in ("你好好上班吧", "不用来", "别来了", "你忙你的", "不用陪我了", "算了吧"):
            self.assertTrue(detect_follow_refuse(text), text)
        self.assertFalse(detect_follow_refuse("好吧"))

    def test_blocks(self) -> None:
        self.assertIn("清远", follow_block(True, "清远"))
        self.assertIn("过去", follow_block(True, "清远"))
        self.assertIn("闷闷不乐", follow_block(False, "清远"))


class ReplyTests(unittest.TestCase):
    def test_agree(self) -> None:
        for text in ("好", "好啊", "行", "可以", "去吧", "去呗", "同意", "嗯嗯", "OK"):
            self.assertTrue(detect_agreement(text), text)

    def test_disagree(self) -> None:
        for text in ("不去", "算了", "别去了", "留下陪我", "下次吧", "不用了"):
            self.assertTrue(detect_refusal(text), text)
        # “我陪你”是答应，不算拒绝
        self.assertFalse(detect_refusal("我陪你"))
        self.assertFalse(detect_refusal("好，我陪你去看"))

    def test_long_not_agree(self) -> None:
        self.assertFalse(detect_agreement("好吧那你自己注意安全别乱跑"))
        self.assertFalse(detect_agreement(""))


class AssessTests(unittest.TestCase):
    def test_low_and_tired(self) -> None:
        low, tired = assess({"疲惫": 70, "郁闷": 40, "开心": 10}, {})
        self.assertTrue(low)
        self.assertTrue(tired)

    def test_good_mood(self) -> None:
        low, tired = assess({"疲惫": 10, "郁闷": 5, "开心": 80, "委屈": 5, "焦虑": 5}, {"status": "身体不错"})
        self.assertFalse(low)
        self.assertFalse(tired)

    def test_bad_health(self) -> None:
        _, tired = assess({"疲惫": 10}, {"status": "有点累"})
        self.assertTrue(tired)

    def test_custom_probability(self) -> None:
        self.assertFalse(want_travel(False, False, FakeRng(0.5), prob_good=0.0))
        self.assertTrue(want_travel(False, False, FakeRng(0.5), prob_good=1.0))
        self.assertFalse(want_travel(True, False, FakeRng(0.9), prob_low=0.85))
        self.assertTrue(want_travel(True, False, FakeRng(0.1), prob_low=0.85))
        # 越界/坏值要夹住，不能崩
        self.assertFalse(want_travel(False, False, FakeRng(0.5), prob_good="bad"))
        self.assertTrue(want_travel(False, False, FakeRng(0.5), prob_good=9))
        self.assertFalse(want_travel(False, False, FakeRng(0.5), prob_good=-1))

    def test_want_probability(self) -> None:
        self.assertTrue(want_travel(True, False, FakeRng(0.5)))
        self.assertFalse(want_travel(True, False, FakeRng(0.95)))
        self.assertTrue(want_travel(False, False, FakeRng(0.1)))
        self.assertFalse(want_travel(False, False, FakeRng(0.5)))


SPOTS = [
    {"name": "黄腾峡", "city": "清远", "area": "清城区", "projects": ["漂流"]},
    {"name": "古龙峡", "city": "清远", "area": "清新区", "projects": ["玻璃大峡谷"]},
    {"name": "故宫博物院", "city": "北京", "area": "东城区", "projects": ["太和殿"]},
]


class AskLimitTests(unittest.TestCase):
    def test_limit(self) -> None:
        self.assertEqual(ask_limit(False), 1)
        self.assertEqual(ask_limit(True), 3)


class SoloReplyTests(unittest.TestCase):
    def test_agree(self) -> None:
        for value in (0.0, 0.4, 0.9):
            self.assertIn("攻略", solo_agree_text("清远", FakeRng(value)))

    def test_refuse(self) -> None:
        for value in (0.0, 0.4, 0.9):
            text = solo_refuse_text(FakeRng(value))
            self.assertTrue("陪" in text or "不去" in text)


class TripDaysTests(unittest.TestCase):
    def test_one_to_three(self) -> None:
        self.assertEqual(trip_days(FakeRng(0.0)), 1)
        self.assertEqual(trip_days(FakeRng(0.4)), 2)
        self.assertEqual(trip_days(FakeRng(0.9)), 3)
        for value in (0.0, 0.33, 0.66, 0.99):
            self.assertIn(trip_days(FakeRng(value)), (1, 2, 3))


class DestinationTests(unittest.TestCase):
    def test_pick_only_cities_with_spots(self) -> None:
        city, spots = pick_destination(SPOTS, FakeRng(0.0))
        self.assertIn(city, ("清远", "北京"))
        self.assertTrue(spots)
        self.assertTrue(all(isinstance(row, dict) for row in spots))

    def test_exclude_current_city(self) -> None:
        for value in (0.0, 0.3, 0.7, 0.99):
            city, _ = pick_destination(SPOTS, FakeRng(value), exclude_city="清远")
            self.assertNotEqual(city, "清远")

    def test_empty(self) -> None:
        self.assertEqual(pick_destination([], FakeRng(0.0)), ("", []))


class TextTests(unittest.TestCase):
    def test_offer_want_mentions_city_and_spot(self) -> None:
        text = offer_text(True, "清远", SPOTS[:1], FakeRng(0.0))
        self.assertIn("清远", text)
        self.assertIn("黄腾峡", text)

    def test_offer_stay(self) -> None:
        text = offer_text(False, "", [], FakeRng(0.0))
        self.assertIn("身边", text)

    def test_plan(self) -> None:
        self.assertIn("明天", plan_text("清远", SPOTS[:1], True, FakeRng(0.0)))
        self.assertIn("现在", plan_text("清远", SPOTS[:1], False, FakeRng(0.0)))

    def test_solo_block(self) -> None:
        text = solo_block("清远", SPOTS[:1])
        self.assertIn("清远", text)
        self.assertIn("黄腾峡", text)
        self.assertIn("一个人", text)
        self.assertEqual(solo_block("", []), "")

    def test_spots_as_names(self) -> None:
        # 行程 kv 里存的是名字，不是 dict
        self.assertIn("黄腾峡", solo_block("清远", ["黄腾峡"]))
        self.assertIn("黄腾峡", plan_text("清远", ["黄腾峡"], False, FakeRng(0.0)))
        self.assertIn("黄腾峡", offer_text(True, "清远", ["黄腾峡"], FakeRng(0.0)))

    def test_offer_blocks(self) -> None:
        self.assertIn("不想去", offer_block(False, "", []))
        self.assertIn("清远", offer_block(True, "清远", SPOTS[:1]))


class WiringTests(unittest.TestCase):
    def test_main_wiring(self) -> None:
        src = _plugin_source.all_source(ROOT)
        self.assertIn("_maybe_wander", src)
        self.assertIn("_start_solo_trip", src)
        self.assertIn("_end_solo_trip", src)
        self.assertIn("wander_trip", src)
        self.assertIn("wander_offer", src)
        self.assertIn("return_day", src)
        self.assertIn("_maybe_solo_return", src)
        self.assertIn("apply_return_overlay", src)
        self.assertIn("wander_asked_day", src)
        self.assertIn("wander_prob_low", src)
        self.assertIn("follow_declined", src)
        self.assertIn("_log_follow_day", src)

    def test_schema_flag(self) -> None:
        with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as fh:
            schema = json.load(fh)
        self.assertTrue(schema["wander_enabled"]["default"])
        self.assertIn("wander_prob_low", schema)
        self.assertIn("wander_ask_three_times", schema)
        self.assertFalse(schema["wander_ask_three_times"]["default"])
        self.assertIn("wander_prob_good", schema)
        self.assertEqual(schema["wander_prob_low"]["default"], 0.85)
        self.assertEqual(schema["wander_prob_good"]["default"], 0.15)


if __name__ == "__main__":
    unittest.main()
