# -*- coding: utf-8 -*-
"""结构审计：main.py 行数、>1800 行文件、>120 行函数、TODO/FIXME。

用法：python work/structure_audit.py [--json]
只读扫描，不改任何文件。
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".test-tmp",
    "node_modules", "photo_cache", "tmp", "data",
}
BIG_FILE = 1800
BIG_FUNC = 120


def _files():
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(names):
            path = os.path.join(base, name)
            if name.endswith(".py") and os.path.abspath(path) != os.path.abspath(__file__):
                yield path


def _line_count(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except (OSError, UnicodeDecodeError):
        return 0


def _functions(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, UnicodeDecodeError, SyntaxError):
        return
    stack = [(tree, "")]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = (prefix + "." + child.name).lstrip(".")
                size = (child.end_lineno or child.lineno) - child.lineno + 1
                yield qual, size, child.lineno
            if isinstance(child, ast.ClassDef):
                stack.append((child, (prefix + "." + child.name).lstrip(".")))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.append((child, (prefix + "." + child.name).lstrip(".")))


def _todo(path):
    comments = raw = 0
    try:
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        raw = len(re.findall(r"TODO|FIXME", text))
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT and re.search(r"\b(TODO|FIXME)\b", token.string):
                comments += 1
    except (OSError, UnicodeDecodeError, tokenize.TokenError):
        return 0, 0
    return comments, raw


def main():
    report = {"main_py_lines": _line_count(os.path.join(ROOT, "main.py")),
              "big_files": [], "big_functions": [], "todo_comments": 0,
              "todo_raw": 0, "files_scanned": 0}
    for path in _files():
        report["files_scanned"] += 1
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        lines = _line_count(path)
        if lines > BIG_FILE:
            report["big_files"].append((rel, lines))
        for qual, size, lineno in _functions(path):
            if size > BIG_FUNC:
                report["big_functions"].append((rel, qual, size, lineno))
        comments, raw = _todo(path)
        report["todo_comments"] += comments
        report["todo_raw"] += raw
    report["big_files"].sort(key=lambda item: -item[1])
    report["big_functions"].sort(key=lambda item: (-item[2], item[0], item[1]))
    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("main.py 行数: %d (上限 820)" % report["main_py_lines"])
    print("扫描 .py 文件: %d" % report["files_scanned"])
    print(">%d 行文件: %d" % (BIG_FILE, len(report["big_files"])))
    for rel, lines in report["big_files"]:
        print("  %s: %d" % (rel, lines))
    print(">%d 行函数: %d" % (BIG_FUNC, len(report["big_functions"])))
    for rel, qual, size, lineno in report["big_functions"]:
        print("  %s:%d %s: %d 行" % (rel, lineno, qual, size))
    print("TODO/FIXME 注释: %d（源码字样总量 %d）"
          % (report["todo_comments"], report["todo_raw"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
