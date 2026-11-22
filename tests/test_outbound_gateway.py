# -*- coding: utf-8 -*-
"""统一 OutboundGateway（amo_outbound）并发/频控/批互斥验收（task-16）。

对应 work/audit/outbound_repeat.md §6.5 的真实场景：
- 07:30 七连发：并发待回复追问只发 1 条；
- 23:33 双晚安：同日同 kind 只发 1 条；
- 09:00 日记质检双发：同 source 90s 第二次被拒；
- 11-19 20:00 天气插队：chat 批进行中非 chat 消息进 deferred，不插中间；
- source 缺省 fail-closed 当 push，绝不默认 chat；
- 频控：20s<=4、60s<=6、300s<=10、nudge 20min<=1、沉默>=6h=0。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_outbound as gw  # noqa: E402
import clock  # noqa: E402
from test_cov2_proactive import FakeProactive, ProactiveDB, _send_lines  # noqa: E402

NOW = clock.now_ts()


class FakeDB:
    def __init__(self, profiles=None):
        self.kv = {}
        self.profiles = list(profiles or [])

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def proactive_profiles(self, limit=100):
        return [dict(row) for row in self.profiles[:limit]]


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))
        return True


class FakePlugin:
    def __init__(self, profiles=None):
        self.db = FakeDB(profiles)
        self.context = FakeContext()


def _plugin(last_seen=None, umo_suffix=""):
    profiles = []
    if last_seen is not None:
        profiles = [{"uid": "u1", "last_seen": last_seen,
                     "private_umo": "umo-test%s" % umo_suffix}]
    return FakePlugin(profiles)


class GatewaySendTests(unittest.TestCase):
    def test_fail_closed_source_defaults_to_push(self):
        p = _plugin(last_seen=NOW - 60, umo_suffix="-fc")
        ok = asyncio.run(gw.send(p, "umo-test-fc", "在干嘛", source=None, now=NOW))
        self.assertTrue(ok)
        self.assertEqual(gw.rows(p, "umo-test-fc")[-1]["source"], "push")

    def test_same_source_90s_second_rejected(self):
        p = _plugin(last_seen=NOW - 60, umo_suffix="-s1")
        self.assertTrue(asyncio.run(gw.send(p, "umo-test-s1", "在干嘛", source="proactive", now=NOW)))
        self.assertFalse(asyncio.run(gw.send(p, "umo-test-s1", "在干嘛", source="proactive", now=NOW + 30)))
        reasons = [row.get("reason") for row in gw.rows(p, "umo-test-s1")]
        self.assertIn("same_source_90s", reasons)

    def test_cross_source_300s_rejected(self):
        p = _plugin(last_seen=NOW - 60, umo_suffix="-x1")
        self.assertTrue(asyncio.run(gw.send(p, "umo-test-x1", "早点睡", source="goodnight", kind="gn", now=NOW)))
        self.assertFalse(asyncio.run(gw.send(p, "umo-test-x1", "今天天气不错", source="push",
                                             now=NOW + 100)))
        self.assertIn("cross_source_300s",
                      [row.get("reason") for row in gw.rows(p, "umo-test-x1")])

    def test_silence_blocks_non_chat_but_not_chat(self):
        p = _plugin(last_seen=NOW - 7 * 3600, umo_suffix="-sil")
        self.assertFalse(asyncio.run(gw.send(p, "umo-test-sil", "你在干嘛", source="push", now=NOW)))
        self.assertIn("silence_6h", [row.get("reason") for row in gw.rows(p, "umo-test-sil")])
        self.assertTrue(asyncio.run(gw.send(p, "umo-test-sil", "早呀", source="chat", now=NOW)))

    def test_concurrent_same_intent_only_one_sent(self):
        p = _plugin(last_seen=NOW - 60, umo_suffix="-cc")

        async def burst():
            return await asyncio.gather(*[
                gw.send(p, "umo-test-cc", "老婆等你半天了，忙完瞄一眼", source="proactive", now=NOW)
                for _ in range(6)
            ])

        results = asyncio.run(burst())
        self.assertEqual(sum(1 for item in results if item), 1, results)
        self.assertEqual(len(p.context.sent), 1, "并发同一意图只能发 1 条")
        drops = [row.get("reason") for row in gw.rows(p, "umo-test-cc")
                 if row.get("decision") == "drop"]
        self.assertTrue(drops)

    def test_nudge_20min_once(self):
        p = _plugin(last_seen=NOW - 60, umo_suffix="-nd")
        self.assertTrue(asyncio.run(gw.send(p, "umo-test-nd", "你美丽的老婆晾这儿半天了",
                                            source="proactive", kind="nudge", now=NOW)))
        self.assertFalse(asyncio.run(gw.send(p, "umo-test-nd", "老公你倒是看我一眼呀",
                                             source="followup", kind="nudge", now=NOW + 700)))
        self.assertIn("nudge_20m",
                      [row.get("reason") for row in gw.rows(p, "umo-test-nd")])

    def test_goodnight_same_day_once(self):
        p = _plugin(last_seen=NOW - 60, umo_suffix="-gn")
        self.assertTrue(asyncio.run(gw.send(p, "umo-test-gn", "晚安，梦里抱一下",
                                            source="goodnight", kind="goodnight", now=NOW)))
        self.assertFalse(asyncio.run(gw.send(p, "umo-test-gn", "老公晚安啦，梦里见",
                                             source="goodnight", kind="goodnight", now=NOW + 900)))
        self.assertIn("same_day_kind",
                      [row.get("reason") for row in gw.rows(p, "umo-test-gn")])

    def test_burst_20s_window_counts_chat(self):
        p = _plugin(last_seen=NOW - 60, umo_suffix="-burst")
        for index in range(5):
            gw.note_chat(p, "umo-test-burst", "聊天第%d句" % index)
        self.assertFalse(asyncio.run(gw.send(p, "umo-test-burst", "系统提醒：到点了",
                                             source="push", now=NOW)))
        self.assertIn("burst_20s",
                      [row.get("reason") for row in gw.rows(p, "umo-test-burst")])


class ChatBatchTests(unittest.TestCase):
    def test_non_chat_deferred_during_chat_batch(self):
        p = _plugin(last_seen=NOW - 60, umo_suffix="-batch")
        batch = gw.begin_chat_batch(p, "umo-test-batch", "b1", now=NOW)
        self.assertTrue(gw.chat_active("umo-test-batch", NOW))
        sent = asyncio.run(gw.send(p, "umo-test-batch", "明天要下雨，伞带好",
                                   source="push", now=NOW))
        self.assertTrue(sent, "已入 deferred 队列，调用方不重试")
        self.assertEqual(p.context.sent, [], "chat 批进行中不能插队")
        queued = gw.take_deferred(p, "umo-test-batch")
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["source"], "push")
        self.assertIn("chat_batch",
                      [row.get("reason") for row in gw.rows(p, "umo-test-batch")])
        gw.end_chat_batch(p, "umo-test-batch", batch, now=NOW)

    def test_chat_batch_end_keeps_cooldown(self):
        p = _plugin(last_seen=NOW - 60, umo_suffix="-cool")
        gw.begin_chat_batch(p, "umo-test-cool", "b2", now=NOW)
        gw.end_chat_batch(p, "umo-test-cool", "b2", now=NOW)
        self.assertTrue(gw.chat_active("umo-test-cool", NOW + 1))
        self.assertFalse(gw.chat_active("umo-test-cool", NOW + 31))
        gw.take_deferred(p, "umo-test-cool")


class FollowupConcurrencyTests(unittest.TestCase):
    def test_seven_concurrent_followups_send_one(self):
        calls = {"n": 0}

        async def live(*args, **kwargs):
            calls["n"] += 1
            await asyncio.sleep(0)
            return "老婆等你半天了，忙完瞄一眼嘛"

        db = ProactiveDB(profiles=[{"uid": "u1", "last_seen": NOW - 60,
                                    "private_umo": "lark:private:u1"}])
        p = FakeProactive(db=db)
        p._followup_text_live = live
        p.config["proactive_followup_minutes"] = 20
        p.config["proactive_no_reply_minutes"] = 0
        p.db.kv["proactive_pending_u1"] = {
            "sent_at": NOW - 25 * 60, "kind": "greet", "text": "在忙吗",
            "umo": "lark:private:u1", "followup": False, "mood": True}

        from unittest import mock

        async def burst():
            with mock.patch("amo_proactive.quiet_now", lambda spec: False):
                await asyncio.gather(*[
                    ProactiveMixin._check_proactive_pending_once(p) for _ in range(7)
                ])

        from amo_proactive import ProactiveMixin  # noqa: E402

        asyncio.run(burst())
        self.assertEqual(calls["n"], 1, "并发扫描只允许一次生成")
        self.assertEqual(len(p.sent), 1, "07:30 七连发收口为 1 条")
        self.assertTrue((p.db.kv.get("proactive_pending_u1") or {}).get("followup"))


if __name__ == "__main__":
    unittest.main()
