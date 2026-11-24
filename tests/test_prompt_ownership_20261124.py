# -*- coding: utf-8 -*-
"""居住/动作归属提示词拆分后的零漂移锁（2026-11-24 P3）。

拆分前这些文字全在 amo_prompt_registry.ACTION_OWNERSHIP_RULES 一个大块里；
2026-11-24 按 owner 归位：

- 分居档居住边界 -> amo_household.wording("boundary")（apart 也有文案了）
- 拍照执行者     -> PHOTO_REQUEST_RULE（只在拍照/自拍轮注入）
- 住处·绝不搬走  -> amo_persona_pack.card_text（人设卡）
- 其余（假拍照红线/发图策略/动作归属）留在 ACTION_OWNERSHIP_RULES

本测试用 tests/golden/ownership_golden.json（拆分前原文快照）逐字节比对，
任何一段被改写、被复制两遍、或顺序错位都会红。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_household as hh  # noqa: E402
import amo_prompt_registry as pr  # noqa: E402
from amo_persona_pack import card_text  # noqa: E402

GOLDEN_PATH = os.path.join(TESTS, "golden", "ownership_golden.json")
with open(GOLDEN_PATH, encoding="utf-8") as _fh:
    GOLDEN = json.load(_fh)
SECTIONS = GOLDEN["sections"]


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def kv_delete(self, key):
        self.kv.pop(key, None)


class FakeState:
    worldview = {}

    def current_activity(self):
        return {"activity": "在家休息", "place": "家", "zone": "home", "category": "rest"}


class FakePlugin:
    def __init__(self, stage=None):
        self.db = FakeDB()
        self.state = FakeState()
        if stage:
            self.db.kv_set("household", {"stage": stage, "since": 0, "night_count": 0})

    def cfg_bool(self, key, default=False):
        return default

    def cfg_str(self, key, default=""):
        return default

    def cfg_int(self, key, default=0):
        return default


class ResidenceSplitTests(unittest.TestCase):
    def test_apart_boundary_is_byte_identical(self):
        plugin = FakePlugin(hh.STAGE_APART)
        self.assertEqual(SECTIONS["residence_apart"],
                         hh.wording("boundary", plugin=plugin))

    def test_other_apart_keys_still_fall_back(self):
        plugin = FakePlugin(hh.STAGE_APART)
        for key in ("worldview", "schedule_boundary", "fact_pool", "date_ending", "morning"):
            self.assertEqual("", hh.wording(key, plugin=plugin), key)

    def test_part_time_and_together_wording_unchanged(self):
        for stage in (hh.STAGE_PART_TIME, hh.STAGE_TOGETHER):
            plugin = FakePlugin(stage)
            self.assertEqual(GOLDEN["wording"][stage], hh.wording("boundary", plugin=plugin), stage)

    def test_wording_reads_stage_only(self):
        """给了 scene 就用 scene.stage，不碰 db（固定块不该每轮算 scene_now）。"""
        plugin = FakePlugin(hh.STAGE_APART)
        scene = hh.Scene(hh.STAGE_TOGETHER, "2026-11-24", 0, 60, "家", "home",
                         hh.PRESENCE_HOME, True, "客厅", "同居·在家")
        self.assertEqual(GOLDEN["wording"][hh.STAGE_TOGETHER],
                         hh.wording("boundary", scene=scene, plugin=plugin))
        self.assertEqual("", hh.wording("boundary"))


class ActionOwnershipSplitTests(unittest.TestCase):
    def test_ownership_rules_are_the_remaining_sections_in_order(self):
        expected = (SECTIONS["photo_fake_redline"] + SECTIONS["photo_send_policy"]
                    + SECTIONS["action_ownership_intro"] + SECTIONS["action_ownership_rest"])
        self.assertEqual(expected, pr.ACTION_OWNERSHIP_RULES)

    def test_executor_bullet_lives_in_photo_request(self):
        rule = pr.PHOTO_REQUEST_RULE
        self.assertEqual(SECTIONS["photo_executor"],
                         pr.PHOTO_EXECUTOR_RULE)
        self.assertEqual(1, rule.count(SECTIONS["photo_executor"]))
        self.assertTrue(rule.startswith(GOLDEN["photo_request_pre_split"]))
        self.assertNotIn(SECTIONS["photo_executor"], pr.ACTION_OWNERSHIP_RULES)

    def test_stay_rule_lives_in_persona_card(self):
        text = card_text({"card": {"core": "测试内核", "forbidden": []}})
        self.assertEqual(1, text.count("【住处·绝不自主搬走】"))
        self.assertIn(SECTIONS["stay_in_place"].rstrip("\n"), text)
        self.assertNotIn("绝不自主搬走", pr.ACTION_OWNERSHIP_RULES)

    def test_golden_is_lossless(self):
        """七段拼回去必须逐字节等于拆分前原文（golden 自身没被截断/漏段）。"""
        order = ("residence_apart", "photo_fake_redline", "photo_send_policy",
                 "action_ownership_intro", "photo_executor", "action_ownership_rest",
                 "stay_in_place")
        self.assertEqual(GOLDEN["whole_pre_split"],
                         "".join(SECTIONS[name] for name in order))

    def test_every_section_has_exactly_one_owner(self):
        """每段在「常驻块 + 人设卡」里只能出现一次；执行者条款只走拍照块。"""
        apart = hh.wording("boundary", plugin=FakePlugin(hh.STAGE_APART))
        card = card_text({"card": {"core": "测试内核", "forbidden": []}})
        resident = apart + pr.ACTION_OWNERSHIP_RULES + card
        for name in ("residence_apart", "photo_fake_redline", "photo_send_policy",
                     "action_ownership_intro", "action_ownership_rest"):
            self.assertEqual(1, resident.count(SECTIONS[name]), name)
        self.assertIn(SECTIONS["stay_in_place"].rstrip("\n"), resident)
        self.assertEqual(0, resident.count(SECTIONS["photo_executor"]))
        self.assertEqual(1, pr.PHOTO_REQUEST_RULE.count(SECTIONS["photo_executor"]))

    def test_amo_llm_uses_single_source(self):
        import amo_llm
        self.assertEqual(pr.RESIDENCE_APART_RULE, amo_llm.APART_RESIDENCE_RULE)
        self.assertEqual(pr.ACTION_OWNERSHIP_RULES, amo_llm.ACTION_OWNERSHIP_RULES)
        source = open(os.path.join(ROOT, "amo_llm.py"), encoding="utf-8").read()
        self.assertNotIn("or ACTION_OWNERSHIP_RULES", source)


if __name__ == "__main__":
    unittest.main()
