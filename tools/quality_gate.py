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
import re
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
    assert len(ROUTE_SPECS) == 85, "路由数变化"
    assert len({item[0] for item in ROUTE_SPECS}) == 85, "路由 path 重复"
    return "routes 85"


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


def dynamic_names():
    """main.py 用 globals()[_n] = load(...) 装配 mixin/logger，pyflakes 静态看不到。"""
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as fh:
        text = fh.read()
    names = set(re.findall(r'globals\(\)\["([A-Za-z_][A-Za-z0-9_]*)"\]', text))
    names.update(re.findall(r'_n = "([A-Za-z_][A-Za-z0-9_]*)"', text))
    # for _mod, _name in (("amo_api", "ApiMixin"), ...) 这种成对装配
    names.update(re.findall(r'\(\s*"[a-z_][a-z0-9_]*"\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*\)', text))
    return names


def check_pyflakes():
    """pyflakes：未使用导入 / 未定义名之类的静态问题。"""
    files = sorted(name for name in os.listdir(ROOT) if name.endswith(".py"))
    result = subprocess.run([sys.executable, "-m", "pyflakes"] + files,
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        # 只卡真问题（未定义名）；纯 unused import 是兼容壳的历史噪音
        allowed = dynamic_names()
        noisy = []
        for line in result.stdout.splitlines():
            if not line.strip() or "imported but unused" in line or "redefinition of unused" in line:
                continue
            if "undefined name" in line and any(("'%s'" % name) in line for name in allowed):
                continue
            noisy.append(line)
        if noisy:
            print("\n".join(noisy))
            raise AssertionError("pyflakes 有告警")
    return "pyflakes clean (%d files)" % len(files)


def check_tests():
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-q"]
    subprocess.run(cmd, cwd=ROOT, check=True)
    return "unittest ok"


def clean_pycache():
    """旧 __pycache__ 会让 Python 复用「mtime+size 相同、内容不同」的 pyc，测试假红。"""
    removed = 0
    for base, dirs, _names in os.walk(ROOT):
        for name in list(dirs):
            if name == "__pycache__":
                shutil.rmtree(os.path.join(base, name), ignore_errors=True)
                dirs.remove(name)
                removed += 1
    return removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="跳过全量单测")
    args = parser.parse_args()
    print("[quality] clean_pycache", clean_pycache())
    checks = [check_schema, check_compile, check_routes, check_dashboard_js, check_pyflakes]
    if not args.fast:
        checks.append(check_tests)
    for check in checks:
        print("[quality]", check.__name__, check())


if __name__ == "__main__":
    main()
