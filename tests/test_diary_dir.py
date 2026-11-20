# -*- coding: utf-8 -*-
"""日记目录合规性测试（上架规范：数据只能写在 data/plugin_data/<plugin_name>）。

审核驳回点：``diary_engine.DEFAULT_DIR`` 和 ``_conf_schema.json`` 的 ``diary_dir``
默认值都是作者本机路径 ``D:\\dafeiyu\\日记``，``_diary_dir()`` 直接拿来 makedirs/写文件，
等于把用户数据写到插件数据目录之外。这里锁住修复后的行为：

- 留空 -> ``<data_root>/diary``
- 相对路径 -> 挂在 ``<data_root>/`` 下
- 绝对路径只有落在 ``<data_root>`` 内才认，写到别处回退默认目录
- 旧的 Windows 盘符默认值一律回退默认目录
- 仓库里不再出现作者个人目录
"""

from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from diary_engine import DEFAULT_DIR, resolve_dir  # noqa: E402

DATA_ROOT = os.path.abspath(os.path.join("/tmp", "astrbot-data", "plugin_data", "Astrbot_Amo_forever"))


class ResolveDirTests(unittest.TestCase):
    def test_empty_setting_uses_plugin_data_subdir(self):
        self.assertEqual(resolve_dir("", DATA_ROOT), os.path.join(DATA_ROOT, "diary"))
        self.assertEqual(resolve_dir(None, DATA_ROOT), os.path.join(DATA_ROOT, "diary"))
        self.assertEqual(resolve_dir("   ", DATA_ROOT), os.path.join(DATA_ROOT, "diary"))

    def test_relative_setting_hangs_under_plugin_data(self):
        self.assertEqual(
            resolve_dir("日记", DATA_ROOT), os.path.abspath(os.path.join(DATA_ROOT, "日记"))
        )
        self.assertEqual(
            resolve_dir("custom/diary", DATA_ROOT),
            os.path.abspath(os.path.join(DATA_ROOT, "custom/diary")),
        )

    def test_absolute_inside_plugin_data_is_kept(self):
        inside = os.path.join(DATA_ROOT, "my_diary")
        self.assertEqual(resolve_dir(inside, DATA_ROOT), os.path.abspath(inside))

    def test_absolute_outside_plugin_data_falls_back(self):
        outside = os.path.join("/tmp", "somewhere-else")
        self.assertEqual(resolve_dir(outside, DATA_ROOT), os.path.join(DATA_ROOT, "diary"))
        self.assertEqual(resolve_dir("/", DATA_ROOT), os.path.join(DATA_ROOT, "diary"))

    def test_legacy_windows_path_falls_back(self):
        for legacy in ("D:\\dafeiyu\\日记", "C:/Users/me/diary", "d:\\日记"):
            self.assertEqual(
                resolve_dir(legacy, DATA_ROOT),
                os.path.join(DATA_ROOT, "diary"),
                "旧默认值 %s 必须回退到插件数据目录" % legacy,
            )

    def test_empty_root_does_not_point_at_cwd(self):
        self.assertEqual(resolve_dir("", ""), "")
        self.assertEqual(resolve_dir("日记", ""), "日记")

    def test_result_never_escapes_root(self):
        for setting in ("../outside", "../../etc", "日记/../../外面", "D:\\dafeiyu\\日记"):
            resolved = resolve_dir(setting, DATA_ROOT)
            self.assertTrue(
                os.path.abspath(resolved).startswith(os.path.abspath(DATA_ROOT) + os.sep),
                "%s 解析后跑到数据目录外了: %s" % (setting, resolved),
            )


class SourceGuardTests(unittest.TestCase):
    def test_default_dir_is_not_author_path(self):
        self.assertEqual(str(DEFAULT_DIR or ""), "")

    def test_schema_default_is_empty(self):
        with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as fp:
            schema = json.load(fp)
        self.assertEqual(schema["diary_dir"]["default"], "")

    def test_no_author_personal_path_in_repo(self):
        offenders = []
        for base, _dirs, files in os.walk(ROOT):
            if os.sep + ".git" in base or os.sep + "tests" in base:
                continue
            for name in files:
                if not name.endswith((".py", ".json")):
                    continue
                path = os.path.join(base, name)
                with open(path, encoding="utf-8", errors="ignore") as fp:
                    if "dafeiyu" in fp.read():
                        offenders.append(os.path.relpath(path, ROOT))
        self.assertEqual(offenders, [], "仓库里仍有作者个人路径: %s" % offenders)


if __name__ == "__main__":
    unittest.main()
