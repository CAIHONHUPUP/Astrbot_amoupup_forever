# -*- coding: utf-8 -*-
"""统一双路径导入：包内相对导入失败时回退到顶层绝对导入。

main.py 里重复了几十次的
    try:
        from .x import y
    except ImportError:
        from x import y
改成：
    y = load(".x", "x", "y")
这样重构时只改一处、也便于离线测试。
"""
from __future__ import annotations

import importlib


def _caller_package():
    """load() 的调用方模块属于哪个包（用它的 __package__ 做真正的相对导入）。"""
    import inspect

    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back else None
        return str(caller.f_globals.get("__package__") or "") if caller else ""
    finally:
        del frame


def load(pkg_name, top_name, attr=None, default=None):
    """先按包路径导入，失败退回顶层路径；都失败时 attr 为 None 则返回 default。"""
    module = None
    if pkg_name.startswith("."):
        package = _caller_package()
        if package:
            try:
                module = importlib.import_module(pkg_name, package)
            except Exception:
                module = None
    if module is None:
        try:
            module = importlib.import_module(top_name)
        except Exception:
            module = None
    if module is None:
        if attr is None:
            return default
        return default
    if attr is None:
        return module
    return getattr(module, attr, default)


def try_attr(obj, attr, default=None):
    """安全取属性：对象为 None 或属性缺失时返回默认值。"""
    if obj is None:
        return default
    return getattr(obj, attr, default)
