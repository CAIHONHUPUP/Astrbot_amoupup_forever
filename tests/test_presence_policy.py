# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import clock  # noqa: E402

import amo_presence as P  # noqa: E402

#: 本文件统一冻结时刻：拒绝「测试用 time.time()、策略用 clock.now_ts()」两套时钟，
#: 冻结后 last_seen/until 与 amo_presence 的沉默判断是同一个「现在」。
FROZEN_NOW = datetime.datetime(2026, 11, 18, 15, 0, 0)


class _State:
    def __init__(self, place, zone, activity):
        self._scene = {"place": place, "zone": zone, "activity": activity}

    def current_activity(self):
        return dict(self._scene)


class _Db:
    def __init__(self, kv=None):
        self.kv = kv or {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)


class _Plugin:
    def __init__(self, scene, kv=None):
        self.state = _State(*scene)
        self.db = _Db(kv)


class PresenceTests(unittest.TestCase):
    def setUp(self):
        # 显式冻结：amo_presence 只认 clock.now_ts()。不依赖全量 conftest 的 autouse
        # freeze，单文件跑 / AMO_NO_FREEZE=1 时也能稳定；结束时还原外层冻结点。
        self._prev_frozen = clock.frozen()
        clock.freeze(FROZEN_NOW)

    def tearDown(self):
        if self._prev_frozen is None:
            clock.unfreeze()
        else:
            clock.freeze(self._prev_frozen)

    def test_silence_blocks_engagement(self):
        old = clock.now_ts() - 7 * 3600
        p = _Plugin(("家", "home", "在家休息"))
        self.assertFalse(P.can_proactive(p, {"last_seen": old}, "nudge"))
        self.assertFalse(P.can_selfie(p, {"last_seen": old}))

    def test_fresh_user_allows_engagement(self):
        p = _Plugin(("家", "home", "在家休息"))
        self.assertTrue(P.can_proactive(p, {"last_seen": clock.now_ts() - 600}, "nudge"))

    def test_shop_only_at_mall(self):
        self.assertFalse(P.can_shop(_Plugin(("家", "home", "在家休息"))))
        self.assertTrue(P.can_shop(_Plugin(("某市·商场", "mall", "逛街"))))

    def test_selfie_blocked_at_home_when_shopping_pending(self):
        kv = {"shopping_prompt": {"line": "在商场试衣服", "until": clock.now_ts() + 600}}
        p = _Plugin(("家", "home", "在家休息"), kv)
        self.assertFalse(P.can_selfie(p, {"last_seen": clock.now_ts() - 60}))


if __name__ == "__main__":
    unittest.main()
