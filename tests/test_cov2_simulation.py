# -*- coding: utf-8 -*-
"""多日全链路仿真进 CI：直接调用 work/simulate_weeks 的 main()，覆盖 JIT/旅游/改日程/账本/重载。

仿真本身只写系统 temp（REAL_DB_UNCHANGED / TEMP_CLEAN 由脚本断言），跑一次 7 天约几秒。
注意：仿真会冻结 amo_shared/amo_transition/amo_trip 的时钟，并把 stdlib 的
time.time 换成固定时间（simulate_24h.set_clock）；main() 原设计是独立进程，不负责恢复。
这里 finally 同时调 simulate_24h.restore_clock() / sim_lib.restore_clock() 并兜底还原
time.time，否则同进程后面的时间相关用例会被冻在 2026-11-01。
"""
import os
import subprocess
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")


def _main(argv):
    """按设计在独立进程里跑仿真：它会给 stdlib time/sys.path 打补丁、装 astrbot 桩，
    在同进程里跑会和整条测试套的全局状态互相污染（CI 上 2026-11-23 就因此红过一次：
    arrive.no_covering / no_transit 两条 hard 只在全量套里出现，单跑不复现）。
    返回 (exit_code, 合并后的 stdout+stderr)。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = WORK + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, os.path.join(WORK, "simulate_weeks.py")] + list(argv),
        cwd=WORK, capture_output=True, text=True, timeout=300, env=env)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_simulation_seven_days_is_green():
    code, out = _main(["--days", "7", "--seed", "7", "--no-negative"])
    assert code == 0, out[-3000:]
    assert "REAL_DB_UNCHANGED True" in out
    assert "TEMP_CLEAN True" in out
    assert "WEEKS days=7" in out


def test_simulation_three_days_other_seed():
    code, out = _main(["--days", "3", "--seed", "11", "--no-negative"])
    assert code == 0, out[-3000:]
    assert "TEMP_CLEAN True" in out
