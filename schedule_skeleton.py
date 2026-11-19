# -*- coding: utf-8 -*-
"""schedule_skeleton：ScheduleEngine 的一部分方法（逐字搬运）。"""

from __future__ import annotations
from typing import Any, TYPE_CHECKING

try:  # 包模式（AstrBot 加载插件）
    from .schedule_plan import (
        Any, CAR_BRANDS, CAR_POOL, CITY_POOL, COMPANY_POOL, DISTRICT_MAP, HOME_STYLES, MOOD_BASE,
        MOOD_KEYS, PROVINCE_CITIES, SWIM_DETAILS, SWIM_PLACES, _city_of, _clock_now,
        _clock_today, _note_swallowed, _place_of, _with_city, amusement_segments,
        business_trip_segments, clamp, date, friend_dinner_segments, homebody_segments,
        indoor_rest_segments, is_long_holiday, md5_int, occupation_kind, outing_segments,
        overtime_segments, parse_hhmm, period_rest_segments, pick_amusement_spot,
        pick_holiday_plan, random, re, scenic_segments, shopping_segments, sick_rest_segments,
        swim_segments, timedelta, travels_for_work, workday_segments,
    )
except ImportError:  # 直接按文件加载时（测试/模拟器）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from schedule_plan import (
            Any, CAR_BRANDS, CAR_POOL, CITY_POOL, COMPANY_POOL, DISTRICT_MAP, HOME_STYLES, MOOD_BASE,
            MOOD_KEYS, PROVINCE_CITIES, SWIM_DETAILS, SWIM_PLACES, _city_of, _clock_now,
            _clock_today, _note_swallowed, _place_of, _with_city, amusement_segments,
            business_trip_segments, clamp, date, friend_dinner_segments, homebody_segments,
            indoor_rest_segments, is_long_holiday, md5_int, occupation_kind, outing_segments,
            overtime_segments, parse_hhmm, period_rest_segments, pick_amusement_spot,
            pick_holiday_plan, random, re, scenic_segments, shopping_segments, sick_rest_segments,
            swim_segments, timedelta, travels_for_work, workday_segments,
        )


# 门面 schedule_engine 会在 ScheduleEngine 定义后把类对象回填到本模块（子模块不能反向
# import 门面，否则循环导入）。这里先给 pyflakes 一个显式静态绑定，运行期由门面覆盖。
ScheduleEngine: Any = None


class ScheduleSkeletonMixin:
    # 方法体逐字搬运自拆分前的 schedule_engine.py。

    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _choose: Any
        _expand: Any
        _fill_activities: Any
        _smooth_moods: Any
        _validate: Any
    def __init__(self, state):
        self.state = state
        self.p = state.p

    # ------------------------------------------------------------------
    # 0. 角色位置/资产：配置优先，其次人格提示词/世界观，最后自动随机
    # ------------------------------------------------------------------
    def resolve_profile(self, world, persona="", seed=""):
        """把“城市/住址/公司/车”解析出来，并在缺失时自动生成一套稳定值。"""
        world = world or {}
        text = "\n".join([
            str(persona or ""),
            str(world.get("city") or ""),
            str(world.get("workplace") or ""),
            str(world.get("daily_life") or ""),
            str(world.get("world_rules") or ""),
            str(world.get("summary") or ""),
        ])
        cached: dict[Any, Any] = {}
        try:
            cached = self.state.db.kv_get("schedule_profile", {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.resolve_profile", _amo_exc)
            cached = {}
        if not isinstance(cached, dict):
            cached = {}
        city = str(self.p.cfg_str("schedule_city", "") or "").strip()
        if not city:
            city = self._extract_city(text)
        if not city:
            city = str(world.get("city") or "").strip()
        if not city or city in ("城市", "未知", "某市"):
            # 配置/人格/世界观都没有城市就留空：日程骨架走「不写具体城市」的分支，
            # 不再从 CITY_POOL 随机编一个；旧档案里的城市也不沿用（否则「清空城市」清不掉）
            city = ""
        if cached.get("city") and cached.get("city") != city:
            cached = {}

        home = str(self.p.cfg_str("schedule_home", "") or "").strip()
        if not home:
            home = self._extract_home(text)
        if not home:
            home = str(cached.get("home") or "").strip()
        if not home:
            home = self._random_home(city, seed)

        workplace = str(self.p.cfg_str("schedule_workplace", "") or "").strip()
        if not workplace:
            workplace = self._extract_work(text)
        if not workplace:
            world_work = str(world.get("workplace") or "").strip()
            if world_work and world_work not in ("公司", "单位", "工作地点", "上班"):
                workplace = world_work
        if not workplace:
            workplace = str(cached.get("workplace") or "").strip()
        if not workplace:
            workplace = self._random_workplace(city, seed)

        # 角色资产：一个字段同时认车/房/奢侈品。车用来开，房用来住，其余当"他送的东西"。
        assets_cfg = str(self.p.cfg_str("schedule_car", "") or "").strip()
        cfg_car, cfg_home, cfg_assets = self._split_assets(assets_cfg)
        car = cfg_car
        if not car:
            car = self._extract_car(text)
        if not car:
            car = str(cached.get("car") or "").strip()
        if not car:
            car = CAR_POOL[md5_int(str(world.get("name") or "") + "|car") % len(CAR_POOL)]
        if cfg_home:
            home = cfg_home

        base = {
            "city": city,
            "home": home,
            "workplace": workplace,
            "car": car,
        }
        try:
            if cached != base:
                self.state.db.kv_set("schedule_profile", base)
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.resolve_profile", _amo_exc)
        profile = dict(base)
        profile["has_gift_car"] = False
        profile["has_gift_home"] = False
        try:
            gift = getattr(self.p, "gift", None)
            if gift is not None:
                car_gift = gift.asset_override("vehicle")
                if car_gift:
                    profile["car"] = car_gift
                    profile["has_gift_car"] = True
                home_gift = gift.asset_override("home")
                if home_gift:
                    profile["has_gift_home"] = True
                    profile["home"] = (home_gift if str(home_gift) not in ("房", "房子", "住宅", "楼", "小区")
                                       else "新家")
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.resolve_profile", _amo_exc)
        return profile

    @staticmethod
    def _split_assets(value):
        """把「角色资产」拆成 车 / 房 / 其他；支持顿号、逗号、分号分隔。"""
        text = str(value or "").strip()
        if not text:
            return "", "", ""
        parts = [p.strip() for p in re.split(r"[、,，;；/|]+", text) if p.strip()]
        car_words = ("奔驰", "宝马", "奥迪", "大众", "丰田", "本田", "保时捷", "特斯拉",
                     "比亚迪", "凯迪拉克", "沃尔沃", "路虎", "捷豹", "雷克萨斯", "蔚来",
                     "理想", "小鹏", "吉利", "五菱", "车")
        home_words = ("房", "别墅", "公寓", "住宅", "大平层", "洋房", "小区", "楼")
        cars, homes, others = [], [], []
        for part in parts:
            if any(word in part for word in home_words):
                homes.append(part)
            elif any(word in part for word in car_words):
                cars.append(part)
            else:
                others.append(part)
        return "、".join(cars), "、".join(homes), "、".join(others)

    @staticmethod
    def _extract_city(text):
        text = str(text or "")
        for city in CITY_POOL:
            if city in text:
                return city
        return ""

    @staticmethod
    def _extract_home(text):
        text = str(text or "")
        patterns = (
            r"(?:住在|家住在|家在|住址在|家在)([^，。；\n]{2,20})",
            r"(?:我家在|我住在)([^，。；\n]{2,20})",
        )
        for pattern in patterns:
            matched = re.search(pattern, text)
            if not matched:
                continue
            value = str(matched.group(1) or "").strip(" 的了是在")
            if value and "公司" not in value and "上班" not in value:
                return value[:20]
        return ""

    @staticmethod
    def _extract_work(text):
        text = str(text or "")
        patterns = (
            r"(?:在|就职于|任职于|工作在)([^，。；：（）「」“”\n]{2,24})(?:上班|工作|任职|实习)",
            r"([^，。；：（）「」“”\n]{2,24})(?:上班|工作)",
        )
        for pattern in patterns:
            matched = re.search(pattern, text)
            if not matched:
                continue
            value = str(matched.group(1) or "").strip(" 的了是在")
            if value and "家" not in value and "住" not in value:
                return value[:24]
        return ""

    @staticmethod
    def _extract_car(text):
        text = str(text or "")
        for brand in CAR_BRANDS:
            if brand in text:
                return brand
        return ""

    @staticmethod
    def _random_home(city, seed):
        rng = random.Random(md5_int(str(city or "") + "|home|" + str(seed or "")))
        if not str(city or "").strip():
            return rng.choice(HOME_STYLES)
        districts = DISTRICT_MAP.get(str(city or "")) or ["市区"]
        return rng.choice(districts) + "·" + rng.choice(HOME_STYLES)

    @staticmethod
    def _random_workplace(city, seed):
        rng = random.Random(md5_int(str(city or "") + "|work|" + str(seed or "")))
        if not str(city or "").strip():
            return rng.choice(COMPANY_POOL)
        return rng.choice(COMPANY_POOL) + "（" + str(city or "市区") + "）"

    # ------------------------------------------------------------------
    # 1. 当天上下文
    # ------------------------------------------------------------------
    def build_context(self, day, meta, weather, world, mood, period, health, persona="", salt=""):
        seed_text = "%s|%s" % (day, salt)
        seed = md5_int(seed_text)
        profile = self.resolve_profile(world, persona, seed=str(seed))
        city = profile["city"]
        home = profile["home"]
        workplace = profile["workplace"]
        car = profile["car"]
        _, _, cfg_assets = self._split_assets(self.p.cfg_str("schedule_car", "") or "")
        yesterday = self.yesterday_summary(day)
        good_weather = self._good_weather(weather)
        event = self._pick_event(day, meta, weather, health, period, seed, world)
        holiday_plan = self._pick_holiday_plan(
            day, meta, weather, health, period, event, good_weather, seed, city,
        )
        if holiday_plan is not None:
            scenic = holiday_plan.get("scenic")
            swim = holiday_plan.get("swim")
        else:
            scenic = self._pick_scenic(day, meta, weather, health, period, event,
                                       good_weather, seed, city)
            swim = None
            if not scenic:
                # 一天只安排一件事：去景区就不去玩水，反之亦然
                swim = self._pick_swim(day, meta, weather, health, period, event,
                                       good_weather, seed, city)

        mood_now = {key: clamp(mood.get(key, MOOD_BASE[key])) for key in MOOD_KEYS}
        return {
            "version": "backbone-v5",
            "day": day,
            "meta": dict(meta or {}),
            "weather": dict(weather or {}),
            "world": dict(world or {}),
            "city": city,
            "home": home,
            "workplace": workplace,
            "car": car,
            "has_gift_car": bool(profile.get("has_gift_car")),
            "has_gift_home": bool(profile.get("has_gift_home")),
            "assets": self._gift_assets(cfg_assets),
            "worn": self._gift_worn(),
            "gift_log": self._gift_log(),
            "mood": mood_now,
            "period": dict(period or {}),
            "health": dict(health or {}),
            "yesterday": yesterday,
            "good_weather": good_weather,
            "event": event,
            "holiday_plan": holiday_plan,
            "scenic": scenic,
            "swim": swim,
            "profile": profile,
            "location": (event.get("where") if event.get("kind") == "business_trip" else city),
            "seed": seed,
            "generated_at": _clock_now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _gift_assets(self, extra=""):
        """他送的大件（车/房/贵重）+ 配置里的角色资产，写进日程上下文。"""
        items = []
        extra = str(extra or "").strip()
        if extra:
            items.append(extra)
        try:
            gift = getattr(self.p, "gift", None)
            if gift is not None:
                text = gift.asset_text()
                if text:
                    items.append(text)
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._gift_assets", _amo_exc)
        return "、".join([item for item in items if item])

    def _gift_worn(self):
        """他送的小件里能穿戴的（手表/围巾/包/口红），写进日程上下文。"""
        try:
            gift = getattr(self.p, "gift", None)
            if gift is not None:
                return gift.worn_text()
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._gift_worn", _amo_exc)
        return ""

    @staticmethod
    def _gift_mention_hours(ctx):
        """每天最多两个整点允许在日记里提他送的礼物。

        按日期固定，避免每小时都在写"捏着挂件/戴着围巾"，把礼物写成了强迫症。
        """
        day = str((ctx or {}).get("day") or "")
        seed = str((ctx or {}).get("seed") or "")
        rnd = random.Random("%s|%s" % (day, seed))
        pool = list(range(9, 22))
        first = rnd.choice(pool)
        second = rnd.choice([h for h in pool if abs(h - first) >= 2] or pool)
        return {first, second}

    @staticmethod
    def _use_gift_today(ctx, label):
        """同一天同一件东西固定选择：六成用他送的，四成用自己的。"""
        day = str(ctx.get("day") or "")
        if not day:
            return True
        return md5_int(day + "|wear|" + label) % 10 < 6

    @staticmethod
    def _wear_phrase(label, gifted):
        if label == "包":
            return "背着包（他送的）" if gifted else "背着包"
        return ("戴了%s（他送的）" if gifted else "戴了%s") % label

    def _apply_gift_effects(self, items, ctx):
        """出门时写穿戴：他送的标「他送的」；自己的只写通用名。"""
        temp = (ctx.get("weather") or {}).get("temp")
        labels = []
        gift = getattr(self.p, "gift", None)
        if gift is not None:
            try:
                labels = gift.wear_labels(temp)
            except Exception as _amo_exc:
                _note_swallowed("schedule_engine.ScheduleEngine._apply_gift_effects", _amo_exc)
                labels = []
        try:
            temp_value = float(temp) if temp is not None else None
        except (TypeError, ValueError):
            # 和风缺字段/旧 kv 里的 "N/A" 会让 float() 抛错，整个主干生成失败、
            # 退回兜底日程（当天活动/地点/穿戴全废）
            temp_value = None
        own = ["围巾"] if temp_value is not None and temp_value <= 20 else []
        if not labels and not own:
            return items
        extras = []
        for label in labels:
            extras.append(self._wear_phrase(label, self._use_gift_today(ctx, label)))
        for label in own:
            if label not in labels:
                extras.append(self._wear_phrase(label, False))
        suffix = "；".join(extras)
        for item in items:
            if item.get("category") not in ("commute", "play"):
                continue
            place = str(item.get("place") or "")
            if not place or place == "家" or place.startswith("家"):
                continue
            detail = str(item.get("detail") or "").strip()
            if suffix and suffix not in detail:
                item["detail"] = (detail + "；" + suffix).strip("；")
        return items

    def _gift_log(self):
        """带日期的礼物记录，让日程日记有迹可查。"""
        try:
            gift = getattr(self.p, "gift", None)
            if gift is not None:
                return gift.gift_log_text()
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._gift_log", _amo_exc)
        return ""

    def yesterday_summary(self, day):
        """读取昨日遗留：熬夜、睡眠不足、生病，今天必须疲惫。"""
        try:
            target = date.fromisoformat(str(day))
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.yesterday_summary", _amo_exc)
            target = _clock_today()
        prev_day = (target - timedelta(days=1)).isoformat()
        items: list[Any] = []
        try:
            items = self.state.schedule.get(prev_day) or self.state.db.get_schedule(prev_day) or []
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.yesterday_summary", _amo_exc)
            items = []
        if not isinstance(items, list):
            items = []
        sleep_hours = 0
        late_night = False
        for item in items:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "")
            activity = str(item.get("activity") or "")
            if category == "sleep":
                sleep_hours += 1
            start = parse_hhmm(item.get("start"), 0)
            if 0 <= start < 4 * 60 and category not in ("sleep", ""):
                late_night = True
            if any(word in activity for word in ("熬夜", "通宵", "失眠", "加班到")):
                late_night = True
        if items and sleep_hours and sleep_hours < 6:
            late_night = True
        note = ""
        if late_night:
            note = "昨晚熬到很晚，今天一整天都容易累、注意力不集中"
        elif sleep_hours and sleep_hours <= 6:
            note = "昨晚睡得不太够，今天起床有点费劲"
        return {
            "day": prev_day,
            "late_night": late_night,
            "sleep_hours": sleep_hours,
            "note": note,
        }

    def _good_weather(self, weather):
        weather = weather or {}
        tag = str(weather.get("tag") or "").lower()
        text = str(weather.get("text") or "")
        if tag in ("rain", "storm", "snow", "thunder", "fog"):
            return False
        if any(word in text for word in ("雨", "雪", "雷", "雾", "沙尘")):
            return False
        try:
            temp = float(weather.get("temp", 20))
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._good_weather", _amo_exc)
            temp = 20.0
        return 5.0 <= temp <= 34.0

    # 各职业的「临时加班」文案
    OVERTIME_NOTES = {
        "tech": ("临时加班", "线上出问题了，被叫去公司处理"),
        "student": ("临时加课", "老师临时加了两节课，晚上才回"),
        "teacher": ("临时代课", "学校临时安排代课/监考，走不开"),
        "med": ("临时排班", "科室临时排了班，得顶上去"),
        "office": ("临时加班", "公司临时有事，被叫过去帮忙"),
    }

    def _pick_event(self, day, meta, weather, health, period, seed, world=None):
        """按日期种子决定突发事件，保证同一天多次读取结果一致。"""
        prob = clamp(self.p.cfg_float("schedule_event_prob", 0.28), 0.0, 0.9)
        rng = random.Random(seed ^ 0x5F3759DF)
        if rng.random() > prob:
            return {"kind": "none"}
        rest = bool((meta or {}).get("rest"))
        if self._is_long_holiday(meta):
            # 长假由 holiday_plan 安排，不再随机塞加班/出差。
            return {"kind": "none"}
        kind = occupation_kind(world)
        if not rest:
            # 只有会跑外勤的职业才出差；其他人是加班/加课/排班
            if travels_for_work(world) and rng.random() < 0.55:
                provinces = sorted(PROVINCE_CITIES.keys())
                province = provinces[rng.randrange(len(provinces))]
                city = PROVINCE_CITIES[province]
                return {
                    "kind": "business_trip",
                    "title": "临时外派",
                    "province": province,
                    "city": city,
                    "where": "%s·%s" % (province, city),
                    "note": "早上接到通知，被派去%s出差，当天回不来" % city,
                    "task": "处理外派的事",
                    "effects": {"疲惫": 10, "焦虑": 10, "开心": -6, "生气": 3},
                    "soothe": False,
                }
            title, note = self.OVERTIME_NOTES.get(kind, self.OVERTIME_NOTES["office"])
            return {
                "kind": "overtime",
                "title": title,
                "note": "早上" + note + "，今天回不去了",
                "task": "处理临时的事",
                "effects": {"疲惫": 12, "焦虑": 8, "生气": 10, "开心": -8},
                "soothe": False,
            }
        # 休息日：临时加班 / 朋友聚餐。
        if rng.random() < 0.55:
            title, note = self.OVERTIME_NOTES.get(kind, self.OVERTIME_NOTES["office"])
            return {
                "kind": "overtime",
                "title": title,
                "note": "上午接到电话，" + note + "，休息日也泡汤了",
                "task": "处理临时的事",
                "effects": {"生气": 16, "暴躁": 8, "焦虑": 12, "开心": -12, "疲惫": 6},
                "soothe": False,
            }
        return {
            "kind": "friend_dinner",
            "title": "朋友聚餐",
            "note": "下午朋友突然发消息，说好久没见了，晚上一起吃饭",
            "effects": {"开心": 14, "依恋": 8, "兴奋": 6, "疲惫": -4},
            "soothe": True,
        }

    def _is_long_holiday(self, meta):
        return is_long_holiday(self.p, meta)

    def _pick_amusement_spot(self, city, seed):
        return pick_amusement_spot(self.p, city, seed)

    def _pick_holiday_plan(self, day, meta, weather, health, period, event,
                           good_weather, seed, city):
        return pick_holiday_plan(
            self, day, meta, weather, health, period, event,
            good_weather, seed, city,
        )

    def _pick_scenic(self, day, meta, weather, health, period, event, good_weather, seed, city):
        if not (meta or {}).get("rest"):
            return None
        if event.get("kind") == "overtime":
            return None
        if (health or {}).get("sick"):
            return None
        if int((period or {}).get("pain") or 0) >= 8:
            return None
        try:
            spots = self.p.scenic_spots() or []
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._pick_scenic", _amo_exc)
            spots = []
        spots = [item for item in spots if isinstance(item, dict) and item.get("name")]
        _city = str(city or "").strip()
        if not _city:
            # 没配城市：不挑任何城市的景点，给一个不写城市的通用公园
            return {
                "name": "人民公园", "city": "", "province": "",
                "area": "", "projects": ["慢慢逛", "找个地方坐会儿", "拍几张照"],
                "duration": "3-4小时", "indoor": False,
            }
        _local = [item for item in spots if _city and _city in str(item.get("city") or "")]
        if _city and not _local and not ScheduleEngine._is_long_holiday(self, meta):
            return None
        if _local:
            spots = _local
        if not spots:
            return {
                "name": "人民公园", "city": _city, "province": "",
                "area": "", "projects": ["慢慢逛", "找个地方坐会儿", "拍几张照"],
                "duration": "3-4小时", "indoor": False,
            }
        # 以前这里写死「贵阳/贵州」：非贵阳城市 guiyang/guizhou 恒为空 →
        # 下雨时 pool=[] 又回退成全部景点（含户外），文案还说「找个室内的地方」。
        # 现在按 spots 自己的 indoor 标记选，和城市无关。
        if not good_weather:
            indoor = [item for item in spots if item.get("indoor")]
            pool = indoor or list(spots)
        elif ScheduleEngine._is_long_holiday(self, meta):
            # 长假去外地：先挑不在本市的
            pool = [item for item in spots if str(city or "") not in str(item.get("city") or "")] or list(spots)
        else:
            pool = list(spots)
        if good_weather:
            outdoor = [item for item in pool if not item.get("indoor")]
            if outdoor:
                pool = outdoor
        longer = [item for item in pool if not str(item.get("duration") or "").startswith("1")]
        if longer:
            pool = longer
        pool = sorted(pool, key=lambda item: str(item.get("name") or ""))
        if not pool:
            pool = sorted(spots, key=lambda item: str(item.get("name") or ""))
        chosen = dict(pool[seed % len(pool)])
        chosen["why"] = "天气不错，想出去走走" if good_weather else "天气一般，找个室内的地方待着"
        chosen["from_city"] = city
        return chosen

    # ------------------------------------------------------------------
    # 2. 主干骨架
    # ------------------------------------------------------------------
    def _pick_swim(self, day, meta, weather, health, period, event, good_weather, seed, city):
        """休息日偶尔去玩水：游泳馆 / 海边 / 水上乐园，随机一个。

        规则（用户 2026-11-12）：
        - 不是每周都去，也不是每个休息日都去；一周最多一次；
        - 冬天（温度低于 ``schedule_swim_min_temp``）或天气不好不去；
        - 这一周已经去过一次，就整周不再安排（旅游时例外）；
        - 三个地方随机挑，去了哪个就记在 kv 里，同一周不重复。
        """
        if not (meta or {}).get("rest"):
            return None
        if (event or {}).get("kind") in ("overtime", "business_trip"):
            return None
        if (health or {}).get("sick") or int((period or {}).get("pain") or 0) >= 8:
            return None
        try:
            temp = float((weather or {}).get("temp") or 0)
        except (TypeError, ValueError):
            temp = 0.0
        min_temp = 22.0
        try:
            min_temp = self.p.cfg_float("schedule_swim_min_temp", 22.0)
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._pick_swim.temp", _amo_exc)
        if not good_weather or temp < float(min_temp):
            return None
        try:
            iso = date.fromisoformat(str(day)).isocalendar()
            weekday = date.fromisoformat(str(day)).weekday()
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._pick_swim.day", _amo_exc)
            return None
        week_key = "swim_week:%04d-%02d" % (iso[0], iso[1])
        used = ""
        try:
            used = str(self.p.db.kv_get(week_key, "") or "")
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._pick_swim.kv", _amo_exc)
            used = ""
        used_kind, _, used_day = used.partition("|")
        traveling = ScheduleEngine._is_long_holiday(self, meta)
        if used_kind and traveling and used_day != str(day):
            # 旅游期间不受「这周已经去过」的限制
            used_kind, used_day = "", ""
        if used_day and used_day != str(day) and not traveling:
            return None
        rng = random.Random("amo-swim:" + week_key)
        preferred = rng.choice([5, 6])
        if used_day != str(day) and not traveling and weekday != preferred:
            return None
        if not used_kind:
            chance = 0.32
            try:
                chance = clamp(self.p.cfg_float("schedule_swim_prob", 0.32), 0.0, 1.0)
            except Exception as _amo_exc:
                _note_swallowed("schedule_engine.ScheduleEngine._pick_swim.prob", _amo_exc)
            if rng.random() > chance:
                return None
            place, activity, detail = SWIM_PLACES[rng.randrange(len(SWIM_PLACES))]
        else:
            match = [item for item in SWIM_PLACES if item[0] == used_kind]
            place, activity, detail = match[0] if match else SWIM_PLACES[0]
        where = ("%s·%s" % (str(city or "").strip(), place)).strip("·")
        try:
            if not used_kind:
                self.p.db.kv_set(week_key, place + "|" + str(day))
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._pick_swim.save", _amo_exc)
        return {
            "place": where,
            "spot": place,
            "activity": activity,
            "detail": detail,
            "zone": "scenic",
            "why": "天气挺热，想去玩水",
        }

    def _swim_segments(self, ctx):
        return swim_segments(ctx, self._seg, self._choose, SWIM_DETAILS)

    def _outing_segments(self, ctx):
        return outing_segments(ctx, self._seg)

    def build_skeleton(self, ctx):
        event_kind = (ctx.get("event") or {}).get("kind")
        meta = ctx.get("meta") or {}
        day_id = str(((ctx or {}).get("day_type") or {}).get("id") or "")
        sick = bool((ctx.get("health") or {}).get("sick"))
        sick_leave = sick and str(ctx.get("sick_action") or "") == "take_sick_leave"
        good_weather = bool(ctx.get("good_weather", True))
        holiday = ctx.get("holiday_plan") or {}
        if sick_leave and not meta.get("rest"):
            segments = self._sick_rest_segments(ctx)
        elif event_kind == "business_trip" and not meta.get("rest"):
            segments = self._business_trip_segments(ctx)
        elif holiday:
            kind = str(holiday.get("kind") or "")
            if kind == "beach":
                segments = self._swim_segments(ctx)
            elif kind == "shopping":
                segments = self._shopping_segments(ctx)
            elif kind == "amusement":
                segments = self._amusement_segments(ctx)
            else:
                segments = self._scenic_segments(ctx)
        elif ctx.get("swim") and not sick and good_weather:
            # 去游泳/海边/水上乐园：整天按玩水安排，衣柜会自动换泳装
            segments = self._swim_segments(ctx)
        elif day_id == "outing":
            # 生病、天气不好就别硬出门，按宅家过
            segments = self._outing_segments(ctx) if (good_weather and not sick) else self._rest_segments(ctx)
        elif day_id == "homebody":
            # 宅家日真的待在家，不再复用“去商场”的室内休息骨架。
            segments = self._homebody_segments(ctx)
        elif meta.get("rest"):
            # 法定节假日/周末优先于加班日，避免节假日小概率还排上班。
            segments = self._rest_segments(ctx)
        elif day_id == "overtime":
            segments = self._workday_segments(ctx, overtime=True)
        else:
            # 工作日的「临时加班」以前只改文案、不改骨架：突发事件写着「今天回不去了」，
            # 日程却还是 18:00 下班回家。这里让骨架真的晚走。
            segments = self._workday_segments(ctx, overtime=(event_kind == "overtime"))
        items = self._expand(segments, ctx)
        items = self._fill_activities(items, ctx)
        items = self._smooth_moods(items, ctx)
        items = self._apply_gift_effects(items, ctx)
        items = self._validate(items, ctx)
        return items

    def _seg(self, start, end, activity, place, zone, category,
             transit="", vehicle="", detail="", effects=None,
             event="", spot="", soothe=False, swim=False, cost_cents=0):
        return {
            "start": start, "end": end, "activity": activity, "place": place,
            "zone": zone, "category": category, "transit": transit,
            "vehicle": vehicle, "detail": detail, "effects": effects or {},
            "event": event, "spot": spot, "soothe": soothe,
            "swim": True if swim else False,
            "cost_cents": max(0, int(cost_cents or 0)),
        }

    def _workday_segments(self, ctx, overtime=False):
        return workday_segments(ctx, self._seg, overtime=overtime)

    def _business_trip_segments(self, ctx):
        return business_trip_segments(ctx, self._seg, _with_city)

    def _rest_segments(self, ctx):
        event_kind = (ctx.get("event") or {}).get("kind")
        health = ctx.get("health") or {}
        period = ctx.get("period") or {}
        if health.get("sick"):
            return self._sick_rest_segments(ctx)
        # 痛经是痛经，不是发烧：以前 pain>=8 也走「吃药、量体温、捂汗」，每个月都被写成病号。
        if int(period.get("pain") or 0) >= 8:
            return self._period_rest_segments(ctx)
        if event_kind == "overtime" and not (ctx.get("meta") or {}).get("rest"):
            return self._overtime_segments(ctx)
        if event_kind == "friend_dinner":
            return self._friend_dinner_segments(ctx)
        if ctx.get("scenic") and ctx.get("good_weather"):
            return self._scenic_segments(ctx)
        return self._indoor_rest_segments(ctx)

    def _scenic_segments(self, ctx):
        return scenic_segments(
            ctx, self._seg, _place_of, _city_of,
            long_holiday=self._is_long_holiday((ctx or {}).get("meta")),
        )

    def _shopping_segments(self, ctx):
        return shopping_segments(ctx, self._seg)

    def _amusement_segments(self, ctx):
        return amusement_segments(ctx, self._seg)

    def _homebody_segments(self, ctx):
        return homebody_segments(ctx, self._seg)

    def _indoor_rest_segments(self, ctx):
        return indoor_rest_segments(ctx, self._seg, _place_of)

    def _sick_rest_segments(self, ctx):
        return sick_rest_segments(ctx, self._seg)

    def _period_rest_segments(self, ctx):
        return period_rest_segments(ctx, self._seg)

    def _overtime_segments(self, ctx):
        return overtime_segments(ctx, self._seg)

    def _friend_dinner_segments(self, ctx):
        return friend_dinner_segments(ctx, self._seg, _place_of)
