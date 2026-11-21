# -*- coding: utf-8 -*-
"""清理礼物墙里的聊天碎片（2026-11-21 污染）。

判定用 gift_engine.gift_item_ok(strict=True)：只保留像具体物品的名字。
默认干跑；--apply 前自动备份 gifts.json。

用法：
    python work/clean_gift_wall.py --file <gifts.json>
    python work/clean_gift_wall.py --file <gifts.json> --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from gift_engine import gift_item_ok  # noqa: E402


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    path = os.path.abspath(args.file)
    if not os.path.isfile(path):
        print("没有找到 %s" % path)
        return 1
    data = json.load(open(path, encoding="utf-8"))
    gifts = list(data.get("gifts") or [])
    bad = [g for g in gifts if not gift_item_ok(str(g.get("item") or ""), strict=True)]
    print("礼物 %d 件，疑似污染 %d 件" % (len(gifts), len(bad)))
    for gift in bad:
        print("  %s  %s  %s" % (str(gift.get("ts") or "")[:10], gift.get("item"), gift.get("raw", "")[:40]))
    if not args.apply:
        print("预览模式：加 --apply 才写入")
        return 0
    if not bad:
        return 0
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path + ".bak-gift-" + stamp
    shutil.copy2(path, backup)
    print("已备份：%s" % backup)
    keep = [g for g in gifts if gift_item_ok(str(g.get("item") or ""), strict=True)]
    data["gifts"] = keep
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print("已删除 %d 件，保留 %d 件" % (len(gifts) - len(keep), len(keep)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
