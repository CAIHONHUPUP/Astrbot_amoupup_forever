# -*- coding: utf-8 -*-
"""她的钱包：amo 是资产权威，金融管家只读余额、只调方法。

设计（见 docs/INTEGRATION-MAP.md）：
- **余额是账本聚合**，不允许直接改 balance 字段；所有变化走 `credit/debit` + `wallet_ledger`；
- 每笔流水必须带 `idem`，重复调用直接返回 False，不会二次入账；
- 金额一律**整数分**，禁止 float；
- 不做 UI：面板在金融插件那边，这里只提供方法。
"""
from __future__ import annotations

import copy
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
        # 流水读取快照（2026-11-21）：同一次写入序列内重复读同一 limit 直接命中缓存。
        # 进程内单实例，写入口只有 _insert，用内存计数器做版本即可，不依赖 kv。
        self._write_seq = 0
        self._ledger_cache: dict[int, tuple[int, list[dict[str, Any]]]] = {}

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
                CREATE TABLE IF NOT EXISTS wallet_holds (
                    idem TEXT PRIMARY KEY,
                    account TEXT NOT NULL DEFAULT 'cash',
                    amount_cents INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL DEFAULT 0,
                    released_at REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_holds_active
                    ON wallet_holds(account, released_at);
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

    # ---------------- 冻结（答应饭局到点才扣的那笔钱） ----------------
    def _held_locked(self, account: str = "cash", now: float = 0.0) -> int:
        current = float(now or _amo_clock.now_ts())
        row = self.db.conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM wallet_holds "
            "WHERE account=? AND released_at=0 AND (expires_at=0 OR expires_at>?)",
            (str(account), current)).fetchone()
        return int(row[0] or 0) if row else 0

    def held(self, account: str = "cash") -> int:
        """被冻结的金额（分）：余额里看得见，但别的扣款动不了。"""
        self._ensure()
        with self.db.lock:
            return self._held_locked(account)

    def available(self, account: str = "cash") -> int:
        """可花余额 = 余额 - 冻结。"""
        self._ensure()
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT balance_cents FROM wallet_account WHERE account=?", (str(account),)
            ).fetchone()
            balance = int(row[0]) if row else 0
            return balance - self._held_locked(account)

    def hold(self, amount_cents, reason, idem, account: str = "cash",
             expires_at: float = 0.0) -> bool:
        """冻结一笔钱：余额不变，只是其他扣款不能动这部分。幂等（同一 idem 只冻一次）。"""
        self._ensure()
        amount = int(amount_cents)
        key = str(idem or "").strip()
        if amount <= 0 or not key:
            return False
        with self.db.lock:
            conn = self.db.conn
            row = conn.execute(
                "SELECT released_at FROM wallet_holds WHERE idem=?", (key,)
            ).fetchone()
            if row:
                # 已冻结 → 幂等成功；已经释放/用掉 → 不重开
                return int(row[0] or 0) == 0
            row = conn.execute(
                "SELECT balance_cents FROM wallet_account WHERE account=?", (str(account),)
            ).fetchone()
            balance = int(row[0]) if row else 0
            if balance - self._held_locked(account) < amount:
                return False
            conn.execute(
                "INSERT INTO wallet_holds(idem, account, amount_cents, reason, created_at, "
                "expires_at, released_at) VALUES(?,?,?,?,?,?,0)",
                (key, str(account), amount, str(reason or ""),
                 _amo_clock.now_ts(), float(expires_at or 0)))
            conn.commit()
            return True

    def release(self, idem) -> bool:
        """解冻（取消邀约/放弃时）；返回是否真解冻了。"""
        key = str(idem or "").strip()
        if not key:
            return False
        self._ensure()
        with self.db.lock:
            cur = self.db.conn.execute(
                "UPDATE wallet_holds SET released_at=? WHERE idem=? AND released_at=0",
                (_amo_clock.now_ts(), key))
            self.db.conn.commit()
            return int(cur.rowcount or 0) > 0

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
        limit = max(1, int(limit))
        cached = self._ledger_cache.get(limit)
        if cached is not None and cached[0] == self._write_seq:
            return copy.deepcopy(cached[1])
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT idem, account, delta_cents, reason, ref, event_id, ts, category, sub_category, item_name, trigger_reason, actor, fund_source FROM wallet_ledger "
                "ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        result = [
            {"idem": r[0], "account": r[1], "delta_cents": int(r[2]),
             "reason": r[3], "ref": r[4], "event_id": r[5] or "",
             "ts": float(r[6]),
             "category": r[7] or "", "sub_category": r[8] or "",
             "item_name": r[9] or "", "trigger_reason": r[10] or r[3],
             "actor": r[11] or "", "fund_source": r[12] or ""}
            for r in rows
        ]
        self._ledger_cache = {limit: (self._write_seq, result)}
        return copy.deepcopy(result)

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
        self._write_seq += 1
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
              event_id: str = "", release_hold: str = "") -> bool:
        """扣款；余额不足或重复 idem 返回 False。

        release_hold：这笔钱本来就冻结过（答应饭局那笔），扣款时原子的「解冻 + 扣」，
        余额看起来不变，但冻结额度要还回去。
        """
        self._ensure()
        amount = int(amount_cents)
        if amount <= 0:
            return False
        with self.db.lock:
            conn = self.db.conn
            row = conn.execute(
                "SELECT balance_cents FROM wallet_account WHERE account=?", (str(account),)
            ).fetchone()
            balance = int(row[0] if row else 0)
            held = self._held_locked(account)
            hold_amount = 0
            if release_hold:
                now = _amo_clock.now_ts()
                held_row = conn.execute(
                    "SELECT amount_cents FROM wallet_holds WHERE idem=? AND account=? "
                    "AND released_at=0 AND (expires_at=0 OR expires_at>?)",
                    (str(release_hold), str(account), now)).fetchone()
                hold_amount = int(held_row[0]) if held_row else 0
            if balance - (held - hold_amount) < amount:
                return False
            if release_hold and hold_amount:
                conn.execute(
                    "UPDATE wallet_holds SET released_at=? WHERE idem=? AND released_at=0",
                    (_amo_clock.now_ts(), str(release_hold)))
            ok = self._insert(account, -amount, reason, idem, ref,
                              category=category, sub_category=sub_category,
                              item_name=item_name, trigger_reason=trigger_reason,
                              actor=actor, fund_source=fund_source,
                              event_id=event_id)
            if not ok and release_hold and hold_amount:
                conn.rollback()   # 扣款被幂等挡掉时，冻结也别解
            return ok

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
        locked = self.held("cash")
        return {"balance_cents": self.balance("cash"),
                "held_cents": locked,
                "available_cents": self.balance("cash") - locked,
                "ledger": self.ledger(5),
                "lines": self.ledger_lines(5)}
