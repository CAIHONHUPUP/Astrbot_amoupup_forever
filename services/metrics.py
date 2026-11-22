# -*- coding: utf-8 -*-
"""统一指标契约 MetricsAPI（v1）：先把「算出来的数」收敛成一个定义。

work/audit/cross_plugin.md P0-3 的根因不是某一个面板写错，而是没有「指标定义」
这一层：守护首页读 watch.expenses（¥473）、守护生活卡读 wallet ledger 却按
reason 前缀排除（¥16,016，含定期 10,000/基金 500），能记“聊天”把 146 条摘要
也算进去（436 vs 真实 290），定格面板只统计“有 event 的命令路径”。

本模块只定义**能陪自己拥有的**指标（钱包消费口径 / 照片用量），并提供唯一
计算函数；守护面板、能记面板、定格面板各自通过跨插件方法调用同一函数，
不再各写一份：

- 本月消费 expense_month_consumption：
  cash 账户负流水，排除
  * 资产类（存款/投资/基金/理财/转账/退款/账户调整）——这部分单列“资产变动”；
  * 生活账单类（房租/水费/电费/燃气费/宽带/物业/取暖）——面板另有账单卡；
  * 退款/冲正（按幂等前缀 + 理由关键词）；
  单位统一整数分。
- 本月资产变动 expense_month_assets_change：资产类流水的绝对值（存起来/买基金）。
- 今日生图 photos_today_by_all_paths：媒体账本（amo_media.photo_count）唯一口径；
  定格插件自己的 usage 统计由 photo 侧记录并回传 usage_recorded，见
  photo/main_tasks.py generate_images_for_plugin(principal=...)。

契约测试：forever/tests/test_metrics_contract.py（与 watch 侧同口径比对）。
"""
from __future__ import annotations

import datetime as _dt

CONTRACT_NAME = "amo-metrics"
CONTRACT_VERSION = 1
SOURCE = "forever"

#: 不算“消费”的分类（资产变动 / 账单）；与 wallet._EXPENSE_EXCLUDE_CATEGORIES 同源扩展
ASSET_CATEGORIES = frozenset(("存款", "投资", "转账", "退款", "理财", "基金", "股票",
                              "账户调整"))
BILL_CATEGORIES = frozenset(("房租", "水电", "物业", "取暖", "宽带"))
BILL_REASON_PREFIXES = ("房租", "水费", "电费", "燃气费", "宽带", "物业", "取暖")
#: 老版 wallet_ledger category 被回填成“其他”的行，靠理由前缀识别
ASSET_REASON_PREFIXES = ("存款", "投资", "转账", "退款", "理财", "基金", "股票",
                         "买入", "卖出", "申购", "赎回", "贷款", "还款", "利息",
                         "分期", "工资", "奖金", "红包", "初始存款", "可取余额",
                         "卖出资金", "用户转账")
#: 幂等前缀（与 amo_wallet._EXPENSE_EXCLUDE_PREFIXES + bank 前缀一致）
ASSET_IDEM_PREFIXES = ("save:", "deposit:", "invest:", "transfer:", "refund:",
                       "cleanup:refund:", "withdrawable:", "bank:dep:")
REFUND_WORDS = ("退款", "退回", "冲正", "撤销", "退还")

#: 分类结果（给测试/面板解释用）
KIND_CONSUMPTION = "consumption"
KIND_BILL = "bill"
KIND_ASSET = "asset"
KIND_REFUND = "refund"
KIND_INCOME = "income"


def contract_descriptor():
    return {
        "contract": CONTRACT_NAME,
        "version": CONTRACT_VERSION,
        "source": SOURCE,
        "metrics": {
            "expense_month_consumption": "cash 负流水去掉资产类/账单类/退款，整数分",
            "expense_month_assets_change": "存款/理财/基金等资产类流水绝对值，整数分",
            "expense_month_bill": "房租/水电/燃气/宽带/物业/取暖，整数分",
            "photos_today_by_all_paths": "媒体账本口径，跨插件生图也计入",
        },
        "units": {"money": "整数分", "day": "YYYY-MM-DD", "month": "YYYY-MM"},
    }


def _clock_now(now=None):
    if isinstance(now, _dt.datetime):
        return now
    if now is not None:
        try:
            return _dt.datetime.fromtimestamp(float(now))
        except (TypeError, ValueError):  # cleanup: 兜底/降级，保持原行为
            pass
    try:
        try:
            from .. import clock as _clock
        except ImportError:
            import clock as _clock
        return _clock.now()
    except Exception:
        return _dt.datetime.now()


def month_of(now=None):
    """当前本地月份 YYYY-MM（统一时钟；不用裸 datetime.now）。"""
    return _clock_now(now).strftime("%Y-%m")


def month_bounds(month="", now=None):
    """本地月份 [start, end) epoch 秒。"""
    text = str(month or "").strip() or month_of(now)
    try:
        start_dt = _dt.datetime.strptime(text + "-01", "%Y-%m-%d")
    except (TypeError, ValueError):
        start_dt = _clock_now(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        text = start_dt.strftime("%Y-%m")
    end_dt = (start_dt.replace(year=start_dt.year + 1, month=1) if start_dt.month == 12
              else start_dt.replace(month=start_dt.month + 1))
    return text, float(start_dt.timestamp()), float(end_dt.timestamp())


def _text(row, key):
    value = row.get(key) if isinstance(row, dict) else None
    return str(value or "")


def _delta(row):
    value = row.get("delta_cents") if isinstance(row, dict) else 0
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def classify_row(row):
    """一行 wallet_ledger -> 指标分类：income/refund/bill/asset/consumption。

    只读纯函数；口径变更只改这里（守护/面板/契约测试共用）。
    """
    delta = _delta(row)
    reason = _text(row, "reason")
    category = _text(row, "category")
    idem = _text(row, "idem")
    if delta > 0:
        return KIND_REFUND if any(word in reason for word in REFUND_WORDS) else KIND_INCOME
    if idem.startswith("refund:") or idem.startswith("cleanup:refund:"):
        return KIND_REFUND
    if any(word in reason for word in REFUND_WORDS):
        return KIND_REFUND
    if category in ASSET_CATEGORIES:
        return KIND_ASSET
    if category in BILL_CATEGORIES:
        return KIND_BILL
    if idem.startswith(ASSET_IDEM_PREFIXES):
        return KIND_ASSET
    if any(reason.startswith(prefix) for prefix in ASSET_REASON_PREFIXES):
        # 工资/奖金等其实出现不到负流水里；负流水命中说明是资产/账单类
        if any(reason.startswith(prefix) for prefix in BILL_REASON_PREFIXES):
            return KIND_BILL
        return KIND_ASSET
    if any(reason.startswith(prefix) for prefix in BILL_REASON_PREFIXES):
        return KIND_BILL
    return KIND_CONSUMPTION


def _month_rows(rows, month=None, now=None):
    """筛出目标月份的行；row['ts'] 缺省视为当月（调用方通常已按区间取过）。"""
    target = str(month or "").strip()
    if not target:
        return list(rows or [])
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if not ts:
            out.append(row)
            continue
        if _dt.datetime.fromtimestamp(ts).strftime("%Y-%m") == target:
            out.append(row)
    return out


def consumption_cents(rows, month=None, now=None):
    """本月消费（整数分）：唯一计算函数。"""
    total = 0
    for row in _month_rows(rows, month, now):
        if classify_row(row) == KIND_CONSUMPTION:
            total += abs(_delta(row))
    return total


def bill_cents(rows, month=None, now=None):
    total = 0
    for row in _month_rows(rows, month, now):
        if classify_row(row) == KIND_BILL:
            total += abs(_delta(row))
    return total


def assets_change_cents(rows, month=None, now=None):
    """本月资产变动（存起来/买基金/转账）：正整数分。"""
    total = 0
    for row in _month_rows(rows, month, now):
        if classify_row(row) == KIND_ASSET:
            total += abs(_delta(row))
    return total


def _wallet_rows(wallet, month=None, now=None):
    """取目标月份流水：优先 rows_between（不受 limit 截断），老钱包退回 ledger。"""
    text, start, end = month_bounds(month, now)
    reader = getattr(wallet, "rows_between", None)
    if callable(reader):
        rows = reader(start, end)
        return [row for row in (rows or []) if isinstance(row, dict)]
    ledger = getattr(wallet, "ledger", None)
    if not callable(ledger):
        return []
    rows = ledger(2000) or []
    return [row for row in rows if isinstance(row, dict)
            and _text(row, "account") in ("cash", "")]


def wallet_month_consumption_cents(wallet, month="", now=None):
    """钱包本月消费：面板/快照/测试唯一入口。"""
    try:
        rows = _wallet_rows(wallet, month, now)
    except Exception:
        return 0
    return consumption_cents(rows, month_of(now) if not month else month, now)


def build_metrics(plugin, now=None):
    """能陪拥有的指标（钱包 + 照片）；读失败只降级不抛。"""
    moment = _clock_now(now)
    month = month_of(moment)
    degraded = []
    wallet_out = {"balance_cents": 0, "withdrawable_cents": 0, "month": month,
                  "month_consumption_cents": 0, "month_assets_change_cents": 0,
                  "month_bill_cents": 0, "source": "wallet_ledger"}
    wallet = getattr(plugin, "wallet", None)
    if wallet is not None:
        try:
            rows = _wallet_rows(wallet, month, moment)
            wallet_out.update({
                "balance_cents": int(wallet.balance("cash") or 0),
                "withdrawable_cents": int(wallet.balance("withdrawable") or 0),
                "month_consumption_cents": consumption_cents(rows, month, moment),
                "month_assets_change_cents": assets_change_cents(rows, month, moment),
                "month_bill_cents": bill_cents(rows, month, moment),
            })
        except Exception as exc:
            degraded.append({"field": "wallet", "reason": "%s: %s" % (type(exc).__name__, exc)})
    else:
        degraded.append({"field": "wallet", "reason": "wallet 未装配"})
    photos_out = {"today": 0, "day": moment.date().isoformat(), "source": "media_ledger"}
    try:
        try:
            from .. import amo_media as _media
        except ImportError:
            import amo_media as _media
        getter = getattr(_media, "photo_count", None)
        if callable(getter):
            photos_out["today"] = int(getter(plugin, moment.date()) or 0)
    except Exception as exc:
        degraded.append({"field": "photos", "reason": "%s: %s" % (type(exc).__name__, exc)})
    return {
        "contract": CONTRACT_NAME,
        "version": CONTRACT_VERSION,
        "source": SOURCE,
        "generated_at": float(moment.timestamp()),
        "day": moment.date().isoformat(),
        "wallet": wallet_out,
        "photos": photos_out,
        "degraded": degraded,
    }
