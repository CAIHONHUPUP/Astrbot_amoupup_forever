# -*- coding: utf-8 -*-
"""她的钱包：amo 是资产权威，金融管家只读余额、只调方法。

设计（见 docs/INTEGRATION-MAP.md）：
- **余额是账本聚合**，不允许直接改 balance 字段；所有变化走 `credit/debit` + `wallet_ledger`；
- 每笔流水必须带 `idem`，重复调用直接返回 False，不会二次入账；
- 金额一律**整数分**，禁止 float；
- 不做 UI：面板在金融插件那边，这里只提供方法。
"""
from __future__ import annotations

import logging
import time
from typing import Any

try:
    from astrbot.api import logger
except Exception:  # 离线单测
    logger = logging.getLogger("amo_wallet")


class Wallet:
    ACCOUNTS = ("cash",)

    def __init__(self, db):
        self.db = db
        self._ready = False

    # ---------------- 建表 / 自检 ----------------
    def ensure_schema(self) -> None:
        with self.db.lock:
            conn = self.db.conn
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_account (
                    account TEXT PRIMARY KEY,
                    balance_cents INTEGER NOT NULL DEFAULT 0,
                    updated REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS wallet_ledger (
                    idem TEXT PRIMARY KEY,
                    account TEXT NOT NULL,
                    delta_cents INTEGER NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    ref TEXT NOT NULL DEFAULT '',
                    ts REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_ledger_ts ON wallet_ledger(ts DESC);
                """
            )
            for account in self.ACCOUNTS:
                conn.execute(
                    "INSERT OR IGNORE INTO wallet_account(account, balance_cents, updated) VALUES(?,0,?)",
                    (account, time.time()),
                )
            conn.commit()
        self._ready = True

    def _ensure(self) -> None:
        if not self._ready:
            self.ensure_schema()

    # ---------------- 读 ----------------
    def balance(self, account: str = "cash") -> int:
        """可用余额（分）。"""
        self._ensure()
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT balance_cents FROM wallet_account WHERE account=?", (str(account),)
            ).fetchone()
        return int(row[0]) if row else 0

    def total_assets(self) -> int:
        """总资产（分）。持仓市值由金融插件加，这里只给现金。"""
        return self.balance("cash")

    def ledger(self, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure()
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT idem, account, delta_cents, reason, ref, ts FROM wallet_ledger "
                "ORDER BY ts DESC, rowid DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
        return [
            {"idem": r[0], "account": r[1], "delta_cents": int(r[2]),
             "reason": r[3], "ref": r[4], "ts": float(r[5])}
            for r in rows
        ]

    # ---------------- 写（唯一入口） ----------------
    def _insert(self, account: str, delta: int, reason: str, idem: str, ref: str) -> bool:
        """锁内写：先插流水，插进去才动余额。"""
        conn = self.db.conn
        cur = conn.execute(
            "INSERT OR IGNORE INTO wallet_ledger(idem, account, delta_cents, reason, ref, ts) "
            "VALUES(?,?,?,?,?,?)",
            (str(idem), str(account), int(delta), str(reason or ""), str(ref or ""), time.time()),
        )
        if int(cur.rowcount or 0) <= 0:
            return False
        conn.execute(
            "UPDATE wallet_account SET balance_cents = balance_cents + ?, updated=? WHERE account=?",
            (int(delta), time.time(), str(account)),
        )
        conn.commit()
        return True

    def credit(self, amount_cents: int, reason: str, idem: str, ref: str = "",
               account: str = "cash") -> bool:
        """入账；重复 idem 返回 False。"""
        self._ensure()
        amount = int(amount_cents)
        if amount <= 0:
            return False
        with self.db.lock:
            return self._insert(account, amount, reason, idem, ref)

    def debit(self, amount_cents: int, reason: str, idem: str, ref: str = "",
              account: str = "cash") -> bool:
        """扣款；余额不足或重复 idem 返回 False。"""
        self._ensure()
        amount = int(amount_cents)
        if amount <= 0:
            return False
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT balance_cents FROM wallet_account WHERE account=?", (str(account),)
            ).fetchone()
            if int(row[0] if row else 0) < amount:
                return False
            return self._insert(account, -amount, reason, idem, ref)

    def snapshot(self) -> dict[str, Any]:
        return {"balance_cents": self.balance("cash"), "ledger": self.ledger(5)}
