# -*- coding: utf-8 -*-
"""compat adapter 回归锁：旧插件名 / 旧数据目录只允许出现在 amo_compat.py。

2026-11-17：改名留下的旧名字面量原本散落在 9 个生产文件里（只读回退用），
现在统一收进 amo_compat.py。本文件锁住三件事：

1. 回退顺序：新名在前、旧名在后（写入永远只写新名）；
2. 生产代码里不再内联写旧名字面量（tests/、work/、docs/ 除外）；
3. 旧路径拼装（配置文件 / DB / 生图数据目录）结果正确。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import amo_compat  # noqa: E402

#: 只允许出现在 amo_compat.py 的旧名字面量
LEGACY_LITERALS = (
    "astrbot_plugin_omnidraw",
    "Astr_Amo_data",
    "astrbot_plugin_amo_data",
    "astrbot_plugin_Astr_Amo_forever",
    "astrbot_plugin_amo_forever",
    "amo_forever_config",
)
#: 允许写的文件（兼容层自己 + 测试/文档/实验脚本除外）
ALLOW_FILES = {"amo_compat.py"}


def _production_py_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("tests", "work", "docs", "__pycache__", ".git")]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(base, name)


class CompatNamesTest(unittest.TestCase):
    def test_new_name_first(self):
        self.assertEqual("Astrbot_amoupup_data", amo_compat.DATA_PLUGIN_NAME)
        self.assertEqual("Astrbot_amoupup_data", amo_compat.DATA_PLUGIN_NAMES[0])
        self.assertEqual("Astrbot_amoupup_photo", amo_compat.IMAGE_PLUGIN_NAMES[0])
        self.assertEqual("Astrbot_amoupup_photo", amo_compat.IMAGE_DATA_DIR_NAMES[0])
        self.assertIn("astrbot_plugin_omnidraw", amo_compat.IMAGE_PLUGIN_NAMES)
        self.assertEqual("astrbot_plugin_omnidraw", amo_compat.IMAGE_DATA_DIR_NAMES[-1])

    def test_no_legacy_literals_outside_compat(self):
        hits = []
        for path in _production_py_files():
            if os.path.basename(path) in ALLOW_FILES:
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for literal in LEGACY_LITERALS:
                if literal in text:
                    hits.append((os.path.relpath(path, ROOT), literal))
        self.assertEqual([], hits, "旧名字面量必须收进 amo_compat.py")

    def test_legacy_config_paths(self):
        paths = amo_compat.legacy_config_paths(os.path.join("C:", "cfg"))
        self.assertEqual(2, len(paths))
        self.assertTrue(paths[0].endswith("amo_forever_config.json"))
        self.assertTrue(paths[1].endswith("astrbot_plugin_amo_forever_config.json"))
        self.assertEqual([], amo_compat.legacy_config_paths(""))

    def test_legacy_db_paths(self):
        paths = amo_compat.legacy_db_paths(os.path.join("C:", "data"))
        self.assertEqual(2, len(paths))
        self.assertIn(os.path.join("plugin_data", "amo_forever"), paths[0])
        self.assertIn(os.path.join("plugin_data", "astrbot_plugin_amo_forever"), paths[1])
        self.assertEqual([], amo_compat.legacy_db_paths(""))

    def test_image_data_dirs(self):
        dirs = amo_compat.image_data_dirs(os.path.join("C:", "pd"), "amo_templates")
        self.assertEqual(2, len(dirs))
        self.assertTrue(dirs[0].endswith(os.path.join("Astrbot_amoupup_photo", "amo_templates")))
        self.assertTrue(dirs[1].endswith(os.path.join("astrbot_plugin_omnidraw", "amo_templates")))
        self.assertEqual([], amo_compat.image_data_dirs(""))

    def test_is_legacy_name(self):
        self.assertTrue(amo_compat.is_legacy_name("Astr_Amo_data"))
        self.assertTrue(amo_compat.is_legacy_name("astrbot_plugin_omnidraw"))
        self.assertTrue(amo_compat.is_legacy_name("amo_forever"))
        self.assertFalse(amo_compat.is_legacy_name("Astrbot_amoupup_data"))
        self.assertFalse(amo_compat.is_legacy_name(""))
        self.assertFalse(amo_compat.is_legacy_name("   "))


if __name__ == "__main__":
    unittest.main()
