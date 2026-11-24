# -*- coding: utf-8 -*-
"""静默 except 审计：python tools/audit_silent_except.py [--update]"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys


def scan(root):
    counts = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in
                   ("tests", "work", "__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".github")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=rel)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                body = [n for n in node.body
                        if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                and isinstance(n.value.value, str))]
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    counts[rel] = counts.get(rel, 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--plugin", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    args = parser.parse_args()
    root = os.path.abspath(args.plugin)
    counts = scan(root)
    baseline_path = os.path.join(root, "tests", "silent_except_baseline.json")
    if args.update:
        with open(baseline_path, "w", encoding="utf-8") as fh:
            json.dump(counts, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        print("baseline written: %d" % sum(counts.values()))
        return 0
    with open(baseline_path, encoding="utf-8") as fh:
        baseline = json.load(fh)
    extra = {name: count for name, count in counts.items()
             if count > int(baseline.get(name, 0))}
    print(json.dumps({"total": sum(counts.values()),
                      "baseline": sum(int(v) for v in baseline.values()),
                      "extra": extra}, ensure_ascii=False, indent=2))
    return 1 if extra else 0


if __name__ == "__main__":
    raise SystemExit(main())
