# -*- coding: utf-8 -*-
"""阶段 0 守卫：旧过渡写侧符号与 friend_dinner_segments 导出必须彻底消失。

这些符号在 A1 切流后已零调用（读侧改由 schedule_engine_v2.safe_view 派生），
阶段 0 整块删除。若有人把它们加回来，这里立刻红——逼着走评审而不是偷偷复活写侧。
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

import amo_transition  # noqa: E402
import schedule_engine  # noqa: E402
from amo_state_schedule import ScheduleStateMixin  # noqa: E402


class Stage0DeletionTests(unittest.TestCase):
    def test_transition_writer_symbols_are_gone(self):
        self.assertEqual([], [name for name in (
            "insert_transit_blocks", "refresh_transit_texts",
            "repair_cross_place_blocks") if hasattr(amo_transition, name)])

    def test_state_schedule_repair_symbols_are_gone(self):
        self.assertEqual([], [name for name in (
            "_repair_transit_blocks", "_mark_transit_action",
            "place_continuity_problems") if hasattr(ScheduleStateMixin, name)])

    def test_facade_no_longer_exports_friend_dinner_segments(self):
        self.assertEqual([], [name for name in ("friend_dinner_segments",)
                              if hasattr(schedule_engine, name)])


if __name__ == "__main__":
    unittest.main()
