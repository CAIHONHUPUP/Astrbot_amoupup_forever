# -*- coding: utf-8 -*-
"""唯一在场/沉默/照片策略（amo_presence）的闸门验收（2026-11-22 架构收口）。

对应真实飞书记录：
- 最后一条用户消息超过 6 小时：禁催问/逛街/自拍/未回复追问，只留问候和时间点提醒；
- 逛街只在 mall/street/scenic 或活动明确逛街/试衣时触发，在家不触发；
- 照片绑定当前事件：逛街 pending 但不在商场不发；商场 → 商场/试衣间；同一 event_id 最多一张图。
"""
from __future__ import annotations

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_presence as P  # noqa: E402


class _State:
    def __init__(self, scene):
        self._scene = dict(scene or {})

    def current_activity(self):
        return dict(self._scene)


class _Db:
    def __init__(self, kv=None, profiles=None):
        self.kv = dict(kv or {})
        self.profiles = list(profiles or [])

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def proactive_profiles(self, limit=100):
        return [dict(row) for row in self.profiles[:limit]]


class _Plugin:
    def __init__(self, scene=None, kv=None, profiles=None):
        self.state = _State(scene or {})
        self.db = _Db(kv, profiles)


def _scene(place="家", zone="home", activity="在家休息", **extra):
    data = {"place": place, "zone": zone, "activity": activity}
    data.update(extra)
    return data


def _plugin(scene=None, kv=None, last_seen=None):
    profiles = []
    if last_seen is not None:
        profiles = [{"uid": "u1", "last_seen": last_seen}]
    return _Plugin(scene or _scene(), kv, profiles)


NOW = time.time()


class SilenceTests(unittest.TestCase):
    def test_silence_hours_unknown_is_zero(self):
        self.assertEqual(P.silence_hours(0), 0.0)
        self.assertEqual(P.silence_hours(None), 0.0)

    def test_silence_hours_basic(self):
        self.assertAlmostEqual(P.silence_hours(NOW - 3600, NOW), 1.0, places=3)
        self.assertEqual(P.silence_hours(NOW + 100, NOW), 0.0)

    def test_is_silent_after_six_hours(self):
        p = _plugin(last_seen=NOW - 5 * 3600)
        self.assertFalse(P.is_silent(p, now=NOW))
        p = _plugin(last_seen=NOW - 7 * 3600)
        self.assertTrue(P.is_silent(p, now=NOW))
        self.assertTrue(P.is_silent(_plugin(), now=NOW) is False, "没记录不能误杀")

    def test_silence_blocks_engagement_kinds(self):
        p = _plugin(last_seen=NOW - 7 * 3600)
        for kind in ("nudge", "shopping", "tryon", "selfie", "photo", "followup",
                     "greet", "share", "slack", "commute", "game", "meal", "offwork"):
            self.assertFalse(P.can_proactive(p, {"uid": "u1", "last_seen": NOW - 7 * 3600},
                                             kind, now=NOW), kind)

    def test_silence_allows_greetings_and_timed_reminders(self):
        p = _plugin(last_seen=NOW - 7 * 3600)
        profile = {"uid": "u1", "last_seen": NOW - 7 * 3600}
        for kind in ("morning", "goodnight", "schedule_reminder"):
            self.assertTrue(P.can_proactive(p, profile, kind, now=NOW), kind)

    def test_silence_keeps_safety_care(self):
        p = _plugin(last_seen=NOW - 7 * 3600)
        profile = {"uid": "u1", "last_seen": NOW - 7 * 3600}
        for kind in ("unwell", "comfort", "birthday"):
            self.assertTrue(P.can_proactive(p, profile, kind, now=NOW), kind)

    def test_fresh_user_allows_engagement(self):
        fresh = {"uid": "u1", "last_seen": NOW - 600}
        p = _plugin(last_seen=NOW - 600)
        self.assertTrue(P.can_proactive(p, fresh, "nudge", now=NOW))
        self.assertTrue(P.can_proactive(p, fresh, "greet", now=NOW))


class ShopLocationTests(unittest.TestCase):
    def test_home_never_shops(self):
        self.assertFalse(P.can_shop(_plugin(_scene())))
        # 在家就算活动写着「试新衣服」也不能触发逛街（家优先）
        self.assertFalse(P.can_shop(_plugin(_scene(activity="试新衣服"))))

    def test_shop_zones(self):
        for zone in ("mall", "street", "scenic"):
            self.assertTrue(P.can_shop(_plugin(_scene(place="外面", zone=zone))), zone)

    def test_explicit_shopping_activity_without_zone(self):
        self.assertTrue(P.can_shop(_plugin(_scene(place="路上", zone="road", activity="逛街"))))
        self.assertTrue(P.can_shop(_plugin(_scene(place="外面", zone="", activity="试衣"))))
        self.assertFalse(P.can_shop(_plugin(_scene(place="公司", zone="office", activity="开会"))))

    def test_zone_inferred_from_place(self):
        p = _plugin({"place": "商场", "zone": "", "activity": "逛逛"})
        self.assertEqual(P.current_scene(p)["zone"], "mall")
        self.assertTrue(P.can_shop(p))


class SelfiePhotoGateTests(unittest.TestCase):
    def _pending(self):
        return {"shopping_prompt": {"line": "在商场试衣服", "until": time.time() + 600}}

    def test_silent_user_no_selfie_no_photo(self):
        p = _plugin(last_seen=NOW - 7 * 3600)
        profile = {"uid": "u1", "last_seen": NOW - 7 * 3600}
        self.assertFalse(P.can_selfie(p, profile, now=NOW))
        self.assertFalse(P.can_photo(p, profile, now=NOW))

    def test_shopping_pending_at_home_blocks_selfie_and_photo(self):
        p = _plugin(_scene(), self._pending(), last_seen=NOW - 60)
        profile = {"uid": "u1", "last_seen": NOW - 60}
        self.assertFalse(P.can_selfie(p, profile, now=NOW))
        self.assertFalse(P.can_photo(p, profile, now=NOW))

    def test_shopping_pending_in_mall_allows(self):
        p = _plugin(_scene(place="万象城", zone="mall", activity="逛街试衣服"),
                    self._pending(), last_seen=NOW - 60)
        profile = {"uid": "u1", "last_seen": NOW - 60}
        self.assertTrue(P.can_selfie(p, profile, now=NOW))
        self.assertTrue(P.can_photo(p, profile, now=NOW))

    def test_plain_home_selfie_allowed(self):
        p = _plugin(_scene(), last_seen=NOW - 60)
        profile = {"uid": "u1", "last_seen": NOW - 60}
        self.assertTrue(P.can_selfie(p, profile, now=NOW))
        self.assertTrue(P.can_photo(p, profile, now=NOW))

    def test_shopping_kind_offsite_blocked(self):
        p = _plugin(_scene())
        self.assertFalse(P.can_photo(p, {"uid": "u1"}, kind="shopping", now=NOW))


class PhotoBindingTests(unittest.TestCase):
    def _pending(self):
        return {"shopping_prompt": {"line": "在商场试开衫", "until": time.time() + 600}}

    def test_silent_binding_fails(self):
        p = _plugin(_scene())
        binding = P.photo_binding(p, {"uid": "u1", "last_seen": NOW - 7 * 3600}, now=NOW)
        self.assertFalse(binding["ok"])
        self.assertEqual(binding["reason"], "silence_6h")

    def test_pending_offsite_binding_fails(self):
        p = _plugin(_scene(), self._pending())
        binding = P.photo_binding(p, {"uid": "u1", "last_seen": NOW - 60}, now=NOW)
        self.assertFalse(binding["ok"])
        self.assertEqual(binding["reason"], "shopping_pending_offsite")

    def test_pending_in_mall_binds_fitting_scene_and_event(self):
        scene = _scene(place="万象城", zone="mall", activity="逛街试开衫",
                       event_id="ev_mall_1", category="life")
        p = _plugin(scene, self._pending())
        binding = P.photo_binding(p, {"uid": "u1", "last_seen": NOW - 60}, now=NOW)
        self.assertTrue(binding["ok"], binding)
        self.assertTrue(binding["shopping"])
        self.assertEqual(binding["event_id"], "ev_mall_1")
        self.assertIn("试衣间", binding["scene"])
        self.assertIn("商场", binding["scene"])

    def test_home_photo_has_no_mall_scene(self):
        p = _plugin(_scene(activity="瘫在沙发上"), None)
        binding = P.photo_binding(p, {"uid": "u1", "last_seen": NOW - 60}, now=NOW)
        self.assertTrue(binding["ok"])
        self.assertFalse(binding["shopping"])
        self.assertEqual(binding["scene"], "")

    def test_manual_photo_bypasses_gate(self):
        p = _plugin(_scene(), self._pending())
        binding = P.photo_binding(p, {"uid": "u1", "last_seen": NOW - 7 * 3600},
                                  manual=True, now=NOW)
        self.assertTrue(binding["ok"])


class PhotoEventDedupeTests(unittest.TestCase):
    def test_same_event_only_one_image(self):
        p = _plugin()
        self.assertTrue(P.claim_photo_event(p, "ev_1", now=NOW))
        self.assertFalse(P.claim_photo_event(p, "ev_1", now=NOW))
        self.assertTrue(P.claim_photo_event(p, "ev_2", now=NOW))

    def test_no_event_does_not_block(self):
        p = _plugin()
        self.assertTrue(P.claim_photo_event(p, "", now=NOW))
        self.assertTrue(P.claim_photo_event(p, "", now=NOW))


class PushGateTests(unittest.TestCase):
    def test_silent_push_blocked(self):
        p = _plugin(last_seen=NOW - 7 * 3600)
        profile = {"uid": "u1", "last_seen": NOW - 7 * 3600}
        self.assertFalse(P.can_push(p, profile, "shopping", now=NOW))
        self.assertFalse(P.can_push(p, profile, "", now=NOW))

    def test_silent_timed_reminder_push_allowed(self):
        p = _plugin(last_seen=NOW - 7 * 3600)
        profile = {"uid": "u1", "last_seen": NOW - 7 * 3600}
        self.assertTrue(P.can_push(p, profile, "schedule_reminder", now=NOW))

    def test_fresh_push_allowed(self):
        p = _plugin(last_seen=NOW - 60)
        profile = {"uid": "u1", "last_seen": NOW - 60}
        self.assertTrue(P.can_push(p, profile, "shopping", now=NOW))


if __name__ == "__main__":
    unittest.main()
