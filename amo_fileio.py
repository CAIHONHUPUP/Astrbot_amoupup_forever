# -*- coding: utf-8 -*-
"""文件小工具（2026-11-15 P2 去重）。

衣柜 / 姿势库 / 礼物三个模块以前各写一份「tmp + os.replace」的 JSON 落盘逻辑，
这里合成一份：先写同目录临时文件、fsync，再原子替换，失败不留半截文件。
"""
from __future__ import annotations

import json
import os


def atomic_write_json(path, data, indent=2, ensure_ascii=False):
    """原子写 JSON：临时文件 + fsync + os.replace；任何异常都会清掉临时文件。"""
    text = os.fspath(path)
    folder = os.path.dirname(text)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = text + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=ensure_ascii, indent=indent)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
                pass
        os.replace(tmp, text)
        return text
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
            pass
        raise
