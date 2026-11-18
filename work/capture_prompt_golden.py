# -*- coding: utf-8 -*-
"""从重构前的代码抓黄金快照；只写 tests/golden/prompts_golden.json。"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import prompt_fixtures as fx  # noqa: E402

OUT = os.path.join(ROOT, "tests", "golden", "prompts_golden.json")


def main():
    data = fx.collect()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    print("WROTE %s bytes=%d" % (OUT, os.path.getsize(OUT)))
    print("private_system chars=%d group_system chars=%d"
          % (len(data["private_system"]), len(data["group_system"])))


if __name__ == "__main__":
    main()
