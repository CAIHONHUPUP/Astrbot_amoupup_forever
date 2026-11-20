# -*- coding: utf-8 -*-
"""她说“在地铁上”，照片就不能是办公室。

日程骨架先定 zone，再让模型改写 activity（模型不许改 zone/place）。
模型写出「地铁上听着歌晃回家」时 zone 还在 office，于是说话按文案、生图按 zone，
两边打架。这里在文案落地之后把 zone/place/transit 对齐。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from schedule_engine import ScheduleEngine  # noqa: E402


def item(activity, zone="office", place="莞城区的写字楼", **extra):
    data = {"activity": activity, "zone": zone, "place": place, "category": "work"}
    data.update(extra)
    return data


class ZoneAlignTests(unittest.TestCase):
    def align(self, *items):
        return ScheduleEngine._align_zone_with_activity(list(items))

    def test_subway_text_moves_zone_to_road(self):
        row = self.align(item("地铁上听着歌晃回家"))[0]
        self.assertEqual(row["zone"], "road")
        self.assertEqual(row["place"], "路上")
        self.assertEqual(row["transit"], "subway")
        self.assertEqual(row["vehicle"], "地铁")

    def test_office_work_text_stays_office(self):
        row = self.align(item("开会，整理文档准备下班"))[0]
        self.assertEqual(row["zone"], "office")
        self.assertEqual(row["place"], "莞城区的写字楼")

    def test_road_zone_with_office_place_is_normalized(self):
        row = self.align(item("下班回家", zone="road", place="莞城区的写字楼"))[0]
        self.assertEqual(row["place"], "路上")
        self.assertEqual(row["transit"], "walk")

    def test_at_home_text_is_not_moved(self):
        row = self.align(item("到家了，躺沙发不想动"))[0]
        self.assertEqual(row["zone"], "office")

    def test_taxi_and_drive_are_guessed(self):
        taxi = self.align(item("打车去客户公司", zone="road", place="路上"))[0]
        drive = self.align(item("开车回家，堵在高架上", zone="road", place="路上"))[0]
        self.assertEqual(taxi["transit"], "taxi")
        self.assertEqual(drive["transit"], "drive")


if __name__ == "__main__":
    unittest.main()
