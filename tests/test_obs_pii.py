# -*- coding: utf-8 -*-
"""Phase 17：可观测性 / PII 脱敏 / 错误码登记（能陪侧）。

钉住三件事：

1. 脱敏是单向的：mask_* 的结果既不含原文，也回不去原文；
2. 结构化日志 `[event] k=v` 里不出现完整 uid / open_id / token / 手机号；
3. /status 只新增 obs_health（原 health 是「人物健康」，语义不同，不许被覆盖），
   错误码登记表里 0/1/403 三个数字码原样保留。
"""
from __future__ import annotations

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import errors  # noqa: E402
import main  # noqa: E402
import observability as obs  # noqa: E402

UID = "123456789"                      # 9 位 QQ 号
OPEN_ID = "ABCDEF1234567890XYZ"        # 19 位平台 open_id
TOKEN = "sk-live-abcdefghijklmnop"     # 23 位 token


class _Recorder:
    """把 logger 换成记录器：只收行，不落盘。"""

    def __init__(self):
        self.lines = []

    def __getattr__(self, _name):
        def _emit(*args, **_kwargs):
            self.lines.append(" ".join(str(arg) for arg in args))
        return _emit


class _FakeDB:
    def schema_status(self):
        return {"user_version": 7, "applied": [], "pending": [], "last_error": ""}

    def counts(self):
        return {"profiles": 3, "memories": 9}


class _BrokenDB:
    def schema_status(self):
        raise RuntimeError("db gone")

    def counts(self):
        raise RuntimeError("db gone")


class _FakeJobs:
    def report(self):
        return [{"name": "tick", "running": False}]


class MaskMatrixTests(unittest.TestCase):
    def test_mask_id_matrix(self):
        self.assertEqual(obs.mask_id(UID), "12****89")
        self.assertEqual(obs.mask_id("123456"), obs.FULL_MASK)
        self.assertEqual(obs.mask_id(""), "")
        self.assertEqual(obs.mask_id(None), "")

    def test_mask_open_id_token_secret(self):
        self.assertEqual(obs.mask_open_id(OPEN_ID), "ABCD****0XYZ")
        self.assertEqual(obs.mask_token(TOKEN), "****mnop")
        self.assertEqual(obs.mask_token("short"), obs.FULL_MASK)
        # api_key / secret 一位都不留
        self.assertEqual(obs.mask_secret("k-1234567890"), obs.FULL_MASK)
        self.assertEqual(obs.mask_secret(None), "")

    def test_mask_is_irreversible(self):
        for raw in (UID, OPEN_ID, TOKEN, "13800138000", "1234"):
            masked = obs.mask_value("uid", raw)
            self.assertNotEqual(masked, raw)
            self.assertNotIn(raw, masked)

    def test_fingerprint_is_stable_not_reversible(self):
        first = obs.fingerprint(UID)
        self.assertEqual(first, obs.fingerprint(UID))
        self.assertEqual(len(first), 8)
        self.assertNotIn(UID, first)
        self.assertNotEqual(first, obs.fingerprint("987654321"))


class EventLogTests(unittest.TestCase):
    def setUp(self):
        self._saved = obs.logger
        self.rec = _Recorder()
        obs.logger = self.rec

    def tearDown(self):
        obs.logger = self._saved

    def test_log_event_masks_sensitive_fields(self):
        line = obs.log_event("info", "profile.delete",
                             uid=UID, open_id=OPEN_ID, token=TOKEN, api_key="apikey-123456")
        self.assertTrue(line.startswith("[profile.delete]"), line)
        self.assertIn("uid=", line)
        for raw in (UID, OPEN_ID, TOKEN, "apikey-123456"):
            self.assertNotIn(raw, line)
        self.assertEqual(self.rec.lines, [line])

    def test_log_event_scrubs_free_text(self):
        line = obs.log_event("warning", "chat.raw", detail="对方 QQ 13800138000 说了一句")
        self.assertNotIn("13800138000", line)
        self.assertIn("chat.raw", line)

    def test_log_event_unknown_level_falls_back_to_info(self):
        line = obs.log_event("not-a-level", "weird", a=1)
        self.assertEqual(self.rec.lines, [line])
        self.assertTrue(line.startswith("[weird]"))

    def test_log_event_never_raises_on_bad_fields(self):
        class Bad:
            def __str__(self):
                raise RuntimeError("boom")
        line = obs.log_event("error", "boom", detail=Bad())  # 不能因为字段炸掉
        self.assertIn("boom", line)


class HealthSnapshotTests(unittest.TestCase):
    def test_health_snapshot_shape(self):
        snap = obs.health_snapshot("Astrbot_amoupup_forever", version="7.4.0",
                                   db={"user_version": 3}, jobs=_FakeJobs(),
                                   counts={"profiles": 1},
                                   started_at=1000.0, now=1064.0)
        self.assertEqual(snap["plugin"], "Astrbot_amoupup_forever")
        self.assertEqual(snap["version"], "7.4.0")
        self.assertEqual(snap["uptime_s"], 64)
        self.assertEqual(snap["db"], {"schema_version": 3, "ok": True})
        self.assertEqual(snap["jobs"][0]["name"], "tick")
        self.assertEqual(snap["counts"], {"profiles": 1})
        self.assertEqual(snap["error_code"], "")

    def test_health_snapshot_without_clock_has_no_uptime(self):
        snap = obs.health_snapshot("demo", db={"user_version": 1})
        self.assertIsNone(snap["uptime_s"])  # 不读系统时钟，给不出就是 None

    def test_health_snapshot_degrades_without_raising(self):
        snap = obs.health_snapshot("demo", db=_BrokenDB(), jobs=object())
        self.assertEqual(snap["db"], {"schema_version": None, "ok": False})
        self.assertEqual(snap["jobs"], [])
        self.assertEqual(snap["error_code"], "health.degraded")

    def test_obs_health_method_reads_plugin_state(self):
        fake = types.SimpleNamespace(db=_FakeDB(), jobs=_FakeJobs())
        snap = main.HumanCorePlugin._obs_health(fake)
        self.assertEqual(snap["plugin"], "Astrbot_amoupup_forever")
        self.assertEqual(snap["version"], main.PLUGIN_VERSION)
        self.assertEqual(snap["db"]["schema_version"], 7)
        self.assertTrue(snap["db"]["ok"])
        self.assertEqual(snap["jobs"], [{"name": "tick", "running": False}])
        self.assertEqual(snap["counts"]["profiles"], 3)

    def test_obs_health_method_degrades_on_broken_db(self):
        fake = types.SimpleNamespace(db=_BrokenDB())
        snap = main.HumanCorePlugin._obs_health(fake)  # 不抛
        self.assertFalse(snap["db"]["ok"])
        self.assertEqual(snap["error_code"], "health.degraded")


class ErrorRegistryTests(unittest.TestCase):
    def test_numeric_codes_unchanged(self):
        self.assertEqual(errors.CODE_BY_NUMBER, {0: "ok", 1: "fail", 403: "forbidden"})
        self.assertEqual(errors.code_number(errors.CODE_OK), 0)
        self.assertEqual(errors.code_number(errors.CODE_FAIL), 1)
        self.assertEqual(errors.code_number(errors.CODE_FORBIDDEN), 403)
        self.assertEqual(errors.code_number(1), 1)  # 数字原样透传

    def test_describe_code(self):
        self.assertIn("业务失败", errors.describe_code(1))
        self.assertIn("守卫拒绝", errors.describe_code("forbidden"))
        self.assertIn("降级", errors.describe_code(errors.CODE_HEALTH_DEGRADED))
        self.assertIn("未知", errors.describe_code("no-such-code"))


class StatusContractTests(unittest.TestCase):
    def _read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_status_response_keeps_health_and_adds_obs_health(self):
        api = self._read("amo_api.py")
        # 老字段（人物健康）逐字保留
        self.assertIn('"health": data["health"]["status"]', api)
        # 只新增 obs_health
        self.assertIn('"obs_health": data.get("obs_health") or {}', api)
        commands = self._read("amo_commands.py")
        self.assertIn('"obs_health": self._obs_health(),', commands)

    def test_route_table_unchanged(self):
        import amo_route_table

        self.assertEqual(len(amo_route_table.ROUTE_SPECS),
                         amo_route_table.ROUTE_COUNT_EXPECTED)


if __name__ == "__main__":
    unittest.main()
