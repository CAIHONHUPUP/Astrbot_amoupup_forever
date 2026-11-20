# -*- coding: utf-8 -*-
"""礼物心愿质量守卫（2026-11-19）。

别人新装后，礼物墙「她最近想要」出现「唔 / 腮帮子鼓鼓 / 呆毛满足地抖了抖 / 店当然」：
心愿是从**她自己的回复**里抽的（含动作旁白），模型 JSON 只校验 confidence 和长度，
规则兜底还会往前一个分句找候选，于是语气词/表情/旁白/句子碎片都进了心愿单。
这里守住：只有 2-12 字的具体物品名才算心愿；旁白在抽取前剥掉；老数据会被清掉。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import gift_engine  # noqa: E402
from gift_engine import GiftEngine, extract_wish_regex, strip_narration, valid_wish_item  # noqa: E402


class WishItemTests(unittest.TestCase):
    def test_junk_from_the_incident_is_rejected(self):
        for junk in ("唔", "嗯", "啊", "呆毛满足地抖了抖", "腮帮子鼓鼓", "店当然",
                     "好想要", "了", "的", "笑了一下", "抖了抖"):
            self.assertFalse(valid_wish_item(junk), junk)

    def test_real_objects_are_accepted(self):
        for item in ("围巾", "蓝色鲸鱼小挂件", "豆沙色口红", "奶白色毛衣",
                     "吊带内衣", "小熊睡衣", "格纹羊绒围巾", "银色小表盘手表"):
            self.assertTrue(valid_wish_item(item), item)

    def test_narration_is_stripped_before_extraction(self):
        self.assertEqual(strip_narration("（呆毛满足地抖了抖）唔，好想要那条围巾"),
                         "唔，好想要那条围巾")
        self.assertEqual(strip_narration("【开心】想要小挂件"), "想要小挂件")
        self.assertEqual(strip_narration("（只有旁白）"), "")

    def test_regex_fallback_does_not_take_previous_clause(self):
        # 以前 index>0 的分支会往前一个分句取候选，切出「店当然」这种碎片
        for text in ("商店当然有啊，好想要一条围巾", "唔，好想要蓝色鲸鱼小挂件"):
            item = extract_wish_regex(text)
            self.assertNotIn(item, ("店当然", "唔", "有啊"))
            self.assertTrue(valid_wish_item(item) or item == "", (text, item))

    def test_llm_json_with_junk_item_is_dropped(self):
        self.assertEqual(GiftEngine._parse_wish_json('{"item": "唔", "confidence": 0.9}'), "")
        self.assertEqual(
            GiftEngine._parse_wish_json('{"item": "呆毛满足地抖了抖", "confidence": 0.9}'), "")
        self.assertEqual(
            GiftEngine._parse_wish_json('{"item": "蓝色鲸鱼小挂件", "confidence": 0.8}'),
            "蓝色鲸鱼小挂件")

    def test_existing_dirty_wishes_are_sanitized(self):
        data = {"wishes": [
            {"item": "唔"}, {"item": "腮帮子鼓鼓"},
            {"item": "蓝色鲸鱼小挂件"}, {"item": "店当然"},
        ]}
        self.assertTrue(GiftEngine._sanitize_wishes(data))
        self.assertEqual([w["item"] for w in data["wishes"]], ["蓝色鲸鱼小挂件"])
        self.assertFalse(GiftEngine._sanitize_wishes(data))

    def test_recent_wishes_hides_old_dirty_entries(self):
        # 直接构造一个只读引擎：不连插件、不落库，只验列表过滤
        from unittest import mock

        engine = object.__new__(GiftEngine)
        engine._lock = __import__("threading").RLock()
        now = __import__("time").time()
        engine._data = {"wishes": [
            {"item": "唔", "status": "wanted", "ts": now},
            {"item": "围巾", "status": "wanted", "ts": now},
        ], "gifts": [], "meta": {}}
        with mock.patch.object(GiftEngine, "ttl", new_callable=mock.PropertyMock,
                               return_value=3600):
            self.assertEqual([w["item"] for w in engine.recent_wishes(limit=5)], ["围巾"])


if __name__ == "__main__":
    unittest.main()
