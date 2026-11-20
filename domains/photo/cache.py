# -*- coding: utf-8 -*-
"""生图缓存清理 / 模板目录选择的纯内核（域内只用标准库）。

根目录 photo_engine.py 只负责读配置、打日志、建目录，判定与文件枚举都在这里。
搬自 photo_engine.py（2026-11-18，行为逐字未改）。
"""
from __future__ import annotations

import os


def purge_old_files(cache_dir, cutoff):
    """删掉 cache_dir 里 mtime < cutoff 的普通文件，返回 (删除数, 失败数)。"""
    removed = 0
    failed = 0
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return 0, 0
    for name in names:
        path = os.path.join(cache_dir, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            failed += 1
    return removed, failed


def choose_template_dir(raw, plugin_dir, canonical):
    """角色模板目录：显式配置 > 生图插件规范目录 > 旧目录 > templates。"""
    raw = str(raw or "").strip()
    plugin_dir = str(plugin_dir or "") or os.getcwd()
    canonical = str(canonical or "")
    if raw:
        raw_path = raw if os.path.isabs(raw) else os.path.join(plugin_dir, raw)
        if canonical and os.path.normcase(os.path.abspath(raw_path)) == os.path.normcase(
                os.path.abspath(canonical)):
            return raw_path
        if os.path.isdir(canonical):
            return canonical
        if os.path.isdir(raw_path):
            return raw_path
        return raw_path
    if canonical:
        return canonical
    return os.path.join(plugin_dir, "templates")
