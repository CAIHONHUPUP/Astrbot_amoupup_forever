# -*- coding: utf-8 -*-
"""分享改写回归：角色反转/重复开场只改触发短语，保留描述（2026-11-22）。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_state_consistency import _SHARE_OPENER, _SHARE_ROLE, rewrite  # noqa: E402


def _plugin(place="家", zone="home"):
    return SimpleNamespace(state=SimpleNamespace(
        current_activity=lambda: {"place": place, "zone": zone}))


def _context(user, *history):
    """复刻 apply_chat_safety 的 _context：他最后一句 + 最近历史。"""
    return "\n".join([user] + list(history))


class _LedgerDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


def _ledger_plugin(place="家", zone="home"):
    plugin = _plugin(place, zone)
    plugin.db = _LedgerDB()
    return plugin


class ShareRewriteTests(unittest.TestCase):
    def test_opener_repeat_keeps_tail(self):
        text = "你快看这个视频，猫在跳来跳去，后面还有一只狗。"
        out = rewrite(text, _plugin(), "我刷到一个猫的视频，笑死我了")
        self.assertNotIn("你快看", out)
        self.assertIn("就是刚才那个猫", out)
        self.assertIn("猫在跳来跳去", out)
        self.assertIn("后面还有一只狗", out)
        self.assertNotIn("我在家呢", out)

    def test_repeat_opener_only_replaces_prefix(self):
        text = "老公我刚刷到一个猫的视频，一只橘猫卡在纸箱里，后面还有一只狗。"
        out = rewrite(text, _plugin(), "我刚才分享过一个猫的视频")
        self.assertNotIn("刚刷到", out)
        self.assertIn("就是刚才那个猫", out)
        self.assertIn("一只橘猫卡在纸箱里", out)
        self.assertIn("后面还有一只狗", out)

    def test_legit_share_is_not_deleted(self):
        for text in ("你快看这个视频，一只橘猫卡在纸箱里。",
                     "给你看个视频，一只橘猫卡在纸箱里。"):
            self.assertEqual(text, rewrite(text, _plugin(), ""))

    def test_legit_share_after_unrelated_chat_is_kept(self):
        text = "你快看这个视频，一只橘猫卡在纸箱里。"
        out = rewrite(text, _plugin(), "哈哈哈 然后在干嘛")
        self.assertIn("你快看这个视频", out)
        self.assertIn("橘猫", out)

    def test_role_reversal_long_object_is_removed(self):
        text = "你发的那个表情包图我还没点开，你再发我一遍？"
        out = rewrite(text, _plugin(), "哈哈哈")
        for word in ("你发的", "再发我", "表情包图"):
            self.assertNotIn(word, out)
        self.assertIn("我在家呢", out)

    def test_role_reversal_keeps_description_tail(self):
        text = "你发的那个猫视频我还没点开，不过那只猫真可爱。"
        out = rewrite(text, _plugin(), "哈哈哈")
        self.assertNotIn("你发的", out)
        self.assertNotIn("再发", out)
        self.assertIn("那只猫真可爱", out)

    def test_followup_context_triggers_opener_without_media_gate(self):
        text = "老公我刚刷到一个兔的视频，一只兔子瘫成一张饼贴在凉席上。"
        ctx = "发的啥 刚才那个兔还有后续，它听见零食袋的声音，一下弹起来。"
        out = rewrite(text, _plugin(), ctx)
        self.assertNotIn("刚刷到", out)
        self.assertIn("就是刚才那个兔", out)
        self.assertIn("一只兔子瘫成一张饼", out)

    def test_thing_repeat_opener_uses_context_label(self):
        text = "老公我刚刷到一件事，楼下奶茶店排队排到马路牙子上。"
        ctx = "哈哈哈 刚才那个奶茶店还有后续，排到一半说珍珠卖完了。"
        out = rewrite(text, _plugin("商场", "outside"), ctx)
        self.assertNotIn("刚刷到", out)
        self.assertIn("就是刚才那个奶茶店", out)
        self.assertIn("楼下奶茶店排队", out)

    def test_scene_parenthetical_does_not_eat_share_description(self):
        text = "老公我刚刷到一个猫的视频（窝在沙发上举着手机），一只橘猫把自己塞进纸箱。"
        out = rewrite(text, _plugin("商场", "outside"), "")
        self.assertNotIn("沙发", out)
        self.assertNotIn("刚刷到", out)
        self.assertIn("猫", out)
        self.assertIn("一只橘猫把自己塞进纸箱", out)

    def test_stale_context_does_not_overwrite_current_object(self):
        text = "老公我刚刷到一个猫的视频，一只橘猫把自己塞进纸箱。"
        out = rewrite(text, _plugin("商场", "outside"),
                      "后来呢 鸟那个样子太可爱了，一只鹦鹉学人说话。")
        self.assertIn("猫", out)
        self.assertNotIn("鸟", out)

    def test_topic_animal_corrects_swap(self):
        text = "哈哈哈这狗也太逗了，它卡在纸箱里出不来。"
        out = rewrite(text, _plugin(), "我刚才分享过一个猫的视频")
        self.assertNotIn("狗", out)
        self.assertIn("猫", out)

    def test_explicit_subject_not_overwritten_by_stale_context(self):
        text = "就是猫的视频，一只橘猫把自己塞进纸箱。"
        ctx = "什么视频 刚才那个兔子还有后续，它听见零食袋的声音，一下弹起来。"
        out = rewrite(text, _plugin(), ctx)
        self.assertIn("猫", out)
        self.assertNotIn("兔", out)

    def test_home_scene_road_parenthetical_keeps_share(self):
        text = "老公我刚刷到一个鸟的视频（在地铁上扶着扶手），一只鹦鹉学人说话。"
        out = rewrite(text, _plugin(), "")
        self.assertNotIn("地铁", out)
        self.assertNotIn("刚刷到", out)
        self.assertIn("鸟", out)
        self.assertIn("鹦鹉", out)

    def test_scene_clause_prune_keeps_share_last_info_point(self):
        """删「刚到家」这种冲突分句，不能把同一句里的分享描述一起删掉。"""
        out = rewrite("我刚到家，一只橘猫把自己塞进纸箱。", _plugin("商场", "outside"), "")
        self.assertNotIn("到家", out)
        self.assertIn("一只橘猫把自己塞进纸箱", out)

    def test_deleted_reply_is_answered_with_share_info_not_generic(self):
        """删空时不能回落成「我到了/我在家呢」，要用她刚分享的最后一个信息点接话。"""
        ctx = _context("哈哈哈", "给你看个图，一只兔子瘫成一张饼贴在凉席上。")
        out = rewrite("（窝在沙发上）我刚洗完澡，趴着呢。", _plugin("商场", "outside"), ctx)
        self.assertNotIn("沙发", out)
        self.assertNotIn("洗澡", out)
        self.assertNotIn("我到了", out)
        self.assertIn("兔子", out)
        self.assertTrue("笑" in out or "哈" in out)

    def test_generic_place_fallback_replaced_by_continuation(self):
        ctx = _context("然后呢然后呢", "刚才那个猫还有后续，它把自己卡住了，越挣扎越出不来。")
        out = rewrite("我还在路上呢，快到了。", _plugin(), ctx)
        self.assertNotIn("我在家呢", out)
        self.assertNotIn("路上", out)
        self.assertIn("卡住", out)
        self.assertTrue(any(word in out for word in ("然后", "后来", "接着")))

    def test_markerless_thing_share_is_still_answered(self):
        """「老公，奶茶店排队…」没有视频/图片/刷到等标记，也要能认出是分享。"""
        ctx = _context("哈哈哈", "老公，楼下奶茶店排队排到马路牙子上，笑死我了。")
        out = rewrite("我在家呢。", _plugin(), ctx)
        self.assertNotIn("我在家呢", out)
        self.assertIn("奶茶店", out)

    def test_where_source_answered_with_share_info(self):
        ctx = _context("在哪看到的",
                       "给你看个图，一只仓鼠把腮帮子塞得鼓鼓的，还在往里塞。（在面馆等面）")
        out = rewrite("你在干嘛呀。", _plugin("面馆", "outside"), ctx)
        self.assertIn("刷到", out)
        self.assertIn("仓鼠", out)

    def test_already_covering_reply_is_untouched(self):
        ctx = _context("哈哈哈", "给你看个图，一只兔子瘫成一张饼贴在凉席上。")
        text = "哈哈哈真的，一只兔子瘫成一张饼贴在凉席上，我笑死了。"
        self.assertEqual(text, rewrite(text, _plugin("面馆", "outside"), ctx))

    def test_ledger_share_used_when_history_missing(self):
        """历史里没有分享句时，用台账里的最后一个信息点接话（同一套 last_info）。"""
        import amo_share_ledger as ledger
        plugin = _ledger_plugin()
        ledger.note(plugin, "老公我刚刷到一个猫的视频，一只橘猫卡在纸箱里")
        out = rewrite("我在家呢。", plugin, "哈哈哈")
        self.assertNotIn("我在家呢", out)
        self.assertIn("橘猫", out)
        self.assertTrue("笑" in out or "哈" in out)

    def test_long_unrelated_question_not_hijacked_by_share(self):
        """「你中午吃的什么」这种长问句不按分享接话处理，不许拿旧分享乱答。"""
        ctx = _context("你中午吃的什么？", "给你看个图，一只兔子瘫成一张饼贴在凉席上。")
        self.assertEqual("嗯嗯，好呀。", rewrite("嗯嗯，好呀。", _plugin("面馆", "outside"), ctx))

    def test_ordinary_chat_without_share_keeps_reply(self):
        self.assertEqual("嗯嗯，好呀。", rewrite("嗯嗯，好呀。", _plugin(), "哈哈哈"))

    def test_share_role_pattern_does_not_match_legit_opener(self):
        self.assertFalse(_SHARE_ROLE.search("你快看这个视频，猫在跳"))
        self.assertFalse(_SHARE_ROLE.search("给你看个视频，猫在跳"))
        self.assertTrue(_SHARE_ROLE.search("你发的那个猫视频我还没点开，你再发我一遍？"))
        self.assertTrue(_SHARE_OPENER.search("老公我刚刷到一件事"))


if __name__ == "__main__":
    unittest.main()
