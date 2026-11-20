# -*- coding: utf-8 -*-
"""system prompt 常量必须是字符串：拼进 req.system_prompt 时不能是 tuple。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_llm  # noqa: E402


def test_ownership_rules_is_str():
    assert isinstance(amo_llm.ACTION_OWNERSHIP_RULES, str)
    assert "【动作归属·最高优先级】" in amo_llm.ACTION_OWNERSHIP_RULES


def test_system_prompt_concat_works():
    system = "前缀"
    merged = (system + amo_llm.ACTION_OWNERSHIP_RULES).lstrip()
    assert isinstance(merged, str) and "跨次元" in merged
