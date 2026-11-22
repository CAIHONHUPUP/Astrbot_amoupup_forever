# -*- coding: utf-8 -*-
"""2026-11-22 diary 剩余事实问题回归：做饭证据 / 场景过渡 / 跨天复读 / 加班误报 / digest 上限。

全部用内存 FakeDB / 纯函数，不碰生产库，不调模型。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_diary_guard as G  # noqa: E402
import amo_schedule as S  # noqa: E402
import work.sim.diary_checks as C  # noqa: E402


def day_facts(*, blocks=None, meals=None, groups=None, out=False):
    return {
        "groups": list(groups or []),
        "places": ["家"],
        "blocks": list(blocks or []),
        "out_blocks": [],
        "out": out,
        "meals": list(meals or []),
        "meal_count": len(meals or []),
        "health": {"status": "健康", "sick": False},
        "health_gen": {"status": "健康", "sick": False},
        "weather": {"text": "晴", "tag": "clear", "temp": 20},
        "together_any": False,
        "wake": "",
        "late_blocks": [],
    }


class SceneTransitionGuardTests(unittest.TestCase):
    def test_cross_place_switch_gets_transition(self):
        text = "在家里把屋子收拾了，衣服也洗了。十点开车去商场，一个人逛。"
        facts = day_facts(groups=["home", "mall", "road"], out=True)
        before = [i for i in C.evaluate(text, facts)[0] if i["category"] == "transition"]
        self.assertTrue(before, "前提：未加守卫时核对器确实判过渡生硬")
        fixed = G.add_scene_transitions(text)
        after = [i for i in C.evaluate(fixed, facts)[0] if i["category"] == "transition"]
        self.assertEqual([], after)
        self.assertIn("后来，十点开车去商场", fixed)

    def test_idempotent(self):
        text = "先把屋子收拾了。十点开车去商场。"
        once = G.add_scene_transitions(text)
        twice = G.add_scene_transitions(once)
        self.assertEqual(once, twice)

    def test_same_place_unchanged(self):
        text = "十点开车去商场。十一点继续逛商场。"
        self.assertEqual(text, G.add_scene_transitions(text))

    def test_existing_connector_unchanged(self):
        text = "先把屋子收拾了。然后十点开车去商场。"
        self.assertEqual(text, G.add_scene_transitions(text))


class UngroundedCookingGuardTests(unittest.TestCase):
    def test_no_evidence_rewrites_claim(self):
        facts = day_facts(blocks=[{"activity": "豆浆配油条", "place": "家",
                                   "zone": "home", "category": "meal"}],
                          meals=[{"start": "08:00", "text": "豆浆配油条", "band": "早餐"}])
        text = "早上煎了两根油条配豆浆，吃得挺满足。"
        self.assertEqual(1, len([i for i in C.evaluate(text, facts)[0]
                                 if i["category"] == "action"]))
        fixed = G.strip_ungrounded_cooking(text, "豆浆配油条")
        self.assertNotIn("煎了", fixed)
        self.assertEqual([], [i for i in C.evaluate(fixed, facts)[0]
                              if i["category"] == "action"])

    def test_with_evidence_keeps_cooking(self):
        text = "晚上自己炒了两个菜，一个人吃。"
        self.assertEqual(text, G.strip_ungrounded_cooking(text, "19:00 炒两个菜（家）"))

    def test_past_clause_not_rewritten(self):
        text = "昨天煎了两根油条配豆浆。"
        self.assertEqual(text, G.strip_ungrounded_cooking(text, "豆浆配油条"))


class RepeatedSentenceGuardTests(unittest.TestCase):
    def test_repeated_sentence_removed(self):
        previous = ["2026年11月1日 晴 今天很平静。算了，不打扰他了，早点睡吧。"]
        text = "今天忙了一天。算了，不打扰他了，早点睡吧。明天再说。"
        fixed = G.strip_repeated_sentences(text, previous, day="2026-11-02")
        self.assertNotIn("算了，不打扰他了，早点睡吧", fixed)
        self.assertIn("明天再说", fixed)

    def test_unique_sentences_kept(self):
        text = "今天去商场逛了。晚上早点睡吧。"
        self.assertEqual(text, G.strip_repeated_sentences(text, ["完全不相干的一天。"]))

    def test_no_previous_returns_original(self):
        text = "今天去商场逛了。"
        self.assertEqual(text, G.strip_repeated_sentences(text, []))


class NameScrubGuardTests(unittest.TestCase):
    def test_place_plus_verb_not_treated_as_name(self):
        # foreign_names 会把「家刷剧」「家看剧」当候选；生产守卫不能把动作换成「一个朋友」
        for text in ("一个人在家刷剧、刷手机", "一个人在家看剧、刷手机",
                     "一个人在家发呆、刷手机"):
            guarded = G.scrub_names_guarded(text)
            self.assertEqual(text, guarded)
            self.assertNotIn("一个朋友", guarded)


class UngroundedSpendingGuardTests(unittest.TestCase):
    def test_money_claim_without_ledger_rewritten(self):
        facts = day_facts()
        text = "今天花了点钱，有点心疼。"
        self.assertEqual(1, len([i for i in C.evaluate(text, facts)[0]
                                 if i["category"] == "fabricated"]))
        fixed = G.strip_ungrounded_spending(text, "")
        self.assertEqual([], [i for i in C.evaluate(fixed, facts)[0]
                              if i["category"] == "fabricated"])

    def test_spending_kept_when_ledger_fact_exists(self):
        text = "今天花了点钱，有点心疼。"
        self.assertEqual(text, G.strip_ungrounded_spending(text, "消费：超市购物 35 元"))


class OvertimeGuardTests(unittest.TestCase):
    def test_chore_phrase_not_rewritten(self):
        # 「忙到十点才出门」是作息不是加班：核对器不再误报，守卫也会去掉才字
        facts = day_facts(blocks=[{"activity": "收拾屋子", "place": "家",
                                   "zone": "home", "category": "life"}])
        text = "收拾屋子、洗衣服，忙到十点才出门。"
        self.assertEqual([], [i for i in C.evaluate(text, facts)[0]
                              if i["subtype"] == "overtime"])
        fixed = G.strip_ungrounded_overtime(text, False)
        self.assertNotIn("忙到十点才出门", fixed)

    def test_actual_overtime_claim_rewritten_without_evidence(self):
        text = "今天加班到十点，累得不行。"
        fixed = G.strip_ungrounded_overtime(text, False)
        self.assertNotIn("加班", fixed)

    def test_overtime_kept_when_schedule_has_it(self):
        text = "今天加班到十点，累得不行。"
        self.assertEqual(text, G.strip_ungrounded_overtime(text, True))


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return []


class FakeState:
    def __init__(self):
        self.schedule = {}


class SeedHost(S.ScheduleMixin):
    def __init__(self):
        self.db = FakeDB()
        self.state = FakeState()


class DigestBudgetTests(unittest.TestCase):
    def test_clip_keeps_whole_fragments(self):
        raw = "；".join(["甲" * 30, "乙" * 30, "丙" * 30, "丁" * 30])
        clipped = S._clip_day_digest(raw, 72)
        self.assertLessEqual(len(clipped), 72)
        for piece in S._digest_fragments(clipped):
            self.assertIn(piece, S._digest_fragments(raw))

    def test_three_days_budget_under_240(self):
        limit = S.DIGEST_DAY_MAX_CHARS
        worst = len("【前几天】") + 3 * 3 + 2 + 3 * limit
        self.assertLess(worst, 240)

    def test_dedupe_drops_previous_day_fragment(self):
        host = SeedHost()
        host.db.kv_set("day_digest:2026-11-01", "今天去了商场；晚上还在敷面膜")
        deduped = S._dedupe_day_digest(host, dt.date(2026, 11, 2),
                                       "晚上还在敷面膜；午餐在楼下小馆吃碗面")
        self.assertNotIn("晚上还在敷面膜", deduped)
        self.assertIn("午餐在楼下小馆吃碗面", deduped)

    def test_seed_day_digest_dedupes_and_clips(self):
        host = SeedHost()
        proposed = {
            "2026-11-01": ["今天去了商场，逛商场", "晚上还在敷面膜", "今天在家收拾屋子、洗衣服"],
            "2026-11-02": ["今天去了公司，回消息处理事情", "晚上还在敷面膜", "今天在家收拾屋子、洗衣服"],
        }

        async def fake_lines(_plugin, day):
            text = day.isoformat() if hasattr(day, "isoformat") else str(day)[:10]
            return list(proposed.get(text) or [])

        original = S._diary_day_fact_lines
        S._diary_day_fact_lines = fake_lines
        try:
            asyncio.run(S._seed_day_digest(host, dt.date(2026, 11, 1)))
            asyncio.run(S._seed_day_digest(host, dt.date(2026, 11, 2)))
        finally:
            S._diary_day_fact_lines = original
        first = host.db.kv_get("day_digest:2026-11-01", "")
        second = host.db.kv_get("day_digest:2026-11-02", "")
        self.assertIn("晚上还在敷面膜", first)
        self.assertNotIn("晚上还在敷面膜", second)
        self.assertIn("今天去了公司", second)
        for digest in (first, second):
            self.assertLessEqual(len(digest), S.DIGEST_DAY_MAX_CHARS)
        self.assertLessEqual(len(first) + len(second) + 40, 240)


if __name__ == "__main__":
    unittest.main()
