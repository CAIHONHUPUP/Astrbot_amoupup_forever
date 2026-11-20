# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from draw_engine import swim_outside_request  # noqa: E402


def test_swim_outside_blocked():
    assert swim_outside_request("换比基尼给我看", "路上") is True
    assert swim_outside_request("换比基尼给我看", "地铁上") is True


def test_swim_home_allowed():
    assert swim_outside_request("换比基尼给我看", "家") is False


def test_normal_photo_not_blocked():
    assert swim_outside_request("拍张自拍给我看", "路上") is False
