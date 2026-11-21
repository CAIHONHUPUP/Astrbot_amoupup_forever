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

CLS = next(v for k, v in vars(m).items() if isinstance(v, type) and hasattr(v, "_align_arrival"))


def _fake(place="路上", zone="road"):
    return SimpleNamespace(state=SimpleNamespace(current_activity=lambda: {"place": place, "zone": zone}))


def test_arrival_on_road_is_rewritten():
    text = "我已经到啦，鞋都换了，正窝还在车上上看剧呢。"
    fixed = CLS._align_arrival(_fake(), text)
    assert "到家" not in fixed and "我已经到" not in fixed
    assert "还在路上" in fixed
    assert "车上上" not in fixed


def test_road_reply_with_home_action_is_rewritten():
    text = "（刚到家把外套脱了奶还在车上上，笑着回你）\n笑什么呢，也在刷视频。\n我还在路上呢，快到了。"
    fixed = CLS._align_arrival(_fake(), text)
    assert "到家" not in fixed
    assert "外套" not in fixed
    assert fixed.count("还在路上") == 1
    assert "车上上" not in fixed


def test_arrival_at_home_is_kept():
    text = "我已经到啦，鞋都换了。"
    assert CLS._align_arrival(_fake(place="家", zone="home"), text) == text
