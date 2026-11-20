# -*- coding: utf-8 -*-
"""P1-6 housing ↔ 照片故障可见（2026-11-17）。

2026-11-17 FIX-3 起 photo_room.py 的防御已收口，只注入坏 housing：
- room_profiles 抛任意异常 / 返回 None/{} / [] → 回退默认房间画像，并且必须打 debug 日志（不静默）；
- room_profiles 返回「值是字符串/None」的坏画像 → 丢掉坏值、回退默认，不 AttributeError；
- room_image / room_scene_line 抛异常 → 回退空串 + debug 日志，不崩；
- 房间参考图文件不存在 → 空串（不崩），默认 plan 的 room_ref 仍为空。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import photo_room  # noqa: E402
from photo_plan import build_plan  # noqa: E402

ACTIVITY = {"zone": "home_sofa", "place": "家", "activity": "窝在沙发上",
            "category": "rest"}


def _plan():
    return build_plan(ACTIVITY)


class RaiseProfiles:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def room_profiles(self):
        self.calls += 1
        raise self.exc

    def room_image(self, room):
        return ""


class ReturnProfiles:
    def __init__(self, value):
        self.value = value

    def room_profiles(self):
        return self.value

    def room_image(self, room):
        return ""


class BrokenImage:
    def __init__(self, exc=None, path=""):
        self.exc = exc
        self.path = path

    def room_profiles(self):
        return None

    def room_image(self, room):
        if self.exc is not None:
            raise self.exc
        return self.path


class HousingFailureFallbackTests(unittest.TestCase):
    def test_broken_room_profiles_falls_back_to_defaults_and_logs(self):
        housing = RaiseProfiles(KeyError("rooms"))
        with mock.patch.object(photo_room, "logger") as log:
            room = photo_room.room_for_plan(_plan(), housing, ACTIVITY)
            bedroom_label = photo_room.room_label("bedroom", housing)
            living_label = photo_room.room_label("living", housing)
        self.assertEqual(room, "living")
        self.assertEqual(bedroom_label, "卧室")
        self.assertEqual(living_label, "客厅")
        self.assertGreaterEqual(housing.calls, 1)
        self.assertTrue(log.debug.called, "housing 故障必须留 debug 日志，不能静默吞")
        self.assertTrue(any("房间画像" in str(call.args[0]) for call in log.debug.call_args_list))

    def test_empty_or_none_room_profiles_falls_back(self):
        for value in (None, {}, []):
            housing = ReturnProfiles(value)
            self.assertEqual(photo_room.room_for_plan(_plan(), housing, ACTIVITY),
                             "living", value)
            self.assertEqual(photo_room.room_label("bedroom", housing), "卧室", value)
            self.assertEqual(photo_room.room_label("living", housing), "客厅", value)
            self.assertEqual(photo_room.room_ref_for_plan(_plan(), housing, ACTIVITY), "")

    def test_room_image_error_is_logged_and_ref_is_empty(self):
        housing = BrokenImage(exc=KeyError("images"))
        plan = _plan()
        with mock.patch.object(photo_room, "logger") as log:
            ref = photo_room.room_ref_for_plan(plan, housing, ACTIVITY)
        self.assertEqual(ref, "")
        self.assertTrue(log.debug.called, "读房间参考图失败必须留 debug 日志")
        self.assertTrue(any("参考图" in str(call.args[0]) for call in log.debug.call_args_list))

    def test_missing_room_image_file_falls_back_without_crash(self):
        housing = BrokenImage(path=os.path.join(ROOT, "definitely-missing-room.png"))
        self.assertEqual(photo_room.room_ref_for_plan(_plan(), housing, ACTIVITY), "")
        self.assertEqual(getattr(_plan(), "room_ref", ""), "",
                         "默认 plan 不该自带 room_ref")

    def test_default_plan_and_no_housing_have_no_room_ref(self):
        plan = _plan()
        self.assertEqual(getattr(plan, "room_ref", ""), "")
        self.assertEqual(photo_room.room_ref_for_plan(plan, None, ACTIVITY), "")

    def test_unexpected_housing_runtime_error_does_not_crash(self):
        housing = RaiseProfiles(RuntimeError("hashicorp boom"))
        with mock.patch.object(photo_room, "logger") as log:
            self.assertEqual(photo_room.room_for_plan(_plan(), housing, ACTIVITY), "living")
        self.assertTrue(log.debug.called, "非配置类异常也必须留 debug 日志，不能静默穿透")

    def test_malformed_rooms_payload_does_not_crash(self):
        housing = ReturnProfiles({"bedroom": None, "living": "not-a-dict"})
        self.assertEqual(photo_room.room_for_plan(_plan(), housing, ACTIVITY), "living")
        self.assertEqual(photo_room.room_label("bedroom", housing), "卧室")
        self.assertEqual(photo_room.room_label("living", housing), "客厅")
        self.assertEqual(photo_room.room_profiles(housing)["bedroom"].get("label"), "卧室")


if __name__ == "__main__":
    unittest.main()
