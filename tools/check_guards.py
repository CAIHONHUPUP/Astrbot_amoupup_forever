# -*- coding: utf-8 -*-
"""命令行守卫健康检查：python tools/check_guards.py --plugin <插件目录>"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    args = parser.parse_args()
    plugin = os.path.abspath(args.plugin)
    sys.path.insert(0, plugin)
    # 没有装 AstrBot 的裸环境（CI / 开发机）里，插件模块没法 import astrbot，
    # 会把「模块缺属性」误报成「模块 import 不了」。用 tests 里的最小桩顶上，
    # 检查范围不变（模块能不能 import + 关键属性在不在）。
    try:
        import astrbot  # noqa: F401
    except ImportError:
        tests_dir = os.path.join(plugin, "tests")
        if os.path.isdir(tests_dir) and tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)
        try:
            import astrbot_stub
        except ImportError:
            astrbot_stub = None   # 没桩就按原样跑，缺什么报什么
        if astrbot_stub is not None:
            astrbot_stub.install()
    import amo_guard_health

    report = amo_guard_health.check_guards()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
