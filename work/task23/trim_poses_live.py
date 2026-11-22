# -*- coding: utf-8 -*-
"""task-23 一次性迁移：把生产 poses.json 收敛成白名单 ≤60 条（备份 + 原子写）。

只读原文件 → trim_poses_data（纯函数）→ 写 backup → atomic write。
不 reload、不碰 DB；下次插件启动/面板保存都按裁剪后的白名单运行。
"""
import json, os, shutil, sys
ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
DATA = r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever"
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub; astrbot_stub.install()
import pose_library
from amo_fileio import atomic_write_json

path = os.path.join(DATA, "poses.json")
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
trimmed, removed = pose_library.trim_poses_data(data)
backup = path + ".bak-task23-20261122"
shutil.copy2(path, backup)
atomic_write_json(path, trimmed)
with open(path, encoding="utf-8") as fh:
    verify = json.load(fh)
print("before", len(data.get("poses") or []), "after", len(verify.get("poses") or []),
      "removed", len(removed), "backup", backup)
print("names:", [pose.get("name") for pose in verify.get("poses") or []])
