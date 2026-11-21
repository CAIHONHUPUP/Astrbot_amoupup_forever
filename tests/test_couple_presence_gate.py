# -*- coding: utf-8 -*-
"""合拍门禁：不在场不能两个人同框（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_household as hh  # noqa: E402
from draw_engine import DrawEngine  # noqa: E402


class CouplePresenceGateTests(unittest.TestCase):
    def _engine(self):
        eng = DrawEngine.__new__(DrawEngine)
        eng.plugin = SimpleNamespace()
        return eng

    def test_apart_cannot_couple(self):
        eng = self._engine()
        with mock.patch.object(hh, "enabled", return_value=True), \
                mock.patch.object(hh, "scene_now", return_value=SimpleNamespace(partner=False)):
            self.assertFalse(eng._can_couple_photo())

    def test_home_together_can_couple(self):
        eng = self._engine()
        with mock.patch.object(hh, "enabled", return_value=True), \
                mock.patch.object(hh, "scene_now", return_value=SimpleNamespace(partner=True)):
            self.assertTrue(eng._can_couple_photo())

    def test_block_reason_comes_from_scene_not_template(self):
        eng = self._engine()
        with mock.patch.object(hh, "scene_now", return_value=SimpleNamespace(partner=False, reason="同居·他上班")):
            self.assertEqual("同居·他上班", eng._couple_block_reason())

    def test_disabled_household_allows(self):
        eng = self._engine()
        with mock.patch.object(hh, "enabled", return_value=False):
            self.assertTrue(eng._can_couple_photo())


if __name__ == "__main__":
    unittest.main()
