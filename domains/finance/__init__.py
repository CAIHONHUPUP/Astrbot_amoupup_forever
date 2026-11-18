# -*- coding: utf-8 -*-
"""金融域（pilot）：只定义钱包契约 + 纯金额工具，不 import 任何插件模块。

规则（沿用 amo_wallet 的设计）：金额一律整数分；余额是账本聚合；
每笔流水必须带 idem，重复调用返回 False，不会二次入账。
"""
from __future__ import annotations

from typing import Any, Protocol


class WalletLike(Protocol):
    """钱包对外契约（实现方：amo_wallet.Wallet）。"""

    def balance(self, account: str = "cash") -> int:
        ...

    def credit(self, amount_cents: int, reason: str, idem: str, ref: str = "",
               account: str = "cash", actor: str = "", fund_source: str = "",
               event_id: str = "") -> bool:
        ...

    def debit(self, amount_cents: int, reason: str, idem: str, ref: str = "",
              account: str = "cash", category: str = "", sub_category: str = "",
              item_name: str = "", trigger_reason: str = "", actor: str = "",
              fund_source: str = "", event_id: str = "") -> bool:
        ...

    def ledger(self, limit: int = 50) -> list[dict[str, Any]]:
        ...


def yuan_text(cents: Any) -> str:
    """整数分 -> 带符号两位小数文本（与 amo_wallet.ledger_lines 口径一致）。"""
    try:
        value = int(cents)
    except (TypeError, ValueError):
        return "+0.00"
    return "%+.2f" % (value / 100.0)


def cents_text(cents: Any) -> str:
    """整数分 -> 两位小数（不带正负号，面板展示用）。"""
    try:
        value = int(cents)
    except (TypeError, ValueError):
        return "0.00"
    return "%.2f" % (value / 100.0)


#: 钱包必须提供的方法名（契约漂移测试用）。
WALLET_METHODS = ("balance", "credit", "debit", "ledger")
