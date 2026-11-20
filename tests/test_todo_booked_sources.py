# -*- coding: utf-8 -*-
"""未来日程 -> 待办：watch 写入的社交邀约必须保留（2026-11-18 明天待办为空事故）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_state_todo import ScheduleTodoMixin  # noqa: E402


class _Todo(ScheduleTodoMixin):
    _BOOKED_CATEGORIES = {"social"}


def test_watch_social_item_is_booked():
    obj = _Todo()
    item = {"source": "watch", "category": "social", "start": "19:00",
            "end": "19:30", "activity": "见沈幼微", "place": "楼下小馆"}
    assert obj._booked_of([item]) == [item]


def test_watch_non_social_item_is_also_kept():
    obj = _Todo()
    item = {"source": "watch", "category": "health", "start": "09:00",
            "end": "11:00", "activity": "去医院体检", "place": "医院"}
    assert obj._booked_of([item]) == [item]


def test_jit_items_are_still_not_booked():
    obj = _Todo()
    item = {"source": "jit", "category": "rest", "start": "15:00",
            "end": "16:00", "activity": "发呆", "place": "家"}
    assert obj._booked_of([item]) == []
