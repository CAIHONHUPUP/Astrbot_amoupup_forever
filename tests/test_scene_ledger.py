# -*- coding: utf-8 -*-
"""task-17 场景账本 P0 验收：scene_at / write 硬约束 / 对称黑名单 / stale 上限 /
照片单 zone + 否定词 / presence 改读账本。"""
from __future__ import annotations

import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_scene_ledger as L  # noqa: E402
import amo_state_common as S  # noqa: E402
import amo_state_snapshot as Snap  # noqa: E402
import amo_presence as P  # noqa: E402
import photo_prompt as PP  # noqa: E402

NOW = datetime.datetime(2026, 11, 18, 23, 0, 0)   # 2026-11-18 周三 23:00（病假事故时点）


class FakeDb:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def kv_keys(self, prefix="", limit=200, offset=0):
        keys = [key for key in sorted(self.kv) if str(key).startswith(str(prefix or ""))]
        return keys[offset:offset + limit]


class FakeState:
    def __init__(self, activity=None, reason="schedule", sick=False, leave=False):
        self.activity = dict(activity or {})
        self.reason = reason
        self.sick = sick
        self.leave = leave

    def current_activity_info(self):
        return dict(self.activity), self.reason

    def current_activity(self):
        return dict(self.activity)

    def on_sick_leave(self, day=None):
        return bool(self.sick)

    def sick_decision(self, day=None):
        return {"action": "take_sick_leave"} if self.sick else {}

    def _trip_day_kind(self, day=None):
        return ""


class FakePlugin:
    def __init__(self, state, kv=None):
        self.state = state
        self.db = FakeDb(kv)


def make_plugin(place="公司", zone="office", activity="在工位上忙工作",
                reason="schedule", sick=False, leave=False, kv=None):
    return FakePlugin(FakeState({"place": place, "zone": zone, "activity": activity},
                                reason=reason, sick=sick, leave=leave), kv)


class ConstraintTests(unittest.TestCase):
    def test_sick_leave_constraints(self):
        p = make_plugin(sick=True)
        cons = L.constraints(p, "2026-11-18", NOW)
        self.assertEqual(cons["kind"], "sick_leave")
        self.assertIn("家", cons["allowed_places"])
        self.assertIn("医院", cons["allowed_places"])

    def test_scene_at_clamps_company_on_sick_leave(self):
        p = make_plugin(place="公司", zone="office", activity="在工位上忙工作", sick=True)
        scene = L.scene_at(p, NOW)
        self.assertEqual(scene["place"], "家")
        self.assertEqual(scene["zone"], "home")
        self.assertEqual(scene["source"], "leave_constraint")
        self.assertEqual(scene["quality"], L.QUALITY_COVERING)
        audit = L.audit_entries(p)
        self.assertTrue(any(row.get("action") == "read_clamp" for row in audit))

    def test_write_rejects_company_on_leave_and_audits(self):
        p = make_plugin(sick=True)
        result = L.write(p, {"place": "公司", "zone": "office", "activity": "加班"}, now=NOW)
        self.assertFalse(result["ok"])
        self.assertTrue(result["violations"])
        self.assertEqual(result["record"]["quality"], L.QUALITY_UNKNOWN)
        audit = L.audit_entries(p)
        self.assertFalse(audit[-1]["ok"])
        self.assertEqual(audit[-1]["action"], "write")

    def test_write_allows_home_on_leave(self):
        p = make_plugin(sick=True)
        result = L.write(p, {"place": "家", "zone": "home", "activity": "请假在家"}, now=NOW)
        self.assertTrue(result["ok"])

    def test_leave_marker_from_kv(self):
        p = make_plugin(place="家", zone="home", kv={
            "mood_applied:leave:2026-11-18:事假一天:mood": True})
        cons = L.constraints(p, "2026-11-18", NOW)
        self.assertEqual(cons["kind"], "leave")

    def test_unknown_quality_when_no_activity(self):
        p = FakePlugin(FakeState({}, reason="gap_unknown"))
        scene = L.scene_at(p, NOW)
        self.assertEqual(scene["quality"], L.QUALITY_UNKNOWN)
        self.assertEqual(scene["place"], "")
        guarded = L.scene_guard("我在商场试米白开衫", scene)
        self.assertNotIn("商场", guarded["text"])
        self.assertTrue(guarded["changed"])


class SymmetricBlacklistTests(unittest.TestCase):
    def test_home_bans_outdoor_places(self):
        for text in ("我在商场试米白开衫", "我在小馆等上菜", "刚在食堂吃完饭"):
            self.assertTrue(Snap.blacklist_hit(text, "home"), text)

    def test_road_bans_home_props(self):
        for text in ("我在沙发上翻了个身", "裹着被子", "在厨房煮面"):
            self.assertTrue(Snap.blacklist_hit(text, "road"), text)

    def test_office_bans_home_props(self):
        self.assertTrue(Snap.blacklist_hit("窝在沙发里", "office"))

    def test_negation_not_hit(self):
        self.assertEqual(Snap.blacklist_hit("画面里只有车厢：不是沙发、不是床", "road"), [])
        self.assertEqual(Snap.blacklist_hit("不要地铁、不要公交", "home"), [])

    def test_force_rewrite_home_outdoor(self):
        out = Snap.force_rewrite("我在商场试衣服", "home")
        self.assertNotIn("商场", out)
        self.assertEqual(Snap.blacklist_hit(out, "home"), [])

    def test_force_rewrite_keeps_negation(self):
        out = Snap.force_rewrite("不是沙发，是地铁座椅", "road")
        self.assertIn("不是沙发", out)


class StaleSlotTests(unittest.TestCase):
    class _Self:
        def __init__(self):
            self.db = FakeDb()
            self.p = None
            self.logs = []

        def _photo_info(self, message):
            self.logs.append(message)

    ITEMS = [
        {"start": "14:00", "end": "17:00", "activity": "柳如烟下午逛商场买冬衣",
         "place": "商场", "zone": "mall", "category": "social"},
    ]

    def _current(self, hhmm):
        hour, minute = [int(part) for part in hhmm.split(":")]
        return hour * 60 + minute

    def test_stale_within_30_minutes_continues(self):
        fake = self._Self()
        data = S._pick_stale(fake, self.ITEMS, self._current("17:20"))
        self.assertEqual(data["_source"], "stale_slot")
        self.assertEqual(data["zone"], "mall")
        self.assertLessEqual(data["_gap_minutes"], 30)

    def test_stale_beyond_30_minutes_is_unknown(self):
        fake = self._Self()
        data = S._pick_stale(fake, self.ITEMS, self._current("21:00"))
        self.assertEqual(data["_source"], "gap_unknown")
        self.assertEqual(data["zone"], "unknown")
        self.assertEqual(data["place"], "")
        self.assertTrue(any("gap_unknown" in line for line in fake.logs))


class PresenceLedgerTests(unittest.TestCase):
    def test_presence_reads_ledger_and_clamps_leave(self):
        p = make_plugin(place="公司", zone="office", activity="在工位上忙工作", sick=True)
        scene = P.current_scene(p)
        self.assertEqual(scene["place"], "家")
        self.assertEqual(scene["zone"], "home")
        self.assertEqual(scene["quality"], L.QUALITY_COVERING)

    def test_presence_unknown_has_unknown_quality(self):
        p = FakePlugin(FakeState({}, reason="gap_unknown"))
        scene = P.current_scene(p)
        self.assertEqual(scene["quality"], L.QUALITY_UNKNOWN)


class _Plan:
    def __init__(self, zone):
        self.zone = zone
        self.debug = {}
        self.scene = ""
        self.where = ""
        self.pose = None
        self.pose_ready = False
        self.pose_line = ""
        self.room_ref = ""


class PhotoPromptZoneTests(unittest.TestCase):
    def test_bed_detection_ignores_negation(self):
        where = ("真实地铁车厢：车壁两侧是一条硬质长条座椅（不是沙发、不是床），"
                 "头顶一根横向扶手杆")
        self.assertFalse(PP._bed_like(where, "站在车厢里", "road"))

    def test_bed_detection_still_true_for_home(self):
        self.assertTrue(PP._bed_like("家里的卧室，床铺、被子和床头的小灯", "", "home_bed"))

    def test_finalize_drops_home_clause_for_road(self):
        prompt = ("自拍，在真实地铁车厢，站在车厢里，手机本体和摄像头不要入镜，"
                  "她正躺在/坐在床上或沙发上，盖着被子，外面下雨")
        fixed, primary = PP._finalize_prompt_zone(prompt, "真实地铁车厢", _Plan("road"))
        self.assertEqual(primary, "road")
        self.assertNotIn("床上或沙发上", fixed)
        self.assertIn("地铁", fixed)
        check = L.verify_photo_prompt(fixed, {"zone": "road", "place": "真实地铁车厢"})
        self.assertTrue(check["ok"], check)

    def test_finalize_drops_outdoor_clause_for_home(self):
        prompt = ("自拍，在楼下小馆，生活里的日常陈设，背景是她的客厅，"
                  "房间里只能出现这些家具：沙发、抱枕、茶几")
        fixed, primary = PP._finalize_prompt_zone(prompt, "楼下小馆", _Plan("restaurant"))
        self.assertEqual(primary, "restaurant")
        self.assertNotIn("沙发", fixed)
        check = L.verify_photo_prompt(fixed, {"zone": "restaurant", "place": "楼下小馆"})
        self.assertTrue(check["ok"], check)

    def test_verify_photo_prompt_detects_two_zones(self):
        check = L.verify_photo_prompt("在商场试衣间，背景是家里的客厅和沙发", {"zone": "mall"})
        self.assertFalse(check["ok"])
        self.assertIn("home", check["extra_zones"])


if __name__ == "__main__":
    unittest.main()
