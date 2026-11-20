# -*- coding: utf-8 -*-
"""康复提示要明确覆盖「换泳装/比基尼」和旧低烧记忆。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_runtime_context import health_recovered_line  # noqa: E402


def test_recovered_line_covers_bikini_and_memory():
    line = health_recovered_line("健康", {"action": "take_sick_leave"})
    assert "泳装" in line and "比基尼" in line
    assert "低烧" in line and "旧状态" in line
