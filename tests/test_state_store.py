# -*- coding: utf-8 -*-
"""StateStore（Phase 5）：kv 命名登记 / TTL 判定 / 只读健康报告 / dry-run 清理。

全部用临时 SQLite（.test-tmp 下的 AmoDB）或内存 FakeDB，不碰真实 plugin_data。
反向验证（TTL 判定失效 / prune 直接删 / 命名校验放行非法 key）见交付报告，

本文件对应的 3 个测试分别是：
- test_ttl_fresh_zero_semantics / test_get_fresh_returns_default_after_expiry
- test_prune_dry_run_does_not_delete
- test_classify_unknown_domain_is_reported
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixtures import FakeDB  # Phase 11 共享测试桩

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_state_store as ss  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_state_store import StateStore, classify_key, ttl_fresh  # noqa: E402

#: 固定“现在”：2026-11-16 附近，避免测试里出现真实时钟抖动
T0 = 1763300000.0




class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-state-")
        self._saved = {}
        # 装了 AstrBot 的环境里 AmoDB 会写真实 plugin_data；测试必须挡住
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.db = AmoDB(self.tmp)
        self.db.init()
        self.store = StateStore(self.db, now=lambda: T0)
        self._saved_health_at = ss._last_health_log_at
        ss._last_health_log_at = 0.0

    def tearDown(self):
        ss._last_health_log_at = self._saved_health_at
        try:
            self.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    # ---------- 命名登记 ----------
    def test_registry_domains_have_shape(self):
        required = ("diary", "schedule", "date", "travel", "trip", "wander", "follow",
                    "weather", "proactive", "health", "jit", "mood", "late", "home",
                    "period", "channels", "worldview", "photo", "shared", "current",
                    "micro", "ledger")
        domains = ss.registered_domains()
        for name in required:
            self.assertIn(name, domains)
        for name in domains:
            spec = ss.domain_spec(name)
            self.assertTrue(str(spec.get("purpose") or "").strip(), name)
            self.assertIsInstance(spec.get("default_ttl"), int, name)
            self.assertIsInstance(spec.get("allow_list"), bool, name)
            self.assertIsInstance(spec.get("allow_dict"), bool, name)
        self.assertEqual(ss.domain_spec("not_registered"), {})

    def test_classify_new_key_is_compliant(self):
        info = classify_key("weather:daily_forecast")
        self.assertTrue(info["compliant"])
        self.assertEqual(info["domain"], "weather")
        self.assertEqual(info["reason"], "")
        info = classify_key("diary:block:2026-11-16")
        self.assertTrue(info["compliant"])
        self.assertEqual(info["domain"], "diary")

    def test_classify_unknown_domain_is_reported(self):
        info = classify_key("nope_domain:thing")
        self.assertFalse(info["compliant"])
        self.assertEqual(info["reason"], "unknown_domain")
        self.assertEqual(info["domain"], "")
        self.assertEqual(info["domain_raw"], "nope_domain")

    def test_classify_legacy_prefix_maps_without_renaming(self):
        info = classify_key("micro_event:action")
        self.assertFalse(info["compliant"])
        self.assertEqual(info["domain"], "micro")
        self.assertEqual(info["reason"], "legacy_alias")
        legacy = classify_key("proactive_ignored_123")
        self.assertEqual(legacy["domain"], "proactive")
        self.assertEqual(legacy["reason"], "legacy_no_colon")

    def test_classify_bad_shape_and_empty(self):
        self.assertEqual(classify_key("")["reason"], "empty")
        self.assertEqual(classify_key("current:has space")["reason"], "bad_shape")
        self.assertEqual(classify_key("current:")["reason"], "bad_shape")

    # ---------- TTL 判定 ----------
    def test_ttl_fresh_zero_semantics(self):
        value = {"until": 0, "action": "吃饭"}
        self.assertEqual(ttl_fresh(value, T0), value)
        self.assertIsNone(ttl_fresh(value, T0, zero_is_expired=True))
        self.assertIsNone(ttl_fresh({}, T0))
        self.assertIsNone(ttl_fresh("text", T0))
        self.assertIsNone(ttl_fresh({"until": "oops"}, T0))
        self.assertIsNone(ttl_fresh({"until": T0 - 1}, T0))
        self.assertEqual(ttl_fresh({"until": T0 + 1}, T0), {"until": T0 + 1})

    def test_set_ttl_and_get_fresh_roundtrip(self):
        payload = self.store.set_ttl("current:action", {"action": "在做饭"}, 60)
        self.assertEqual(payload["until"], T0 + 60)
        self.assertEqual(self.store.get_fresh("current:action", now=T0 + 30)["action"], "在做饭")
        self.assertIsNone(self.store.get_fresh("current:action", now=T0 + 61))
        # 过期只是读侧不认，键还在库里（清理留给 prune）
        self.assertEqual(self.db.kv_get("current:action")["action"], "在做饭")

    def test_get_fresh_returns_default_after_expiry(self):
        self.store.set_ttl("date:fact", {"line": "刚约会完"}, 0, until=T0 - 1)
        self.assertEqual(self.store.get_fresh("date:fact", now=T0, default="fallback"), "fallback")
        self.store.set_ttl("date:fact", {"line": "刚约会完"}, 0, until=T0 + 1)
        self.assertEqual(self.store.get_fresh("date:fact", now=T0)["line"], "刚约会完")

    def test_set_ttl_rejects_scalar(self):
        with self.assertRaises(TypeError):
            self.store.set_ttl("current:scalar", 42, 60)

    # ---------- AmoDB kv 封装 ----------
    def test_kv_keys_prefix_pagination(self):
        for key in ("a:1", "a:2", "a:3", "b:1"):
            self.db.kv_set(key, key)
        self.assertEqual(self.db.kv_keys("a:", limit=2), ["a:1", "a:2"])
        self.assertEqual(self.db.kv_keys("a:", limit=2, offset=2), ["a:3"])
        self.assertEqual(self.db.kv_keys(limit=2), ["a:1", "a:2"])
        self.assertEqual(len(self.db.kv_keys(prefix="", limit=100000)), 4)

    def test_kv_delete_and_delete_many(self):
        self.db.kv_set("x:1", 1)
        self.db.kv_set("x:2", 2)
        self.db.kv_set("x:3", 3)
        self.assertTrue(self.db.kv_delete("x:1"))
        self.assertFalse(self.db.kv_delete("x:1"))
        self.assertIsNone(self.db.kv_get("x:1"))
        self.assertEqual(self.db.kv_delete_many(["x:2", "x:3"]), 2)
        self.assertEqual(self.db.kv_delete_many([]), 0)
        self.assertEqual(self.db.kv_keys("x:", limit=10), [])

    def test_kv_rows_returns_updated_and_parsed_value(self):
        self.db.kv_set("y:1", {"n": 1})
        rows = self.db.kv_rows("y:")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], "y:1")
        self.assertEqual(rows[0]["value"], {"n": 1})
        self.assertGreater(rows[0]["updated"], 0)
        self.db.conn.execute("INSERT OR REPLACE INTO kv(key, value, updated) VALUES(?,?,?)",
                             ("y:bad", "{not json", T0))
        self.db.conn.commit()
        bad = [row for row in self.db.kv_rows("y:") if row["key"] == "y:bad"][0]
        self.assertIsNone(bad["value"])

    def test_iter_prefix(self):
        self.store.set("z:1", {"a": 1})
        self.store.set("z:2", {"a": 2})
        pairs = dict(self.store.iter_prefix("z:"))
        self.assertEqual(pairs, {"z:1": {"a": 1}, "z:2": {"a": 2}})
        self.assertEqual(self.store.iter_prefix("none:"), [])

    # ---------- 健康报告 / prune ----------
    def test_prune_dry_run_does_not_delete(self):
        self.store.set_ttl("current:old", {"action": "旧"}, 0, until=T0 - 100)
        self.store.set_ttl("current:fresh", {"action": "新"}, 600)
        self.store.set("micro_event:bias:2020-01-01", {"bias": "太阳"})
        before = set(self.db.kv_keys(limit=100000))
        plan = self.store.prune(now=T0)
        self.assertFalse(plan["applied"])
        self.assertTrue(plan["dry_run"])
        self.assertIn("current:old", plan["candidates"])
        self.assertIn("micro_event:bias:2020-01-01", plan["candidates"])
        self.assertNotIn("current:fresh", plan["candidates"])
        self.assertEqual(set(self.db.kv_keys(limit=100000)), before)

    def test_prune_apply_requires_confirm_token(self):
        self.store.set_ttl("current:old", {"action": "旧"}, 0, until=T0 - 1)
        with self.assertRaises(ValueError):
            self.store.prune(now=T0, apply=True)
        self.assertIsNotNone(self.db.kv_get("current:old"))

    def test_prune_apply_deletes_only_candidates(self):
        self.store.set_ttl("current:old", {"action": "旧"}, 0, until=T0 - 1)
        self.store.set("micro_event:bias:2020-01-01", {"bias": "太阳"})
        self.store.set_ttl("current:fresh", {"action": "新"}, 600)
        plan = self.store.prune(now=T0, apply=True, confirm_token=ss.PRUNE_TOKEN)
        self.assertTrue(plan["applied"])
        self.assertGreaterEqual(plan["deleted"], 2)
        self.assertIsNone(self.db.kv_get("current:old"))
        self.assertIsNone(self.db.kv_get("micro_event:bias:2020-01-01"))
        self.assertIsNotNone(self.db.kv_get("current:fresh"))

    def test_kv_health_json_serializable_and_counts(self):
        self.store.set_ttl("current:old", {"action": "旧"}, 0, until=T0 - 1)
        self.store.set("nope_domain:thing", {"x": 1})
        self.store.set("proactive_ignored_u1", 2)
        report = self.store.kv_health(now=T0)
        text = json.dumps(report, ensure_ascii=False)
        self.assertIn("total_keys", text)
        self.assertEqual(report["total_keys"], len(self.db.kv_keys(limit=100000)))
        self.assertGreaterEqual(report["by_domain"].get("current", 0), 1)
        self.assertGreaterEqual(report["expired_count"], 1)
        self.assertIn("current:old", report["expired_keys"])
        self.assertIn("nope_domain", report["unknown_domains"])
        self.assertGreaterEqual(report["noncompliant_count"], 1)
        reasons = {item["reason"] for item in report["noncompliant_keys"]}
        self.assertIn("unknown_domain", reasons)
        self.assertIn("legacy_no_colon", reasons)
        self.assertFalse(report["prune"]["applied"])

    def test_orphan_candidates_use_updated_age(self):
        self.db.kv_set("legacy_state", {"v": 1})
        self.db.conn.execute("UPDATE kv SET updated=? WHERE key=?",
                             (T0 - 30 * 86400, "legacy_state"))
        self.db.conn.commit()
        report = self.store.kv_health(now=T0, orphan_days=14)
        self.assertIn("legacy_state", report["orphan_candidates"])
        self.assertGreaterEqual(report["orphan_count"], 1)
        # 只报告，不删除
        self.assertEqual(self.db.kv_get("legacy_state"), {"v": 1})

    def test_legacy_keys_are_classified_not_renamed(self):
        self.db.kv_set("proactive_ignored_u2", 5)
        self.db.kv_set("micro_event:last", {"at": 1})
        self.store.kv_health(now=T0)
        self.assertEqual(self.db.kv_get("proactive_ignored_u2"), 5)
        self.assertEqual(self.db.kv_get("micro_event:last"), {"at": 1})
        self.assertEqual(classify_key("proactive_ignored_u2")["domain"], "proactive")
        self.assertEqual(classify_key("micro_event:last")["domain"], "micro")

    def test_health_line_is_compact(self):
        self.store.set("current:action", {"action": "在做饭"})
        line = self.store.health_line(now=T0)
        self.assertIn("kv_health", line)
        self.assertIn("total=", line)
        self.assertIn("prune_candidates=", line)

    def test_should_log_health_is_throttled(self):
        self.assertTrue(ss.should_log_health(T0))
        self.assertFalse(ss.should_log_health(T0 + 10))
        self.assertTrue(ss.should_log_health(T0 + ss.HEALTH_REPORT_INTERVAL + 1))

    # ---------- 封装/兼容 ----------
    def test_for_plugin_and_attach(self):
        plugin = types.SimpleNamespace(db=FakeDB())
        store = ss.attach(plugin)
        self.assertIs(ss.for_plugin(plugin), store)
        self.assertIs(store.db, plugin.db)
        plain = types.SimpleNamespace(db=FakeDB())
        self.assertIsInstance(ss.for_plugin(plain), StateStore)

    def test_state_store_degrades_on_kv_get_only_db(self):
        store = StateStore(FakeDB(), now=lambda: T0)
        store.set_ttl("current:action", {"action": "在做饭"}, 60)
        self.assertEqual(store.get_fresh("current:action", now=T0 + 1)["action"], "在做饭")
        self.assertEqual(store.iter_prefix("current:"), [])
        self.assertEqual(store.rows(), [])
        self.assertFalse(store.delete("current:action"))

    def test_no_bare_kv_sql_outside_state_store(self):
        allowed = {"amo_db.py", "amo_state_store.py"}
        offenders = []
        for base, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in
                       ("__pycache__", ".git", ".test-tmp", "tests", "tools", "work")]
            for name in sorted(names):
                if not name.endswith(".py") or name in allowed:
                    continue
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as fh:
                    if "FROM kv" in fh.read():
                        offenders.append(os.path.relpath(path, ROOT).replace("\\", "/"))
        self.assertEqual(offenders, [])
        for name in ("amo_shared.py", "amo_social.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                self.assertNotIn("FROM kv", fh.read())

    def test_concurrent_kv_access_is_lock_safe(self):
        errors = []

        def worker(index):
            try:
                for step in range(20):
                    key = "thread:%d:%d" % (index, step)
                    self.db.kv_set(key, {"i": index, "s": step})
                    self.assertEqual(self.db.kv_get(key)["s"], step)
                    if step % 2:
                        self.db.kv_delete(key)
            except Exception as exc:  # 线程里的断言失败要带回主线程
                errors.append("%s: %s" % (type(exc).__name__, exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        live = [key for key in self.db.kv_keys("thread:", limit=1000)]
        self.assertEqual(len(live), 6 * 10)
        self.assertTrue(all(key.startswith("thread:") for key in live))


if __name__ == "__main__":
    unittest.main()
