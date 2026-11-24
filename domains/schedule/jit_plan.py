# -*- coding: utf-8 -*-
"""日程 JIT 到点计划纯内核：只决定「这一刻她做什么」，不读写库、不碰插件。

域内只允许标准库（架构棘轮 leaf 规则）；根目录 schedule_plan.py 只做 re-export 适配。
搬自 schedule_plan.py（2026-11-18，逐字未改）。
"""
from __future__ import annotations

from datetime import date as _date

_BAD_WEATHER_WORDS = ("雨", "雪", "雷", "冰雹", "大风", "沙尘")

#: JIT 是 domains 叶子（只许标准库），所以星期文案在域内自查；
#: 日期标签的公共函数在 amo_shared_time.day_label，域外调用方应以 target_day 传入。
_WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _target_weekday(target_day):
    """target_day（date/datetime/ISO 字符串）→ 0-6；解析失败返回 None。"""
    if target_day is None:
        return None
    try:
        if hasattr(target_day, "weekday"):
            return int(target_day.weekday())
        return int(_date.fromisoformat(str(target_day)[:10]).weekday())
    except (TypeError, ValueError):
        return None


def _weekday_label(weekday, weekday_label=""):
    """星期几的中文短标签；没有可信的 target_day/weekday 时返回空串，绝不硬编码。"""
    text = str(weekday_label or "").strip()
    if text:
        return text
    try:
        index = int(weekday)
    except (TypeError, ValueError):
        return ""
    if 0 <= index <= 6:
        return _WEEKDAY_LABELS[index]
    return ""

def _is_bad_weather(weather_text="", weather_temp=None):
    body = str(weather_text or "")
    if any(word in body for word in _BAD_WEATHER_WORDS):
        return True
    try:
        temp = float(weather_temp)
    except (TypeError, ValueError):
        return False
    return temp >= 35.0 or temp <= 0.0

def _sleep_plan():
    return {"action": "洗漱完准备睡了", "place": "家", "zone": "home",
            "category": "sleep", "cost_cents": 0, "diary": "今天就这样吧，睡觉。"}

def _home_day_plan(hour, reason="在家歇着"):
    if hour >= 23:
        return _sleep_plan()
    if hour == 8:
        return {"action": "睡到自然醒，今天不用上班", "place": "家", "zone": "home",
                "category": "rest", "cost_cents": 0, "diary": "难得能赖一会儿床。"}
    if hour == 12:
        return {"action": "在家随便弄点吃的", "place": "家", "zone": "home",
                "category": "meal", "cost_cents": 0, "diary": "在家对付一口。"}
    if hour == 15:
        return {"action": "窝在沙发上刷手机，没什么安排", "place": "家", "zone": "home",
                "category": "rest", "cost_cents": 0, "diary": "下午慢慢过。"}
    if hour == 19:
        return {"action": "在家热点吃的，早点洗漱", "place": "家", "zone": "home",
                "category": "meal", "cost_cents": 0, "diary": "在家歇一天。"}
    return {"action": reason, "place": "家", "zone": "home",
            "category": "rest", "cost_cents": 0, "diary": ""}

def _period_day_plan(hour):
    if hour >= 23:
        return _sleep_plan()
    if hour in (8, 12, 19):
        return {"action": "肚子疼，在家喝点热水垫一口", "place": "家", "zone": "home",
                "category": "life", "cost_cents": 0, "diary": "痛经，只想在家躺着。"}
    return {"action": "窝在床上捂着热水袋", "place": "家", "zone": "home",
            "category": "rest", "cost_cents": 0, "diary": "难受到不想动。"}

def _outing_day_plan(hour):
    if hour >= 23:
        return _sleep_plan()
    if hour < 10:
        return _home_day_plan(hour)
    if hour >= 21:
        return _home_day_plan(hour, "逛完回家了，早点洗漱")
    if hour == 10:
        return {"action": "出门逛逛，顺路买点小东西", "place": "商场", "zone": "mall",
                "category": "play", "cost_cents": 0, "diary": "休息日出门走走。"}
    if hour == 12:
        return {"action": "在外面找家店吃午饭", "place": "餐厅", "zone": "restaurant",
                "category": "meal", "cost_cents": 0, "diary": "在外面随便吃点。"}
    if hour == 15:
        return {"action": "还在外面闲逛，不着急回去", "place": "商场", "zone": "mall",
                "category": "play", "cost_cents": 0, "diary": "逛到哪算哪。"}
    if hour in (18, 19):
        return {"action": "逛完回家，路上慢悠悠", "place": "路上", "zone": "road",
                "category": "commute", "cost_cents": 0, "diary": "往家走。"}
    return {"action": "在外面溜达", "place": "商场", "zone": "mall",
            "category": "play", "cost_cents": 0, "diary": ""}

def _overtime_day_plan(hour):
    if hour in (18, 19, 20):
        return {"action": "还在公司加班，灯都亮着", "place": "公司", "zone": "office",
                "category": "work", "cost_cents": 0, "diary": "活没干完，得晚点走。"}
    if hour == 21:
        return {"action": "加完班往家赶，路上很空", "place": "路上", "zone": "road",
                "category": "commute", "cost_cents": 0, "diary": "终于下班了。"}
    return {"action": "在工位上忙工作", "place": "公司", "zone": "office",
            "category": "work", "cost_cents": 0, "diary": "加班日，事情一件接一件。"}

def _slack_day_plan(hour):
    if hour == 12:
        return {"action": "在公司附近随便吃点", "place": "公司", "zone": "office",
                "category": "meal", "cost_cents": 0, "diary": "摸鱼日的午饭。"}
    return {"action": "在工位上摸鱼，偷偷刷手机", "place": "公司", "zone": "office",
            "category": "rest", "cost_cents": 0, "diary": "今天没什么心思干活。"}

def jit_plan(hour, hunger, cash_cents, sick=False, mood_word="", stock_pnl=0, weekday=None, sick_action=None,
             mood_value=0, leave_day=False, rest_day=None, weather_text="", weather_temp=None,
             period_phase="", day_type_id="", target_day=None, weekday_label=""):
    """到点即时决策：只给一个当前动作，不排未来。

    target_day：这次 JIT 的「目标日」（date/datetime/ISO 字符串）。给了就从它推星期，
    不再让调用方另算一份，日记文案也不会再写死「周一早上」。weekday 仍兼容旧调用
    （有 target_day 时以 target_day 为准）。
    """
    # cash_cents 只为兼容旧调用方保留：钱包 2026-11-23 已退役（balance 恒 10^9 分），
    # 余额不再参与任何判断，这里直接忽略。
    hour = int(hour)
    hunger = float(hunger)
    if target_day is not None:
        _derived_weekday = _target_weekday(target_day)
        if _derived_weekday is not None:
            weekday = _derived_weekday
    day_weekday_label = _weekday_label(weekday, weekday_label)
    if rest_day is None:
        is_workday = weekday is not None and int(weekday) < 5
    else:
        is_workday = not bool(rest_day)
    bad_weather = _is_bad_weather(weather_text, weather_temp)
    period_home = str(period_phase or "") == "月经期"
    day_kind = str(day_type_id or "")
    if sick and sick_action == "go_to_work_sick":
        if hour == 7:
            return {"action": "硬撑着洗漱，量了体温还是烧", "place": "家", "zone": "home",
                    "category": "life", "cost_cents": 0, "diary": "烧还没退，可今天得去上班。"}
        if hour == 8:
            return {"action": "戴着口罩去挤地铁，咳得嗓子疼", "place": "路上", "zone": "road",
                    "category": "commute", "cost_cents": 0, "diary": "带病通勤，只想快点到公司。"}
        if hour == 9:
            return {"action": "到公司先趴一会儿，头昏昏沉沉", "place": "公司", "zone": "office",
                    "category": "work", "cost_cents": 0, "diary": "撑着坐到工位，先缓缓。"}
        if 10 <= hour <= 17 and hour != 12:
            return {"action": "在工位上昏昏沉沉，时不时咳嗽", "place": "公司", "zone": "office",
                    "category": "work", "cost_cents": 0, "diary": "带病上班，效率低得可怜。"}
        if hour == 12:
            return {"action": "没胃口，在工位上趴了一会儿", "place": "公司", "zone": "office",
                    "category": "meal", "cost_cents": 0, "diary": "午饭吃不下，只想闭眼。"}
        if hour == 18:
            return {"action": "下班回家，路上走得慢吞吞", "place": "路上", "zone": "road",
                    "category": "commute", "cost_cents": 0, "diary": "终于下班了，浑身发软。"}
        if hour == 19:
            return {"action": "到家瘫在沙发上，连饭都不想做", "place": "家", "zone": "home",
                    "category": "rest", "cost_cents": 0, "diary": "带病上一天班，回家只想躺着。"}
    if sick:
        if hour in (8, 12, 19):
            return {"action": "喝白粥、吃药休息", "place": "家", "zone": "home",
                    "category": "life", "cost_cents": 1000,
                    "diary": "生病了只能喝粥，好想吃火锅。"}
        if is_workday and 7 <= hour <= 18:
            return {"action": "请假在家休息", "place": "家", "zone": "home",
                    "category": "rest", "cost_cents": 0, "diary": "今天请假，在家养病。"}
    if leave_day and not sick:
        # 请了病假就是全天在家：哪怕病提前好了，当天也不回公司（2026-11-18 用户要求）。
        if hour == 8:
            return {"action": "睡到自然醒，今天请了假不用上班", "place": "家", "zone": "home",
                    "category": "rest", "cost_cents": 0, "diary": "请了假，难得能赖一会儿床。"}
        if hour == 12:
            return {"action": "在家随便弄点吃的，今天不出门", "place": "家", "zone": "home",
                    "category": "meal", "cost_cents": 0, "diary": "在家对付一口，不出门了。"}
        if hour == 19:
            return {"action": "在家热点吃的，早早洗漱", "place": "家", "zone": "home",
                    "category": "meal", "cost_cents": 0, "diary": "在家歇一天，早点睡。"}
        if hour >= 23:
            return _sleep_plan()
        return {"action": "在家歇着，看看剧", "place": "家", "zone": "home",
                "category": "rest", "cost_cents": 0, "diary": "请了假，就在家待着。"}
    if period_home:
        return _period_day_plan(hour)
    if day_kind in ("homebody", "restday"):
        return _home_day_plan(hour)
    if day_kind == "outing" and not bad_weather:
        return _outing_day_plan(hour)
    if bad_weather and not is_workday:
        return _home_day_plan(hour, "外面天气不好，在家待着")
    if day_kind == "overtime" and is_workday and 18 <= hour <= 21:
        return _overtime_day_plan(hour)
    # 心情优先于日历类型：情绪到极端时，工作日也会摸鱼发呆、周末也会突然想大扫除。
    # 以前这条心情支写在「工作日 / 周末」分支后面，工作日和周末都被前面拦掉，永远是死代码。
    if hour == 15 and float(mood_value or 0) >= 60:
        mood_text = str(mood_word or "")
        if any(word in mood_text for word in ("郁闷", "低落", "委屈", "压力", "疲惫")):
            if is_workday:
                return {"action": "在工位上发呆，手头的事一点也不想动", "place": "公司", "zone": "office",
                        "category": "rest", "cost_cents": 0, "diary": "下午情绪很低，坐在工位上发呆。"}
            return {"action": "窝着发呆，不太想动", "place": "家", "zone": "home",
                    "category": "rest", "cost_cents": 0, "diary": "下午有点丧，什么都不想做。"}
        if any(word in mood_text for word in ("开心", "兴奋", "好奇")):
            if is_workday:
                return {"action": "心情很好，偷偷摸鱼刷了会儿手机", "place": "公司", "zone": "office",
                        "category": "life", "cost_cents": 0, "diary": "下午心情不错，偷偷摸了会儿鱼。"}
            return {"action": "忽然有兴致，把家里彻底打扫了一遍", "place": "家", "zone": "home",
                    "category": "life", "cost_cents": 0, "diary": "心情好，难得想搞大扫除。"}
    if day_kind == "slacking" and is_workday and 9 <= hour <= 17:
        return _slack_day_plan(hour)
    if is_workday:
        # 工作日法则：07-09 必须出门/通勤，09-18 物理位置默认公司，不能在家发呆。
        if hour == 7:
            return {"action": "急急忙忙洗漱，准备出门", "place": "家", "zone": "home",
                    "category": "life", "cost_cents": 0,
                    "diary": ((day_weekday_label + "早上，得赶紧出门。")
                              if day_weekday_label else "早上，得赶紧出门。")}
        if hour == 8:
            return {"action": "下楼买早餐，边走边吃去地铁站", "place": "路上", "zone": "road",
                    "category": "commute", "cost_cents": 0, "diary": "边赶路边吃，别迟到。"}
        if hour == 9:
            return {"action": "到公司，先泡杯咖啡", "place": "公司", "zone": "office",
                    "category": "work", "cost_cents": 0, "diary": "到工位了，先缓一口气。"}
        if 10 <= hour <= 17 and hour != 12:
            return {"action": "在工位上忙工作", "place": "公司", "zone": "office",
                    "category": "work", "cost_cents": 0, "diary": "手头的事一件接一件。"}
        if hour == 12:
            return {"action": "在公司食堂吃午饭", "place": "公司", "zone": "office",
                    "category": "meal", "cost_cents": 0, "diary": "中午在食堂随便吃点。"}
        if hour == 18:
            return {"action": "下班回家", "place": "路上", "zone": "road",
                    "category": "commute", "cost_cents": 0, "diary": "终于下班了。"}
        if hour == 19:
            return {"action": "到家先歇一会儿，准备晚饭", "place": "家", "zone": "home",
                    "category": "life", "cost_cents": 0, "diary": "到家了，先坐一会儿。"}
    if weekday is not None and not is_workday and hour in (8, 12, 15, 19):
        if hour == 15:
            return {"action": "窝在沙发上刷手机，没什么安排", "place": "家", "zone": "home",
                    "category": "rest", "cost_cents": 0, "diary": "周末下午，慢慢过。"}
        return {"action": "在家收拾屋子，看看剧", "place": "家", "zone": "home",
                "category": "life", "cost_cents": 0, "diary": "周末在家，不着急。"}
    if hour in (8, 12, 19) and hunger < 80:
        if hour == 12:
            food, cost = "楼下吃碗牛肉面", 1500
        elif hour == 8:
            food, cost = "买个包子和豆浆", 800
        else:
            food, cost = "随便吃点家常菜", 2000
        return {"action": "正在" + food, "place": "楼下小馆", "zone": "restaurant",
                "category": "meal", "cost_cents": cost,
                "diary": "有点饿，花了 %d 块%s。" % (cost // 100, food)}
    if hour == 15:
        # 心情支已经挪到最前面（心情优先于日历类型）；这里只剩「日历不明」时的普通下午。
        return {"action": "在忙手头的事，偶尔刷手机", "place": "", "zone": "home",
                "category": "life", "cost_cents": 0, "diary": "下午慢慢过。"}
    if hour == 23:
        return {"action": "洗漱完准备睡了", "place": "家", "zone": "home",
                "category": "sleep", "cost_cents": 0, "diary": "今天就这样吧，睡觉。"}
    return {"action": "在忙自己的事", "place": "", "zone": "home",
            "category": "life", "cost_cents": 0, "diary": ""}
