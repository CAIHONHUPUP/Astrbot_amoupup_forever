# -*- coding: utf-8 -*-
"""ConfigModel：以 _conf_schema.json 为单一真相的配置模型（Phase 4）。

职责：
- 读配置（cfg/cfg_list/cfg_bool/cfg_int/cfg_float/cfg_str 的唯一实现）；
- 类型 / 可选值 / min-max 校验，未知键与旧键报告（不抛异常）；
- secret 掩码、只读快照；
- 迁移钩子集中登记与统一编排（迁移函数本体仍留在各 mixin）。

本模块不 import 插件其它模块，避免循环依赖；错误记录通过 error_logger 回调。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping
from typing import Any, Callable

SCHEMA_FILE = "_conf_schema.json"

LIST_TYPES = ("list", "template_list")
DICT_TYPES = ("dict", "object")
STR_TYPES = ("string", "text")
NUM_TYPES = ("int", "float")

#: 历史键 → 现在的键（面板/日志提示用；不主动改写配置）
LEGACY_KEYS: dict[str, str] = {}

#: 迁移钩子：(id, 插件方法名)。顺序就是执行顺序，集中登记在这里。
MIGRATION_HOOKS: list[tuple[str, str]] = [
    ("legacy_config", "_migrate_legacy_config"),
    ("delay_default", "_migrate_delay_default"),
    ("proactive_timing", "_migrate_proactive_timing"),
    ("typing_defaults", "_migrate_typing_defaults"),
    ("legacy_admin_default", "_migrate_legacy_admin_default"),
    ("scenic_kb_name", "_migrate_scenic_kb_name"),
    ("schedule_defaults", "_migrate_schedule_defaults"),
    ("diary_continuity", "_migrate_diary_continuity"),
]
BOOT_MIGRATION = "_migrate_legacy_config"


@dataclass(frozen=True)
class Issue:
    level: str
    kind: str
    key: str
    message: str
    value: Any = None

    def as_dict(self) -> dict:
        return {"level": self.level, "kind": self.kind, "key": self.key,
                "message": self.message, "value": self.value}


def register_legacy_key(key: str, replacement: str = "") -> None:
    LEGACY_KEYS[str(key)] = str(replacement or "")


def register_migration(hook_id: str, method_name: str, position: int | None = None) -> None:
    item = (str(hook_id), str(method_name))
    if position is None:
        MIGRATION_HOOKS.append(item)
    else:
        MIGRATION_HOOKS.insert(int(position), item)


def load_schema(plugin_dir: str) -> dict:
    path = os.path.join(str(plugin_dir or ""), SCHEMA_FILE)
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def mask_secret(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return text[:4] + "*" * (len(text) - 8) + text[-4:]


def coerce_value(kind: str, value: Any) -> Any:
    """面板回写时的类型收敛（与改造前的 _coerce_config_value 完全一致）。"""
    if kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是")
    if kind == "int":
        return int(float(value))
    if kind == "float":
        return float(value)
    if kind in LIST_TYPES:
        if isinstance(value, list):
            return value
        parsed = json.loads(value) if isinstance(value, str) and value.strip() else []
        return parsed if isinstance(parsed, list) else []
    if kind in DICT_TYPES:
        if isinstance(value, dict):
            return value
        parsed = json.loads(value) if isinstance(value, str) and value.strip() else {}
        return parsed if isinstance(parsed, dict) else {}
    return str(value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class ConfigModel:
    def __init__(self, config: Any, schema: Any, source: str = "",
                 error_logger: Callable[[str, Exception], Any] | None = None) -> None:
        self._config = config if isinstance(config, Mapping) else {}
        self._schema = schema if isinstance(schema, dict) else {}
        self.source = source
        self._error_logger = error_logger

    @classmethod
    def from_plugin_dir(cls, config, plugin_dir: str,
                        error_logger: Callable[[str, Exception], Any] | None = None) -> "ConfigModel":
        return cls(config, load_schema(plugin_dir), source=str(plugin_dir or ""),
                   error_logger=error_logger)

    # ---- 基础 ----
    @property
    def config(self) -> Any:
        return self._config

    @property
    def schema(self) -> dict:
        return self._schema

    def get(self, key: str, default: Any = None) -> Any:
        try:
            value = self._config.get(key, default)
        except Exception as exc:
            self._report("ConfigModel.get", exc)
            value = default
        return default if value is None else value

    def get_list(self, key: str, default: Any = None) -> list:
        value = self.get(key, [] if default is None else default)
        if isinstance(value, list):
            return value
        if isinstance(value, (tuple, set)):
            return list(value)
        return []

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是")

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(float(self.get(key, default)))
        except Exception as exc:
            self._report("ConfigModel.get_int", exc)
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get(key, default))
        except Exception as exc:
            self._report("ConfigModel.get_float", exc)
            return default

    def get_str(self, key: str, default: str = "") -> str:
        try:
            return str(self.get(key, default))
        except Exception as exc:
            self._report("ConfigModel.get_str", exc)
            return default

    # ---- 校验 ----
    def spec(self, key: str) -> dict:
        meta = self._schema.get(key)
        return meta if isinstance(meta, dict) else {}

    @staticmethod
    def _type_ok(kind: str, value: Any) -> bool:
        if kind == "bool":
            return isinstance(value, bool)
        if kind == "int":
            return isinstance(value, int) and not isinstance(value, bool)
        if kind == "float":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if kind in STR_TYPES:
            return isinstance(value, str)
        if kind in LIST_TYPES:
            return isinstance(value, list)
        if kind in DICT_TYPES:
            return isinstance(value, dict)
        return True

    @staticmethod
    def _option_values(meta: dict) -> list:
        options = meta.get("options")
        if not isinstance(options, list):
            return []
        values = []
        for item in options:
            if isinstance(item, dict):
                values.append(item.get("value"))
            else:
                values.append(item)
        return values

    def validate(self, key: str, value: Any) -> tuple[str, str]:
        """返回 (kind, message)；kind 为空字符串表示通过。"""
        meta = self._schema.get(key)
        if not isinstance(meta, dict):
            kind = "legacy_key" if key in LEGACY_KEYS else "unknown_key"
            hint = LEGACY_KEYS.get(key) or ""
            message = ("配置项是旧键，建议改用 %s" % hint) if hint else "配置项未在 schema 中登记"
            return kind, message
        expected = str(meta.get("type") or "string")
        if not self._type_ok(expected, value):
            return "type_mismatch", "期望 %s，实际 %s" % (expected, type(value).__name__)
        options = self._option_values(meta)
        if options and value not in options:
            return "out_of_range", "值 %r 不在可选范围 %r" % (value, options)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            low, high = meta.get("min"), meta.get("max")
            if isinstance(low, (int, float)) and value < low:
                return "out_of_range", "值 %r 小于下限 %r" % (value, low)
            if isinstance(high, (int, float)) and value > high:
                return "out_of_range", "值 %r 大于上限 %r" % (value, high)
        return "", ""

    def issues(self) -> list[Issue]:
        out: list[Issue] = []
        try:
            keys = list(self._config.keys())
        except Exception as exc:
            self._report("ConfigModel.issues", exc)
            return out
        for key in keys:
            value = self.get(key)
            kind, message = self.validate(key, value)
            if not kind:
                continue
            level = "warning" if kind in ("unknown_key", "legacy_key") else "error"
            out.append(Issue(level, kind, key, message, value))
        return out

    def health(self) -> list[dict]:
        return [issue.as_dict() for issue in self.issues()]

    # ---- 只读快照 / 掩码 ----
    def snapshot(self) -> Mapping:
        try:
            return _freeze(dict(self._config))
        except Exception as exc:
            self._report("ConfigModel.snapshot", exc)
            return MappingProxyType({})

    @staticmethod
    def mask_secret(value: Any) -> str:
        return mask_secret(value)

    @staticmethod
    def coerce(kind: str, value: Any) -> Any:
        return coerce_value(kind, value)

    # ---- 迁移编排 ----
    def _report(self, scope: str, exc: Exception) -> None:
        if self._error_logger is not None:
            try:
                self._error_logger("amo_config_model.%s" % scope, exc)
            except Exception:  # cleanup：记录器自身失败不能影响配置读取
                return


def run_migrations(plugin: Any, order: tuple[str, ...] | list[str] | None = None,
                   boot: bool = False, logger: Any = None) -> list[str]:
    """按登记顺序调用插件上的迁移方法；单个失败只记日志，不阻断。"""
    if order is None:
        if boot:
            order = (BOOT_MIGRATION,)
        else:
            order = tuple(method for _, method in MIGRATION_HOOKS
                          if method != BOOT_MIGRATION)
    ran: list[str] = []
    for name in order:
        fn = getattr(plugin, str(name), None)
        if not callable(fn):
            continue
        try:
            fn()
            ran.append(str(name))
        except Exception as exc:
            if logger is not None:
                logger.debug("[amo] 迁移 %s 失败: %s" % (name, exc))
    return ran
