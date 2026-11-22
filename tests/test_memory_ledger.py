# -*- coding: utf-8 -*-
"""记忆事实账本 P0-1/P0-2/P0-3/P0-6（task-28）。

验收口径来自 work/audit/memory_persona.md：
- 同一件商品（米白色针织开衫）只能一个 active 状态，旧的买了/没买/试穿互相 supersede；
- 82 条 active 后台词记忆不再进召回（系统词零进记忆/提示词）；
- 精确余额/价格/等待分钟只以大概数注入；
- 「米白色米白色」「还在车上上」这类确定性改写残留折叠。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_memory_ledger as ledger  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()


def _fact(text, source="chat", now=0):
    return ledger.extract_facts(text, source=source, now=now)


class FactExtractionTests(unittest.TestCase):
    def test_bought_item_has_clean_subject_and_price(self):
        facts = _fact("我回来啦！今天买了米白色针织开衫，花了649元")
        self.assertEqual(1, len(facts))
        fact = facts[0]
        self.assertEqual("米白色针织开衫", fact.subject)
        self.assertEqual("bought", fact.state)
        self.assertEqual("item:米白色针织开衫", fact.conflict_key)
        self.assertEqual("六百多块", fact.value)

    def test_duplicate_prefix_folded_in_subject(self):
        facts = _fact("（把脸偏过来）我试了这件米白色米白色针织开衫，好看吗")
        self.assertEqual(1, len(facts))
        self.assertEqual("米白色针织开衫", facts[0].subject)
        self.assertEqual("tried", facts[0].state)

    def test_last_strong_state_wins(self):
        # 线上 #1378：「试了喜欢的衣服但没买」——最终结论是没买
        facts = _fact("试了喜欢的衣服但没买，看中985元，已放回去")
        self.assertEqual("not_bought", facts[0].state)

    def test_plain_chat_has_no_fact(self):
        self.assertEqual([], _fact("明天要下雨，伞带好"))

    def test_fact_carries_required_fields(self):
        fact = _fact("买了一条围巾，花了120元", source="milestone", now=1700000000)[0]
        for name in ("subject", "state", "source", "valid_from", "supersedes", "conflict_key"):
            self.assertTrue(hasattr(fact, name), name)
        self.assertEqual("milestone", fact.source)
        self.assertEqual(1700000000, fact.valid_from)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin()
        self.ledger = ledger.FactLedger(self.plugin, "u1")

    def test_same_item_only_one_active_state(self):
        self.ledger.record(ledger.MemoryFact("item:开衫", "开衫", "bought"), memory_id=101)
        superseded = self.ledger.record(
            ledger.MemoryFact("item:开衫", "开衫", "not_bought"), memory_id=102)
        self.assertEqual(1, len(superseded))
        self.assertEqual(101, superseded[0]["memory_id"])
        active = self.ledger.active()
        self.assertEqual(1, len(active))
        self.assertEqual("not_bought", active[0]["state"])
        self.assertEqual(int(active[0]["entry_id"]), int(superseded[0]["superseded_by"]))
        self.assertEqual(102, int(active[0]["memory_id"]))
        self.assertEqual("not_bought", self.ledger.active_state("item:开衫"))

    def test_same_state_is_deduped(self):
        self.ledger.record(ledger.MemoryFact("item:开衫", "开衫", "bought"))
        self.ledger.record(ledger.MemoryFact("item:开衫", "开衫", "bought"))
        self.assertEqual(1, len(self.ledger.active()))

    def test_different_items_do_not_supersede(self):
        self.ledger.record(ledger.MemoryFact("item:开衫", "开衫", "bought"))
        self.ledger.record(ledger.MemoryFact("item:羽绒服", "羽绒服", "tried"))
        self.assertEqual(2, len(self.ledger.active()))

    def test_superseded_memory_ids(self):
        self.ledger.record(ledger.MemoryFact("item:开衫", "开衫", "bought"), memory_id=101)
        self.ledger.record(ledger.MemoryFact("item:开衫", "开衫", "not_bought"), memory_id=102)
        self.assertEqual({101}, self.ledger.superseded_memory_ids())

    def test_record_memory_facts_registers(self):
        rows = ledger.record_memory_facts(
            self.plugin, "u1", "今天买了米白色针织开衫，花了649元", memory_id=7)
        self.assertEqual([], rows)
        self.assertEqual("bought", ledger.ledger_for(self.plugin, "u1").active_state(
            "item:米白色针织开衫"))


class RecallFilterTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin()

    def _row(self, content, mid=0, created=0):
        return {"content": content, "id": mid, "created_at": created}

    def test_system_word_memories_are_dropped(self):
        rows = [
            self._row("【日记质检】昨天有 4 条对不上，详情在面板", 1),
            self._row("token 快用完了，模型也换了", 2),
            self._row("他周末喜欢在家煮面", 3),
        ]
        kept = ledger.filter_recalled(self.plugin, "u1", rows)
        self.assertEqual(1, len(kept))
        self.assertEqual(3, kept[0]["id"])

    def test_conflicting_item_keeps_newest(self):
        rows = [
            self._row("我买了米白色针织开衫，花了649元", 11, created=100),
            self._row("试了米白色针织开衫但没买，后来放回去了", 12, created=200),
            self._row("买了米白色针织开衫，花649元", 13, created=50),
        ]
        kept = ledger.filter_recalled(self.plugin, "u1", rows)
        self.assertEqual([12], [row["id"] for row in kept])

    def test_ledger_superseded_memory_dropped(self):
        l = ledger.ledger_for(self.plugin, "u1")
        l.record(ledger.MemoryFact("item:开衫", "开衫", "bought"), memory_id=101)
        l.record(ledger.MemoryFact("item:开衫", "开衫", "not_bought"), memory_id=102)
        rows = [self._row("我买了开衫", 101), self._row("我没买开衫", 102)]
        kept = ledger.filter_recalled(self.plugin, "u1", rows)
        self.assertEqual([102], [row["id"] for row in kept])

    def test_missing_db_does_not_break_recall(self):
        class NoDB:
            pass

        rows = [{"content": "他喜欢喝冰美式", "created_at": 1}]
        self.assertEqual(1, len(ledger.filter_recalled(NoDB(), "u1", rows)))


class TextHygieneTests(unittest.TestCase):
    def test_collapse_duplicate_prefix(self):
        self.assertEqual("我试了这件米白色针织开衫",
                         ledger.collapse_repeats("我试了这件米白色米白色针织开衫"))

    def test_collapse_rewrite_residue(self):
        self.assertEqual("到了，刚进门。还在车上了",
                         ledger.collapse_repeats("到了，刚进门。还在车上上了"))

    def test_keep_laughter(self):
        self.assertEqual("哈哈哈哈", ledger.collapse_repeats("哈哈哈哈"))

    def test_keep_single_char_run(self):
        self.assertEqual("长" * 300, ledger.collapse_repeats("长" * 300))

    def test_approximate_balance_price_duration(self):
        text = "就是985，我卡里还有4072，等了十五分钟，花了649元"
        out = ledger.approximate_amounts(text)
        for exact in ("4072", "十五分钟", "649元"):
            self.assertNotIn(exact, out)
        self.assertIn("四千多", out)
        self.assertIn("好一会儿", out)
        self.assertIn("六百多", out)

    def test_approximate_leaves_normal_text(self):
        self.assertEqual("明天要下雨，伞带好", ledger.approximate_amounts("明天要下雨，伞带好"))

    def test_format_memory_text_both(self):
        out = ledger.format_memory_text("买了米白色米白色开衫，卡里还有4072")
        self.assertNotIn("米白色米白色", out)
        self.assertNotIn("4072", out)


class WriteGuardTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin()

    def test_system_word_memory_dropped(self):
        text, info = ledger.guard_memory_write(
            self.plugin, "u1", "【日记质检】待重写 4 条，详情在面板")
        self.assertEqual("", text)
        self.assertEqual("system_word", info["drop"])

    def test_latin_backend_word_dropped(self):
        text, info = ledger.guard_memory_write(self.plugin, "u1", "API token 过期了")
        self.assertEqual("", text)
        self.assertEqual("system_word", info["drop"])

    def test_normal_memory_collapses_repeats(self):
        text, info = ledger.guard_memory_write(
            self.plugin, "u1", "今天买了米白色米白色针织开衫")
        self.assertEqual("今天买了米白色针织开衫", text)
        self.assertEqual("", info["drop"])


if __name__ == "__main__":
    unittest.main()
