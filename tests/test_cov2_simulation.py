# -*- coding: utf-8 -*-
"""多日全链路仿真进 CI：直接调用 work/simulate_weeks 的 main()，覆盖 JIT/旅游/改日程/账本/重载。

仿真本身只写系统 temp（REAL_DB_UNCHANGED / TEMP_CLEAN 由脚本断言），跑一次 7 天约几秒。
注意：仿真会冻结 amo_shared/amo_transition/amo_trip 的时钟，并把 stdlib 的
time.time 换成固定时间（simulate_24h.set_clock）；main() 原设计是独立进程，不负责恢复。
这里 finally 同时调 simulate_24h.restore_clock() / sim_lib.restore_clock() 并兜底还原
time.time，否则同进程后面的时间相关用例会被冻在 2026-11-01。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")


def _main(argv):
    if WORK not in sys.path:
        sys.path.insert(0, WORK)
    import sim_lib  # noqa: E402
    import simulate_24h  # noqa: E402
    import simulate_weeks  # noqa: E402  延迟导入：它会给 sys.path 加 work 并装 astrbot 桩
    original_time = time.time
    try:
        return simulate_weeks.main(argv)
    finally:
        simulate_24h.restore_clock()
        sim_lib.restore_clock()
        time.time = original_time


def test_simulation_seven_days_is_green(capsys):
    code = _main(["--days", "7", "--seed", "7", "--no-negative"])
    out = capsys.readouterr().out
    assert code == 0, out[-3000:]
    assert "REAL_DB_UNCHANGED True" in out
    assert "TEMP_CLEAN True" in out
    assert "WEEKS days=7" in out


def test_simulation_three_days_other_seed(capsys):
    code = _main(["--days", "3", "--seed", "11", "--no-negative"])
    out = capsys.readouterr().out
    assert code == 0, out[-3000:]
    assert "TEMP_CLEAN True" in out
