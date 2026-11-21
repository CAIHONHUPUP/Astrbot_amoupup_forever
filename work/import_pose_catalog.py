# -*- coding: utf-8 -*-
"""把 AI Pose Gallery 的 288 组姿势导入姿势库（只导名字/分类，不导图）。

数据源：https://github.com/anhao/ai-pose-gallery-zh 的 data/pose-zh.json
- 每个「体系 + 姿势编号」取正面一张，共 288 组；
- 双人缠绵（55 组）也入库，但 note 标记「双人姿势，自拍不用」，选姿势时会跳过；
- 参考图不预生成，第一次用到时按需生成。

用法：
    python work/import_pose_catalog.py --db <poses.json> [--source <pose-zh.json>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import urllib.request

SOURCE_URL = "https://raw.githubusercontent.com/anhao/ai-pose-gallery-zh/main/data/pose-zh.json"


def _load_source(path):
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--source", default="")
    args = parser.parse_args(argv)
    raw = _load_source(args.source)
    lib = json.load(open(args.db, encoding="utf-8"))
    existing = {str(item.get("name") or "") for item in lib.get("poses") or []}
    chosen = {}
    for row in raw:
        if str(row.get("角度") or "") != "正面":
            continue
        chosen.setdefault((str(row.get("体系") or ""), str(row.get("姿势编号") or "")), row)
    items = []
    for (sysname, _number), row in sorted(chosen.items()):
        name = str(row.get("姿势名称") or "").strip()
        if not name or name in existing:
            continue
        note = "%s·%s：%s" % (sysname, row.get("分类") or "", name)
        if "双人" in sysname:
            note = "（双人姿势，自拍不用）" + note
        items.append({"id": "p-" + hashlib.md5((sysname + name).encode("utf-8")).hexdigest()[:10],
                      "name": name, "note": note, "created_at": time.time(),
                      "usage": "couple" if "双人" in sysname else "selfie"})
    if not items:
        print("没有要导入的姿势")
        return 0
    shutil.copy2(args.db, args.db + ".bak-poseimport")
    lib.setdefault("poses", []).extend(items)
    with open(args.db, "w", encoding="utf-8") as fh:
        json.dump(lib, fh, ensure_ascii=False, indent=1)
    print("导入 %d 组，姿势库共 %d 组" % (len(items), len(lib["poses"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
