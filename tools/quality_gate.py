# -*- coding: utf-8 -*-
"""本地质量门：schema/编译/路由契约/面板 JS/全量单测。

用法：
    python tools/quality_gate.py
    python tools/quality_gate.py --fast   # 跳过全量单测
"""
from __future__ import annotations

import argparse
import compileall
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_schema():
    path = os.path.join(ROOT, "_conf_schema.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, dict) and len(data) > 200, "schema 太小"
    assert "local_chat_cache" not in data, "local_chat_cache 应已删除"
    assert data.get("schedule_holiday_min_days", {}).get("default") == 4
    return "schema %d keys" % len(data)


def check_compile():
    ok = compileall.compile_dir(ROOT, quiet=1, force=False)
    assert ok, "py_compile 失败"
    return "compile ok"


def check_routes():
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import astrbot_stub  # noqa: E402
    astrbot_stub.install()
    from amo_route_table import ROUTE_SPECS  # noqa: E402
    assert len(ROUTE_SPECS) == 84, "路由数变化"
    assert len({item[0] for item in ROUTE_SPECS}) == 84, "路由 path 重复"
    return "routes 84"


def check_dashboard_js():
    html_path = os.path.join(ROOT, "views", "dashboard", "index.html")
    with open(html_path, encoding="utf-8") as fh:
        html = fh.read()
    start = html.find("<script>")
    end = html.find("</script>", start)
    assert start >= 0 and end > start, "找不到面板 script"
    node = shutil.which("node")
    if not node:
        return "dashboard js skipped (node missing)"
    tmp = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    try:
        tmp.write(html[start + len("<script>"):end])
        tmp.close()
        subprocess.run([node, "--check", tmp.name], check=True)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return "dashboard js ok"


def check_tests():
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-q"]
    subprocess.run(cmd, cwd=ROOT, check=True)
    return "unittest ok"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="跳过全量单测")
    args = parser.parse_args()
    checks = [check_schema, check_compile, check_routes, check_dashboard_js]
    if not args.fast:
        checks.append(check_tests)
    for check in checks:
        print("[quality]", check.__name__, check())


if __name__ == "__main__":
    main()
