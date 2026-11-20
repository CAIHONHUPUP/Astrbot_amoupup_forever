# -*- coding: utf-8 -*-
"""餐饮消费口径（2026-11-17）：夜间不扣款、10 分钟内同物不重复、金额走 price_book item。

背景（用户账单）：02:12 仍扣烧烤和泡面、17:30/17:31 两条面条、小蛋糕 36.67/41.95
这种随机分位。这里把「吃什么、多少钱、能不能扣」收口成纯函数：
- 夜间 00:00-06:00 只叙述不扣钱（饥饿可以写，钱包不动）；
- 同一 item 10 分钟内只允许一条（跨 meal/hunger/late/micro 路径）；
- 金额先查 price_book v1 的 meal item（min=max 的固定价），查不到用固定兜底表，
  绝不随机分位。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import datetime as _dt
import hashlib
import logging
import re

try:  # Phase 11：统一时钟
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

try:
    from . import price_book as _price_book
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import price_book as _price_book

logger = logging.getLogger("amo.food")

NIGHT_START_HOUR = 0
NIGHT_END_HOUR = 6
DEDUPE_MINUTES = 10

#: 日常餐饮固定价（分）；price_book.json 有同 item 的 v1 行时以表为准。
FIXED_ITEMS = {
    "包子": 200, "豆浆和包子": 800, "豆浆": 300, "面条": 1500, "泡面": 500,
    "烧烤": 2800, "夜宵": 1500, "小蛋糕": 3000, "奶茶": 1500, "果茶": 1500,
    "外卖": 2500, "清淡外卖": 2000, "家常菜": 2000, "粥": 1000, "咖啡": 1500,
    "零食": 1000,
}

#: 说法 → 价目表 item 名（顺序敏感：长的在前）。
ALIASES = (
    ("豆浆和包子", "豆浆和包子"),
    ("清淡外卖", "清淡外卖"),
    ("牛肉面", "面条"),
    ("火腿肠", "零食"),
    ("小蛋糕", "小蛋糕"),
    ("家常菜", "家常菜"),
    ("烧烤和泡面", "烧烤"),
    ("烧烤", "烧烤"),
    ("泡面", "泡面"),
    ("面条", "面条"),
    ("包子", "包子"),
    ("豆浆", "豆浆"),
    ("夜宵", "夜宵"),
    ("奶茶", "奶茶"),
    ("果茶", "果茶"),
    ("外卖", "外卖"),
    ("白粥", "粥"),
    ("粥", "粥"),
    ("咖啡", "咖啡"),
    ("零食", "零食"),
)

#: 微型事件的 key → 价目表 item；late_snack 用事件自带的 food。
EVENT_ITEMS = {
    "breakfast": "豆浆和包子",
    "takeout": "外卖",
    "sick_takeout": "清淡外卖",
    "tea_time": "奶茶",
    "fruit_tea": "果茶",
}

_QUANTITY_RE = re.compile(r"\s*[×xX*]\s*\d+\s*$")
_FOOD_WORDS = ("吃", "饭", "面", "粥", "包子", "烧烤", "泡面", "蛋糕", "外卖",
               "夜宵", "奶茶", "果茶", "咖啡", "零食", "小吃")

#: 通勤说法 → 统一方式（顺序敏感：共享单车在单车前）。
TRAFFIC_MODES = (("共享单车", "共享单车"), ("单车", "共享单车"), ("骑车", "共享单车"),
                 ("自行车", "共享单车"), ("公交", "公交"), ("巴士", "公交"),
                 ("地铁", "地铁"), ("打车", "打车"), ("出租", "打车"), ("网约", "打车"),
                 ("开车", "开车"), ("自驾", "开车"))


def normalize_item(text):
    """去掉「× 1」「一份/一碗」这类修饰，得到可比较的 item 文本。"""
    body = str(text or "").strip()
    body = _QUANTITY_RE.sub("", body).strip()
    for prefix in ("刚刚", "正在", "刚", "在", "去", "买了", "点了", "吃了", "吃的",
                   "一份", "一碗", "一杯", "一个", "一份"):
        if body.startswith(prefix) and len(body) > len(prefix):
            body = body[len(prefix):].strip()
    return body


def item_key(text):
    """任意说法 → 价目表 item 名；认不出时原样返回（后备表查不到返回空价）。"""
    body = normalize_item(text)
    if not body:
        return ""
    for alias, key in ALIASES:
        if alias in body:
            return key
    return body


def event_item(event):
    """微型/餐饮事件 → 价目表 item。"""
    event = event or {}
    key = str(event.get("key") or "")
    food = str(event.get("food") or "").strip()
    if key == "late_snack":
        return item_key(food or "夜宵") or "夜宵"
    if food:
        return item_key(food)
    if key in EVENT_ITEMS:
        return item_key(EVENT_ITEMS[key])
    return item_key(event.get("text"))


def looks_like_food(event):
    """事件是不是餐饮类（给微事件和旧路径共用）。"""
    event = event or {}
    key = str(event.get("key") or "")
    if key in EVENT_ITEMS or key == "late_snack":
        return True
    if event.get("food"):
        return True
    body = str(event.get("text") or "")
    return any(word in body for word in _FOOD_WORDS)


#: 生病（发烧/感冒/不舒服）期间禁止外卖/宵夜/重口；在家煮粥煮面可以。
SICK_FORBIDDEN_WORDS = ("外卖", "点餐", "烧烤", "炸鸡", "小蛋糕", "奶茶", "果茶",
                        "泡面", "夜宵", "宵夜", "串串", "火锅")
SICK_ALLOWED_WORDS = ("粥", "面条", "家常菜", "清淡", "煮", "蒸", "温水", "自己做", "家里做")
SICK_FORBIDDEN_KEYS = ("takeout", "sick_takeout", "late_snack", "tea_time", "fruit_tea")


def late_snack_hours(now):
    """宵夜/夜间加餐窗口：22:00-次日 06:00（健康门只作用于这个窗口）。"""
    return int(now.hour) >= 22 or int(now.hour) < 6


def sick_status(plugin):
    """读 amo 权威健康状态：只认 state.health_info()['sick']，不在调用方各判各的。"""
    info = getattr(getattr(plugin, "state", None), "health_info", None)
    if not callable(info):
        return False
    try:
        return bool(dict(info() or {}).get("sick"))
    except Exception as exc:
        logger.debug("[amo] 读健康状态失败，按健康处理: %r", exc)
        return False


def sick_food_allowed(event):
    """生病期间这份餐饮能不能出现：外卖/外卖类事件一律不许，自家清淡煮食可以。"""
    event = event or {}
    key = str(event.get("key") or "")
    if key in SICK_FORBIDDEN_KEYS:
        return False
    if key in ("home_cook", "cook"):
        return True
    text = "%s %s" % (event.get("text") or "", event.get("food") or "")
    if any(word in text for word in SICK_FORBIDDEN_WORDS):
        return False
    if any(word in text for word in SICK_ALLOWED_WORDS):
        return True
    return True


def price_for(item, city="", moment=None):
    """item 的固定价（分）：price_book v1 的 item 行优先，其次固定兜底；绝不出随机分位。"""
    key = item_key(item)
    if not key:
        return 0
    try:
        low, high = _price_book.price(None, "meal", item=key, city=city, moment=moment)
        if int(low) == int(high):
            return int(low)
    except Exception as exc:  # 表缺失/没这个 item：用兜底价，不阻断吃饭
        logger.debug("[amo] 价目表查不到 %r，用固定兜底: %r", key, exc)
    return int(FIXED_ITEMS.get(key, 0))


def in_night(now):
    return NIGHT_START_HOUR <= int(now.hour) < NIGHT_END_HOUR


def _minute_of(text):
    try:
        hour, minute = str(text or "").strip()[:5].split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _text_of(minute):
    minute = max(0, min(24 * 60, int(minute)))
    return "24:00" if minute >= 24 * 60 else "%02d:%02d" % (minute // 60, minute % 60)


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _block_id_for(day, row):
    """日程块稳定 id：优先用 amo_timeline.block_id，导入失败时按同口径哈希。"""
    try:
        try:
            from .amo_timeline import block_id as _bid
        except ImportError:
            from amo_timeline import block_id as _bid
        return _bid(day, row)
    except Exception as exc:
        logger.debug("[amo] block_id 不可用，按哈希兜底: %r", exc)
        raw = "%s-%s|%s|%s" % (str(row.get("start") or ""), str(row.get("end") or ""),
                               str(row.get("activity") or ""), str(row.get("place") or ""))
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _new_event_id():
    """新块的新身份（让返回的 block_id 与落库 event_id 一致）；失败返回空串。"""
    try:
        try:
            from .amo_timeline import new_event_id as _new
        except ImportError:
            from amo_timeline import new_event_id as _new
        return str(_new() or "")
    except Exception as exc:
        logger.debug("[amo] 新事件 id 不可用，交给启发式兜底: %r", exc)
        return ""


def _split_block(day, parent, parts, inherit_index=0):
    """显式切块（H5）：父 id 只落 inherit_index 段；导入失败时退回启发式。"""
    try:
        try:
            from .amo_timeline import split_schedule_block as _split
        except ImportError:
            from amo_timeline import split_schedule_block as _split
        rows, _info = _split(day, parent, parts, inherit_index=inherit_index)
        return rows
    except Exception as exc:
        logger.debug("[amo] 显式切块不可用，退回启发式: %r", exc)
        return parts


def schedule_rows(state, day):
    """读当天日程：内存缓存优先，缺了再读库；坏行不参与切分。"""
    rows = []
    try:
        rows = [dict(row) for row in (getattr(state, "schedule", {}) or {}).get(day) or []
                if isinstance(row, dict)]
    except Exception as exc:
        logger.debug("[amo] 读 schedule 缓存失败: %r", exc)
        rows = []
    if rows:
        return rows
    try:
        getter = getattr(getattr(state, "db", None), "get_schedule", None)
        if callable(getter):
            rows = [dict(row) for row in (getter(day) or []) if isinstance(row, dict)]
    except Exception as exc:
        logger.debug("[amo] 读 schedule 表失败: %r", exc)
        rows = []
    return rows


def ensure_block_window(plugin, day, start, end, activity, detail="", category="meal",
                        source="food_spend", reason="餐饮落日程"):
    """在 day 的日程里为核心窗口 [start,end) 落一个块；覆盖块切成「前段/中段/后段」。

    餐饮消费（微型事件、宵夜领域入口）必须先在日程里有块才允许扣款，这里是唯一实现。
    返回 {ok, block_id, day, start, end, activity, reused}；补不上返回 {ok: False, reason}。
    """
    state = getattr(plugin, "state", None)
    if state is None:
        return {"ok": False, "reason": "no_schedule"}
    day = str(day or "")
    begin = _minute_of(str(start or "")[:5])
    finish = _minute_of(str(end or "")[:5])
    if not day or begin is None or finish is None or finish <= begin:
        return {"ok": False, "reason": "bad_window"}
    start_text, end_text = _text_of(begin), _text_of(finish)
    rows = schedule_rows(state, day)
    body = str(activity or "宵夜")
    for row in rows:
        a, b = _minute_of(row.get("start")), _minute_of(row.get("end"))
        if a is None or b is None or not _overlaps(a, b, begin, finish):
            continue
        text = str(row.get("activity") or "")
        if str(row.get("category") or "") == "meal" and (body in text or "宵夜" in text or "夜宵" in text):
            return {"ok": True, "block_id": _block_id_for(day, row), "day": day,
                    "start": str(row.get("start") or ""), "end": str(row.get("end") or ""),
                    "activity": text, "reused": True}
    new_row = {"start": start_text, "end": end_text, "activity": body,
               "place": "家", "zone": "home", "category": str(category or "meal"),
               "source": str(source or "food_spend"), "appointment": True,
               "detail": str(detail or "")}
    kept, inserted = [], False
    for row in rows:
        a, b = _minute_of(row.get("start")), _minute_of(row.get("end"))
        if a is None or b is None or not _overlaps(a, b, begin, finish):
            kept.append(row)
            continue
        if a <= begin and b >= finish:
            if inserted:
                logger.warning("[amo] 餐饮时段命中多个覆盖块，拒绝补块 day=%s %s-%s",
                               day, start_text, end_text)
                return {"ok": False, "reason": "overlap"}
            # H5：显式切块，父 id 由这里指定落在最早那段（before，没有则新块），
            # 不再交给 stamp_event_ids 按重叠比例猜。
            parts = []
            new_index = 0
            if a < begin:
                before = dict(row)
                before["end"] = start_text
                parts.append(before)
                new_index = 1
            parts.append(new_row)
            if b > finish:
                after = dict(row)
                after["start"] = end_text
                parts.append(after)
            pieces = _split_block(day, row, parts, 0)
            if new_index < len(pieces):
                piece_id = str(pieces[new_index].get("event_id") or "")
                if piece_id:
                    new_row["event_id"] = piece_id
            kept.extend(pieces)
            inserted = True
            continue
        logger.warning("[amo] 餐饮时段与已有块部分重叠，拒绝补块 day=%s %s-%s", day,
                       start_text, end_text)
        return {"ok": False, "reason": "overlap"}
    if not inserted:
        kept.append(new_row)
        # 全新块没有父 id 可继承：显式拿一个新 event_id，返回的 block_id 才能
        # 写进账本 ref（H4 只解析 block:ev_<id>）。
        fresh = _new_event_id()
        if fresh:
            new_row["event_id"] = fresh
    kept.sort(key=lambda row: _minute_of(row.get("start"))
              if _minute_of(row.get("start")) is not None else 24 * 60)
    ok = False
    try:
        commit = getattr(state, "commit_schedule_change", None)
        apply = getattr(state, "apply_schedule", None)
        saver = getattr(state, "save_day", None)
        source_text = str(source or "food_spend")
        reason_text = str(reason or "餐饮落日程")
        if callable(apply):
            saved = apply(day, lambda current: list(kept), source=source_text,
                          reason=reason_text)
            ok = bool(saved.get("saved", True)) if isinstance(saved, dict) else bool(saved)
        elif callable(commit):
            saved = commit(day, kept, old_items=rows, source=source_text,
                           reason=reason_text)
            ok = bool(saved.get("saved", True)) if isinstance(saved, dict) else bool(saved)
        elif callable(saver):
            ok = bool(saver(day, kept))
    except Exception as exc:
        logger.warning("[amo] 补餐饮块失败 day=%s: %r", day, exc)
        return {"ok": False, "reason": "save_failed"}
    if not ok:
        return {"ok": False, "reason": "save_failed"}
    try:
        state.schedule[day] = kept
    except Exception as exc:
        logger.debug("[amo] 回写 schedule 快照失败: %r", exc)
    return {"ok": True, "block_id": _block_id_for(day, new_row), "day": day,
            "start": start_text, "end": end_text, "activity": body, "reused": False}


def ensure_food_block(plugin, item, now, reason="", minutes=15):
    """夜间餐饮扣款前，确保 schedule 里有对应显示块（02:12 就要有 02:12-02:27 宵夜）。

    返回 True 表示已有块或已补块；False 表示补不上（那就不许扣钱）。
    切块/落库统一走 ensure_block_window，微型事件与宵夜领域入口共用同一实现。
    """
    label = str(item or "宵夜")
    start = int(now.hour) * 60 + int(now.minute)
    end = min(24 * 60, start + max(1, int(minutes)))
    activity = "吃%s（宵夜）" % label if in_night(now) else "吃%s" % label
    result = ensure_block_window(plugin, now.strftime("%Y-%m-%d"), _text_of(start),
                                 _text_of(end), activity, str(reason or ""))
    return bool(result.get("ok"))


def night_quiet(now=None):
    now = now or _amo_clock.now()
    return in_night(now)


def recent_duplicate(wallet, item, now=None, minutes=DEDUPE_MINUTES):
    """同一 item 在 minutes 分钟内是否已经扣过款（只读账本，不改余额）。"""
    target = item_key(item)
    if not target:
        return False
    now = now or _amo_clock.now()
    rows = []
    since = getattr(wallet, "rows_between", None)
    ledger = getattr(wallet, "ledger", None)
    try:
        if callable(since):
            rows = since(float(now.timestamp()) - minutes * 60, float(now.timestamp()) + 1)
        elif callable(ledger):
            rows = [row for row in (ledger(50) or [])
                    if float(row.get("ts") or 0) >= float(now.timestamp()) - minutes * 60]
    except Exception as exc:
        logger.debug("[amo] 查近期餐饮流水失败，按未重复处理: %r", exc)
        return False
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if int(row.get("delta_cents") or 0) >= 0:
            continue
        body = str(row.get("item_name") or row.get("reason") or "")
        if item_key(body) == target:
            return True
    return False


def food_idem(source, item, now=None, minutes=DEDUPE_MINUTES):
    """餐饮幂等键：来源 + 日期 + item + 10 分钟桶，17:30/17:31 落同一个键。"""
    now = now or _amo_clock.now()
    bucket = int(now.minute) // max(1, int(minutes))
    return "food:%s:%s:%s:%02d%02d" % (source, now.strftime("%Y-%m-%d"),
                                       item_key(item) or "meal", int(now.hour), bucket)


#: 早/午/晚饭点窗口（分钟）。只用于判断“这一餐吃过了没”，不参与聊天措辞。
MEAL_WINDOWS = {"breakfast": (6 * 60, 10 * 60), "lunch": (10 * 60, 15 * 60),
                "dinner": (16 * 60, 22 * 60)}
#: 能算“一顿饭”的词；奶茶/果茶/咖啡属于饮料，不算饭。
MEAL_WORDS = ("面条", "牛肉面", "米饭", "米饭套餐", "快餐", "包子", "豆浆", "白粥",
              "粥", "外卖", "家常菜", "泡面", "饺子", "汉堡", "披萨", "烧烤", "肠粉", "米粉")
DRINK_WORDS = ("奶茶", "果茶", "咖啡", "果汁", "可乐", "饮料")


def meal_slot(now=None):
    """当前饭点：breakfast / lunch / dinner；不在饭点返回空串。"""
    now = now or _amo_clock.now()
    minute = int(now.hour) * 60 + int(now.minute)
    for slot, (start, end) in MEAL_WINDOWS.items():
        if start <= minute < end:
            return slot
    return ""


#: 判重用的宽窗口：与守护三餐的窄窗口（7:30-9 / 11-13 / 18-19:30）分工不同——
#: 宽窗口决定「这一顿有没有着落」，窄窗口决定「几点吃」。改动请同步 watch 侧
#: services/daily_engine.CLAIM_WINDOWS（tests/test_meal_claim_contract.py 会比对）。
CLAIM_WINDOWS = MEAL_WINDOWS

#: 算「一顿饭」的活动词。奶茶/咖啡/果茶不算，避免下午茶占掉午餐位。
CLAIM_WORDS = MEAL_WORDS + ("吃", "饭", "餐", "火锅", "烤肉", "聚餐", "夜宵", "宵夜")


def _looks_like_meal(text):
    body = str(text or "")
    return any(word in body for word in CLAIM_WORDS)


def meal_claim(plugin, day, slot):
    """这个饭点在日程里是不是已经有着落（饭局/约会/餐饮块）。

    返回占住它的活动文字；空串表示空闲。2026-11-20：18:00 买面条时，19:09 的
    火锅合同还没到点扣款，账本干干净净——只看流水必然漏。
    """
    window = CLAIM_WINDOWS.get(slot)
    if not window:
        return ""
    low, high = int(window[0]), int(window[1])
    rows = schedule_rows(getattr(plugin, "state", None), str(day or ""))
    for row in rows:
        if str(row.get("category") or "") not in ("meal", "social", "date"):
            continue
        begin, finish = _minute_of(row.get("start")), _minute_of(row.get("end"))
        if begin is None or finish is None or finish <= begin:
            continue
        if not _overlaps(begin, finish, low, high):
            continue
        text = str(row.get("activity") or "")
        if _looks_like_meal(text):
            return text
    return ""


def meal_spent(plugin, now=None, ledger=None):
    """这个饭点是不是已经吃过了（任何来源的一顿都算）。

    只读共享钱包流水：
    - 守护三餐：idem = meal:<day>:<slot>:N（最可靠）；
    - 其它餐饮：reason 命中主食/外卖词；奶茶/果茶这类饮料不算一顿饭。
    2026-11-18 事故：11:01 守护买了「面条 × 1」，12:53 陪伴又抽到外卖；
    旧判定只看 cooked（自己做饭），买来的面条既不是做饭也不是外卖，直接漏掉。
    """
    now = now or _amo_clock.now()
    slot = meal_slot(now)
    if not slot:
        return False
    day = now.strftime("%Y-%m-%d")
    # 先看日程：已经排了饭局/约会/餐饮块，这一顿就算有着落（不看钱到没到点）
    if meal_claim(plugin, day, slot):
        return True
    wallet = getattr(plugin, "wallet", None)
    start, end = MEAL_WINDOWS[slot]
    if ledger is not None:
        rows = list(ledger)
    elif wallet is None:
        return False
    else:
        try:
            rows = list(wallet.ledger(200) or [])
        except Exception as exc:
            logger.debug("[amo] 查饭点流水失败，按没吃过处理: %r", exc)
            return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
            raw_delta = row.get("delta_cents")
            delta = int(raw_delta if raw_delta is not None else (row.get("delta") or 0))
        except (TypeError, ValueError):
            continue
        if delta >= 0:
            continue
        try:
            moment = _dt.datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            continue
        if moment.strftime("%Y-%m-%d") != day:
            continue
        minute = moment.hour * 60 + moment.minute
        if not (start <= minute < end):
            continue
        idem = str(row.get("idem") or "")
        if idem.startswith("meal:%s:%s" % (day, slot)):
            return True
        reason = str(row.get("reason") or "")
        if any(word in reason for word in DRINK_WORDS) and not any(
                word in reason for word in ("饭", "面", "套餐")):
            continue
        if any(word in reason for word in MEAL_WORDS):
            return True
    return False


def _today_spend_rows(plugin, now):
    """今天到此刻为止的支出流水（只读钱包，归一 delta_cents）。"""
    wallet = getattr(plugin, "wallet", None)
    if wallet is None:
        return []
    day = now.strftime("%Y-%m-%d")
    try:
        rows = list(wallet.ledger(200) or [])
    except Exception as exc:
        logger.debug("[amo] 查今日流水失败，按没有处理: %r", exc)
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
            raw = row.get("delta_cents")
            delta = int(raw if raw is not None else (row.get("delta") or 0))
        except (TypeError, ValueError):
            continue
        if delta >= 0:
            continue
        try:
            moment = _dt.datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            continue
        if moment.strftime("%Y-%m-%d") != day:
            continue
        out.append((moment, str(row.get("idem") or ""), str(row.get("reason") or "")))
    return out


def claimed(plugin, now=None, prefixes=(), words=(), low=0, high=24 * 60):
    """今天某个时间窗内是否已有指定来源/说法的支出（跨插件共用钱包流水）。"""
    now = now or _amo_clock.now()
    for moment, idem, reason in _today_spend_rows(plugin, now):
        minute = moment.hour * 60 + moment.minute
        if not (low <= minute < high):
            continue
        if any(idem.startswith(prefix) for prefix in prefixes):
            return True
        if any(word in reason for word in words):
            return True
    return False


def commute_mode(plugin, now=None):
    """今天的通勤方式：地铁/公交/共享单车/打车/开车/走路；拿不到返回空串。"""
    now = now or _amo_clock.now()
    day = now.strftime("%Y-%m-%d")
    rows = schedule_rows(getattr(plugin, "state", None), day)
    low, high = 6 * 60, 10 * 60 + 30
    mapping = {"subway": "地铁", "bus": "公交", "taxi": "打车", "drive": "开车",
               "bicycle": "共享单车", "bike": "共享单车", "walk": "走路"}
    for row in rows:
        if not isinstance(row, dict):
            continue
        start, end = _minute_of(row.get("start")), _minute_of(row.get("end"))
        if start is None or end is None or not (start < high and low < end):
            continue
        code = str(row.get("transit") or "").strip().lower()
        if code in mapping:
            return mapping[code]
        text = "%s %s %s" % (row.get("activity") or "", row.get("vehicle") or "",
                             row.get("detail") or "")
        for word, mode in TRAFFIC_MODES:
            if word in text:
                return mode
    return ""


def traffic_spent(plugin, now=None):
    """今天早高峰是否已经有一笔通勤（守护 traffic: 或陪伴 micro:subway）。"""
    now = now or _amo_clock.now()
    day = now.strftime("%Y-%m-%d")
    return claimed(plugin, now, prefixes=("traffic:%s" % day, "micro:%s:subway" % day),
                   words=("地铁", "公交", "共享单车", "打车"),
                   low=6 * 60, high=10 * 60 + 30)


def drink_spent(plugin, item, now=None, hours=2):
    """同一杯饮料（奶茶/果茶等）在 hours 内是否已经买过。"""
    target = item_key(item)
    if not target:
        return False
    now = now or _amo_clock.now()
    wallet = getattr(plugin, "wallet", None)
    if wallet is None:
        return False
    cutoff = float(now.timestamp()) - int(hours) * 3600
    try:
        rows = list(wallet.ledger(200) or [])
    except Exception as exc:
        logger.debug("[amo] 查饮料流水失败，按没买过处理: %r", exc)
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
            raw = row.get("delta_cents")
            delta = int(raw if raw is not None else (row.get("delta") or 0))
        except (TypeError, ValueError):
            continue
        if delta >= 0 or ts < cutoff:
            continue
        if item_key(str(row.get("reason") or "")) == target:
            return True
    return False


def late_snack_spent(plugin, now=None, hours=3):
    """hours 内是否已经有一顿夜宵（守护 late_night 或陪伴 micro/late_snack）。"""
    now = now or _amo_clock.now()
    cutoff = float(now.timestamp()) - int(hours) * 3600
    wallet = getattr(plugin, "wallet", None)
    if wallet is None:
        return False
    try:
        rows = list(wallet.ledger(200) or [])
    except Exception as exc:
        logger.debug("[amo] 查夜宵流水失败，按没有处理: %r", exc)
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
            raw = row.get("delta_cents")
            delta = int(raw if raw is not None else (row.get("delta") or 0))
        except (TypeError, ValueError):
            continue
        if delta >= 0 or ts < cutoff:
            continue
        idem = str(row.get("idem") or "")
        reason = str(row.get("reason") or "")
        if idem.startswith("late_snack:") or any(
                word in reason for word in ("夜宵", "宵夜", "烧烤", "泡面")):
            return True
    return False


def pay_food(plugin, item, reason, source="meal", now=None, amount=None, idem=None,
             allow_night=False):
    """餐饮扣款唯一入口：夜间跳过扣款、10 分钟内同物去重、金额走价目表。

    返回 {ok, charged, reason, amount, idem}；夜间是「叙述但不扣钱」，ok=True。
    """
    now = now or _amo_clock.now()
    item = item_key(item) or "餐饮"
    wallet = getattr(plugin, "wallet", None)
    if wallet is None:
        return {"ok": False, "charged": False, "reason": "no_wallet", "amount": 0, "idem": ""}
    if (sick_status(plugin) and late_snack_hours(now)
            and not sick_food_allowed({"text": item, "food": item})):
        logger.info("[amo] 生病期间禁止宵夜/夜间加餐，跳过扣款：%s", item)
        return {"ok": False, "charged": False, "reason": "sick", "amount": 0, "idem": ""}
    if in_night(now) and not allow_night:
        if not ensure_food_block(plugin, item, now, reason):
            logger.warning("[amo] 夜间餐饮找不到/补不上日程块，拒绝扣款：%s", item)
            return {"ok": False, "charged": False, "reason": "no_block",
                    "amount": 0, "idem": ""}
    if recent_duplicate(wallet, item, now=now):
        logger.warning("[amo] %d 分钟内重复餐饮已拦截：%s", DEDUPE_MINUTES, item)
        return {"ok": False, "charged": False, "reason": "duplicate",
                "amount": 0, "idem": ""}
    price = int(amount if amount is not None else price_for(item))
    if price <= 0:
        return {"ok": False, "charged": False, "reason": "no_price", "amount": 0, "idem": ""}
    idem = idem or food_idem(source, item, now)
    try:
        paid = bool(wallet.debit(price, str(reason or item), idem))
    except Exception as exc:
        logger.warning("[amo] 餐饮扣款异常: %r", exc)
        return {"ok": False, "charged": False, "reason": "exception",
                "amount": price, "idem": idem}
    if not paid:
        return {"ok": False, "charged": False, "reason": "no_money",
                "amount": price, "idem": idem}
    return {"ok": True, "charged": True, "reason": "", "amount": price, "idem": idem}
