# -*- coding: utf-8 -*-
"""把 AI Pose Gallery 的姿势原图下载到 outfit_refs/姿势库，并按姿势名命名。

数据源：https://github.com/anhao/ai-pose-gallery-zh （img/<id>.png，正面图）
文件名规则和 PoseLibrary.ref_path 一致：safe_name(姿势名称) + .png

用法：
    python work/fetch_pose_images.py --poses <poses.json> --source <pose-zh.json> --dir <姿势库目录>
"""
from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import os
import re
import urllib.request

RAW = "https://raw.githubusercontent.com/anhao/ai-pose-gallery-zh/main/"
_SAFE_RE = re.compile(r'[\\/:*?"<>|\r\n]+')


def safe_name(name):
    text = _SAFE_RE.sub("_", str(name or "").strip())
    return text[:40].strip(" .") or "pose"


def _fetch(url, path):
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
        if not data:
            return False
        with open(path, "wb") as fh:
            fh.write(data)
        return True
    except Exception:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--poses", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    lib = json.load(open(args.poses, encoding="utf-8"))
    names = {str(item.get("name") or "") for item in lib.get("poses") or []}
    raw = json.load(open(args.source, encoding="utf-8"))
    gallery = {}
    for row in raw:
        if str(row.get("角度") or "") != "正面":
            continue
        name = str(row.get("姿势名称") or "").strip()
        if name in names and name not in gallery:
            gallery[name] = str(row.get("原图") or row.get("缩略图") or "")
    os.makedirs(args.dir, exist_ok=True)
    tasks = []
    for name, rel in gallery.items():
        if not rel:
            continue
        path = os.path.join(args.dir, safe_name(name) + ".png")
        if os.path.isfile(path) and not args.force:
            continue
        tasks.append((RAW + rel, path))
    print("待下载 %d 张" % len(tasks))

    def work(item):
        return _fetch(*item)

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for ok in pool.map(work, tasks):
            done += 1 if ok else 0
            if done and done % 40 == 0:
                print("完成 %d" % done)
    total = len([n for n in os.listdir(args.dir) if n.endswith(".png")])
    print("下载成功 %d 张，目录现有 %d 张" % (done, total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
