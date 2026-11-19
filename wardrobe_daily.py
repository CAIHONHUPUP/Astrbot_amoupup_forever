# -*- coding: utf-8 -*-
"""wardrobe_daily：Wardrobe 的一部分方法（逐字搬运）。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

try:  # 包模式（AstrBot 加载插件）
    from .wardrobe_data import (
        COOL_BOTTOM_WORDS, HOME_PLACE_WORDS, HOTEL_PLACE_WORDS, OUTSIDE_PLACE_WORDS,
        SLEEP_HOUR_FROM, SLEEP_HOUR_TO, SUMMER_BAD_WORDS, SUMMER_DRESS_WORDS,
        SWIM_NEGATIVE_WORDS, SWIM_STRONG_WORDS, SWIM_WORDS, WARM_WORDS, WORK_SCENE_WORDS,
        _amo_clock, _clock_now, _new_id, _note_swallowed, date, is_dress, is_home_socks, is_long_socks,
        is_short_socks, is_tights, logger, random, re, timedelta, today_str,
    )
except ImportError:  # 直接按文件加载时（测试/模拟器）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from wardrobe_data import (
            COOL_BOTTOM_WORDS, HOME_PLACE_WORDS, HOTEL_PLACE_WORDS, OUTSIDE_PLACE_WORDS,
            SLEEP_HOUR_FROM, SLEEP_HOUR_TO, SUMMER_BAD_WORDS, SUMMER_DRESS_WORDS,
            SWIM_NEGATIVE_WORDS, SWIM_STRONG_WORDS, SWIM_WORDS, WARM_WORDS, WORK_SCENE_WORDS,
            _amo_clock, _clock_now, _new_id, _note_swallowed, date, is_dress, is_home_socks, is_long_socks,
            is_short_socks, is_tights, logger, random, re, timedelta, today_str,
        )


class WardrobeDailyMixin:
    # 方法体逐字搬运自拆分前的 wardrobe.py。

    if TYPE_CHECKING:  # 宿主（插件主类/其它 mixin）提供的成员：严格口径用声明而不是 ignore
        _ensure_sleep: Any
        _ensure_swim: Any
        _today_cache: Any

    # ---------- 查询 ----------
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _ensure_home: Any
        _ensure_hotel: Any
        _load: Any
        _lock: Any
        _save: Any
        _seed: Any
        _sync_sidecar: Any
        _trim_daily: Any
        plugin: Any
    def _items_map(self, data):
        return {item["id"]: item for item in data.get("items", [])}

    def _outfit_by_id(self, data, outfit_id):
        for outfit in data.get("outfits", []):
            if outfit.get("id") == outfit_id:
                return outfit
        return None

    def _describe(self, data, outfit):
        items = self._items_map(data)
        coat = items.get(outfit.get("coat")) or {}
        top = items.get(outfit.get("top")) or {}
        bottom = items.get(outfit.get("bottom")) or {}
        skirt = items.get(outfit.get("skirt")) or {}
        shoes = items.get(outfit.get("shoes")) or {}
        names = [str(part.get("name") or "") for part in (coat, top, skirt, bottom, shoes)]
        names = [name for name in names if name]
        return {
            "outfit": outfit.get("id", ""),
            "coat": str(coat.get("name") or ""),
            "top": str(top.get("name") or ""),
            "bottom": str(bottom.get("name") or ""),
            "skirt": str(skirt.get("name") or ""),
            "shoes": str(shoes.get("name") or ""),
            "text": "，".join(names),
            "items": [coat.get("id", ""), top.get("id", ""), bottom.get("id", ""),
                      skirt.get("id", ""), shoes.get("id", "")],
            "swim": bool(outfit.get("swim")),
        }

    def _weather_temp(self):
        try:
            return float((getattr(self.plugin.state, "weather", {}) or {}).get("temp") or 0)
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._weather_temp", _amo_exc)
            return 0.0

    def _clean_enough(self, items, outfit):
        """抽衣时排除 dirty / washing 的单品（洗衣引擎状态）。"""
        for key in ("coat", "top", "bottom", "skirt", "shoes"):
            item = items.get(outfit.get(key) or "") or {}
            if str(item.get("clean_status") or "clean") in ("dirty", "washing"):
                return False
        return True

    def _mark_worn(self, data, outfit):
        """穿过即置 dirty；睡衣/家居服/泳装不参与。"""
        try:
            by_id = self._items_map(data)
            for key in ("coat", "top", "bottom", "skirt", "shoes"):
                item = by_id.get(outfit.get(key) or "")
                if not item or item.get("sleep") or item.get("swim") or item.get("homewear"):
                    continue
                item["clean_status"] = "dirty"
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._mark_worn", _amo_exc)

    def _candidates(self, data):
        items = self._items_map(data)

        def usable(outfit):
            if outfit.get("sleep") or outfit.get("swim") or not outfit.get("shoes"):
                return False
            skirt = items.get(outfit.get("skirt")) or {}
            if is_dress(skirt.get("name")):
                return True
            if outfit.get("skirt"):
                return bool(outfit.get("top"))
            return bool(outfit.get("top")) and bool(outfit.get("bottom"))

        outfits = [outfit for outfit in data.get("outfits", []) if usable(outfit)]
        # 洗衣系统（2026-11-15）：正在洗 / 已经脏的单品不参与抽衣；全脏时兜底不空手
        clean = [outfit for outfit in outfits if self._clean_enough(items, outfit)]
        if clean:
            outfits = clean
        keys = ("coat", "top", "bottom", "skirt", "shoes")

        def names_of(outfit):
            return [str(items.get(outfit.get(key), {}).get("name") or "")
                    for key in keys]

        def has(outfit, words):
            return any(word in name for name in names_of(outfit) for word in words)

        # 上班/通勤/见客户：不穿泳衣、吊带、热裤这类
        scene = ""
        try:
            activity = self.plugin.state.current_activity() or {}
            scene = str(activity.get("place") or "") + " " + str(activity.get("activity") or "")
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._candidates", _amo_exc)
            scene = ""
        if any(word in scene for word in WORK_SCENE_WORDS):
            work_safe = [outfit for outfit in outfits if not has(outfit, SWIM_WORDS)]
            if work_safe:
                outfits = work_safe
        temp = self._weather_temp()
        if temp:
            if temp >= 28:
                cool = [outfit for outfit in outfits
                        if not has(outfit, SUMMER_BAD_WORDS)]
                if cool:
                    return cool
            elif temp <= 15:
                warm = [outfit for outfit in outfits
                        if not has(outfit, SUMMER_DRESS_WORDS)]
                if temp <= 5:
                    down = [outfit for outfit in warm if has(outfit, ("羽绒服",))]
                    if down:
                        return down
                if warm:
                    return warm
        return outfits

    def _is_warm(self, items, outfit):
        names = [str(items.get(outfit.get(key), {}).get("name") or "")
                 for key in ("coat", "top", "bottom", "skirt", "shoes")]
        return any(word in name for name in names for word in WARM_WORDS + COOL_BOTTOM_WORDS)

    def _repair_outfit(self, data, outfit):
        """整套里只有部分单品脏/在洗：只换那几件，别把整套扔掉（2026-11-19）。"""
        items = self._items_map(data)
        out = dict(outfit)
        changed = False
        for slot in ("coat", "top", "bottom", "skirt", "shoes"):
            item = items.get(out.get(slot) or "") or {}
            if str(item.get("clean_status") or "clean") not in ("dirty", "washing"):
                continue
            pool = [row for row in data.get("items", [])
                    if row.get("category") == slot
                    and str(row.get("clean_status") or "clean") == "clean"
                    and bool(row.get("sleep")) == bool(outfit.get("sleep"))
                    and bool(row.get("hotel")) == bool(outfit.get("hotel"))
                    and bool(row.get("swim")) == bool(outfit.get("swim"))]
            if not pool:
                return None
            picker = random.Random("amo-repair:%s:%s" % (outfit.get("id", ""), slot))
            out[slot] = picker.choice(pool).get("id") or ""
            changed = True
        return out if changed else outfit

    def _adjust_coat(self, data, result):
        """按当前温度加/减外套：热了脱、冷了加（只动外套，不重锁整套）。"""
        try:
            temp = float((getattr(self.plugin.state, "weather", {}) or {}).get("temp") or 0)
        except Exception:
            temp = 0.0
        if not temp:
            return result
        result = dict(result)
        coat = str(result.get("coat") or "")
        if coat and temp >= 26:
            result["coat"] = ""
            text = str(result.get("text") or "")
            result["text"] = text.replace(coat + "，", "").replace("，" + coat, "").replace(coat, "")
            return result
        if not coat and temp <= 10:
            pool = [row for row in data.get("items", [])
                    if row.get("category") == "coat"
                    and str(row.get("clean_status") or "clean") == "clean"
                    and not row.get("sleep") and not row.get("swim") and not row.get("hotel")]
            if pool:
                picker = random.Random("amo-coat:%s" % result.get("day", ""))
                chosen = picker.choice(pool)
                result["coat"] = str(chosen.get("name") or "")
                text = str(result.get("text") or "")
                result["text"] = (result["coat"] + ("，" + text if text else "")) if result["coat"] else text
        return result

    def _socks_fit(self, result, sock_name):
        """出门时袜子是否合当前下装：长裤不配长袜；长裙不配长筒/过膝/渔网。"""
        name = str(sock_name or "")
        if not name:
            return True
        bottom = str(result.get("bottom") or "")
        skirt = str(result.get("skirt") or "")
        if skirt and is_dress(skirt):
            return not (is_long_socks(name) and not is_tights(name))
        if bottom and "短" not in bottom:
            return not is_long_socks(name)
        return True

    def _pick(self, data, day, exclude=""):
        outfits = self._candidates(data)
        if exclude:
            outfits = [outfit for outfit in outfits if outfit.get("id") != exclude] or outfits
        if not outfits:
            # 衣服被删光了：补回种子（新 id），不然今日穿搭会指向不存在的单品
            seed = self._seed()
            data.setdefault("items", []).extend(seed["items"])
            data.setdefault("outfits", []).extend(seed["outfits"])
            outfits = seed["outfits"]
        chooser = random.Random("amo-wardrobe:" + day)
        chosen = chooser.choice(outfits)
        repaired = self._repair_outfit(data, chosen)
        return repaired or chosen

    # ---------- 每日穿搭 ----------

    def _auto_socks(self, data, day, outfit):
        """白天没选袜子时按规则自动挑一双（用户 2026-11-19：没生成就按规则配一双）。

        规则：长裤只能短袜/船袜；长裙只能连裤袜/丝袜/短袜；短裤短裙随便；
        ≥28℃ 只能短袜；≤10℃ 优先连裤袜/厚袜；优先干净袜子。
        """
        try:
            items = [item for item in data.get("items", [])
                     if item.get("category") == "socks"]
        except Exception:
            return None
        if not items:
            return None
        temp = self._weather_temp()
        by_id = self._items_map(data)
        bottom = by_id.get(outfit.get("bottom") or "") or {}
        skirt = by_id.get(outfit.get("skirt") or "") or {}
        bottom_name = str(bottom.get("name") or "")
        skirt_name = str(skirt.get("name") or "")
        candidates = []
        for item in items:
            name = str(item.get("name") or "")
            if temp >= 28 and is_long_socks(name):
                continue
            if skirt_name and is_dress(skirt_name):
                if is_long_socks(name) and not is_tights(name):
                    continue
            elif bottom_name and "短" not in bottom_name:
                if is_long_socks(name):
                    continue
            candidates.append(item)
        if not candidates:
            candidates = [item for item in items if not is_long_socks(item.get("name"))] or items
        if temp <= 10:
            tights = [item for item in candidates if is_tights(item.get("name"))]
            if tights:
                candidates = tights
        clean = [item for item in candidates
                 if str(item.get("clean_status") or "clean") == "clean"]
        if clean:
            candidates = clean
        chooser = random.Random("amo-socks:" + str(day))
        return chooser.choice(candidates)


    def today(self, day="", data=None):
        day = str(day or today_str())
        with self._lock:
            if self._today_cache and self._today_cache.get("day") == day:
                return dict(self._today_cache)
            data = data if isinstance(data, dict) else self._load()
            entry = (data.get("daily") or {}).get(day) or {}
            outfit = self._outfit_by_id(data, entry.get("outfit"))
            if outfit is None:
                outfit = self._pick(data, day)
                self._mark_worn(data, outfit)
                data.setdefault("daily", {})[day] = {
                    "outfit": outfit.get("id", ""),
                    "ts": _amo_clock.now_ts(),
                }
                self._trim_daily(data)
                self._save(data)
            result = self._describe(data, outfit)
            socks_item = self._items_map(data).get(str(entry.get("socks") or "")) or {}
            if not socks_item:
                auto = self._auto_socks(data, day, outfit)
                if auto is not None:
                    socks_item = auto
                    data.setdefault("daily", {}).setdefault(day, {})["socks"] = str(auto.get("id") or "")
                    data["daily"][day]["socks_auto"] = True
                    entry = data["daily"][day]
                    self._save(data)
            socks_name = str(socks_item.get("name") or "")
            if socks_name:
                result["socks"] = socks_name
                result["socks_id"] = str(socks_item.get("id") or "")
                result["text"] = ((result.get("text") + "，") if result.get("text") else "") + socks_name
                if not entry.get("socks_worn"):
                    socks_item["clean_status"] = "dirty"
                    data.setdefault("daily", {}).setdefault(day, {})["socks_worn"] = True
                    self._save(data)
            result["day"] = day
            self._today_cache = result
        return dict(result)

    def _sleep_candidates(self, data, hotel=False):
        """选睡衣时也看天气：夏天不选厚的，冬天优先法兰绒/珊瑚绒/加厚。"""
        outfits = self._sleep_outfits(data, hotel)
        temp = self._weather_temp()
        if not temp:
            return outfits
        thick_words = ("法兰绒", "珊瑚绒", "加厚", "夹棉", "毛绒", "羊毛", "针织")
        if temp >= 28:
            cool = [outfit for outfit in outfits
                    if not any(word in str((self._items_map(data).get(outfit.get("top")) or {}).get("name") or "")
                               for word in thick_words)]
            return cool or outfits
        if temp <= 15:
            warm = [outfit for outfit in outfits
                    if any(word in "".join(str((self._items_map(data).get(outfit.get(key)) or {}).get("name") or "")
                                           for key in ("top", "bottom", "skirt"))
                           for word in thick_words)]
            return warm or outfits
        return outfits

    def _sleep_outfits(self, data, hotel=False):
        """家里只取家里睡衣、酒店只取酒店睡衣，两边绝不混搭。"""
        return [outfit for outfit in data.get("outfits", [])
                if outfit.get("sleep") and bool(outfit.get("hotel")) == bool(hotel)]

    def _night_entry(self, data, day):
        entry = (data.get("daily") or {}).get(day) or {}
        return self._outfit_by_id(data, str(entry.get("night") or ""))

    def _describe_sleep(self, data, outfit, day):
        result = self._describe(data, outfit)
        result["day"] = day
        result["sleep"] = True
        result["hotel"] = bool(outfit.get("hotel"))
        return result

    # ---------- 泳装（游泳馆 / 海边 / 水上乐园） ----------

    def _swim_outfits(self, data):
        """整套泳装：上衣和泳裤都得在、而且属于同一套（不留单件/混搭）。"""
        items = self._items_map(data)
        result = []
        for outfit in data.get("outfits", []):
            if not outfit.get("swim"):
                continue
            top = items.get(outfit.get("top")) or {}
            bottom = items.get(outfit.get("bottom")) or {}
            if not top or not bottom:
                continue
            if str(top.get("set") or "") != str(bottom.get("set") or ""):
                continue
            result.append(outfit)
        return result

    def swim_sets(self, data=None):
        """给面板用：整套泳装列表（一套一行，不能拆开挑）。"""
        data = data if isinstance(data, dict) else self._load()
        items = self._items_map(data)
        sets = []
        for outfit in self._swim_outfits(data):
            top = items.get(outfit.get("top")) or {}
            bottom = items.get(outfit.get("bottom")) or {}
            sets.append({
                "id": outfit.get("id") or "",
                "set": outfit.get("set") or top.get("set") or "",
                "top": str(top.get("name") or ""),
                "bottom": str(bottom.get("name") or ""),
                "top_id": str(top.get("id") or ""),
                "bottom_id": str(bottom.get("id") or ""),
            })
        sets.sort(key=lambda entry: entry["set"])
        return sets

    def is_swim_activity(self, activity):
        """严格判定：真正玩水才算泳装场景；海边散步/拍照/看海/买泳装不算。"""
        data = activity if isinstance(activity, dict) else {}
        if data.get("swim") is True:
            return True
        spot = str(data.get("spot") or "").strip()
        if spot in ("游泳馆", "泳池", "水上乐园", "水世界"):
            return True
        text = " ".join(str(data.get(key) or "") for key in
                        ("place", "activity", "detail", "spot"))
        if any(word in text for word in SWIM_STRONG_WORDS):
            return True
        if any(word in text for word in SWIM_NEGATIVE_WORDS):
            return False
        # “海边/沙滩”本身不够，必须有玩水动作；没有就当普通散步/看海。
        return False

    def _at_swim(self, activity=None):
        """现在是不是在游泳/海边/水上乐园：是的话自动换泳装。

        activity 可由调用方传（同一次快照只读一次 current_activity）。
        """
        if activity is None:
            try:
                activity = self.plugin.state.current_activity() or {}
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe._at_swim", _amo_exc)
                return False
        return self.is_swim_activity(activity)

    def at_swim(self):
        return self._at_swim()

    def _swim_window(self, day):
        """今天的泳装时段：返回 {state: now/later/none, start, end}。"""
        try:
            items = self.plugin.state.schedule.get(str(day)) or []
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._swim_window", _amo_exc)
            items = []
        try:
            now = _clock_now()
            now_min = now.hour * 60 + now.minute
        except Exception:
            now_min = 0

        def minutes(text):
            try:
                hour, minute = str(text or "").split(":")[:2]
                return int(hour) * 60 + int(minute)
            except Exception:
                return None

        later = None
        for item in items:
            if not isinstance(item, dict):
                continue
            if not self.is_swim_activity(item):
                continue
            start = minutes(item.get("start"))
            end = minutes(item.get("end"))
            if start is None:
                continue
            if end is None or end <= start:
                end = 24 * 60
            if start <= now_min < end:
                return {"state": "now", "start": start, "end": end}
            if now_min < start and later is None:
                later = {"state": "later", "start": start, "end": end}
        return later or {"state": "none", "start": 0, "end": 0}

    def _describe_swim(self, data, outfit, day):
        result = self._describe(data, outfit)
        result["day"] = day
        result["swim"] = True
        result["set"] = str(outfit.get("set") or "")
        return result

    def swim_outfit(self, day="", data=None):
        """今天去游泳/海边时穿的那套泳装：按日期锁一套，面板可改。"""
        day = str(day or today_str())
        with self._lock:
            data = data if isinstance(data, dict) else self._load()
            entry = (data.get("daily") or {}).get(day) or {}
            outfit = self._outfit_by_id(data, str(entry.get("swim") or ""))
            if outfit is None or not outfit.get("swim"):
                pool = self._swim_outfits(data)
                if not pool:
                    self._ensure_swim(data)
                    self._save(data)
                    pool = self._swim_outfits(data)
                if not pool:
                    return {"pending": True, "swim": True, "day": day, "text": ""}
                chosen = random.Random("amo-wardrobe-swim:" + day).choice(pool)
                entry = data.setdefault("daily", {}).setdefault(day, {})
                entry["swim"] = chosen["id"]
                entry.setdefault("ts", _amo_clock.now_ts())
                self._trim_daily(data)
                self._save(data)
                outfit = chosen
            return self._describe_swim(data, outfit, day)

    def set_swim(self, day="", top="", bottom=""):
        """面板「泳衣」保存：只选上衣（泳衣）+ 裤子（泳裤），没有鞋。"""
        day = str(day or today_str())
        with self._lock:
            data = self._load()
            items = self._items_map(data)
            top_item = items.get(str(top or ""))
            bottom_item = items.get(str(bottom or ""))
            if not (top_item and top_item.get("category") == "top" and top_item.get("swim")):
                return {"ok": False, "msg": "先选一件泳衣上衣"}
            if not (bottom_item and bottom_item.get("category") == "bottom"
                    and bottom_item.get("swim")):
                return {"ok": False, "msg": "先选一条泳裤"}
            # 泳装只能整套穿：上衣和泳裤必须是同一套，不允许混搭
            if str(top_item.get("set") or "") != str(bottom_item.get("set") or "") \
                    or not str(top_item.get("set") or ""):
                return {"ok": False, "msg": "泳装要穿整套：上衣和泳裤要选同一套"}
            outfit = None
            for entry in data.get("outfits", []):
                if (entry.get("swim") and entry.get("top") == top_item["id"]
                        and entry.get("bottom") == bottom_item["id"]):
                    outfit = entry
                    break
            if outfit is None:
                outfit = {
                    "id": _new_id("o-"),
                    "top": top_item["id"],
                    "bottom": bottom_item["id"],
                    "shoes": "",
                    "source": "manual",
                    "swim": True,
                    "set": str(top_item.get("set") or ""),
                }
                data.setdefault("outfits", []).append(outfit)
            entry = data.setdefault("daily", {}).setdefault(day, {})
            entry["swim"] = outfit["id"]
            entry.setdefault("ts", _amo_clock.now_ts())
            self._trim_daily(data)
            self._save(data)
            self._today_cache = None
        result = self.swim_outfit(day)
        result["ok"] = True
        return result

    def night(self, day="", hour=None, hotel=None, data=None):
        """今晚的睡衣：白天也直接生成并显示；住酒店时用酒店睡衣，否则用家里睡衣。"""
        day = str(day or today_str())
        if hotel is None:
            hotel = self._at_hotel()
        hotel = bool(hotel)
        with self._lock:
            data = data if isinstance(data, dict) else self._load()
            entry = (data.get("daily") or {}).get(day) or {}
            outfit = self._outfit_by_id(data, str(entry.get("night") or ""))
            if (outfit is not None and outfit.get("sleep")
                    and bool(outfit.get("hotel")) == hotel):
                return self._describe_sleep(data, outfit, day)
        return self.set_night(day, hotel=hotel, data=data)

    def set_night(self, day="", outfit_id="", reroll=False, top="", bottom="", shoes="", hotel=None,
                  data=None):
        """锁定今晚的睡衣：家里用家里睡衣，住酒店用酒店睡衣，可换一套/指定一套。"""
        day = str(day or today_str())
        if hotel is None:
            hotel = self._at_hotel()
        hotel = bool(hotel)
        with self._lock:
            data = data if isinstance(data, dict) else self._load()
            outfits = self._sleep_candidates(data, hotel)
            if not outfits:
                if hotel:
                    self._ensure_hotel(data)
                else:
                    self._ensure_sleep(data)
                outfits = self._sleep_candidates(data, hotel)
            items = self._items_map(data)
            current = self._night_entry(data, day)
            if current is None or bool(current.get("hotel")) != hotel:
                current = None
            chosen = None
            if not reroll and top and bottom:
                top_item = items.get(str(top))
                bottom_item = items.get(str(bottom))
                shoes_item = items.get(str(shoes or ""))
                if (top_item and top_item.get("category") == "top" and top_item.get("sleep")
                        and bool(top_item.get("hotel")) == hotel
                        and bottom_item and bottom_item.get("category") == "bottom" and bottom_item.get("sleep")
                        and bool(bottom_item.get("hotel")) == hotel):
                    if not (shoes_item and shoes_item.get("category") == "shoes" and shoes_item.get("sleep")
                            and bool(shoes_item.get("hotel")) == hotel):
                        pool = [item for item in data["items"]
                                if item.get("category") == "shoes" and item.get("sleep")
                                and bool(item.get("hotel")) == hotel]
                        shoes_item = random.Random("amo-wardrobe-night-shoes:" + day).choice(pool) if pool else None
                    if shoes_item is not None:
                        for outfit in outfits:
                            if (outfit.get("top") == top_item["id"]
                                    and outfit.get("bottom") == bottom_item["id"]
                                    and outfit.get("shoes") == shoes_item["id"]):
                                chosen = outfit
                                break
                        if chosen is None:
                            chosen = {
                                "id": _new_id("o-"),
                                "top": top_item["id"],
                                "bottom": bottom_item["id"],
                                "shoes": shoes_item["id"],
                                "source": "manual",
                                "sleep": True,
                                "hotel": True if hotel else False,
                            }
                            data["outfits"].append(chosen)
            if chosen is None and not reroll and outfit_id:
                candidate = self._outfit_by_id(data, outfit_id)
                if (candidate is not None and candidate.get("sleep")
                        and bool(candidate.get("hotel")) == hotel):
                    chosen = candidate
            if chosen is None and current is not None and not reroll:
                chosen = current
            if chosen is None:
                pool = [outfit for outfit in outfits
                        if current is None or outfit.get("id") != current.get("id")] or outfits
                chosen = random.Random("amo-wardrobe-night:" + day).choice(pool)
            entry = data.setdefault("daily", {}).setdefault(day, {})
            entry["night"] = chosen["id"]
            entry["night_kind"] = "travel" if hotel else "home"
            entry.setdefault("ts", _amo_clock.now_ts())
            self._trim_daily(data)
            self._save(data)
        return self._describe_sleep(data, chosen, day)

    def current(self, day="", at_home=None, hour=None, at_hotel=None, data=None,
                activity=None, swim=None):
        """她现在实际穿的那套：白天穿的；晚上 20 点回到家/酒店自动换睡衣。

        activity / at_home / at_hotel / swim 可由一次快照算好传进来：默认只读一次
        current_activity（旧实现这里会读 3 次）。
        """
        day = str(day or today_str())
        if hour is None:
            try:
                hour = int(_clock_now().hour)
            except Exception:
                hour = 20
        if activity is None:
            try:
                activity = self.plugin.state.current_activity() or {}
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe.current.activity", _amo_exc)
                activity = {}
        if at_home is None:
            at_home = self._at_home(activity)
        if at_hotel is None:
            at_hotel = self._at_hotel(activity)
        if swim is None:
            swim = self._at_swim(activity)
        home_early = False
        try:
            home_early = bool(self.plugin.db.kv_get("home_early:" + day, False))
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe.current", _amo_exc)
            home_early = False
        # 在游泳馆/海边/水上乐园：不管几点、在不在家，都换泳装
        if swim:
            result = self.swim_outfit(day, data=data)
            if result.get("pending"):
                result = self.today(day, data=data)
            self._sync_sidecar(result)
            return dict(result)
        # 0–8 点：还穿着昨晚的睡衣，但不生成/锁定“今晚”的睡衣。
        if hour < SLEEP_HOUR_TO:
            try:
                previous = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
            except Exception:
                previous = ""
            if previous:
                data = data if isinstance(data, dict) else self._load()
                outfit = self._night_entry(data, previous)
                if outfit is not None:
                    result = self._describe_sleep(data, outfit, previous)
                else:
                    # 昨晚没锁睡衣：沿用昨天的白天穿搭，别在凌晨生成“今晚”的睡衣。
                    result = self.today(previous, data=data)
                self._sync_sidecar(result)
                return dict(result)
        night = hour >= SLEEP_HOUR_FROM or hour < SLEEP_HOUR_TO
        if home_early and at_home is True:
            night = True
        if night and (at_hotel or at_home is not False):
            result = self.night(day, hour=hour, hotel=at_hotel, data=data)
            if result.get("pending"):
                result = self.today(day, data=data)
        elif at_home is True:
            result = self.home_outfit(day, hour=hour, data=data)
            try:
                home_data = data if isinstance(data, dict) else self._load()
                entry = (home_data.get("daily") or {}).get(day) or {}
                sock = self._items_map(home_data).get(str(entry.get("socks") or "")) or {}
                if sock and is_home_socks(sock.get("name")):
                    result = dict(result)
                    result["socks"] = str(sock.get("name") or "")
                    result["socks_id"] = str(sock.get("id") or "")
                    base_text = str(result.get("text") or "")
                    result["text"] = (base_text + "，" if base_text else "") + result["socks"]
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe.current.home_socks", _amo_exc)
        else:
            result = self.today(day, data=data)
            outfit_data = data if isinstance(data, dict) else self._load()
            try:
                entry = (outfit_data.get("daily") or {}).get(day) or {}
                outfit = self._outfit_by_id(outfit_data, entry.get("outfit"))
                sock = str(result.get("socks") or "")
                if outfit is not None and sock and not self._socks_fit(result, sock):
                    fixed = self._auto_socks(outfit_data, day, outfit)
                    if fixed is not None:
                        new_name = str(fixed.get("name") or "")
                        if new_name and new_name != sock:
                            result = dict(result)
                            text = str(result.get("text") or "")
                            result["text"] = text.replace(sock, new_name)
                            result["socks"] = new_name
                            result["socks_id"] = str(fixed.get("id") or "")
                result = self._adjust_coat(outfit_data, result)
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe.current.adjust", _amo_exc)
        self._sync_sidecar(result)
        return dict(result)

    def invalidate(self, day="", reason=""):
        """日程变更后清掉穿搭侧缓存；下次读取按新日程实时算状态。"""
        self._today_cache = None
        logger.info(
            "[OUTFIT_DEBUG] time=%s schedule=changed day_slot=- night_slot=- "
            "swim_slot=- reason=%s",
            str(day or today_str()), str(reason or "schedule_changed"),
        )

    def tick(self):
        """后台 30 秒 tick：8:00 生成白天穿搭，20:00 在家生成晚上穿搭。

        不依赖面板是否打开；20:00 不在家（医院/超市/外面）不生成晚上穿搭，
        等回家后的下一次 tick 再生成/复用同一套。
        """
        day = today_str()
        try:
            hour = int(_clock_now().hour)
        except Exception:
            hour = 20
        if hour < 8:
            return
        try:
            self.today(day)
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe.tick.day", _amo_exc)
        if hour < SLEEP_HOUR_FROM:
            return
        if self._at_swim():
            # 泳装优先：泳装场景不生成/不切换睡衣
            return
        at_hotel = self._at_hotel()
        at_home = self._at_home()
        if at_hotel or at_home is not False:
            try:
                self.night(day, hour=hour, hotel=at_hotel)
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe.tick.night", _amo_exc)

    def _at_home(self, activity=None):
        """看当前日程地点判断在不在家：True 在家 / False 在外面 / None 判断不了。"""
        if activity is None:
            try:
                activity = self.plugin.state.current_activity() or {}
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe._at_home", _amo_exc)
                return None
        place = str(activity.get("place") or "")
        if not place:
            return None
        if any(word in place for word in HOME_PLACE_WORDS):
            return True
        if any(word in place for word in OUTSIDE_PLACE_WORDS):
            return False
        return None

    def at_home(self):
        """给聊天换装判断用：True 在家 / False 在外面 / None 不知道。"""
        return self._at_home()

    def home_outfit(self, day="", hour=None, data=None):
        """在家、还没到睡点的居家便服：从居家单品里按天挑一套（不进白天搭配）。"""
        day = str(day or today_str())
        try:
            hour = int(hour if hour is not None else _clock_now().hour)
        except Exception:
            hour = 14
        pick = 0
        try:
            pick = int(self.plugin.db.kv_get("home_pick:" + day, 0) or 0)
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe.home_outfit.pick", _amo_exc)
            pick = 0

        def _pool(data, category):
            return [item for item in (data.get("items") or [])
                    if item.get("homewear") and item.get("category") == category]

        with self._lock:
            data = data if isinstance(data, dict) else self._load()
            tops = _pool(data, "top")
            bottoms = _pool(data, "bottom")
            shoes = _pool(data, "shoes")
            if not tops or not bottoms:
                self._ensure_home(data)
                self._save(data)
                tops = _pool(data, "top")
                bottoms = _pool(data, "bottom")
                shoes = _pool(data, "shoes")
        rng = random.Random("amo-home:%s:%d" % (day, pick))
        top = rng.choice(tops) if tops else None
        bottom = rng.choice(bottoms) if bottoms else None
        shoe = rng.choice(shoes) if shoes else None
        top_name = str((top or {}).get("name") or "")
        bottom_name = str((bottom or {}).get("name") or "")
        shoe_name = str((shoe or {}).get("name") or "")
        wear = "、".join([name for name in (top_name, bottom_name) if name])
        text = "在家穿的居家便服" + ("：" + wear if wear else "")
        if shoe_name:
            text += "，" + shoe_name
        transition = 18 <= hour < int(SLEEP_HOUR_FROM)
        if transition:
            text += "（外套和外面的鞋已经脱了）"
        return {
            "day": day,
            "outfit": "home:%s:%s:%s" % ((top or {}).get("id", ""), (bottom or {}).get("id", ""),
                                         (shoe or {}).get("id", "")),
            "coat": "",
            "top": top_name,
            "bottom": bottom_name,
            "skirt": "",
            "shoes": shoe_name,
            "text": text,
            "homewear": True,
            "sleep": False,
            "hotel": False,
            "transition": transition,
        }

    def home_outfit_guard(self, text):
        """她在家时，把回复里“外面的衣服”改成居家服，避免自己拆自己台。"""
        text = str(text or "")
        try:
            if self.at_home() is not True:
                return text
        except Exception:
            return text
        for word, repl in (("牛仔裤", "家居裤"), ("帆布鞋", "拖鞋"), ("运动鞋", "拖鞋"),
                           ("高跟鞋", "拖鞋"), ("靴子", "拖鞋"), ("小皮鞋", "拖鞋")):
            text = text.replace(word, repl)
        # 外套：只有「脱掉/换下/收好」这类动作保留，其余改成居家服
        text = re.sub(r"(?<!脱掉)(?<!换下)(?<!收好)外套", "居家服", text)
        return text

    def _at_hotel(self, activity=None):
        """现在是不是在酒店/民宿（出门旅游住店）。"""
        if activity is None:
            try:
                activity = self.plugin.state.current_activity() or {}
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe._at_hotel", _amo_exc)
                return False
        place = str(activity.get("place") or "")
        return any(word in place for word in HOTEL_PLACE_WORDS)

    def at_hotel(self):
        return self._at_hotel()

    def reroll(self, day=""):
        day = str(day or today_str())
        result = self._reroll_day(day)
        try:
            if self.at_home() is True and self._at_hotel() is False and not self._at_swim():
                before = ""
                try:
                    before = str(self.home_outfit(day).get("outfit") or "")
                except Exception as _amo_exc:
                    _note_swallowed("wardrobe.Wardrobe.reroll.home_before", _amo_exc)
                for _ in range(4):
                    try:
                        cur = int(self.plugin.db.kv_get("home_pick:" + day, 0) or 0)
                        self.plugin.db.kv_set("home_pick:" + day, cur + 1)
                    except Exception as _amo_exc:
                        _note_swallowed("wardrobe.Wardrobe.reroll.home_pick", _amo_exc)
                        break
                    picked = self.home_outfit(day)
                    if str(picked.get("outfit") or "") != before:
                        return picked
                return self.home_outfit(day)
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe.reroll.home", _amo_exc)
        return result

    def _reroll_day(self, day=""):
        day = str(day or today_str())
        with self._lock:
            data = self._load()
            entry = (data.get("daily") or {}).get(day) or {}
            outfit = self._pick(data, day, exclude=str(entry.get("outfit") or ""))
            data.setdefault("daily", {})[day] = {
                "outfit": outfit.get("id", ""),
                "ts": _amo_clock.now_ts(),
            }
            self._trim_daily(data)
            self._save(data)
            self._today_cache = None
        self.current(day)  # 同步 sidecar：白天换新一套、晚上保持睡衣
        return self.today(day)

    def set_today(self, day="", coat="", top="", bottom="", skirt="", shoes="",
                  socks=""):
        """面板手动选好一套后保存：今天锁这一套。

        外套最多一件、可以套在衬衫外面；长裙单穿；短裙配上衣，裤子可选。
        """
        day = str(day or today_str())
        with self._lock:
            data = self._load()
            items = self._items_map(data)
            coat_item = items.get(str(coat or ""))
            top_item = items.get(str(top or ""))
            bottom_item = items.get(str(bottom or ""))
            skirt_item = items.get(str(skirt or ""))
            if coat_item and coat_item.get("category") != "coat":
                coat_item = None
            if top_item and top_item.get("category") != "top":
                top_item = None
            if bottom_item and bottom_item.get("category") != "bottom":
                bottom_item = None
            if skirt_item and skirt_item.get("category") != "skirt":
                skirt_item = None
            if skirt_item and is_dress(skirt_item.get("name")):
                top_item = None
                bottom_item = None
            elif skirt_item:
                if not top_item:
                    return {"ok": False, "msg": "短裙要配一件上衣"}
            elif not top_item:
                return {"ok": False, "msg": "先选一件上衣（衬衫）"}
            elif not bottom_item:
                probe = items.get(str(socks or ""))
                if not (probe and is_tights(probe.get("name"))):
                    return {"ok": False, "msg": "先选一条裤子/裙子（穿连裤袜/丝袜可以不穿裤子）"}
            socks_item = items.get(str(socks or ""))
            if socks_item and socks_item.get("category") != "socks":
                socks_item = None
            socks_name = str((socks_item or {}).get("name") or "")
            if (skirt_item and is_dress(skirt_item.get("name")) and socks_name
                    and is_long_socks(socks_name) and not is_tights(socks_name)):
                return {"ok": False, "msg": "长裙可以配连裤袜/丝袜/短袜，不能配长筒袜/过膝袜/渔网袜"}
            bottom_name = str((bottom_item or {}).get("name") or "")
            short_bottom = "短" in bottom_name
            if socks_name and is_tights(socks_name) and bottom_item and not short_bottom:
                return {"ok": False, "msg": "连裤袜只能配短裤/短裙，不能配长裤"}
            if (socks_name and not is_tights(socks_name) and is_long_socks(socks_name)
                    and bottom_item and not short_bottom):
                return {"ok": False, "msg": "长袜只能配短裤，长裤不穿长袜"}
            try:
                temp = float((getattr(self.plugin.state, "weather", {}) or {}).get("temp") or 0)
            except Exception:
                temp = 0.0
            if socks_name and temp >= 28 and is_long_socks(socks_name):
                return {"ok": False, "msg": "太热了，只能穿短袜/船袜"}
            shoes_item = items.get(str(shoes or ""))
            if not shoes_item or shoes_item.get("category") != "shoes":
                shoes_item = None
                entry = (data.get("daily") or {}).get(day) or {}
                old = self._outfit_by_id(data, entry.get("outfit"))
                old_shoes = items.get(old.get("shoes")) if old else None
                if old_shoes and old_shoes.get("category") == "shoes":
                    shoes_item = old_shoes
                else:
                    pool = [item for item in data["items"] if item.get("category") == "shoes"]
                    if pool:
                        shoes_item = random.Random("amo-wardrobe-shoes:" + day).choice(pool)
            if shoes_item is None:
                return {"ok": False, "msg": "还没有鞋子，先加一双"}
            ids = {
                "coat": coat_item["id"] if coat_item else "",
                "top": top_item["id"] if top_item else "",
                "bottom": bottom_item["id"] if bottom_item else "",
                "skirt": skirt_item["id"] if skirt_item else "",
                "shoes": shoes_item["id"],
            }
            outfit = None
            for entry in data.get("outfits", []):
                if all((entry.get(key) or "") == value for key, value in ids.items()):
                    outfit = entry
                    break
            if outfit is None:
                outfit = {
                    "id": _new_id("o-"),
                    **ids,
                    "source": "manual",
                }
                data.setdefault("outfits", []).append(outfit)
            data.setdefault("daily", {})[day] = {
                "outfit": outfit["id"],
                "socks": socks_item["id"] if socks_item else "",
                "ts": _amo_clock.now_ts(),
            }
            self._trim_daily(data)
            self._save(data)
            self._today_cache = None
        self.current(day)  # 同步 sidecar（晚上仍保持睡衣）
        result = self.today(day)
        result["ok"] = True
        return result
