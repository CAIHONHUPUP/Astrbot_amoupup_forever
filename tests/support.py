# -*- coding: utf-8 -*-
"""测试小工具（2026-11-24 T1）：临时目录。"""
import tempfile


def make_temp_dir(prefix="amo-test-"):
    return tempfile.mkdtemp(prefix=prefix)
