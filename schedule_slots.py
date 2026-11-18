# -*- coding: utf-8 -*-
"""schedule_slots：ScheduleEngine 的一部分方法（逐字搬运）。"""

from __future__ import annotations
from typing import Any, TYPE_CHECKING

import logging

try:  # 包模式（AstrBot 加载插件）
    from .schedule_plan import (
        Any, BREAKFAST_BANK, CATEGORY_MOOD, COMMUTE_BANK, DINNER_BANK, HOME_LIFE_BANK,
        HOTEL_BANK, INDOOR_BANK, LUNCH_BANK, MOOD_BASE, MOOD_KEYS, NEGATIVE_LABELS,
        NIGHT_LIFE_BANK, POSITIVE_LABELS, REST_BANK, ROAD_BANK, SAFE_EVENT, SCENIC_BANK,
        STATION_BANK, SWIM_DETAILS, TAXI_BANK, _brief_world, _mood_text, _scenic_text,
        _weather_text, clamp, date, detail_conflicts, fmt_hhmm, parse_hhmm, random, re,
        work_bank_for,
    )
except ImportError:  # 直接按文件加载时（测试/模拟器）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from schedule_plan import (
            Any, BREAKFAST_BANK, CATEGORY_MOOD, COMMUTE_BANK, DINNER_BANK, HOME_LIFE_BANK,
            HOTEL_BANK, INDOOR_BANK, LUNCH_BANK, MOOD_BASE, MOOD_KEYS, NEGATIVE_LABELS,
            NIGHT_LIFE_BANK, POSITIVE_LABELS, REST_BANK, ROAD_BANK, SAFE_EVENT, SCENIC_BANK,
            STATION_BANK, SWIM_DETAILS, TAXI_BANK, _brief_world, _mood_text, _scenic_text,
            _weather_text, clamp, date, detail_conflicts, fmt_hhmm, parse_hhmm, random, re,
            work_bank_for,
        )

try:  # 文案池按地点选 + 地点自洽校验（2026-11-17 楼下/家 矛盾）
    from .schedule_plan import activity_place_conflict, meal_bank_for
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from schedule_plan import activity_place_conflict, meal_bank_for


logger = logging.getLogger(__name__)


class ScheduleSlotsMixin:
    # 方法体逐字搬运自拆分前的 schedule_engine.py。

    # ------------------------------------------------------------------
    # 3. 展开成 24 个整点
    # ------------------------------------------------------------------
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        llm_json: Any
        p: Any
    def _expand(self, segments, ctx):
        items = []
        for seg in segments:
            start = parse_hhmm(seg.get("start"), 0)
            end = parse_hhmm(seg.get("end"), start + 60)
            if end <= start:
                end = start + 60
            start = max(0, min(24 * 60, start))
            end = max(start, min(24 * 60, end))
            first_hour = start // 60
            last_hour = (end + 59) // 60
            for hour in range(first_hour, last_hour):
                if hour >= 24:
                    break
                slot_start = hour * 60
                slot_end = min(end, (hour + 1) * 60)
                item = {
                    "start": fmt_hhmm(slot_start),
                    "end": fmt_hhmm(slot_end),
                    "activity": str(seg.get("activity") or "休息"),
                    "place": str(seg.get("place") or ctx["home"]),
                    "zone": str(seg.get("zone") or "home"),
                    "category": str(seg.get("category") or "rest"),
                    "transit": str(seg.get("transit") or ""),
                    "vehicle": str(seg.get("vehicle") or ""),
                    "detail": str(seg.get("detail") or "") if hour == first_hour else "",
                    "spot": str(seg.get("spot") or ""),
                    "swim": bool(seg.get("swim")),
                    "soothe": bool(seg.get("soothe")),
                    "event": str(seg.get("event") or "") if hour == first_hour else "",
                    "effects": dict(seg.get("effects") or {}) if hour == first_hour else {},
                }
                items.append(item)
        # 补洞：极端情况下骨架没有覆盖完整 24 小时。
        by_start = {}
        for item in items:
            by_start[parse_hhmm(item.get("start"))] = item
        result = []
        for hour in range(24):
            key = hour * 60
            item: Any = by_start.get(key)
            if item is None:
                item = {
                    "start": fmt_hhmm(key), "end": fmt_hhmm(key + 60),
                    "activity": "睡觉" if hour < 7 or hour >= 23 else "休息",
                    "place": ctx["home"], "zone": "home",
                    "category": "sleep" if hour < 7 or hour >= 23 else "rest",
                    "transit": "", "vehicle": "", "detail": "", "spot": "",
                    "swim": False, "soothe": False, "event": "", "effects": {},
                }
            item["end"] = fmt_hhmm(key + 60)
            result.append(item)
        return result

    # ------------------------------------------------------------------
    # 4. 活动文案（LLM 不可用时的真人化兜底）
    # ------------------------------------------------------------------
    def _fill_activities(self, items, ctx):
        rng = random.Random(int(ctx.get("seed") or 0) ^ 0x9E3779B9)
        prev = ""
        for item in items:
            hour = parse_hhmm(item.get("start")) // 60
            activity = self._activity_for(item, ctx, hour, rng, prev)
            if activity and activity != prev:
                item["activity"] = activity
            if not item.get("detail"):
                item["detail"] = self._detail_for(item, ctx, hour, rng)
            prev = item.get("activity", "")
        return self._sync_transit_vehicle(self._enforce_place_text(items))

    def _activity_for(self, item, ctx, hour, rng, prev):
        zone = str(item.get("zone") or "")
        category = str(item.get("category") or "")
        transit = str(item.get("transit") or "")
        spot = str(item.get("spot") or "")
        base = str(item.get("activity") or "")
        generic = base in ("上班", "休息", "吃午饭", "吃晚饭", "睡觉", "起床吃早餐", "洗澡护肤")
        if not generic:
            return base
        if category == "sleep":
            if hour == 7 and (ctx.get("yesterday") or {}).get("late_night"):
                return "赖床、起不来"
            return "睡觉"
        if zone == "road":
            if transit == "drive":
                bank = ROAD_BANK
            elif transit == "taxi":
                bank = TAXI_BANK
            elif transit == "train":
                return "坐高铁去" + str((ctx.get("event") or {}).get("city") or "外地")
            elif transit == "subway":
                bank = COMMUTE_BANK
            else:
                bank = ["在路上"]
            return self._choose(bank, rng, prev)
        if zone == "station":
            return self._choose(STATION_BANK, rng, prev)
        if zone == "hotel":
            return self._choose(HOTEL_BANK, rng, prev)
        if zone == "office":
            # 按类别挑，而不是白名单小时：加班到 18-21 点时也要是「上班」，
            # 不能再落到 REST_BANK 的「瘫在沙发上」。
            if category == "work":
                return self._choose(work_bank_for(ctx.get("world")), rng, prev)
            return "午休" if hour == 13 else self._choose(REST_BANK, rng, prev)
        if zone == "scenic":
            if spot:
                projects = [str(x) for x in ((ctx.get("scenic") or {}).get("projects") or []) if str(x)]
                if projects:
                    project = projects[hour % len(projects)]
                    return "逛" + spot + "：" + project
            return self._choose(SCENIC_BANK, rng, prev)
        if zone == "restaurant":
            if category == "meal":
                bank = self._meal_bank(hour, zone, item.get("place"))
                return self._choose(bank, rng, prev)
            return self._choose(["喝杯东西", "坐着歇脚", "刷手机"], rng, prev)
        if zone == "mall":
            return self._choose(INDOOR_BANK, rng, prev)
        if zone == "home":
            if category == "meal":
                bank = self._meal_bank(hour, zone, item.get("place"))
                return self._choose(bank, rng, prev)
            if category == "work":
                return self._choose(work_bank_for(ctx.get("world")), rng, prev)
            if category == "rest":
                return self._choose(REST_BANK, rng, prev)
            if category == "life":
                bank = NIGHT_LIFE_BANK if hour >= 20 else HOME_LIFE_BANK
                return self._choose(bank, rng, prev)
        return base or self._choose(REST_BANK, rng, prev)

    @staticmethod
    def _meal_bank(hour, zone="", place=""):
        """早饭/午饭/晚饭分开，且按「在哪吃」分池。

        08:00 不该「下楼吃小馆子」，22:00 也不该吃早餐库；
        place=家 的块更不能抽到「楼下吃碗粉」（2026-11-17 08:00 事故）。
        """
        return meal_bank_for(hour, zone, place)

    def _detail_for(self, item, ctx, hour, rng):
        zone = str(item.get("zone") or "")
        category = str(item.get("category") or "")
        activity = str(item.get("activity") or "")

        def pick(bank):
            # 细节只是点缀，不能和这一小时的活动对着干（便利店却说被拉进会议）。
            bank = [word for word in bank if word and not detail_conflicts(activity, word)]
            return self._choose(bank, rng, "")

        if zone == "scenic":
            text = str(item.get("place") or "") + " " + activity
            if any(word in text for word in ("游泳", "泳池", "海边", "沙滩", "水上乐园",
                                             "水世界", "玩水")):
                return pick(list(SWIM_DETAILS))
            return pick([
                "看到猴子抢游客的水", "排队买奶茶排了二十分钟", "停车位找了半小时",
                "拍照拍到手机快没电", "被路边小吃香得走不动",
            ])
        if zone == "road":
            if item.get("vehicle"):
                return pick([
                    "导航一路飘红", "停车场绕了三圈", "被加塞了", "堵在市区路上",
                ])
            return pick(["车上人很多", "差点坐过站", "一路小跑"])
        if zone == "playground":
            return pick(["排队排到腿酸", "过山车下来嗓子都喊哑了", "气球差点飞走",
                         "巡游队伍太热闹了", "买了个发箍戴上"])
        if zone == "office" and category == "work":
            return pick(["改到第三版", "需求又变了", "咖啡续了一杯", "被拉进临时会议"])
        if zone == "restaurant":
            return pick(["多放了点辣", "辣得直吸气", "吃到一半才想起拍照"])
        if zone == "home" and category == "rest":
            return pick(["沙发太舒服了", "剧追到停不下来", "刷到一个好笑的视频"])
        return ""

    def _repair_details(self, items, ctx):
        """模型润色完 activity 之后，把和它对不上的 detail 重新挑一个。

        `build_skeleton()` 里 detail 是按 zone/category 选的，随后 `enrich()`
        会用模型重写 activity；重写后两者可能打架（见 detail_conflicts）。
        这里在润色之后统一兜一次底，保证存下来的骨架自洽。
        """
        rng = random.Random(int(ctx.get("seed") or 0) ^ 0x5EED5)
        self._enforce_place_text(items)
        for item in items:
            detail = str(item.get("detail") or "").strip()
            if not detail or not detail_conflicts(item.get("activity"), detail):
                continue
            hour = parse_hhmm(item.get("start")) // 60
            item["detail"] = self._detail_for(item, ctx, hour, rng)
        return self._sync_transit_vehicle(items)


    @staticmethod
    def _choose(bank, rng, prev):
        bank = [item for item in bank if item and item != prev]
        if not bank:
            return ""
        return bank[rng.randrange(len(bank))]

    # ------------------------------------------------------------------
    # 5. 情绪连贯
    # ------------------------------------------------------------------
    def _smooth_moods(self, items, ctx):
        vector = {key: clamp((ctx.get("mood") or {}).get(key, MOOD_BASE[key])) for key in MOOD_KEYS}
        yesterday = ctx.get("yesterday") or {}
        health = ctx.get("health") or {}
        period = ctx.get("period") or {}
        if yesterday.get("late_night"):
            vector["疲惫"] = max(clamp(vector["疲惫"] + 16), 68.0)
            vector["开心"] = clamp(vector["开心"] - 6)
            vector["暴躁"] = clamp(vector["暴躁"] + 3)
        if health.get("sick"):
            vector["疲惫"] = clamp(vector["疲惫"] + 10)
            vector["开心"] = clamp(vector["开心"] - 5)
        if int(period.get("pain") or 0) >= 7:
            vector["疲惫"] = clamp(vector["疲惫"] + 8)
            vector["委屈"] = clamp(vector["委屈"] + 5)
        prev_label = ""
        for item in items:
            hour = parse_hhmm(item.get("start")) // 60
            before = dict(vector)
            # 缓慢回落到基础值，避免情绪一条直线。
            for key in MOOD_KEYS:
                vector[key] = vector[key] + (MOOD_BASE[key] - vector[key]) * 0.05
            if 0 <= hour < 6:
                vector["疲惫"] = vector["疲惫"] + 2
                vector["平静"] = vector["平静"] + 1
            elif 6 <= hour < 9:
                vector["疲惫"] = vector["疲惫"] - 2
                vector["开心"] = vector["开心"] + 1
            elif 12 <= hour < 14:
                vector["开心"] = vector["开心"] + 1
            elif 22 <= hour < 24:
                vector["疲惫"] = vector["疲惫"] + 2
                vector["依恋"] = vector["依恋"] + 1
            for key, delta in (CATEGORY_MOOD.get(item.get("category"), {}) or {}).items():
                if key in vector:
                    vector[key] = vector[key] + delta
            effects = item.get("effects") or {}
            if effects:
                for key, delta in effects.items():
                    if key in vector:
                        vector[key] = vector[key] + float(delta)
            # 单小时最多变化 8 点；突发事件允许 22 点。
            cap = 22.0 if effects else 8.0
            for key in MOOD_KEYS:
                vector[key] = clamp(vector[key], before[key] - cap, before[key] + cap)
                vector[key] = clamp(vector[key])
            if yesterday.get("late_night") and hour < 18:
                vector["疲惫"] = max(vector["疲惫"], 64.0)
            label = self._mood_label(vector)
            if prev_label and not item.get("soothe") and not effects:
                if prev_label in NEGATIVE_LABELS and label in POSITIVE_LABELS:
                    label = prev_label
                elif prev_label in POSITIVE_LABELS and label in NEGATIVE_LABELS:
                    label = prev_label
            item["mood"] = label
            item["mood_valence"] = round(self._mood_valence(vector), 1)
            prev_label = label
        return items

    @staticmethod
    def _mood_label(vector):
        if max(vector["生气"], vector["暴躁"]) >= 58:
            return "烦躁"
        if vector["委屈"] >= 58:
            return "委屈"
        if vector["郁闷"] >= 58:
            return "低落"
        if vector["疲惫"] >= 64:
            return "疲惫"
        if vector["焦虑"] >= 58:
            return "焦虑"
        if max(vector["开心"], vector["兴奋"]) >= 64:
            return "开心"
        if vector["依恋"] >= 64:
            return "黏人"
        return "平静"

    @staticmethod
    def _mood_valence(vector):
        positive = (vector["开心"] + vector["兴奋"] + vector["平静"] + vector["依恋"]) / 4.0
        negative = (vector["郁闷"] + vector["疲惫"] + vector["生气"] + vector["暴躁"] + vector["委屈"] + vector["焦虑"]) / 6.0
        return clamp(50 + (positive - negative) * 0.8)

    # ------------------------------------------------------------------
    # 6. 物理校验：时间连续 + 不瞬移
    # ------------------------------------------------------------------
    def _validate(self, items, ctx):
        for item in items:
            item.pop("segments", None)
            item["start"] = fmt_hhmm(parse_hhmm(item.get("start"), 0))
            item["end"] = fmt_hhmm(parse_hhmm(item.get("end"), parse_hhmm(item.get("start"), 0) + 60))
            if parse_hhmm(item["end"]) <= parse_hhmm(item["start"]):
                item["end"] = fmt_hhmm(parse_hhmm(item["start"]) + 60)
        for index, item in enumerate(items):
            expected = fmt_hhmm(index * 60)
            if item.get("start") != expected:
                item["start"] = expected
            item["end"] = fmt_hhmm((index + 1) * 60)
        # 最后一条必须是 24:00 闭环，不能留 23:59。
        if items:
            items[-1]["end"] = "24:00"

        direct_ok = {
            ("office", "restaurant"), ("restaurant", "office"),
            ("office", "mall"), ("mall", "office"),
            ("scenic", "restaurant"), ("restaurant", "scenic"),
            ("restaurant", "hotel"), ("hotel", "restaurant"),
            ("restaurant", "home"), ("home", "restaurant"),
            ("home", "hospital"), ("hospital", "home"), ("hospital", "hospital"),
            ("mall", "restaurant"), ("restaurant", "mall"),
            ("station", "restaurant"), ("restaurant", "station"),
            ("home", "home"), ("office", "office"), ("scenic", "scenic"),
            ("restaurant", "restaurant"), ("mall", "mall"), ("playground", "playground"),
            ("playground", "restaurant"), ("restaurant", "playground"),
        }
        for index in range(1, len(items)):
            prev = items[index - 1]
            cur = items[index]
            prev_zone = str(prev.get("zone") or "")
            zone = str(cur.get("zone") or "")
            if not zone or not prev_zone or zone == prev_zone:
                continue
            if prev_zone == "road" or zone == "road":
                continue
            if (prev_zone, zone) in direct_ok:
                continue
            # 兜底修复：把当前小时改成“在路上”，下一小时再到达。
            original_place = str(cur.get("place") or "")
            cur["activity"] = "从%s赶去%s" % (prev.get("place") or "上一个地方", original_place)
            cur["place"] = "路上"
            cur["zone"] = "road"
            cur["category"] = "commute"
            cur["transit"] = cur.get("transit") or ("drive" if ctx.get("car") else "taxi")
            cur["vehicle"] = cur.get("vehicle") or (ctx.get("car") if cur["transit"] == "drive" else "")
            cur["detail"] = "上一小时还在%s，这一小时在路上" % (prev.get("place") or "别处")
            cur["effects"] = {}
        # 周末景区必须体现资产绑定：开车、堵车、找停车位。
        if (ctx.get("meta") or {}).get("rest") and ctx.get("scenic") and ctx.get("good_weather"):
            scenic_items = [item for item in items if item.get("zone") == "scenic"]
            if scenic_items:
                first = scenic_items[0]
                if not first.get("vehicle"):
                    first["vehicle"] = ctx.get("car") or ""
                if "停车" not in str(first.get("detail") or ""):
                    first["detail"] = (str(first.get("detail") or "") + "；自己开车来的，停车位找了很久").strip("；")
        return self._sync_transit_vehicle(items)

    #: 文案里出现这些词 = 人在路上，区域必须是 road
    TRAVEL_WORDS = (
        "地铁", "公交", "车厢", "车上", "车站", "站台", "打车", "出租车", "开车",
        "骑车", "骑行", "路上", "通勤", "换乘", "高铁", "火车", "飞机", "赶去", "赶往",
    )
    #: 「到家 / 在家」这类不是“在路上”，别误判
    AT_HOME_WORDS = ("到家", "在家", "家里", "进门", "躺沙发", "被窝")

    @staticmethod
    def _guess_transit(text):
        body = str(text or "")
        if "地铁" in body:
            return "subway"
        if "公交" in body:
            return "bus"
        if "打车" in body or "出租车" in body:
            return "taxi"
        if "开车" in body:
            return "drive"
        if "高铁" in body or "火车" in body:
            return "train"
        if "飞机" in body:
            return "plane"
        return "walk"

    @classmethod
    def _align_zone_with_activity(cls, items):
        """文案和区域对齐：她说“在地铁上”，生图取景就必须是车厢/路面，不能是办公室。

        LLM 只被允许改 activity（骨架的 zone/place 不让它动），所以它写出
        「地铁上听着歌晃回家」时 zone 还停在 office —— 照片就成了办公室。
        """
        for item in items or []:
            zone = str(item.get("zone") or "")
            place = str(item.get("place") or "")
            act = str(item.get("activity") or "")
            if zone == "road":
                # 区域是“路上”，地点也统一成“路上”，别出现“地铁上 / 写字楼”并存
                if place and place != "路上":
                    item["place"] = "路上"
                if not item.get("transit"):
                    item["transit"] = cls._guess_transit(act)
                # 文案写“在工位/在家/沙发/看电视”但区域是路上：改成真正的通勤文案。
                # “下班回家/到家/路上”这类本来就是路上，不误伤。
                if (str(item.get("category") or "") == "commute"
                        and any(w in act for w in ("工位", "公司", "办公室", "沙发", "床", "电视", "厨房", "被窝", "家"))
                        and not any(w in act for w in ("回家", "到家", "路上", "地铁", "公交", "开车", "打车", "走", "车厢", "等车"))):
                    transit = str(item.get("transit") or "")
                    item["activity"] = {
                        "subway": "挤地铁去公司", "drive": "开车去公司",
                        "bus": "坐公交去公司", "walk": "走路去公司",
                        "taxi": "打车去公司",
                    }.get(transit) or "赶路去公司"
                    item["detail"] = ""
                continue
            if zone == "restaurant":
                # 在楼下小馆吃饭，文案却写「在家煮面」：换成外面吃的文案。
                if activity_place_conflict(act, place, zone):
                    item["activity"] = cls._place_safe_activity(item)
                    item["detail"] = ""
                continue
            if zone in ("home", "hospital"):
                if zone == "home" and activity_place_conflict(act, place, zone):
                    # place=家 却写「楼下/出去/外面」：换成真在家做的文案（2026-11-17 事故）。
                    item["activity"] = cls._place_safe_activity(item)
                    item["detail"] = ""
                continue
            if not any(word in act for word in cls.TRAVEL_WORDS):
                continue
            if any(word in act for word in cls.AT_HOME_WORDS):
                continue
            item["place"] = "路上"
            item["zone"] = "road"
            item["transit"] = item.get("transit") or cls._guess_transit(act)
            item["vehicle"] = item.get("vehicle") or ("地铁" if item["transit"] == "subway" else "")
            item["effects"] = {}
        return items

    #: activity 里出现这些词 = 这一小时用的是这种交通方式（顺序即优先级）
    TRANSIT_MODE_WORDS = (
        ("地铁", ("地铁", "换乘")),
        ("公交", ("公交", "巴士")),
        ("打车", ("打车", "出租车", "网约车", "滴滴", "的士")),
        ("开车", ("开车", "自驾", "驾车", "自己开", "驾驶")),
        ("自行车", ("骑车", "骑行", "单车", "自行车")),
        ("步行", ("步行", "走路")),
    )
    #: vehicle 字段里可能存的是别名或车名（出租车 / 白色大众高尔夫 / 小电驴）
    VEHICLE_MODE_ALIAS = {
        "地铁": "地铁", "坐地铁": "地铁",
        "公交": "公交", "公交车": "公交", "坐公交": "公交",
        "打车": "打车", "出租车": "打车", "网约车": "打车", "滴滴": "打车",
        "开车": "开车", "自驾": "开车", "自己开": "开车",
        "步行": "步行", "走路": "步行",
        "自行车": "自行车", "骑车": "自行车", "单车": "自行车", "电动车": "自行车",
    }
    #: 中文交通方式 -> transit 字段代码（与 schedule_*_segments 的取值一致）
    MODE_CODE = {
        "地铁": "subway", "公交": "bus", "打车": "taxi",
        "开车": "drive", "自行车": "bicycle", "步行": "walk",
    }
    #: transit 字段认得的合法代码；历史 outbound/return 之类的脏值要重写
    TRANSIT_CODES = frozenset({
        "subway", "bus", "taxi", "drive", "train", "plane", "walk", "bicycle", "bike",
    })
    #: activity 里没写交通方式时，按 vehicle 补一句（括号缀在句子后面，不硬造新句）
    VEHICLE_TEXT = {
        "地铁": "坐地铁", "公交": "坐公交", "开车": "开车",
        "打车": "打车", "步行": "走路", "自行车": "骑车",
    }

    @classmethod
    def _mode_from_text(cls, text):
        """从 activity 里认出交通方式；认不出返回空串（不瞎猜）。"""
        body = str(text or "")
        for mode, words in cls.TRANSIT_MODE_WORDS:
            for word in words:
                if word in body:
                    return mode
        return ""

    @classmethod
    def _vehicle_mode(cls, item):
        """把 vehicle 字段折算成标准交通方式；折算不出返回空串。"""
        raw = str((item or {}).get("vehicle") or "").strip()
        if not raw:
            return ""
        if raw in cls.VEHICLE_MODE_ALIAS:
            return cls.VEHICLE_MODE_ALIAS[raw]
        for alias, mode in cls.VEHICLE_MODE_ALIAS.items():
            if alias in raw:
                return mode
        # vehicle 里存的是车名（白色大众高尔夫 / 小电驴）：transit=drive 就是自己开车。
        if str((item or {}).get("transit") or "") == "drive":
            return "开车"
        return ""

    @classmethod
    def _sync_transit_vehicle(cls, items):
        """收口：路上块的 activity 和 vehicle 必须是同一种交通方式。

        `amo_transition._transit_row()` 先按目的地/天气定 vehicle 再写 activity，
        之后 `_fill_activities` / 模型润色 / `_repair_details` 都可能把 activity
        改成另一种方式，vehicle 却留在原地（2026-11-17 10:00 的块文案写「坐地铁」、
        vehicle=开车，面板头就显示成「路上 · 开车」）。这里在文案定稿之后统一对齐：

        - activity 里认得出交通方式 -> 以文案为准同步 vehicle；
        - activity 里没有，但 vehicle 认得出来 -> 给文案补一句「（坐地铁）」；
        - 两边都认不出 -> 原样保留，只留 debug 日志，绝不猜。
        """
        for item in items or []:
            if not isinstance(item, dict):
                continue
            zone = str(item.get("zone") or "")
            source = str(item.get("source") or "")
            if zone != "road" and source != "transit_block":
                continue
            activity = str(item.get("activity") or "").strip()
            raw = str(item.get("vehicle") or "").strip()
            by_text = cls._mode_from_text(activity)
            by_vehicle = cls._vehicle_mode(item)
            if by_text:
                code = cls.MODE_CODE.get(by_text) or ""
                if by_vehicle == by_text:
                    if code and str(item.get("transit") or "") != code:
                        item["transit"] = code
                    continue
                # 文案说开车、vehicle 存的是车名：开车本来就开自己的车，别把车名删了。
                if by_text == "开车" and raw and not by_vehicle:
                    item["transit"] = "drive"
                    continue
                item["vehicle"] = by_text
                if code:
                    item["transit"] = code
                continue
            suffix = cls.VEHICLE_TEXT.get(by_vehicle) or ""
            if not suffix:
                logger.debug("[amo] 路上块 activity/vehicle 都认不出交通方式，保留原样：%s %s",
                             item.get("start"), activity)
                continue
            if not raw:
                item["vehicle"] = by_vehicle
            code = cls.MODE_CODE.get(by_vehicle) or ""
            if code and str(item.get("transit") or "") not in cls.TRANSIT_CODES:
                item["transit"] = code
            if suffix not in activity:
                item["activity"] = (activity + "（" + suffix + "）") if activity else suffix
        return items

    @classmethod
    def _place_safe_activity(cls, item):
        """按 place/zone 挑一条不矛盾的文案（家 -> 在家弄的，外面 -> 楼下吃的）。"""
        start_min = parse_hhmm(item.get("start"), 0)
        hour = start_min // 60
        zone = str(item.get("zone") or "")
        place = str(item.get("place") or "")
        category = str(item.get("category") or "")
        if category == "meal":
            bank = meal_bank_for(hour, zone, place)
        elif category == "life":
            bank = list(NIGHT_LIFE_BANK if hour >= 20 else HOME_LIFE_BANK)
        elif category == "work":
            bank = list(work_bank_for(None))
        else:
            bank = list(REST_BANK)
        bank = [word for word in bank if not activity_place_conflict(word, place, zone)]
        if not bank:
            return ""
        return bank[start_min % len(bank)]

    def _enforce_place_text(self, items):
        """生成/润色之后兜底：activity 不许跟 place/zone 对着干。

        2026-11-17 08:02 事故：骨架写 place=家，模型把 activity 润色成「楼下吃碗热汤粉」，
        bot 照抄这句话对用户说「我在楼下吃粉呢」。
        """
        for item in items or []:
            if not isinstance(item, dict):
                continue
            activity = item.get("activity")
            place = item.get("place")
            zone = item.get("zone")
            if not activity_place_conflict(activity, place, zone):
                continue
            fixed = self._place_safe_activity(item)
            if not fixed:
                continue
            item["activity"] = fixed
            if item.get("detail") and detail_conflicts(fixed, item["detail"]):
                item["detail"] = ""
        return items

    # ------------------------------------------------------------------
    # 7. 让模型只改写 activity，不改物理骨架
    # ------------------------------------------------------------------
    async def enrich(self, umo, ctx, items, llm):
        if not items or llm is None or not self.p.cfg_bool("schedule_llm_backbone", True):
            return items
        lines = []
        for item in items:
            lines.append("%s-%s｜%s｜%s｜%s｜%s" % (
                item.get("start"), item.get("end"), item.get("place"),
                item.get("category"), item.get("zone"), item.get("activity"),
            ))
        event = ctx.get("event") or {}
        scenic = ctx.get("scenic") or {}
        day_type = ctx.get("day_type") or {}
        day_fact = ""
        try:
            day_obj = date.fromisoformat(str(ctx.get("day") or "")[:10])
            weekday_label = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")[day_obj.weekday()]
            day_kind = "法定工作日，她需要上班" if day_obj.weekday() < 5 else "休息日，不用上班"
            day_fact = "【绝对事实】今天是%s，%s。\n" % (weekday_label, day_kind)
        except Exception:
            day_fact = ""
        day_type_line = ""
        if str(day_type.get("guide") or "").strip():
            day_type_line = "今天是「%s」：%s\n" % (
                str(day_type.get("name") or "普通一天"), str(day_type["guide"]).strip(),
            )
        system = "你是角色的日程文案助手。只输出 JSON 数组，不要输出解释。"
        prompt = (
            "角色设定：\n" + _brief_world(ctx.get("world") or {}) + "\n"
            "今天：%s，%s；天气：%s。\n"
            "%s"
            "%s"
            "情绪：%s；生理期：%s；身体：%s；昨日：%s\n"
            "突发事件：%s\n"
            "景区：%s\n"
            "下面是已经固定好的 24 个整点骨架（时间｜地点｜类别｜区域｜当前文案）：\n%s\n"
            "请为每个小时写一句更真实、更有生活感的 activity（8-22字），"
            "输出要求：\n"
            "1. 顺序对应，正好 24 个字符串；\n"
            "2. 时间、地点、类别按骨架原样保留；睡觉就写睡觉，在路上就写在路上；\n"
            "3. 在景区要写具体项目；开车要体现堵车/找停车位；\n"
            "4. 相邻两小时用不同的句子；写生活本身，不写“日程”“AI”“模型”这些词；\n"
            "5. 工作日 07:00-09:00 必须写通勤/出门，09:00-18:00 必须写公司/工位，不能写在家发呆、看剧；\n"
            "6. 只输出 JSON 数组，例如 [\"...\",\"...\"]。"
        ) % (
            ctx.get("day"), (ctx.get("meta") or {}).get("label") or "普通一天",
            _weather_text(ctx.get("weather") or {}),
            day_type_line,
            day_fact,
            _mood_text(ctx.get("mood") or {}),
            (ctx.get("period") or {}).get("phase", ""),
            (ctx.get("health") or {}).get("status", "健康"),
            (ctx.get("yesterday") or {}).get("note") or "正常",
            event.get("note") or "无",
            _scenic_text(scenic),
            "\n".join(lines),
        )
        data = await self.llm_json(llm, umo, system, prompt, timeout=15)
        if isinstance(data, dict):
            data = data.get("activities") or data.get("items") or data.get("schedule")
        if not isinstance(data, list) or len(data) != len(items):
            # JSON 不合法：保留冷数据，空 activity 用中性事实兜底，不抛异常
            for item in items:
                if not str(item.get("activity") or "").strip():
                    item["activity"] = str(SAFE_EVENT.get("event") or "日常发呆")
                    item.setdefault("category", "rest")
            return items
        for item, value in zip(items, data):
            text = ""
            detail = ""
            if isinstance(value, dict):
                text = str(value.get("activity") or "").strip()
                detail = str(value.get("detail") or "").strip()
            else:
                text = str(value or "").strip()
            text = re.sub(r"\s+", " ", text)
            if 2 <= len(text) <= 30:
                item["activity"] = text
            if detail and len(detail) <= 40:
                item["detail"] = detail
        # activity 被改写过，detail 可能已经和它对不上，统一按新活动重新挑。
        self._align_zone_with_activity(items)
        return self._repair_details(items, ctx)
