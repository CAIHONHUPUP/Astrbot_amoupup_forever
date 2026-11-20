# -*- coding: utf-8 -*-
"""人物指代归一（2026-11-20）：第一次裸「他」补成「老公」。"""
from __future__ import annotations

import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_person_ref as pr  # noqa: E402


def test_first_bare_him_becomes_husband():
    assert pr.normalize("他说也在忙。") == "老公说也在忙。"


def test_second_him_is_kept():
    assert pr.normalize("老公说他在忙。") == "老公说他在忙。"


def test_only_first_occurrence_is_rewritten():
    assert pr.normalize("他先到家，他放下包。") == "老公先到家，他放下包。"


def test_protected_words_are_untouched():
    for text in ("其他事情", "他们走了", "他人很好", "他乡遇故知", "吉他",
                 "他妈的", "他杀", "他律"):
        assert pr.normalize(text) == text, text


def test_kinship_gets_de():
    assert pr.normalize("他妈妈来了") == "老公的妈妈来了"
    assert pr.normalize("他的公司") == "老公的公司"


def test_idempotent():
    once = pr.normalize("他抱着我")
    assert pr.normalize(once) == once


def test_chat_label_is_rewritten():
    assert pr.normalize("他：今天加班") == "老公：今天加班"


def test_empty_and_none():
    assert pr.normalize("") == ""
    assert pr.normalize(None) == ""


def test_schedule_batch_preserves_other_fields():
    items = [{"activity": "他和朋友吃饭", "start": "10:00", "place": "家"}]
    out = pr.normalize_schedule(items)
    assert out[0]["activity"] == "老公和朋友吃饭"
    assert out[0]["start"] == "10:00"
    assert items[0]["activity"] == "他和朋友吃饭"  # 不原地改
