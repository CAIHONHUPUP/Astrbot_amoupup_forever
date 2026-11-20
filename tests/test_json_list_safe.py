# -*- coding: utf-8 -*-
"""json_extract_list：任何脏输入都返回 list，不抛异常。"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import amo_shared  # noqa: E402


def test_empty_and_garbage_return_empty_list():
    for raw in ("", "   ", "模型今天不开心", "{bad json}"):
        assert amo_shared.json_extract_list(raw) == []


def test_fenced_array_and_prefix():
    raw = "好的，事实如下：\n" + chr(96) * 3 + "json\n[{\"fact\": \"a\"}, {\"fact\": \"b\"}]\n" + chr(96) * 3
    assert amo_shared.json_extract_list(raw) == [{"fact": "a"}, {"fact": "b"}]


def test_single_object_wrapped_in_list():
    assert amo_shared.json_extract_list("{\"fact\": \"x\"}") == [{"fact": "x"}]
