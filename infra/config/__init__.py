# -*- coding: utf-8 -*-
"""配置化骨架：把「会变的文案 / 阈值 / 规则表」从代码里挪到 JSON。

设计（pilot，2026-11-17）：
- JSON 放在 infra/config/<name>.json，随插件打包；
- 代码里保留一份 default，JSON 缺失 / 损坏 / 类型不符时静默回退 default 并记一次吞异常；
- 读到的值做深拷贝，调用方改了也不会污染下一次读取；
- 只做 JSON 读取（不写盘、不监听变更）；改配置后重载插件即可生效。

为什么不用插件配置（_conf_schema.json）：那是给用户看的开关/数值；这里是
结构化的规则表 + 长文案，塞进插件配置会让面板难用、迁移难做。
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Callable, Optional

#: 已加载的配置缓存：path -> (mtime, value)
_CACHE: dict[str, tuple[float, Any]] = {}


def config_dir() -> str:
    """infra/config 目录的绝对路径。"""
    return os.path.dirname(os.path.abspath(__file__))


def config_path(name: str) -> str:
    """按名字给出 JSON 文件路径（name 不带 .json 后缀）。"""
    clean = str(name or "").strip()
    if not clean or "/" in clean or "\\" in clean or ".." in clean:
        return ""
    return os.path.join(config_dir(), clean + ".json")


def load_config(name: str, default: Any, validator: Optional[Callable[[Any], bool]] = None) -> Any:
    """读 infra/config/<name>.json；任何异常都返回 default 的深拷贝。

    validator(value) -> bool 用来挡「文件在、但结构不对」的情况（例如需要 list[dict]）。
    """
    fallback = copy.deepcopy(default)
    path = config_path(name)
    if not path or not os.path.isfile(path):
        return fallback
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return fallback
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return copy.deepcopy(cached[1])
    try:
        with open(path, encoding="utf-8-sig") as stream:
            value = json.load(stream)
    except (OSError, ValueError):
        return fallback
    if validator is not None:
        try:
            if not validator(value):
                return fallback
        except Exception:  # silent: 校验器自己出错也按「配置不可用」处理
            return fallback
    _CACHE[path] = (mtime, value)
    return copy.deepcopy(value)


def clear_cache() -> None:
    """测试 / 重载用：清掉配置缓存。"""
    _CACHE.clear()
