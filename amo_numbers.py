# -*- coding: utf-8 -*-
"""时间/数字纯函数门面（2026-11-17）。

实现已搬进 domains/kernel/numbers.py（跨域共享的纯函数）；本文件只做 re-export，
旧导入路径（from amo_numbers import … / amo_shared 转出）与返回值完全不变。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:  # 包模式
    from .domains.kernel.numbers import (  # noqa: F401  re-export
        DAY_END_MINUTES,
        clamp,
        to_int,
        to_float,
        safe_float,
        safe_float_any,
        parse_hhmm,
        minutes_of,
        clock_of,
        minutes_of_fact,
        clock_of_fact,
        hhmm_text,
        minutes_of_plain,
        minutes_of_loose,
        clock_of_ts,
        clock_of_ts_guarded,
        day_of_ts,
        day_text,
        clock_after,
        md5_int,
        yuan,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.kernel.numbers import (  # noqa: F401  re-export
            DAY_END_MINUTES,
            clamp,
            to_int,
            to_float,
            safe_float,
            safe_float_any,
            parse_hhmm,
            minutes_of,
            clock_of,
            minutes_of_fact,
            clock_of_fact,
            hhmm_text,
            minutes_of_plain,
            minutes_of_loose,
            clock_of_ts,
            clock_of_ts_guarded,
            day_of_ts,
            day_text,
            clock_after,
            md5_int,
            yuan,
        )
