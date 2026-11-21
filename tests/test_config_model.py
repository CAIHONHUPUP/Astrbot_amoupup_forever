# -*- coding: utf-8 -*-
"""ConfigModel（Phase 4）：schema 单一真相 / 校验 / 掩码 / 只读快照 / 迁移编排。

这些测试只碰内存与临时目录，不读写 plugin_data。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_config_model as cm  # noqa: E402
from amo_config_model import ConfigModel, load_schema, mask_secret, run_migrations  # noqa: E402
from amo_config import ConfigMixin  # noqa: E402

SCHEMA = {
    "flag": {"type": "bool", "default": True},
    "count": {"type": "int", "default": 3, "min": 1, "max": 10},
    "ratio": {"type": "float", "default": 0.5},
    "name": {"type": "string", "default": "小鱼"},
    "mode": {"type": "string", "default": "a", "options": [{"value": "a"}, {"value": "b"}]},
    "items": {"type": "list", "default": []},
    "rows": {"type": "template_list", "default": []},
}


class ConfigDict(dict):
    config_path = ""

    def save_config(self):
        return None


class FakePlugin(ConfigMixin):
    def __init__(self, config=None, plugin_dir=""):
        self.config = config if config is not None else ConfigDict()
        self.plugin_dir = plugin_dir


def make_plugin(values):
    directory = tempfile.mkdtemp(prefix="amo-model-")
    with open(os.path.join(directory, "_conf_schema.json"), "w", encoding="utf-8") as fh:
        json.dump(SCHEMA, fh)
    return FakePlugin(ConfigDict(values), plugin_dir=directory)


class SchemaLoadTests(unittest.TestCase):
    def test_real_schema_shape_is_272_keys(self):
        schema = load_schema(ROOT)
        self.assertEqual(272, len(schema), "schema 键数变化需要同步 ConfigModel 测试")
        counts = {}
        for meta in schema.values():
            counts[str(meta.get("type"))] = counts.get(str(meta.get("type")), 0) + 1
        self.assertEqual(89, counts.get("int"))
        self.assertEqual(88, counts.get("bool"))
        self.assertEqual(56, counts.get("string"))
        self.assertEqual(30, counts.get("float"))

    def test_missing_schema_returns_empty(self):
        self.assertEqual({}, load_schema(os.path.join(ROOT, "没有这个目录")))

    def test_quality_gate_schema_key_count_matches(self):
        import importlib.util
        gate_path = os.path.join(ROOT, "tools", "quality_gate.py")
        spec = importlib.util.spec_from_file_location("quality_gate_schema_count", gate_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.SCHEMA_KEY_COUNT_EXPECTED, len(load_schema(ROOT)))


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.model = ConfigModel({"count": 7, "name": None}, SCHEMA)

    def test_get_live_value_and_none_default(self):
        self.assertEqual(7, self.model.get("count"))
        self.assertEqual("兜底", self.model.get("name", "兜底"))
        self.assertEqual("默认", self.model.get("missing", "默认"))

    def test_bool_parsing(self):
        model = ConfigModel({"a": "yes", "b": True, "c": "off", "d": "是", "e": 0}, SCHEMA)
        self.assertTrue(model.get_bool("a"))
        self.assertTrue(model.get_bool("b"))
        self.assertFalse(model.get_bool("c"))
        self.assertTrue(model.get_bool("d"))
        self.assertFalse(model.get_bool("e"))

    def test_int_float_parsing_and_bad_fallback(self):
        model = ConfigModel({"num": "3.7", "bad": "abc"}, SCHEMA)
        self.assertEqual(3, model.get_int("num"))
        self.assertEqual(9, model.get_int("bad", 9))
        self.assertEqual(3.7, model.get_float("num"))
        self.assertEqual(1.5, model.get_float("bad", 1.5))

    def test_str_and_list(self):
        model = ConfigModel({"text": 123, "rows": [{"a": 1}], "bad": "x"}, SCHEMA)
        self.assertEqual("123", model.get_str("text"))
        self.assertEqual([{"a": 1}], model.get_list("rows"))
        self.assertEqual([], model.get_list("bad"))
        self.assertEqual([1], model.get_list("missing", [1]))

    def test_broken_config_returns_default(self):
        class Boom(dict):
            def get(self, key, default=None):
                raise RuntimeError("配置挂了")

        model = ConfigModel(Boom(), SCHEMA)
        self.assertEqual("兜底", model.get_str("name", "兜底"))


class ValidationTests(unittest.TestCase):
    def test_type_mismatch_reported(self):
        model = ConfigModel({"count": "abc"}, SCHEMA)
        issue = [item for item in model.issues() if item.key == "count"][0]
        self.assertEqual("type_mismatch", issue.kind)
        self.assertEqual("error", issue.level)

    def test_unknown_key_reported(self):
        model = ConfigModel({"zeta": 1}, SCHEMA)
        issue = [item for item in model.issues() if item.key == "zeta"][0]
        self.assertEqual("unknown_key", issue.kind)
        self.assertEqual("warning", issue.level)

    def test_options_out_of_range(self):
        model = ConfigModel({"mode": "z"}, SCHEMA)
        issue = [item for item in model.issues() if item.key == "mode"][0]
        self.assertEqual("out_of_range", issue.kind)

    def test_min_max_out_of_range(self):
        model = ConfigModel({"count": 99}, SCHEMA)
        issue = [item for item in model.issues() if item.key == "count"][0]
        self.assertEqual("out_of_range", issue.kind)

    def test_legacy_key_reported_then_cleaned(self):
        cm.register_legacy_key("old_name", "name")
        try:
            model = ConfigModel({"old_name": "x"}, SCHEMA)
            issue = [item for item in model.issues() if item.key == "old_name"][0]
            self.assertEqual("legacy_key", issue.kind)
            self.assertIn("name", issue.message)
        finally:
            cm.LEGACY_KEYS.pop("old_name", None)

    def test_health_is_json_serializable(self):
        model = ConfigModel({"count": "abc", "zeta": 1}, SCHEMA)
        health = model.health()
        self.assertTrue(health)
        json.dumps(health, ensure_ascii=False)
        self.assertTrue(all(isinstance(item, dict) for item in health))

    def test_weird_values_do_not_raise(self):
        model = ConfigModel({"count": {"nested": [1]}, "rows": "not-a-list"}, SCHEMA)
        self.assertEqual(2, len(model.issues()))


class SnapshotAndMaskTests(unittest.TestCase):
    def test_mask_secret(self):
        self.assertEqual("", mask_secret(""))
        self.assertEqual("****", mask_secret("abcd"))
        self.assertEqual("abcd****wxyz", mask_secret("abcd1234wxyz"))

    def test_snapshot_is_read_only_and_frozen(self):
        model = ConfigModel({"nested": {"rows": [1, 2]}, "items": [{"a": 1}]}, SCHEMA)
        snap = model.snapshot()
        with self.assertRaises(TypeError):
            snap["items"] = ()
        with self.assertRaises(TypeError):
            snap["nested"]["rows"] = ()
        self.assertEqual((1, 2), snap["nested"]["rows"])
        self.assertIsInstance(snap["items"], tuple)


class MigrationTests(unittest.TestCase):
    def test_default_order_runs_registered_hooks_in_order(self):
        class Plugin:
            def __init__(self):
                self.calls = []
                for _, method in cm.MIGRATION_HOOKS:
                    setattr(self, method, lambda name=method: self.calls.append(name))

        plugin = Plugin()
        ran = run_migrations(plugin)
        expected = [method for _, method in cm.MIGRATION_HOOKS
                    if method != cm.BOOT_MIGRATION]
        self.assertEqual(expected, plugin.calls)
        self.assertEqual(expected, ran)

    def test_boot_order_only_legacy(self):
        class Plugin:
            def __init__(self):
                self.calls = []

            def _migrate_legacy_config(self):
                self.calls.append("legacy")

        plugin = Plugin()
        run_migrations(plugin, boot=True)
        self.assertEqual(["legacy"], plugin.calls)

    def test_failure_is_swallowed_and_others_still_run(self):
        class Plugin:
            def __init__(self):
                self.calls = []

            def _migrate_a(self):
                self.calls.append("a")

            def _migrate_b(self):
                raise RuntimeError("boom")

            def _migrate_c(self):
                self.calls.append("c")

        plugin = Plugin()
        ran = run_migrations(plugin, order=("_migrate_a", "_migrate_b", "_migrate_c"))
        self.assertEqual(["a", "c"], plugin.calls)
        self.assertEqual(["_migrate_a", "_migrate_c"], ran)


class ConfigMixinDelegateTests(unittest.TestCase):
    def test_cfg_helpers_delegate_to_model(self):
        plugin = make_plugin({"flag": "yes", "count": "4.9", "rows": [{"a": 1}], "name": 123})
        self.assertTrue(plugin.cfg_bool("flag"))
        self.assertEqual(4, plugin.cfg_int("count"))
        self.assertEqual([{"a": 1}], plugin.cfg_list("rows"))
        self.assertEqual("123", plugin.cfg_str("name"))
        self.assertIsInstance(plugin._get_config_model(), ConfigModel)

    def test_config_health_and_schema_map(self):
        plugin = make_plugin({"count": "abc", "zeta": 1, "mode": "z"})
        health = plugin.config_health()
        kinds = {item["kind"] for item in health}
        self.assertIn("type_mismatch", kinds)
        self.assertIn("unknown_key", kinds)
        self.assertIn("out_of_range", kinds)
        self.assertEqual(SCHEMA, plugin._schema_map())
        self.assertEqual("a*b", plugin._coerce_config_value("string", "a*b"))

    def test_run_config_migrations_delegates(self):
        plugin = make_plugin({})
        plugin._migrate_delay_default = lambda: plugin.__dict__.setdefault("ran", []).append("d")
        ran = plugin.run_config_migrations()
        self.assertIn("_migrate_delay_default", ran)
        self.assertEqual(["d"], plugin.ran)


if __name__ == "__main__":
    unittest.main()
