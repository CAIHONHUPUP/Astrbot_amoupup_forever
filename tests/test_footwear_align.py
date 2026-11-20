# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_runtime_reply import align_footwear  # noqa: E402


class W:
    def __init__(self, shoes, sleep=False, hotel=False):
        self.shoes, self.sleep, self.hotel = shoes, sleep, hotel

    def current(self):
        return {"shoes": self.shoes, "sleep": self.sleep, "hotel": self.hotel}


def test_replaces_slippers_with_real_shoes():
    assert align_footwear("（把脚收回来，套上拖鞋）", W("裸色高跟鞋")) == "（把脚收回来，套上裸色高跟鞋）"


def test_keeps_slippers_for_pajamas():
    text = "（套上拖鞋）"
    assert align_footwear(text, W("兔子毛绒拖鞋", sleep=True)) == text
