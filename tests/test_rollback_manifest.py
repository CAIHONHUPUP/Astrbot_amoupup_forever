# -*- coding: utf-8 -*-
"""P0 回滚演练：清单生成 / sha 校验 / 容器布局 / 默认根 / 缺清单拒绝 / 副本 apply。

背景（retro-audit 实测）：22/22 备份目录存在，但 D:/dafeiyu/backup/**/manifest.json = 0，
apply 全部 "EXIT 2 清单不存在"；plan 默认参数又因路径越界拒绝 4/4 备份。
这里用临时目录复现实盘布局，不依赖 D:/dafeiyu。
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def load_tool(name):
    path = os.path.join(ROOT, "tools", name)
    spec = importlib.util.spec_from_file_location("audit_" + name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rollback = load_tool("rollback.py")
make_manifest = load_tool("make_manifest.py")


def make_tree(root, files):
    for rel, text in files.items():
        path = os.path.join(root, rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="audit-rollback-")
        self.backup = os.path.join(self.base, "backup")
        self.target = os.path.join(self.base, "plugins", "Astrbot_amoupup_forever")
        os.makedirs(self.backup, exist_ok=True)
        os.makedirs(self.target, exist_ok=True)
        make_tree(self.backup, {"main.py": "VALUE = 2\n", "services/a.py": "X = 1\n"})
        make_tree(self.target, {"main.py": "VALUE = 1\n", "services/a.py": "X = 0\n"})

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _manifest(self):
        return rollback.write_manifest(
            self.backup, os.path.join(self.backup, rollback.MANIFEST_NAME))

    def test_manifest_schema_sha_and_created_at(self):
        self._manifest()
        with open(os.path.join(self.backup, rollback.MANIFEST_NAME), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["schema"], 2)
        self.assertEqual(saved["plugin_root"], ".")
        self.assertTrue(saved["created_at"])
        self.assertEqual(saved["files"]["main.py"], sha("VALUE = 2\n"))
        self.assertEqual(saved["count"], 2)
        checked = rollback.verify(self.backup, os.path.join(self.backup, "manifest.json"),
                                  self.base)
        self.assertTrue(checked["ok"], checked)

    def test_verify_detects_tampered_sha(self):
        self._manifest()
        with open(os.path.join(self.backup, "services", "a.py"), "w", encoding="utf-8") as fh:
            fh.write("X = 9\n")
        checked = rollback.verify(self.backup, os.path.join(self.backup, "manifest.json"),
                                  self.base)
        self.assertFalse(checked["ok"])
        self.assertIn("services/a.py", checked["diff"]["changed"])

    def test_container_layout_generates_and_plans(self):
        # 容器布局：<backup>/plugins/Astrbot_amoupup_data/... + <backup>/README
        backup = os.path.join(self.base, "backup", "full-refactor-pre-20261116-195014")
        make_tree(backup, {"README.txt": "x\n"})
        make_tree(os.path.join(backup, "plugins", "Astrbot_amoupup_data"),
                  {"main.py": "VALUE = 2\n", "services/a.py": "X = 1\n"})
        target = os.path.join(self.base, "plugins", "Astrbot_amoupup_data")
        make_tree(target, {"main.py": "VALUE = 1\n", "services/a.py": "X = 0\n"})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = rollback.main(["manifest", "--dir", backup, "--root", self.base])
        self.assertEqual(code, 0, out.getvalue())
        with open(os.path.join(backup, "manifest.json"), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["plugin_root"], "plugins/Astrbot_amoupup_data")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rollback.main(["verify", "--dir", backup, "--root", self.base]),
                             0, out.getvalue())
            self.assertEqual(rollback.main(["plan", "--backup", backup, "--target", target,
                                            "--root", self.base]), 0, out.getvalue())
        result = rollback.plan(backup, target, self.base)
        self.assertEqual((result["add"], result["overwrite"]), (0, 2), result)
        self.assertEqual(os.path.normcase(result["root"]),
                         os.path.normcase(os.path.join(backup, "plugins",
                                                       "Astrbot_amoupup_data")))

    def test_multi_plugin_container_writes_per_plugin_manifests(self):
        backup = os.path.join(self.base, "backup", "multi-bk")
        for name in ("Astrbot_amoupup_data", "Astrbot_amoupup_forever"):
            make_tree(os.path.join(backup, "plugins", name), {"main.py": "VALUE = 2\n"})
        targets = rollback.manifest_targets(backup)
        self.assertEqual(len(targets), 2, targets)
        for backup_dir, root, out in targets:
            data = rollback.write_manifest(root, out, backup_dir=backup_dir)
            self.assertEqual(data["plugin_root"], ".")
            self.assertTrue(os.path.isfile(out))
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            code = rollback.main(["verify", "--dir", backup, "--root", self.base])
        self.assertEqual(code, 0, stream.getvalue())
        self.assertIn("Astrbot_amoupup_data", stream.getvalue())
        self.assertIn("Astrbot_amoupup_forever", stream.getvalue())
        target = os.path.join(self.base, "plugins", "Astrbot_amoupup_forever")
        make_tree(target, {"main.py": "VALUE = 1\n"})
        result = rollback.plan(backup, target, self.base)
        self.assertEqual(os.path.normcase(result["root"]),
                         os.path.normcase(os.path.join(backup, "plugins",
                                                       "Astrbot_amoupup_forever")))
        self.assertEqual((result["add"], result["overwrite"]), (0, 1), result)

    def test_default_root_auto_finds_backup_sibling(self):
        # 实盘形态：<tmp>/plugins/<plugin> 与 <tmp>/backup/<bk>；不传 --root 也要能跑
        sibling = os.path.join(self.base, "backup", "clean-schedule-db-20261117-0304")
        make_tree(sibling, {"amo_forever.db": "db\n"})
        original = rollback.DEFAULT_BASE
        rollback.DEFAULT_BASE = os.path.join(self.base, "plugins")
        try:
            base = rollback.pick_base([sibling, self.target], "")
            self.assertEqual(os.path.normcase(base), os.path.normcase(self.base))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(rollback.main(["manifest", "--dir", sibling]),
                                 0, out.getvalue())
                self.assertEqual(rollback.main(["plan", "--backup", sibling,
                                                "--target", self.target]),
                                 0, out.getvalue())
        finally:
            rollback.DEFAULT_BASE = original

    def test_missing_manifest_is_refused(self):
        with self.assertRaises(rollback.Refused):
            rollback.verify(self.backup, os.path.join(self.base, "nope.json"), self.base)
        with self.assertRaises(rollback.Refused):
            rollback.apply(self.backup, self.target, "whatever",
                           os.path.join(self.base, "nope.json"), self.base)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = rollback.main(["apply", "--backup", self.backup, "--target", self.target,
                                  "--root", self.base])
        self.assertEqual(code, 2)
        self.assertIn("清单不存在", out.getvalue())

    def test_apply_rehearses_on_temp_copy(self):
        self._manifest()
        manifest_path = os.path.join(self.backup, "manifest.json")
        dry = rollback.apply(self.backup, self.target, "", manifest_path, self.base)
        self.assertTrue(dry["dry_run"])
        result = rollback.apply(self.backup, self.target, dry["token"], manifest_path, self.base)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["dry_run"])
        self.assertTrue(os.path.isdir(result["shelter"]))
        with open(os.path.join(self.target, "main.py"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "VALUE = 2\n")
        self.assertEqual(result["compile"]["checked"], 2)
        self.assertEqual((result["add"], result["overwrite"]), (0, 2))

    def test_make_manifest_batch_scan(self):
        make_tree(os.path.join(self.backup, "photo-bg-20261117011923"),
                  {"main.py": "VALUE = 9\n"})
        code = make_manifest.main(["--scan", self.backup, "--check", "--root", self.base])
        self.assertEqual(code, 0)
        path = os.path.join(self.backup, "photo-bg-20261117011923", "manifest.json")
        self.assertTrue(os.path.isfile(path), path)
        self.assertTrue(rollback.verify(os.path.dirname(path), path, self.base)["ok"])


if __name__ == "__main__":
    unittest.main()
