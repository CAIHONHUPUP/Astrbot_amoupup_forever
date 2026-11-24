# -*- coding: utf-8 -*-
"""居住关系（同居/半同居/分居）在场判定（2026-11-21 补测）。

amo_household 之前零测试。这里锁住：三档矩阵、他上班、出差覆盖、迁移门槛、
过夜计数幂等、apart 文案回落。
"""
from __future__ import annotations

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


class FakeDB:
    """字典背书的 DB 桩：household 只用到 kv 读写。"""

    def __init__(self, kv=None):
        self.data = dict(kv or {})
        # 兼容直接摸 .kv 的写法（同一个 dict，不复制）
        self.kv = self.data
        self.writes = []

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.writes.append((key, value))
        self.data[key] = value

    def kv_delete(self, key):
        self.data.pop(key, None)


class FakeState:
    """状态桩：household 只读/清 worldview。"""

    def __init__(self, **over):
        self.worldview = {}
        for key, value in over.items():
            setattr(self, key, value)


class FakePlugin:
    """插件桩：cfg_* / db / state 就够 household 用。"""

    def __init__(self, kv=None, config=None, **cfg):
        self.db = FakeDB(kv)
        self.config = dict(config or {})
        self.config.update(cfg)
        self.plugin_dir = ROOT
        self.state = FakeState()

    def cfg(self, key, default=None):
        return self.config.get(key, default)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))


WED = "2026-11-18"   # 工作日
SAT = "2026-11-21"   # 周六
SUN = "2026-11-22"   # 周日


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin(config={"household_enabled": True})

    def _scene(self, stage, day, start, end, place="家", zone="home"):
        self.plugin.db.kv_set("household", {"stage": stage, "since": 0, "night_count": 0})
        return hh.scene_at(self.plugin, day, start, end, place, zone=zone)

    def test_apart_alone_at_home(self):
        scene = self._scene(hh.STAGE_APART, WED, "20:00", "21:00")
        self.assertEqual("alone", scene.presence)
        self.assertFalse(scene.partner)

    def test_apart_out_is_alone(self):
        scene = self._scene(hh.STAGE_APART, WED, "10:00", "11:00", "公司", "office")
        self.assertEqual("alone", scene.presence)

    def test_together_home_evening_partner(self):
        scene = self._scene(hh.STAGE_TOGETHER, WED, "20:00", "21:00")
        self.assertEqual("home", scene.presence)
        self.assertTrue(scene.partner)

    def test_together_home_work_hours_alone(self):
        scene = self._scene(hh.STAGE_TOGETHER, WED, "10:00", "11:00")
        self.assertEqual("alone", scene.presence)

    def test_together_weekend_daytime_partner(self):
        scene = self._scene(hh.STAGE_TOGETHER, SAT, "10:00", "11:00")
        self.assertEqual("home", scene.presence)

    def test_together_out_is_alone(self):
        scene = self._scene(hh.STAGE_TOGETHER, WED, "15:00", "16:00", "东莞·商场", "mall")
        self.assertEqual("alone", scene.presence)

    def test_part_time_weekday_alone(self):
        scene = self._scene(hh.STAGE_PART_TIME, WED, "20:00", "21:00")
        self.assertEqual("alone", scene.presence)

    def test_part_time_saturday_partner(self):
        scene = self._scene(hh.STAGE_PART_TIME, SAT, "10:00", "11:00")
        self.assertEqual("home", scene.presence)

    def test_part_time_sunday_late_alone(self):
        scene = self._scene(hh.STAGE_PART_TIME, SUN, "22:30", "23:30")
        self.assertEqual("alone", scene.presence)

    def test_away_override_beats_together(self):
        self._scene(hh.STAGE_TOGETHER, WED, "20:00", "21:00")
        hh.set_away(self.plugin, True, reason="出差")
        scene = hh.scene_at(self.plugin, WED, "20:00", "21:00", "家", zone="home")
        self.assertEqual("alone", scene.presence)
        self.assertEqual("他出差", scene.reason)

    def test_away_cleared(self):
        hh.set_away(self.plugin, True, reason="出差")
        hh.set_away(self.plugin, False)
        self.assertFalse(hh.away_state(self.plugin)["away"])

    def test_away_with_days_sets_until(self):
        data = hh.set_away(self.plugin, True, reason="出差", days=2)
        self.assertGreater(data["until"], data["since"])
        self.assertTrue(hh.away_state(self.plugin)["away"])

    def test_away_expires(self):
        hh.set_away(self.plugin, True, reason="出差", days=1)
        state = self.plugin.db.kv_get("partner_away", {})
        state["until"] = 1.0
        self.plugin.db.kv_set("partner_away", state)
        self.assertFalse(hh.away_state(self.plugin)["away"])

    def test_holiday_means_not_at_work(self):
        self.assertFalse(hh.partner_at_work(0, 10 * 60, 11 * 60, holiday=True))
        self.assertTrue(hh.partner_at_work(0, 10 * 60, 11 * 60, holiday=False))

    def test_2200_block_does_not_count_as_night(self):
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_PART_TIME, "night_count": 0})
        scene = hh.scene_at(self.plugin, SAT, "22:00", "23:00", "家", zone="home")
        hh.count_night(self.plugin, SAT, scene)
        self.assertEqual(0, hh.load(self.plugin)["night_count"])

    def test_boundary_says_he_is_user(self):
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_TOGETHER})
        self.assertIn("用户本人", hh.wording("boundary", plugin=self.plugin))


class WordingTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin(config={"household_enabled": True})

    def test_apart_boundary_is_the_residence_rule(self):
        # 2026-11-24 收口：分居档的居住边界从 ACTION_OWNERSHIP_RULES 迁进 wording("boundary")，
        # 所以 apart 不再是空串；逐字节比对见 test_prompt_ownership_20261124。
        import amo_prompt_registry as pr
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_APART})
        self.assertEqual(pr.RESIDENCE_APART_RULE, hh.wording("boundary", plugin=self.plugin))

    def test_together_has_boundary(self):
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_TOGETHER})
        self.assertIn("同居", hh.wording("boundary", plugin=self.plugin))

    def test_presence_line_apart(self):
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_APART})
        self.assertIn("同城不同住", hh.presence_line(self.plugin))


class WorldviewTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin(config={"household_enabled": True})

    def test_apart_keeps_worldview(self):
        data = {"daily_life": "一个人住，约会儿才见面。", "world_rules": "同城不同住。"}
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_APART})
        self.assertEqual(data, hh.apply_worldview(self.plugin, data))

    def test_together_removes_conflicting_lines(self):
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_TOGETHER})
        data = {"daily_life": "她住出租屋，老公同城但不同住。周末会一起去超市。",
                "world_rules": "同城不同住，约会才见面。", "summary": "普通女孩。"}
        out = hh.apply_worldview(self.plugin, data)
        self.assertNotIn("同城但不同住", out["daily_life"])
        self.assertNotIn("同城不同住", out["world_rules"])
        self.assertIn("周末会一起去超市", out["daily_life"])
        self.assertIn("住在一起", out["world_rules"])

    def test_transition_clears_in_memory_worldview(self):
        self.plugin.state.worldview = {"summary": "旧世界观"}
        hh.transition(self.plugin, hh.STAGE_TOGETHER, force=True)
        self.assertEqual({}, self.plugin.state.worldview)


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin(config={"household_enabled": True,
                                         "household_to_together_nights": 2})

    def test_apart_to_together_blocked(self):
        ok, msg = hh.can_transition(self.plugin, hh.STAGE_TOGETHER)
        self.assertFalse(ok)
        self.assertIn("半同居", msg)

    def test_force_allows(self):
        ok, _msg = hh.can_transition(self.plugin, hh.STAGE_TOGETHER, force=True)
        self.assertTrue(ok)

    def test_part_time_to_together_needs_nights(self):
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_PART_TIME, "night_count": 0})
        ok, msg = hh.can_transition(self.plugin, hh.STAGE_TOGETHER)
        self.assertFalse(ok)
        self.assertIn("过夜", msg)

    def test_nights_ready_allows(self):
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_PART_TIME,
                                            "night_count": 2, "ready": True})
        ok, _msg = hh.can_transition(self.plugin, hh.STAGE_TOGETHER)
        self.assertTrue(ok)


class NightCountTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin(config={"household_enabled": True,
                                         "household_to_together_nights": 2})

    def test_counts_once_per_day(self):
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_PART_TIME, "night_count": 0})
        scene = hh.scene_at(self.plugin, SAT, "23:00", "24:00", "家", zone="home")
        hh.count_night(self.plugin, SAT, scene)
        hh.count_night(self.plugin, SAT, scene)
        self.assertEqual(1, hh.load(self.plugin)["night_count"])

    def test_daytime_does_not_count(self):
        self.plugin.db.kv_set("household", {"stage": hh.STAGE_PART_TIME, "night_count": 0})
        scene = hh.scene_at(self.plugin, SAT, "14:00", "15:00", "家", zone="home")
        hh.count_night(self.plugin, SAT, scene)
        self.assertEqual(0, hh.load(self.plugin)["night_count"])


class FixApartTextTests(unittest.TestCase):
    def _plugin(self, stage):
        plugin = FakePlugin(config={"household_enabled": True})
        plugin.db.kv_set("household", {"stage": stage, "since": 0, "night_count": 0})
        return plugin

    def test_rewrites_apart_phrases_when_together(self):
        text = "明明老公在同城另一头，要是隔几条街，一起吃饭多好"
        out = hh.fix_apart_text(self._plugin(hh.STAGE_TOGETHER), text)
        self.assertNotIn("同城另一头", out)
        self.assertNotIn("隔几条街", out)

    def test_keeps_text_when_apart(self):
        text = "老公在同城另一头"
        self.assertEqual(text, hh.fix_apart_text(self._plugin(hh.STAGE_APART), text))


if __name__ == "__main__":
    unittest.main()
