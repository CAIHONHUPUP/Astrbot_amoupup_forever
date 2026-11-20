# -*- coding: utf-8 -*-
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402

astrbot_stub.install()
import amo_runtime_reply as m  # noqa: E402

CLS = next(v for k, v in vars(m).items() if isinstance(v, type) and hasattr(v, "_align_swim_where"))


def _fake(activity, place, zone="road", transit="subway"):
    return SimpleNamespace(state=SimpleNamespace(current_activity=lambda: {
        "activity": activity, "place": place, "zone": zone, "transit": transit}))


def test_subway_not_car():
    fixed = CLS._align_transit_word(_fake("坐地铁回家", "路上"), "（从还在车上上坐起来）")
    assert "车上" not in fixed and "地铁上" in fixed


def test_outside_swim_is_deferred_home():
    fixed = CLS._align_swim_where(_fake("坐地铁回家", "路上"), "行，就一张啊。", "换比基尼给我看")
    assert "到家" in fixed and "行" not in fixed


def test_home_swim_is_kept():
    text = "行，就一张啊。"
    assert CLS._align_swim_where(_fake("在家休息", "家", zone="home"), text, "换比基尼给我看") == text
