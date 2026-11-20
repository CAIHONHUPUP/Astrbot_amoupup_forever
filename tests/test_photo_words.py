# -*- coding: utf-8 -*-
"""他明确要照片时，必须真的发图（说没说“拍好了”都要发）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import draw_engine  # noqa: E402


def test_claim_words_cover_indirect_claims():
    for word in ("拍进去", "我拍", "拍给你", "拍一下", "发张", "给你发"):
        assert word in draw_engine.PHOTO_CLAIM_WORDS
