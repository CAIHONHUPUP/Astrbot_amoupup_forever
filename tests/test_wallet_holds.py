# -*- coding: utf-8 -*-
"""钱包冻结（2026-11-20 第七条）：答应饭局先冻住那笔钱，到点扣款时原子解冻。

余额不变（面板余额不提前掉），但 `available()` 变少，其他扣款动不了这部分。
"""
from __future__ import annotations

import clock  # noqa: E402  （能陪统一时钟，测试里被冻结在 2026-11-18）

from amo_wallet import Wallet

from plugin_fakes import WalletFakeDB as FakeDB  # noqa: E402


def make():
    w = Wallet(FakeDB())
    w.ensure_schema()
    w.credit(100000, "工资", "salary:seed")
    return w


def test_hold_reduces_available_not_balance():
    w = make()
    assert w.hold(20000, "今晚火锅", "hold:1") is True
    assert w.balance() == 100000
    assert w.held() == 20000
    assert w.available() == 80000


def test_debit_cannot_touch_held_money():
    w = make()
    w.hold(20000, "今晚火锅", "hold:1")
    assert w.debit(90000, "买基金", "buy:1") is False
    assert w.balance() == 100000
    assert w.debit(80000, "买基金", "buy:2") is True
    assert w.balance() == 20000 and w.available() == 0


def test_debit_with_release_hold_consumes_it():
    w = make()
    w.hold(20000, "今晚火锅", "hold:1")
    assert w.debit(20000, "下班和柳如烟吃火锅", "meal:1", release_hold="hold:1") is True
    assert w.balance() == 80000
    assert w.held() == 0 and w.available() == 80000


def test_release_frees_money():
    w = make()
    w.hold(20000, "今晚火锅", "hold:1")
    assert w.release("hold:1") is True
    assert w.release("hold:1") is False
    assert w.held() == 0 and w.available() == 100000


def test_hold_is_idempotent():
    w = make()
    assert w.hold(20000, "x", "hold:1") is True
    assert w.hold(20000, "x", "hold:1") is True
    assert w.held() == 20000


def test_expired_hold_does_not_block():
    w = make()
    assert w.hold(20000, "x", "hold:1", expires_at=clock.now_ts() - 10) is True
    assert w.held() == 0
    assert w.available() == 100000
    assert w.debit(100000, "花掉", "spend:1") is True


def test_failed_debit_keeps_the_hold():
    w = make()
    w.hold(20000, "x", "hold:1")
    assert w.debit(20000, "火锅", "meal:1", release_hold="hold:1") is True
    assert w.hold(5000, "x2", "hold:2") is True
    # 同一个 idem 再扣一次（幂等挡掉）→ 不能让这笔的冻结跟着丢掉
    assert w.debit(20000, "火锅", "meal:1", release_hold="hold:2") is False
    assert w.held() == 5000
