# -*- coding: utf-8 -*-
"""叙事只引用事实（F1）：三类叙事统一读 build_day_view，矛盾才改、遗漏不改。

覆盖：
1. 20 条矛盾/遗漏样本表驱动（矛盾必改、遗漏不改）；
2. 三类入口各接一次（大日记/块日记、聊天回复、主动消息）；
3. 反向验证：去掉视图判断，三类入口都变红（>=3 条）；
4. 结构约束：日记/回复守卫不再各自读日程/账本/健康。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import inspect
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from fixtures import FixedClock  # noqa: E402  # 主动消息入口走真实时钟，测试里必须钉住

import amo_diary_guard as guard  # noqa: E402
import amo_fact_timeline as timeline  # noqa: E402
import amo_proactive  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
import amo_runtime_reply  # noqa: E402

DAY = "2026-11-17"
NOW = _dt.datetime(2026, 11, 17, 10, 30)
AFTERNOON = _dt.datetime(2026, 11, 17, 15, 30)
HEALTHY = {"status": "健康", "sick": False}
SICK = {"status": "感冒", "sick": True}

OFFICE = {"start": "09:00", "end": "18:00", "activity": "上班", "place": "公司",
          "category": "work", "zone": "office"}
HOME = {"start": "09:00", "end": "18:00", "activity": "窝在沙发上", "place": "家",
        "category": "rest", "zone": "home"}
HOSPITAL = {"start": "09:00", "end": "18:00", "activity": "在医院排队看诊", "place": "医院",
            "category": "health", "zone": "hospital"}
SLEEP = {"start": "09:00", "end": "12:00", "activity": "补觉", "place": "家",
         "category": "sleep", "zone": "home"}
LUNCH = {"start": "12:00", "end": "13:00", "activity": "吃午饭", "place": "家",
         "category": "meal", "zone": "home"}
SNACK = {"start": "23:30", "end": "24:00", "activity": "吃泡面（宵夜）", "place": "家",
         "category": "meal", "zone": "home"}
NOODLE = {"start": "12:00", "end": "13:00", "activity": "中午煮了碗面", "place": "家",
          "category": "meal", "zone": "home"}
SNACK_ROW = {"delta_cents": -1800, "reason": "烧烤外卖",
             "ts": _dt.datetime(2026, 11, 17, 23, 30).timestamp(), "idem": "led-1"}


class FakeDB:
    def __init__(self, kv=None, diaries=None, schedule=None):
        self.kv = dict(kv or {})
        self.diaries = dict(diaries or {})
        self.schedule = dict(schedule or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def get_schedule(self, day):
        return list(self.schedule.get(day) or [])

    def list_block_diaries(self, day):
        return list(self.diaries.get(day) or [])


class FakeState:
    """最小 state：日程 + 健康；current_activity 故意留空（事实只该来自视图）。"""

    def __init__(self, schedule=None, health=None, db=None):
        self.schedule = dict(schedule or {})
        self.db = db
        self.health = dict(health or {})

    def health_info(self):
        return dict(self.health)

    def current_activity(self):
        return {}


class FakeWallet:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def ledger(self, limit=200):
        return list(self.rows)

    def rows_between(self, start, end):
        raise AssertionError("叙事守卫不许再直接读账本区间（只读唯一事实视图）")


class FakePlugin:
    def __init__(self, schedule=None, health=None, ledger=None, kv=None, diaries=None):
        self.db = FakeDB(kv=kv, diaries=diaries, schedule=schedule)
        self.state = FakeState(schedule=schedule, health=health, db=self.db)
        self.wallet = FakeWallet(ledger)
        self.config = {}


def _plugin(blocks, health=HEALTHY, ledger=None):
    return FakePlugin({DAY: list(blocks or [])}, health=health, ledger=ledger)


#: 20 条样本：矛盾必改（changed=True）/ 遗漏不改（changed=False）
SAMPLES = (
    {"name": "reply_在家_vs_公司", "scope": "reply", "blocks": [OFFICE],
     "text": "我在家躺着呢，晚点再聊", "changed": True, "contains": "公司"},
    {"name": "reply_公司_vs_家", "scope": "reply", "blocks": [HOME],
     "text": "我在公司加班呢", "changed": True, "contains": "家"},
    {"name": "reply_在家_vs_医院", "scope": "reply", "blocks": [HOSPITAL],
     "text": "我在家呢", "changed": True, "contains": "医院"},
    {"name": "reply_说睡觉_vs_上班", "scope": "reply", "blocks": [OFFICE],
     "text": "我在睡觉呢", "changed": True, "contains": "上班"},
    {"name": "reply_说上班_vs_睡觉", "scope": "reply", "blocks": [SLEEP],
     "text": "我在上班呢", "changed": True, "contains": "睡觉"},
    {"name": "reply_非饭点说刚吃完", "scope": "reply", "blocks": [OFFICE],
     "text": "我刚吃完饭，好撑", "changed": True, "contains": "等会儿",
     "when": AFTERNOON},
    {"name": "reply_没病说发烧", "scope": "reply", "blocks": [HOME],
     "text": "我发烧了，好难受", "changed": True, "contains": "累"},
    {"name": "reply_病着说好了", "scope": "reply", "blocks": [HOME], "health": SICK,
     "text": "我好了，别担心我", "changed": True, "contains": "不舒服"},
    {"name": "reply_没约会说抱我", "scope": "reply", "blocks": [HOME],
     "text": "他刚抱我，好开心", "changed": True, "contains": "手机上"},
    {"name": "reply_没旅游说住酒店", "scope": "reply", "blocks": [HOME],
     "text": "我在酒店呢", "changed": True, "contains": "家"},
    {"name": "reply_没旅游说在旅游", "scope": "reply", "blocks": [HOME],
     "text": "我在旅游呢", "changed": True, "contains": "家"},
    {"name": "diary_没出门_vs_公司", "scope": "diary", "blocks": [OFFICE],
     "text": "今天没出门，一直在家。", "changed": True},
    {"name": "diary_没点外卖_vs_账本", "scope": "diary", "blocks": [],
     "ledger": [SNACK_ROW], "text": "今晚没点外卖。", "changed": True},
    {"name": "diary_早睡_vs_宵夜", "scope": "diary", "blocks": [SNACK],
     "text": "今天早早睡了。", "changed": True},
    {"name": "diary_上班_vs_医院", "scope": "diary", "blocks": [HOSPITAL],
     "text": "今天在公司上班，忙到很晚。", "changed": True},
    {"name": "diary_说他抱着我_没约会", "scope": "diary", "blocks": [OFFICE],
     "text": "他抱着我，心里一下就软了。", "changed": True},
    {"name": "reply_公司说得对", "scope": "reply", "blocks": [OFFICE],
     "text": "我在公司忙着呢，晚点聊", "changed": False},
    {"name": "reply_在家说得对", "scope": "reply", "blocks": [HOME],
     "text": "我在家躺着呢", "changed": False},
    {"name": "diary_只是没提宵夜", "scope": "diary", "blocks": [OFFICE, SNACK],
     "text": "今天有点累，吃了饭就睡了。", "changed": False},
    {"name": "diary_没提某顿饭", "scope": "diary", "blocks": [OFFICE],
     "text": "今天在公司忙了一天，晚上回家吃了饭。", "changed": False},
)


class SampleTableTests(unittest.TestCase):
    """表驱动：矛盾必改、遗漏不改（同一份视图，两个 scope）。"""

    def test_twenty_samples(self):
        self.assertEqual(len(SAMPLES), 20)
        for sample in SAMPLES:
            name = sample["name"]
            scope = sample["scope"]
            when = sample.get("when") or NOW
            plugin = _plugin(sample["blocks"], sample.get("health") or HEALTHY,
                             sample.get("ledger"))
            view = guard.day_view(plugin, DAY, when)
            fixed, violations = guard.narrative_check(
                sample["text"], view, scope, plugin=plugin, now=when)
            with self.subTest(name=name):
                self.assertEqual(bool(violations), bool(sample["changed"]),
                                 "%s 违规列表不符：%s" % (name, violations))
                if scope == "diary":
                    self.assertEqual(fixed, sample["text"], "日记 scope 不该就地改写")
                elif sample["changed"]:
                    self.assertNotEqual(fixed, sample["text"], "%s 该改却没改" % name)
                    if sample.get("contains"):
                        self.assertIn(sample["contains"], fixed, name)
                else:
                    self.assertEqual(fixed, sample["text"], "%s 遗漏不该改" % name)


def _office_view(now=NOW):
    """手工拼一份最小事实视图（不经过插件），验证三类叙事只认这一份。"""
    return {"day": DAY, "now": now, "count": 1, "sources": {"schedule": 1},
            "events": [{"event_id": "ev-1", "day": DAY, "start": 9 * 60,
                        "end": 18 * 60, "time": "09:00", "activity": "上班",
                        "place": "公司", "amount": 0, "amount_cents": 0, "diary": "",
                        "sign": "", "reason": "", "source": "schedule",
                        "sources": ["schedule"], "type": "日程", "detail": "上班（公司）"}]}


class SharedViewTests(unittest.TestCase):
    def test_three_entries_share_one_view(self):
        view = _office_view()
        fixed, violations = guard.narrative_check("我在家躺着呢", view, "reply")
        self.assertIn("公司", fixed)
        self.assertTrue(violations)
        _fixed, diary_hits = guard.narrative_check("今天没出门，一直在家。", view, "diary")
        self.assertTrue(diary_hits)
        proactive, proactive_hits = guard.narrative_check(
            "我在家躺着呢", view, "proactive")
        self.assertIn("公司", proactive)
        self.assertTrue(proactive_hits)


    def test_reply_night_denial_rewritten(self):
        plugin = _plugin([SNACK])
        view = guard.day_view(plugin, DAY, NOW)
        fixed, violations = guard.narrative_check(
            "我今晚没吃宵夜", view, "reply", plugin=plugin, now=NOW)
        self.assertIn("吃了点东西", fixed)
        self.assertEqual([item["rule"] for item in violations], ["meal"])


class DiaryEntryTests(unittest.TestCase):
    """入口一：大日记/块日记（amo_schedule 调 amo_diary_guard.diary_fact_mismatch）。"""

    def test_diary_entry_uses_view(self):
        plugin = _plugin([SNACK])
        self.assertTrue(guard.diary_fact_mismatch(plugin, DAY, "今天早早睡了。"))
        self.assertFalse(guard.diary_fact_mismatch(plugin, DAY, "今天吃了饭才睡的。"))

    def test_schedule_diary_wiring(self):
        with open(os.path.join(ROOT, "amo_schedule.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("amo_diary_guard import diary_fact_mismatch", source)
        self.assertIn("def diary_fact_mismatch", source)
        self.assertIn("sanitize_diary", source)


class ReplyEntryTests(unittest.TestCase):
    """入口二：聊天回复（apply_fact_guard，装饰阶段发送前）。"""

    def test_reply_entry_uses_view(self):
        plugin = _plugin([OFFICE])
        fixed = pipeline.apply_fact_guard("我在家躺着呢", plugin, now=NOW)
        self.assertIn("公司", fixed)
        self.assertNotIn("家", fixed)


class _FakeProactive:
    def __init__(self, plugin):
        self.state = plugin.state
        self.wallet = plugin.wallet
        self.db = plugin.db
        self.sent = []
        self._proactive_burst = {}

    def _proactive_candidates(self, profile, target):
        return ["umo"]

    async def _send_to_umo(self, umo, text):
        self.sent.append(text)
        return True


async def _fake_pipeline_run(text, context):
    return {"text": text}


class ProactiveEntryTests(unittest.TestCase):
    """入口三：主动消息（_send_proactive_lines 切句前补一次）。"""

    def _send(self, plugin, text):
        fake = _FakeProactive(plugin)
        # 主动消息入口不传 now（生产里应该用真实时间），测试里必须把时钟钉在
        # OFFICE 段内（09:00-18:00），否则 18:00 之后跑就会红（2026-11-17 19:0x 实测）。
        with FixedClock(NOW):
            with mock.patch.object(pipeline, "context_for_proactive", lambda *a, **k: None), \
                    mock.patch.object(pipeline, "run", _fake_pipeline_run):
                asyncio.run(amo_proactive.ProactiveMixin._send_proactive_lines(
                    fake, {}, "umo", text))
        return "".join(fake.sent)

    def test_proactive_entry_uses_view(self):
        plugin = _plugin([OFFICE])
        sent = self._send(plugin, "我在家躺着呢")
        self.assertIn("公司", sent)


class ReverseVerificationTests(unittest.TestCase):
    """反向验证：去掉视图判断，三类入口都变红。"""

    @staticmethod
    def _empty_view():
        return {"day": DAY, "now": NOW, "events": [], "sources": {}, "count": 0}

    def test_without_view_diary_gate_goes_green(self):
        plugin = _plugin([SNACK])
        self.assertTrue(guard.diary_fact_mismatch(plugin, DAY, "今天早早睡了。"))
        with mock.patch.object(guard, "day_view", return_value=self._empty_view()):
            self.assertFalse(guard.diary_fact_mismatch(plugin, DAY, "今天早早睡了。"))

    def test_without_view_reply_entry_goes_green(self):
        plugin = _plugin([OFFICE])
        with mock.patch.object(timeline, "build_day_view",
                               return_value=self._empty_view()):
            self.assertEqual(pipeline.apply_fact_guard("我在家躺着呢", plugin, now=NOW),
                             "我在家躺着呢")
        self.assertIn("公司", pipeline.apply_fact_guard("我在家躺着呢", plugin, now=NOW))

    def test_without_view_proactive_entry_goes_green(self):
        plugin = _plugin([OFFICE])
        sender = ProactiveEntryTests()
        with mock.patch.object(timeline, "build_day_view",
                               return_value=self._empty_view()):
            self.assertEqual(sender._send(plugin, "我在家躺着呢"), "我在家躺着呢")
        self.assertIn("公司", sender._send(plugin, "我在家躺着呢"))

    def test_without_view_place_is_blank(self):
        plugin = _plugin([OFFICE])
        self.assertEqual(guard.narrative_place(plugin, NOW), "公司")
        with mock.patch.object(timeline, "build_day_view",
                               return_value=self._empty_view()):
            self.assertEqual(guard.narrative_place(plugin, NOW), "")


class StructureTests(unittest.TestCase):
    """结构约束：守卫只读唯一视图，不再各自拼日程/账本/健康。"""

    def test_diary_facts_only_read_view(self):
        source = inspect.getsource(guard.diary_day_facts)
        self.assertIn("day_view", source)
        for forbidden in ("get_schedule", "rows_between", "health_info"):
            self.assertNotIn(forbidden, source)

    def test_reply_context_only_read_view(self):
        source = inspect.getsource(pipeline.fact_guard_context)
        self.assertIn("narrative_view", source)
        for forbidden in ("_today_items", "health_info"):
            self.assertNotIn(forbidden, source)
        # 只有「视图完全没有事实」才允许退回日程快照（老库/极简 fake 的兼容口）
        self.assertIn("not (view or {}).get(\"events\")", source)
        self.assertIn("_current_activity", source)

    def test_runtime_place_reads_view(self):
        # 3a-2b：decorate 不再自己读 place 再调老守卫，地点/事实统一交给 fact guard；
        # 它内部只读 narrative_view（上一条 test_reply_context_only_read_view 钉住），
        # 这里反过来钉住「生产链里不许再出现自己拼 place 的老口径」。
        source = inspect.getsource(amo_runtime_reply.RuntimeReplyMixin._deco_soft_guards)
        self.assertIn("apply_fact_guard", source)
        self.assertNotIn("_guard_self_location", source)
        self.assertNotIn("_narrative_place", source)


class RepetitionTests(unittest.TestCase):
    """跨轮/跨段状态去重 + 他问吃啥先答（现场序列）。"""

    def setUp(self):
        pipeline.reset_status_memory()
        self.umo = "aiocqhttp:FriendMessage:1"
        self.when = _dt.datetime(2026, 11, 17, 12, 30)
        self.plugin = _plugin([NOODLE])

    def _segments(self, text):
        return [item for item in re.split(r"(?<=[。！？])", text) if item.strip()]

    def test_proactive_then_food_question(self):
        proactive = "药拿好了，正等车回去呢"
        self.assertEqual(
            pipeline.apply_proactive_dedup(proactive, self.plugin, self.umo, self.when),
            proactive)
        # 主动消息的分段也走 send 阶段：先把它记进这一轮
        pipeline.apply_segment_dedup(proactive, self.plugin, self.umo, self.when)
        raw = ("药拿好了，等会儿打车回去。我这边拿了药，正等车回去呢。"
               "嗯嗯。我这边等车呢，回去就吃药睡觉。")
        fixed = pipeline.apply_narrative_repetition(
            raw, self.plugin, "好，你中午吃啥", self.umo, self.when)
        self.assertTrue(fixed.startswith("中午煮了碗面"), fixed)
        for word in ("等车", "拿药", "打车", "回去"):
            self.assertNotIn(word, fixed)
        self.assertIn("嗯嗯", fixed)

    def test_four_segments_do_not_repeat_status(self):
        proactive = "药拿好了，正等车回去呢"
        pipeline.apply_proactive_dedup(proactive, self.plugin, self.umo, self.when)
        pipeline.apply_segment_dedup(proactive, self.plugin, self.umo, self.when)
        raw = ("药拿好了，等会儿打车回去。我这边拿了药，正等车回去呢。"
               "嗯嗯。我这边等车呢，回去就吃药睡觉。")
        fixed = pipeline.apply_narrative_repetition(
            raw, self.plugin, "好，你中午吃啥", self.umo, self.when)
        sent = []
        for part in self._segments(fixed):
            out = pipeline.apply_segment_dedup(part, self.plugin, self.umo, self.when)
            if out:
                sent.append(out)
        self.assertTrue(sent, "分段后不该全部空")
        self.assertTrue(sent[0].startswith("中午煮了碗面"), sent)
        repeated = sum("".join(sent).count(word) for word in ("等车", "拿药", "打车"))
        self.assertLessEqual(repeated, 1, sent)

    def test_cross_turn_repeat_collapsed(self):
        pipeline.begin_status_turn(self.umo)
        pipeline.apply_segment_dedup("我这边拿了药，正等车回去呢",
                                     self.plugin, self.umo, self.when)
        fixed = pipeline.apply_narrative_repetition(
            "我这边等车呢，回去就吃药睡觉", self.plugin, "嗯", self.umo, self.when)
        self.assertEqual(fixed, "还是老样子。")

    def test_food_answer_prepended_before_status(self):
        fixed = pipeline.apply_narrative_repetition(
            "药拿好了，正等车回去呢", self.plugin, "你中午吃啥", self.umo, self.when)
        self.assertTrue(fixed.startswith("中午煮了碗面"), fixed)

    def test_food_answer_uses_view_fact_only(self):
        plugin = _plugin([OFFICE])  # 当天没有吃饭事实
        answer = guard.food_answer(plugin, self.when)
        self.assertEqual(answer, "还没顾上吃呢")

    def test_proactive_same_status_30min_cooldown(self):
        text = "药拿好了，正等车回去呢"
        self.assertEqual(
            pipeline.apply_proactive_dedup(text, self.plugin, self.umo, self.when), text)
        pipeline.apply_segment_dedup(text, self.plugin, self.umo, self.when)
        soon = self.when + _dt.timedelta(minutes=5)
        self.assertEqual(
            pipeline.apply_proactive_dedup(text, self.plugin, self.umo, soon), "")
        later = self.when + _dt.timedelta(minutes=31)
        self.assertEqual(
            pipeline.apply_proactive_dedup(text, self.plugin, self.umo, later), text)


class RepetitionReverseTests(unittest.TestCase):
    """反向验证：去掉去重/先答，现场序列就会复读或答非所问。"""

    def setUp(self):
        pipeline.reset_status_memory()
        self.umo = "aiocqhttp:FriendMessage:1"
        self.when = _dt.datetime(2026, 11, 17, 12, 30)
        self.plugin = _plugin([NOODLE])

    def test_naive_sequence_repeats(self):
        raw = ("药拿好了，等会儿打车回去。我这边拿了药，正等车回去呢。"
               "嗯嗯。我这边等车呢，回去就吃药睡觉。")
        naive = sum(raw.count(word) for word in ("等车", "拿药", "打车"))
        self.assertGreaterEqual(naive, 2, "样本本身就该有复读，否则测不出东西")

    def test_remove_conversation_dedupe_is_red(self):
        raw = "我这边等车呢，回去就吃药睡觉"
        pipeline.begin_status_turn(self.umo)
        pipeline.apply_segment_dedup("我这边拿了药，正等车回去呢",
                                     self.plugin, self.umo, self.when)
        # 去掉跨轮去重：原文原样留下（红）
        self.assertIn("等车", raw)
        fixed = pipeline.apply_narrative_repetition(raw, self.plugin, "嗯",
                                                    self.umo, self.when)
        self.assertNotIn("等车", fixed)

    def test_remove_segment_dedupe_is_red(self):
        parts = ["药拿好了，正等车回去呢", "我这边拿了药，还在等车"]
        naive = sum(part.count("等车") for part in parts)
        self.assertEqual(naive, 2)
        pipeline.begin_status_turn(self.umo)
        kept = [pipeline.apply_segment_dedup(part, self.plugin, self.umo, self.when)
                for part in parts]
        self.assertLessEqual(sum(text.count("等车") for text in kept if text), 1)

    def test_remove_answer_first_is_red(self):
        raw = "药拿好了，正等车回去呢。中午随便吃了点。"
        without = pipeline.apply_narrative_repetition(raw, self.plugin, "", self.umo, self.when)
        self.assertFalse(without.startswith("中午煮了碗面"))
        with_answer = pipeline.apply_narrative_repetition(
            raw, self.plugin, "你中午吃啥", self.umo, self.when)
        self.assertTrue(with_answer.startswith("中午煮了碗面"))
        self.assertLessEqual(with_answer.count("等车"), 1, with_answer)


class RepetitionWiringTests(unittest.TestCase):
    def test_runtime_calls_repetition_between_fact_and_tone(self):
        source = inspect.getsource(amo_runtime_reply.RuntimeReplyMixin._deco_soft_guards)
        self.assertIn("apply_narrative_repetition", source)
        self.assertLess(source.index("apply_fact_guard"),
                        source.index("apply_narrative_repetition"))
        self.assertLess(source.index("apply_narrative_repetition"),
                        source.index("apply_tone_guard"))

    def test_proactive_calls_status_dedup(self):
        source = inspect.getsource(amo_proactive.ProactiveMixin._send_proactive_lines)
        self.assertIn("apply_proactive_dedup", source)

    def test_send_phase_has_segment_dedup(self):
        send = [stage.id for stage in pipeline.PIPELINE.ordered(pipeline.PHASE_SEND)]
        self.assertIn("send.status_dedup", send)
        self.assertLess(send.index("send.status_dedup"), send.index("send.date_context"))


if __name__ == "__main__":
    unittest.main()
