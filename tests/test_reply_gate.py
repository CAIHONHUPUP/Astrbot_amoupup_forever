# -*- coding: utf-8 -*-
"""ReplyGate：群聊插话/私聊回复概率的判定逻辑。

这块是纯计算 + 一点随机，之前只有间接覆盖；阈值算法改坏了很难在聊天里发现，
所以把五维打分、动态阈值、LLM 兜底判定都钉住。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402
from amo_shared import MOOD_BASE  # noqa: E402
from main import ReplyGate  # noqa: E402

QQ_UMO = "大肥鱼:FriendMessage:302578963"
GROUP = "aiocqhttp:GroupMessage:1"


class FakeLLM:
    def __init__(self, verdict=None):
        self.verdict = verdict
        self.calls = []

    async def decide(self, umo, question, state):
        self.calls.append((umo, question, state))
        return self.verdict


class FakePlugin:
    def __init__(self, **cfg):
        self.config = {
            "quiet_hours": "",
            "reply_threshold": 60,
            "enable_llm_gate": False,
            "llm_gate_band": 10,
            "gate_w_relevance": 0.30,
            "gate_w_willingness": 0.25,
            "gate_w_social": 0.15,
            "gate_w_timing": 0.15,
            "gate_w_coherence": 0.15,
        }
        self.config.update(cfg)
        self._group_times = {}
        self._last_reply_at = {}
        self.bot_aliases = ["大肥鱼"]
        self.llm = FakeLLM()

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def _depressed_active(self, uid):
        return self.config.get("depressed", False)


def make_snap(**over):
    mood = {key: MOOD_BASE[key] for key in MOOD_BASE}
    mood.update(over.pop("mood", {}))
    snap = {
        "mood": mood,
        "period": {"phase": "卵泡期"},
        "health": {"sick": False, "status": "健康"},
        "activity": {"category": "meal", "activity": "吃饭"},
        "dominant": "平静",
        "weather": {"tag": "sunny"},
    }
    snap.update(over)
    return snap


class ActivityTests(unittest.TestCase):
    def test_no_group_is_zero(self) -> None:
        gate = ReplyGate(FakePlugin())
        self.assertEqual(gate.activity(""), 0.0)

    def test_prunes_old_entries(self) -> None:
        gate = ReplyGate(FakePlugin())
        now = main.now_ts()
        gate.p._group_times["g"] = [now - 1000, now - 10, now - 5]
        self.assertAlmostEqual(gate.activity("g"), 2 / 5.0)
        self.assertEqual(gate.p._group_times["g"], [now - 10, now - 5])


class ScoreTests(unittest.TestCase):
    def test_scores_are_bounded(self) -> None:
        gate = ReplyGate(FakePlugin())
        scores = gate.score_group(GROUP, "g", "你们在聊什么", [], False, {}, make_snap())
        self.assertEqual(
            set(scores), {"relevance", "willingness", "social", "timing", "coherence", "total"}
        )
        for value in scores.values():
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)

    def test_directed_raises_relevance(self) -> None:
        gate = ReplyGate(FakePlugin())
        snap = make_snap()
        plain = gate.score_group(GROUP, "g", "今天吃什么", [], False, {}, snap)
        directed = gate.score_group(GROUP, "g", "今天吃什么", [], True, {}, snap)
        self.assertGreater(directed["relevance"], plain["relevance"])
        self.assertGreater(directed["total"], plain["total"])

    def test_question_and_name_boost(self) -> None:
        gate = ReplyGate(FakePlugin())
        snap = make_snap()
        plain = gate.score_group(GROUP, "g", "在", [], False, {}, snap)
        ask = gate.score_group(GROUP, "g", "大肥鱼在吗？", [], False, {}, snap)
        self.assertGreater(ask["relevance"], plain["relevance"])

    def test_insult_and_ads_lower_social(self) -> None:
        gate = ReplyGate(FakePlugin())
        snap = make_snap()
        normal = gate.score_group(GROUP, "g", "今天天气不错", [], False, {}, snap)
        ad = gate.score_group(GROUP, "g", "加群优惠扫码返利", [], False, {}, snap)
        insult = gate.score_group(GROUP, "g", "你这个废物", [], False, {}, snap)
        self.assertLess(ad["social"], normal["social"])
        self.assertLess(insult["social"], normal["social"])

    def test_blocked_words_lower_social(self) -> None:
        gate = ReplyGate(FakePlugin(blocked_words="政治,赌博"))
        snap = make_snap()
        normal = gate.score_group(GROUP, "g", "今天吃了面", [], False, {}, snap)
        blocked = gate.score_group(GROUP, "g", "聊聊赌博", [], False, {}, snap)
        self.assertLess(blocked["social"], normal["social"])

    def test_sleep_lowers_timing(self) -> None:
        gate = ReplyGate(FakePlugin())
        awake = gate.score_group(GROUP, "g", "还没睡呀", [], False, {},
                                 make_snap(activity={"category": "meal", "activity": "吃饭"}))
        asleep = gate.score_group(GROUP, "g", "还没睡呀", [], False, {},
                                  make_snap(activity={"category": "sleep", "activity": "睡觉"}))
        self.assertLess(asleep["timing"], awake["timing"])

    def test_recent_reply_lowers_willingness(self) -> None:
        gate = ReplyGate(FakePlugin())
        snap = make_snap()
        cold = gate.score_group(GROUP, "g", "在吗", [], False, {}, snap)
        gate.p._last_reply_at[GROUP] = main.now_ts() - 5
        warm = gate.score_group(GROUP, "g", "在吗", [], False, {}, snap)
        self.assertLess(warm["willingness"], cold["willingness"])

    def test_relation_and_sickness(self) -> None:
        gate = ReplyGate(FakePlugin())
        snap = make_snap()
        normal = gate.score_group(GROUP, "g", "在吗", [], False, {"relation": "普通"}, snap)
        lover = gate.score_group(GROUP, "g", "在吗", [], False, {"relation": "相恋"}, snap)
        hostile = gate.score_group(GROUP, "g", "在吗", [], False, {"relation": "仇恨"}, snap)
        sick = gate.score_group(GROUP, "g", "在吗", [], False, {},
                                make_snap(health={"sick": True, "status": "感冒"}))
        self.assertGreater(lover["willingness"], normal["willingness"])
        self.assertLess(hostile["willingness"], normal["willingness"])
        self.assertLess(sick["willingness"], normal["willingness"])


class ThresholdTests(unittest.TestCase):
    def test_threshold_keys_and_bounds(self) -> None:
        gate = ReplyGate(FakePlugin())
        info = gate.dynamic_threshold(0, make_snap(), {})
        self.assertEqual(
            set(info),
            {"value", "base", "activity_delta", "mood_delta", "relation_delta",
             "health_delta", "period_delta", "time_delta", "weather_delta"},
        )
        self.assertGreaterEqual(info["value"], 20)
        self.assertLessEqual(info["value"], 92)

    def test_activity_raises_threshold(self) -> None:
        gate = ReplyGate(FakePlugin())
        quiet = gate.dynamic_threshold(0, make_snap(), {})
        busy = gate.dynamic_threshold(30, make_snap(), {})
        self.assertGreater(busy["value"], quiet["value"])

    def test_legacy_fraction_threshold(self) -> None:
        gate = ReplyGate(FakePlugin(base_reply_threshold=0.55, reply_threshold=0))
        info = gate.dynamic_threshold(0, make_snap(), {})
        self.assertAlmostEqual(info["base"], 55.0)

    def test_mood_and_relation_and_health(self) -> None:
        gate = ReplyGate(FakePlugin())
        base = gate.dynamic_threshold(0, make_snap(), {})
        happy = gate.dynamic_threshold(0, make_snap(mood={"开心": 90, "兴奋": 85, "平静": 80}), {})
        hostile = gate.dynamic_threshold(0, make_snap(), {"relation": "仇恨"})
        sick = gate.dynamic_threshold(0, make_snap(health={"sick": True, "status": "感冒"}), {})
        rainy = gate.dynamic_threshold(0, make_snap(weather={"tag": "rain"}), {})
        self.assertLess(happy["value"], base["value"])
        self.assertGreater(hostile["value"], base["value"])
        self.assertGreater(sick["value"], base["value"])
        self.assertGreater(rainy["value"], base["value"])


class GroupDecisionTests(unittest.TestCase):
    def _run(self, gate, text="在吗", directed=False, recent=None, profile=None, snap=None):
        return asyncio.run(gate.should_reply_group(
            GROUP, "g", text, recent or [], directed, profile or {}, snap or make_snap()))

    def test_high_score_always_replies(self) -> None:
        gate = ReplyGate(FakePlugin(reply_threshold=20))
        with mock.patch.object(main.random, "random", return_value=0.0):
            result = self._run(gate, text="大肥鱼在吗？", directed=True)
        self.assertTrue(result["reply"])
        self.assertEqual(result["probability"], 0.92)
        self.assertFalse(result["used_llm"])

    def test_low_score_rarely_replies(self) -> None:
        gate = ReplyGate(FakePlugin(reply_threshold=92))
        result = self._run(gate, text="嗯", snap=make_snap(activity={"category": "sleep", "activity": "睡觉"}))
        self.assertLess(result["probability"], 0.2)

    def test_bot_already_talked_suppresses(self) -> None:
        plugin = FakePlugin()
        gate = ReplyGate(plugin)
        text = "随便说说"
        # 把阈值放到刚好略高于当前打分，diff 落在 0~20 之间才会触发「她已经说过两句」的抑制
        total = gate.score_group(GROUP, "g", text, [], False, {}, make_snap())["total"]
        plugin.config["reply_threshold"] = total + 5
        recent = [{"role": "bot", "content": "在"}, {"role": "bot", "content": "嗯"}]
        result = self._run(gate, text=text, recent=recent)
        self.assertFalse(result["reply"])

    def test_llm_gate(self) -> None:
        plugin = FakePlugin(reply_threshold=50, enable_llm_gate=True, llm_gate_band=100)
        plugin.llm = FakeLLM(verdict=True)
        gate = ReplyGate(plugin)
        result = self._run(gate)
        self.assertTrue(result["used_llm"])
        self.assertTrue(result["reply"])
        self.assertIn("应该自然地插话", plugin.llm.calls[0][1])

    def test_llm_gate_verdict_false(self) -> None:
        plugin = FakePlugin(reply_threshold=50, enable_llm_gate=True, llm_gate_band=100)
        plugin.llm = FakeLLM(verdict=False)
        gate = ReplyGate(plugin)
        self.assertFalse(self._run(gate)["reply"])

    def test_llm_gate_none_keeps_probability(self) -> None:
        plugin = FakePlugin(reply_threshold=50, enable_llm_gate=True, llm_gate_band=100)
        plugin.llm = FakeLLM(verdict=None)
        gate = ReplyGate(plugin)
        result = self._run(gate)
        self.assertTrue(result["used_llm"])
        self.assertIn("probability", result)


class PrivateProbabilityTests(unittest.TestCase):
    def test_default_follows_relation(self) -> None:
        # 没写关系时按「普通」= 5 成；恋爱关系见 RelationProbabilityTests
        gate = ReplyGate(FakePlugin())
        idle = make_snap(activity={"category": "idle"})
        self.assertAlmostEqual(gate.private_probability(idle, {}), 0.5, places=2)

    def test_sleep_work_and_sickness(self) -> None:
        gate = ReplyGate(FakePlugin())
        self.assertLess(gate.private_probability(make_snap(activity={"category": "sleep"}), {}), 0.05)
        self.assertLess(gate.private_probability(make_snap(activity={"category": "work"}), {}), 0.5)
        sick = gate.private_probability(make_snap(health={"status": "低烧"}), {})
        self.assertLess(sick, 0.5)

    def test_relation_floors_and_caps(self) -> None:
        gate = ReplyGate(FakePlugin())
        lover = gate.private_probability(make_snap(activity={"category": "work"}), {"relation": "相恋"})
        self.assertGreaterEqual(lover, 0.90)
        hate = gate.private_probability(make_snap(), {"relation": "仇恨"})
        self.assertLess(hate, 0.3)
        self.assertLessEqual(hate, 1.0)

    def test_mood_and_period(self) -> None:
        gate = ReplyGate(FakePlugin())
        idle = {"category": "idle"}
        angry = gate.private_probability(make_snap(mood={"生气": 90}, activity=idle), {})
        happy = gate.private_probability(make_snap(mood={"开心": 80}, activity=idle), {})
        period = gate.private_probability(make_snap(period={"phase": "月经期"}, activity=idle), {})
        self.assertLess(angry, 0.5)
        self.assertGreaterEqual(happy, 0.5)
        self.assertLess(period, 0.5)

    def test_depressed_cuts_probability(self) -> None:
        plugin = FakePlugin()
        plugin.config["depressed"] = True
        gate = ReplyGate(plugin)
        self.assertLess(gate.private_probability(make_snap(), {"uid": "1"}), 0.95)

class RelationProbabilityTests(unittest.TestCase):
    """私聊回复概率按关系分档：恋人必回，其它 9/7/5/3/1 成。"""

    def _prob(self, relation, **snap):
        gate = ReplyGate(FakePlugin())
        return gate.private_probability(make_snap(**snap), {"relation": relation, "uid": "u1"})

    def test_lover_always_replies(self) -> None:
        for snap in (
            {"activity": {"category": "sleep"}},
            {"health": {"sick": True, "status": "感冒"}},
            {"mood": {"生气": 95}},
        ):
            self.assertEqual(self._prob("相恋", **snap), 1.0, "恋人必须 100% 回复")

    def test_other_relations_base(self) -> None:
        for relation, expect in (("家人", 0.9), ("熟识", 0.7), ("普通", 0.5),
                                 ("陌生人", 0.3), ("仇恨", 0.1)):
            self.assertAlmostEqual(
                self._prob(relation, activity={"category": "rest"}), expect, places=3,
                msg="关系 %s 的基础概率应为 %.1f" % (relation, expect))

    def test_unknown_relation_uses_config(self) -> None:
        gate = ReplyGate(FakePlugin(private_reply_prob=0.42))
        self.assertAlmostEqual(
            gate.private_probability(make_snap(activity={"category": "rest"}),
                                     {"relation": "网友", "uid": "u1"}),
            0.42, places=3)

    def test_state_still_lowers_non_lover(self) -> None:
        self.assertLess(self._prob("普通", activity={"category": "sleep"}), 0.05)


if __name__ == "__main__":
    unittest.main()
