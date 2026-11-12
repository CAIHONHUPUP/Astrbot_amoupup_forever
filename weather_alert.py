# -*- coding: utf-8 -*-
"""明天天气变坏时的提醒文案（纯逻辑，无 astrbot 依赖，便于单测）。"""

from __future__ import annotations

from typing import List, Optional

THUNDER_WORDS = ("雷", "冰雹")
SNOW_WORDS = ("雪", "冻雨")
RAIN_WORDS = ("雨",)

FIELDS = ("fxDate", "textDay", "textNight", "tempMax", "tempMin")


def parse_daily(payload: Optional[dict]) -> List[dict]:
    """把和风 /v7/weather/{n}d 的响应整理成按日期排列的列表。"""
    daily = (payload or {}).get("daily")
    if not isinstance(daily, list):
        return []
    items = []
    for row in daily:
        if not isinstance(row, dict):
            continue
        item = {key: str(row.get(key) or "") for key in FIELDS}
        if not item["fxDate"]:
            continue
        items.append(item)
    items.sort(key=lambda x: x["fxDate"])
    return items


def _day_text(day: dict) -> str:
    parts = [str((day or {}).get("textDay") or ""), str((day or {}).get("textNight") or "")]
    return " ".join(part for part in parts if part)


def _to_int(value) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def evaluate(day_today: Optional[dict], day_tomorrow: Optional[dict], min_temp_drop: int = 8) -> str:
    """明天的天气变坏就返回一句提醒；正常返回空串。

    day_* 来自 parse_daily()，至少要有 textDay / textNight / tempMax。
    """
    tomorrow_text = _day_text(day_tomorrow or {})
    if not tomorrow_text:
        return ""

    parts = []
    if any(word in tomorrow_text for word in THUNDER_WORDS):
        parts.append("明天雷雨，别在外面乱跑")
    elif any(word in tomorrow_text for word in SNOW_WORDS):
        parts.append("明天有雪，路滑，慢点走")
    elif any(word in tomorrow_text for word in RAIN_WORDS):
        parts.append("明天有雨，伞别忘了")

    today_max = _to_int((day_today or {}).get("tempMax"))
    tomorrow_max = _to_int((day_tomorrow or {}).get("tempMax"))
    if today_max is not None and tomorrow_max is not None:
        drop = today_max - tomorrow_max
        if drop >= max(1, int(min_temp_drop)):
            parts.append("明天降温 %d 度，多穿件衣服" % drop)

    if not parts:
        return ""
    return "；".join(parts) + "。"



# ---- 一天只在早上/晚上各判断一次（纯逻辑，便于单测） ----
MORNING = "morning"
EVENING = "evening"


def parse_clock(value, default_hour: int, default_minute: int = 0) -> int:
    """把 "08:00" / "8" / "晚上8点" 解析成当天第几分钟；空值或非法值退回默认时间。

    面板里的时间字段是自由文本，用户填 "20"（只写小时）或 "晚上8点" 很常见。
    以前只认 "H:MM"，单段时 `[int(x) for x in parts[:2]]` 直接 ValueError、
    静默退回默认值（填 "8" 恰好还是 08:00，填 "21" 就悄悄变成 20:00）。
    """
    text = str(value or "").strip().replace("：", ":").replace("．", ".").replace("。", ".")
    if not text:
        return default_hour * 60 + default_minute
    # 去掉「晚上 / 早上 / 上午 / 下午 / 中午 / 凌晨」这类时段词，
    # 下午/晚上 1-11 点按 24 小时制 +12。
    period = ""
    for word in ("凌晨", "早上", "上午", "中午", "下午", "傍晚", "晚上", "夜里"):
        if text.startswith(word):
            text = text[len(word):].strip()
            period = word
            break
    text = (text.replace("时", ":").replace("点", ":").replace("分", "")
                .replace("小时", ":").strip().rstrip(":"))
    parts = [p for p in text.split(":") if p.strip() != ""]
    if not parts:
        return default_hour * 60 + default_minute
    try:
        hour = int(float(parts[0]))
        minute = int(float(parts[1])) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return default_hour * 60 + default_minute
    if period in ("下午", "傍晚", "晚上", "夜里") and 1 <= hour <= 11:
        hour += 12
    elif period == "中午" and 1 <= hour <= 2:
        hour += 12
    if period in ("晚上", "夜里", "凌晨") and hour == 12:
        # 「晚上12点」是午夜 00:00，不是中午
        hour = 0
    if hour == 24 and minute == 0:
        hour = 23
        minute = 59
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default_hour * 60 + default_minute
    return hour * 60 + minute


def slot_key(today: str, slot: str) -> str:
    """kv 里记“哪天哪个槽位已经判断过”，例如 2026-11-08|evening。"""
    return "%s|%s" % (today, slot)


def next_check_slot(now_minutes, morning, evening, checked: str, today: str) -> str:
    """这一刻该跑哪个检查槽位；都判断过、或者还没到点就返回空串。

    - evening（weather_alert_time，默认 20:00）：唯一会真的发提醒的槽位；
    - morning（weather_alert_morning_time，默认 08:00）：只判断、不发送，
      让“明天天气正常/要变天”一天只算一次，后台循环不用每 30 秒重算。
    morning 传 None（关掉早上那次）或 >= evening 时忽略早上。
    """
    if now_minutes is None:
        return ""
    try:
        now_minutes = int(now_minutes)
        evening = int(evening)
    except (TypeError, ValueError):
        return ""
    if now_minutes < 0:
        return ""
    if now_minutes >= evening:
        return "" if checked == slot_key(today, EVENING) else EVENING
    if morning is None:
        return ""
    try:
        morning = int(morning)
    except (TypeError, ValueError):
        return ""
    if morning >= evening:
        return ""
    if now_minutes >= morning and checked != slot_key(today, MORNING):
        return MORNING
    return ""



# ---- 他问天气时，用缓存拼一句“现在 + 今天 + 明天” ----
WEATHER_QUERY_WORDS = (
    "天气", "气温", "温度", "几度", "多少度", "下雨", "下雪", "雨伞", "带伞",
    "冷不冷", "热不热", "冷吗", "热吗", "降温", "升温", "刮风", "风大",
    "晒不晒", "穿什么", "穿多", "穿少", "会不会下雨", "会不会下雪",
)


def is_weather_query(text) -> bool:
    """这句是不是在问天气（问得越具体越算；太长的消息多半不是纯问天气）。"""
    t = str(text or "").strip()
    if not t or len(t) > 120:
        return False
    return any(word in t for word in WEATHER_QUERY_WORDS)


def forecast_brief(now, today, tomorrow) -> str:
    """现在天气 + 今天 + 明天（含雨雪、温差）拼成一句；一点数据都没有返回空串。"""
    parts = []

    def _num(value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    now = now or {}
    now_text = str(now.get("text") or "").strip()
    if now_text:
        # 和风的 now.temp / humidity 缺字段时可能是 None，直接 % 拼会得到「None℃」
        # 并原样进 <amo_state>，和「只按数据答、不许编温度」的规则打架。
        now_temp = _num(now.get("temp"))
        now_hum = _num(now.get("humidity"))
        bits = ["现在", str(now.get("city") or "").strip(), now_text]
        if now_temp is not None:
            bits.append("%d℃" % now_temp)
        if now_hum is not None:
            bits.append("湿度%d%%" % now_hum)
        wind = str(now.get("wind") or "").strip()
        if wind:
            bits.append(wind)
        line = " ".join(part for part in bits if part)
        if line:
            parts.append(line)

    today = today or {}
    tomorrow = tomorrow or {}
    today_max = _num(today.get("tempMax"))
    today_min = _num(today.get("tempMin"))
    if today_max is not None and today_min is not None:
        parts.append("今天 %d~%d℃" % (today_min, today_max))
    elif today_max is not None:
        parts.append("今天最高 %d℃" % today_max)

    t_text = _day_text(tomorrow)
    day_word = str(tomorrow.get("textDay") or "").strip()
    night_word = str(tomorrow.get("textNight") or "").strip()
    if day_word and day_word == night_word:
        t_text = day_word  # 「多云 多云」去重
    t_max = _num(tomorrow.get("tempMax"))
    t_min = _num(tomorrow.get("tempMin"))
    if t_text:
        line = "明天 " + t_text
        if t_max is not None and t_min is not None:
            line += " %d~%d℃" % (t_min, t_max)
        elif t_max is not None:
            line += " 最高%d℃" % t_max
        parts.append(line)
        if any(word in t_text for word in THUNDER_WORDS):
            parts.append("明天有雷雨，别在外面乱跑")
        elif any(word in t_text for word in SNOW_WORDS):
            parts.append("明天有雪，路滑，慢点走")
        elif any(word in t_text for word in RAIN_WORDS):
            parts.append("明天有雨，记得带伞")

    if today_max is not None and t_max is not None:
        drop = today_max - t_max
        if drop >= 3:
            parts.append("明天比今天最高温低 %d℃，多穿件" % drop)
        elif drop <= -3:
            parts.append("明天比今天最高温高 %d℃" % (-drop))

    return "；".join(part for part in parts if part)
