# -*- coding: utf-8 -*-
"""JSON/文件态 schema 版本（Phase 7）。

登记 wardrobe / poses / housing / gifts / uid_map 的版本与迁移函数：

- 读时缺 version 按 0 处理（等价于"老文件"）；
- 迁移函数是纯函数（dict → dict），不碰磁盘，方便单测；
- 写回统一走 amo_fileio.atomic_write_json；**解析失败或迁移抛错时不覆盖原文件**，
  只留一份 .bak 只读副本，并在报告里记 error。

uid_map.json 例外：它是人工维护的 uid → uid 映射表，往里塞 version 键会污染别名
解析，所以登记成"只读审计"——版本恒按 0 读，迁移只做校验，从不落盘。
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

import json
import os
import shutil

try:  # 包模式优先
    from .amo_fileio import atomic_write_json
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_fileio import atomic_write_json  # type: ignore[no-redef]

try:  # AstrBot 运行环境和单测桩都提供 astrbot.api.logger
    from astrbot.api import logger
except ImportError:  # 直接按文件加载时的兜底
    import logging

    logger = logging.getLogger("amo_json_schema")


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _migrate_wardrobe(data):
    """wardrobe.json v1：补齐 items/outfits/daily，盖上 version。"""
    data["version"] = 1
    if not isinstance(data.get("items"), list):
        data["items"] = []
    if not isinstance(data.get("outfits"), list):
        data["outfits"] = []
    if not isinstance(data.get("daily"), dict):
        data["daily"] = {}
    return data


def _migrate_poses(data):
    """poses.json v1：补齐 poses，seed_version 归一成整数。"""
    data["version"] = 1
    if not isinstance(data.get("poses"), list):
        data["poses"] = []
    data["seed_version"] = _as_int(data.get("seed_version"), 0)
    return data


def _migrate_housing(data):
    """housing（kv）v1：补齐 homes / residence_id。"""
    data["version"] = 1
    if not isinstance(data.get("homes"), list):
        data["homes"] = []
    residence = data.get("residence_id")
    data["residence_id"] = "" if residence is None else str(residence)
    return data


def _migrate_gifts(data):
    """gifts.json v1：补齐 wishes/gifts/meta，第一次补上 version。"""
    data["version"] = 1
    for key in ("wishes", "gifts"):
        if not isinstance(data.get(key), list):
            data[key] = []
    if not isinstance(data.get("meta"), dict):
        data["meta"] = {}
    return data


def _migrate_uid_map(data):
    """uid_map.json：只把值归一成字符串，不加 version（人工维护的映射表）。"""
    for key in list(data.keys()):
        value = data.get(key)
        if not isinstance(value, str):
            data[key] = "" if value is None else str(value)
    return data


#: 登记表：名字 → {kind, 文件名或 kv 键, 当前版本, 迁移函数, 是否只读}
STORE_SPECS: dict[str, dict[str, Any]] = {
    "wardrobe": {
        "kind": "json", "filename": "wardrobe.json", "version": 1,
        "migrate": _migrate_wardrobe, "read_only": False,
    },
    "poses": {
        "kind": "json", "filename": "poses.json", "version": 1,
        "migrate": _migrate_poses, "read_only": False,
    },
    "housing": {
        "kind": "kv", "kv_key": "housing", "version": 1,
        "migrate": _migrate_housing, "read_only": False,
    },
    "gifts": {
        "kind": "json", "filename": "gifts.json", "version": 1,
        "migrate": _migrate_gifts, "read_only": False,
    },
    "uid_map": {
        "kind": "json", "filename": "uid_map.json", "version": 0,
        "migrate": _migrate_uid_map, "read_only": True,
    },
}

#: 登记顺序（报告/批量升级按这个顺序输出）
STORE_NAMES = tuple(STORE_SPECS.keys())


def spec(name):
    """取一条登记（不存在抛 KeyError）。"""
    entry = STORE_SPECS[name]
    return dict(entry)


def current_version(name):
    return int(STORE_SPECS[name]["version"])


def version_of(name, data):
    """读版本：缺 version / 坏 version 一律按 0。"""
    if not isinstance(data, dict):
        return 0
    return _as_int(data.get("version"), 0)


def stamp(name, data):
    """写时补版本：就地盖当前 version 并返回同一个 dict（只读 store 不动）。"""
    entry = STORE_SPECS[name]
    if isinstance(data, dict) and not entry["read_only"]:
        data["version"] = int(entry["version"])
    return data


def upgrade_payload(name, data):
    """在内存里迁移一份数据，返回报告（不碰磁盘）。"""
    entry = STORE_SPECS[name]
    from_version = version_of(name, data)
    report = {
        "store": name,
        "from_version": from_version,
        "to_version": from_version,
        "changed": False,
        "data": data,
        "error": None,
        "read_only": bool(entry["read_only"]),
    }
    if not isinstance(data, dict):
        report["error"] = "顶层不是对象（%s）" % type(data).__name__
        return report
    if not entry["read_only"] and from_version > int(entry["version"]):
        # 文件比代码新（回滚过）：不降级，也不改写。
        logger.warning("[amo] %s 版本 %d 高于代码 %d，不降级",
                       name, from_version, int(entry["version"]))
        report["ahead"] = True
        return report
    migrate = entry.get("migrate")
    if not callable(migrate):
        return report
    try:
        migrated = migrate(data)
    except Exception as exc:  # 迁移函数出错：保留原数据，只报告
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        return report
    report["data"] = migrated if isinstance(migrated, dict) else data
    report["to_version"] = version_of(name, report["data"])
    report["changed"] = (report["to_version"] != from_version)
    return report


def _keep_backup(path):
    """失败时留一份 .bak 只读副本；返回备份路径（失败返回空串）。"""
    target = str(path) + ".bak"
    try:
        shutil.copy2(path, target)
    except Exception as exc:
        logger.warning("[amo] 备份 %s 失败：%r", path, exc)
        return ""
    return target


def upgrade_file(name, path, backup=True):
    """迁移一个 JSON 文件；解析/迁移/写回失败都不动原文件。"""
    entry = STORE_SPECS[name]
    report = {
        "store": name, "path": str(path), "from_version": 0, "to_version": 0,
        "changed": False, "written": False, "backup_path": "", "error": None,
    }
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        report["missing"] = True
        return report
    except Exception as exc:
        report["error"] = "解析失败: %s: %s" % (type(exc).__name__, exc)
        report["backup_path"] = _keep_backup(path) if backup else ""
        return report
    payload = upgrade_payload(name, raw)
    report["from_version"] = payload["from_version"]
    report["to_version"] = payload.get("to_version", payload["from_version"])
    report["error"] = payload["error"]
    if payload["error"]:
        report["backup_path"] = _keep_backup(path) if backup else ""
        return report
    if entry["read_only"] or not payload["changed"]:
        return report
    try:
        atomic_write_json(path, payload["data"])
    except Exception as exc:
        report["error"] = "写回失败: %s: %s" % (type(exc).__name__, exc)
        report["backup_path"] = _keep_backup(path) if backup else ""
        return report
    report["changed"] = True
    report["written"] = True
    return report


def upgrade_kv(name, db):
    """迁移一个 kv 里的 JSON 对象（housing）；写回走 db.kv_set。"""
    entry = STORE_SPECS[name]
    if entry["kind"] != "kv":
        raise TypeError("%s 不是 kv store" % name)
    report = {"store": name, "kv_key": entry["kv_key"], "from_version": 0,
              "to_version": 0, "changed": False, "written": False, "error": None}
    try:
        raw = db.kv_get(entry["kv_key"], None)
    except Exception as exc:
        report["error"] = "读取失败: %s: %s" % (type(exc).__name__, exc)
        return report
    if not isinstance(raw, dict):
        report["missing"] = True
        return report
    payload = upgrade_payload(name, raw)
    report["from_version"] = payload["from_version"]
    report["to_version"] = payload.get("to_version", payload["from_version"])
    report["error"] = payload["error"]
    if payload["error"] or not payload["changed"]:
        return report
    try:
        db.kv_set(entry["kv_key"], payload["data"])
    except Exception as exc:
        report["error"] = "写回失败: %s: %s" % (type(exc).__name__, exc)
        return report
    report["changed"] = True
    report["written"] = True
    return report


def store_path(name, base_dir):
    """JSON store 在数据目录下的路径。"""
    filename = STORE_SPECS[name].get("filename")
    if not filename:
        raise KeyError("%s 不是文件 store" % name)
    return os.path.join(str(base_dir), str(filename))


