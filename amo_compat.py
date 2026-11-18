# -*- coding: utf-8 -*-
"""兼容层（compat adapter）：旧插件名 / 旧数据目录 / 旧配置名 / 旧 DB 目录集中在这里。

为什么单独一个模块：
- 改名（Astrbot_Amo_forever -> Astrbot_amoupup_forever、astrbot_plugin_omnidraw -> Astrbot_amoupup_photo）
  在 10 多个文件里留下了**只读回退**的字面量；散落各处时既难审计、也容易被误当成当前名字。
- 现在所有旧名只出现在本模块；生产代码统一 import 这里的常量/纯函数。
- 只放常量与纯函数，不 import 插件内部模块（避免循环依赖），也不读写任何文件。

约定：
- 新名字（当前标识）是单数常量；旧名字进 *_LEGACY 或 *_NAMES 元组，**只用于只读回退**；
- 回退顺序一律「新名在前、旧名在后」；写入永远只写新名。
"""
from __future__ import annotations

import os

# ---- 想记 / 数据插件 -------------------------------------------------------
DATA_PLUGIN_NAME = "Astrbot_amoupup_data"
DATA_PLUGIN_LEGACY_NAMES = ("Astr_Amo_data", "astrbot_plugin_amo_data", "AstrAmoData")
#: 查找顺序：新名优先，旧名兜底（握手 / 记忆挂载都用这一份）
DATA_PLUGIN_NAMES = (DATA_PLUGIN_NAME, *DATA_PLUGIN_LEGACY_NAMES)

# ---- 生图插件「我能永远定格你」 -------------------------------------------
IMAGE_PLUGIN_NAME = "Astrbot_amoupup_photo"
IMAGE_PLUGIN_LEGACY_NAME = "astrbot_plugin_omnidraw"
#: 插件注册名查找顺序（context.get_registered_star 按它查）
IMAGE_PLUGIN_NAMES = (IMAGE_PLUGIN_NAME, IMAGE_PLUGIN_LEGACY_NAME)
#: plugin_data 下的目录名：新名优先，旧名只读回退
IMAGE_DATA_DIR_NAME = IMAGE_PLUGIN_NAME
IMAGE_DATA_DIR_NAMES = (IMAGE_DATA_DIR_NAME, IMAGE_PLUGIN_LEGACY_NAME)

# ---- 本插件（能陪）自己的旧名 ---------------------------------------------
#: 注册 Web API 时要清理的旧路由前缀（老插件标识留下的路由，重载后不清会指向死实例）
PLUGIN_ID_LEGACY_NAMES = (
    "astrbot_plugin_Astrbot_amoupup_forever",
    "astrbot_plugin_Astrbot_Amo_forever",
    "astrbot_plugin_amo_forever",
)
LEGACY_DB_DIRS = ("amo_forever", "astrbot_plugin_amo_forever")
LEGACY_CONFIG_FILES = ("amo_forever_config.json", "astrbot_plugin_amo_forever_config.json")


def is_legacy_name(name: str) -> bool:
    """name 是否是某个旧名字（插件名或数据目录名）。"""
    text = str(name or "").strip()
    if not text:
        return False
    return text in DATA_PLUGIN_LEGACY_NAMES or text in (IMAGE_PLUGIN_LEGACY_NAME, *LEGACY_DB_DIRS)


def legacy_config_paths(base_dir: str) -> list[str]:
    """旧配置文件的候选路径（新名配置由调用方自己拼；这里只给旧名）。"""
    base = str(base_dir or "")
    if not base:
        return []
    return [os.path.join(base, name) for name in LEGACY_CONFIG_FILES]


def legacy_db_paths(data_root: str, db_name: str = "amo_forever.db") -> list[str]:
    """旧版插件目录下的 DB 候选路径（只读回退 / 一次性迁移用）。"""
    root = str(data_root or "")
    if not root:
        return []
    return [os.path.join(root, "plugin_data", name, db_name) for name in LEGACY_DB_DIRS]


def image_data_dirs(root: str, subdir: str = "") -> list[str]:
    """生图插件的数据目录候选：root/<新名>[/subdir]、root/<旧名>[/subdir]。"""
    base = str(root or "")
    if not base:
        return []
    dirs: list[str] = []
    for name in IMAGE_DATA_DIR_NAMES:
        path = os.path.join(base, name)
        if subdir:
            path = os.path.join(path, subdir)
        dirs.append(path)
    return dirs
