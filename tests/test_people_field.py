# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_state_common import people_from_text  # noqa: E402


def test_people_from_text_matches_npc():
    npc = {"沈幼微": "远房表姐", "柳如烟": "闺蜜"}
    assert people_from_text("沈幼微顺路来楼下小馆", npc) == ["沈幼微"]
    assert people_from_text("一个人待着", npc) == []
