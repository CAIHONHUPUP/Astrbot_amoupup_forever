# -*- coding: utf-8 -*-
"""她的钱包：amo 是资产权威，金融管家只读余额、只调方法。

设计（见 docs/INTEGRATION-MAP.md）：
- **余额是账本聚合**，不允许直接改 balance 字段；所有变化走 `credit/debit` + `wallet_ledger`；
- 每笔流水必须带 `idem`，重复调用直接返回 False，不会二次入账；
- 金额一律**整数分**，禁止 float；
- 不做 UI：面板在金融插件那边，这里只提供方法。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import logging
import time
from typing import Any

try:
    from astrbot.api import logger
except Exception:  # 离线单测
    logger = logging.getLogger("amo_wallet")


class Wallet:
    ACCOUNTS = ("cash", "withdrawable")  # cash=可用（含可取），withdrawable=可取（现金的子集）

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
                    event_id TEXT NOT NULL DEFAULT '',
                    ts REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_ledger_ts ON wallet_ledger(ts DESC);
                """
            )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(wallet_ledger)")}
            for col, ddl in (("category", "TEXT NOT NULL DEFAULT ''"),
                             ("sub_category", "TEXT NOT NULL DEFAULT ''"),
                             ("item_name", "TEXT NOT NULL DEFAULT ''"),
                             ("trigger_reason", "TEXT NOT NULL DEFAULT ''"),
                             ("actor", "TEXT NOT NULL DEFAULT ''"),
                             ("fund_source", "TEXT NOT NULL DEFAULT ''"),
                             ("event_id", "TEXT NOT NULL DEFAULT ''")):
                if col not in cols:
                    conn.execute("ALTER TABLE wallet_ledger ADD COLUMN %s %s" % (col, ddl))
            # 列补齐后再建索引：旧库先跑 execscript 时这一列还不存在。
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wallet_ledger_event "
                         "ON wallet_ledger(event_id)")
            conn.execute("UPDATE wallet_ledger SET category='其他', sub_category='日常', "
                         "item_name=reason, trigger_reason=reason "
                         "WHERE category IS NULL OR category=''")
            for account in self.ACCOUNTS:
                conn.execute(
                    "INSERT OR IGNORE INTO wallet_account(account, balance_cents, updated) VALUES(?,0,?)",
                    (account, _amo_clock.now_ts()),
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

    def has_idem(self, idem: str) -> bool:
        """只读查询某笔流水的幂等键是否已入账（旅游扣款区分 duplicate/no_money）。"""
        text = str(idem or "").strip()
        if not text:
            return False
        self._ensure()
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT 1 FROM wallet_ledger WHERE idem=? LIMIT 1", (text,)
            ).fetchone()
        return bool(row)

    def ledger(self, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure()
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT idem, account, delta_cents, reason, ref, event_id, ts, category, sub_category, item_name, trigger_reason, actor, fund_source FROM wallet_ledger "
                "ORDER BY ts DESC, rowid DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
        return [
            {"idem": r[0], "account": r[1], "delta_cents": int(r[2]),
             "reason": r[3], "ref": r[4], "event_id": r[5] or "",
             "ts": float(r[6]),
             "category": r[7] or "", "sub_category": r[8] or "",
             "item_name": r[9] or "", "trigger_reason": r[10] or r[3],
             "actor": r[11] or "", "fund_source": r[12] or ""}
            for r in rows
        ]

    def rows_between(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
        """只读：ts 区间内的流水（面板「本时段消费」用）；不改余额、不做写。"""
        self._ensure()
        lo, hi = float(start_ts), float(end_ts)
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT idem, account, delta_cents, reason, ref, event_id, ts, category, sub_category, item_name, trigger_reason, actor, fund_source FROM wallet_ledger "
                "WHERE ts>=? AND ts<? ORDER BY ts ASC, rowid ASC", (lo, hi)
            ).fetchall()
        return [
            {"idem": r[0], "account": r[1], "delta_cents": int(r[2]),
             "reason": r[3], "ref": r[4], "event_id": r[5] or "",
             "ts": float(r[6]),
             "category": r[7] or "", "sub_category": r[8] or "",
             "item_name": r[9] or "", "trigger_reason": r[10] or r[3],
             "actor": r[11] or "", "fund_source": r[12] or ""}
            for r in rows
        ]

    # ---------------- 写（唯一入口） ----------------
    def _insert(self, account: str, delta: int, reason: str, idem: str, ref: str,
                category: str = "", sub_category: str = "", item_name: str = "",
                trigger_reason: str = "", actor: str = "", fund_source: str = "",
                event_id: str = "") -> bool:
        """锁内写：先插流水，插进去才动余额。"""
        conn = self.db.conn
        cur = conn.execute(
            "INSERT OR IGNORE INTO wallet_ledger(idem, account, delta_cents, reason, ref, event_id, ts, category, sub_category, item_name, trigger_reason, actor, fund_source) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(idem), str(account), int(delta), str(reason or ""), str(ref or ""),
             str(event_id or ""), _amo_clock.now_ts(),
             str(category or ""), str(sub_category or ""), str(item_name or ""), str(trigger_reason or reason or ""),
             str(actor or ""), str(fund_source or "")),
        )
        if int(cur.rowcount or 0) <= 0:
            return False
        conn.execute(
            "UPDATE wallet_account SET balance_cents = balance_cents + ?, updated=? WHERE account=?",
            (int(delta), _amo_clock.now_ts(), str(account)),
        )
        if str(account) == "cash" and int(delta) < 0:
            # 可取不能超过可用
            row = conn.execute("SELECT balance_cents FROM wallet_account WHERE account='cash'").fetchone()
            cash = int(row[0]) if row else 0
            conn.execute(
                "UPDATE wallet_account SET balance_cents=? WHERE account='withdrawable' AND balance_cents>?",
                (cash, cash),
            )
        conn.commit()
        return True

    def credit(self, amount_cents: int, reason: str, idem: str, ref: str = "",
               account: str = "cash", actor: str = "", fund_source: str = "",
               event_id: str = "") -> bool:
        """入账；重复 idem 返回 False。event_id 可空（旧调用完全兼容）。"""
        self._ensure()
        amount = int(amount_cents)
        if amount <= 0:
            return False
        with self.db.lock:
            return self._insert(account, amount, reason, idem, ref,
                                actor=actor, fund_source=fund_source, event_id=event_id)

    def debit(self, amount_cents: int, reason: str, idem: str, ref: str = "",
              account: str = "cash", category: str = "", sub_category: str = "",
              item_name: str = "", trigger_reason: str = "", actor: str = "", fund_source: str = "",
              event_id: str = "") -> bool:
        """扣款；余额不足或重复 idem 返回 False。event_id 可空（旧调用完全兼容）。"""
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
            return self._insert(account, -amount, reason, idem, ref,
                                category=category, sub_category=sub_category,
                                item_name=item_name, trigger_reason=trigger_reason,
                                actor=actor, fund_source=fund_source,
                                event_id=event_id)

    # ---------------- 展示（只读） ----------------
    @staticmethod
    def format_ts(ts) -> str:
        """流水时间统一成完整年月日 + 时分：YYYY-MM-DD HH:MM。"""
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts or 0)))
        except (TypeError, ValueError, OverflowError, OSError):
            return ""

    def ledger_lines(self, limit: int = 20) -> list[str]:
        """明细流水文本：最新在上（ts 倒序），完整年月日 + 金额 + 原因。

        终端/面板/命令要展示流水时统一走这里，避免各处自己 strftime 出半截日期。
        """
        lines = []
        for row in self.ledger(limit):
            amount = int(row.get("delta_cents") or 0) / 100.0
            reason = str(row.get("item_name") or row.get("reason") or "")
            category = str(row.get("category") or "")
            tag = ("[%s] " % category) if category else ""
            lines.append("%s  %+.2f 元  %s%s" % (self.format_ts(row.get("ts")),
                                                amount, tag, reason))
        return lines

    def snapshot(self) -> dict[str, Any]:
        return {"balance_cents": self.balance("cash"), "ledger": self.ledger(5),
                "lines": self.ledger_lines(5)}
