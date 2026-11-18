# -*- coding: utf-8 -*-
"""老 health 数据 until=0（永不过期）必须在超期后自动康复（2026-11-18 病好还说难受）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_human_state import HumanState  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_set(self, key, value):
        self.kv[key] = value

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)


class Fake:
    def __init__(self, since):
        self.health = {"status": "感冒", "since": since, "until": 0, "note": ""}
        self.db = FakeDB()


def test_legacy_zero_until_recovers_after_window():
    fake = Fake(time.time() - 49 * 3600)
    info = HumanState.health_info(fake)
    assert info["status"] == "健康" and info["sick"] is False
    assert fake.health["status"] == "健康"


def test_legacy_zero_until_recent_stays_sick_with_expiry():
    fake = Fake(time.time() - 2 * 3600)
    info = HumanState.health_info(fake)
    assert info["status"] == "感冒" and info["sick"] is True
    assert info["until"] > time.time()
