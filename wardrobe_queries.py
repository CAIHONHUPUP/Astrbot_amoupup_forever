# -*- coding: utf-8 -*-
"""wardrobe_queries：Wardrobe 的一部分方法（逐字搬运）。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

import contextlib
import contextvars

try:  # 包模式（AstrBot 加载插件）
    from .wardrobe_data import (
        CATEGORY_KEYS, MAX_OUTFITS, _amo_clock, _clock_now, _combo_candidates, _item_ref_path,
        _new_id, _note_swallowed, _outfit_slots, _wardrobe_category_list, classify_item,
        classify_sleep_item, clean_name, combo_compatible, is_day_outfit, json, logger, os,
        random, today_str,
    )
except ImportError:  # 直接按文件加载时（测试/模拟器）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from wardrobe_data import (
            CATEGORY_KEYS, MAX_OUTFITS, _amo_clock, _clock_now, _combo_candidates, _item_ref_path,
            _new_id, _note_swallowed, _outfit_slots, _wardrobe_category_list, classify_item,
            classify_sleep_item, clean_name, combo_compatible, is_day_outfit, json, logger, os,
            random, today_str,
        )


#: 本请求是否在「他点名了衣服」的上下文里。必须按 asyncio task / to_thread 隔离：
#: 实例上的全局计数器会让两个并发会话互相“少锁一次”（B2c）。
_OUTFIT_LOCK_DEPTH = contextvars.ContextVar("amo_outfit_lock_depth", default=0)


def _outfit_lock_depth():
    return max(0, int(_OUTFIT_LOCK_DEPTH.get() or 0))


class WardrobeQueriesMixin:
    # 方法体逐字搬运自拆分前的 wardrobe.py。

    # ---------- 增删 ----------
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _at_home: Any
        _at_hotel: Any
        _ensure_day: Any
        _load: Any
        _lock: Any
        _refill_sleep_combos: Any
        _save: Any
        _sleep_outfits: Any
        _swim_outfits: Any
        _swim_window: Any
        _synced: str
        current: Any
        is_swim_activity: Any
        night: Any
        path: Any
        plugin: Any
        sidecar_path: Any
        swim_outfit: Any
        swim_sets: Any
        today: Any
    def add_item(self, category, name, source="manual", gift_id="", sleep=False, hotel=False,
                 swim=False):
        category = str(category or "").strip()
        name = clean_name(name)
        hotel = bool(hotel)
        swim = bool(swim)
        sleep = (bool(sleep) or hotel) and not swim
        if category not in CATEGORY_KEYS:
            return {"ok": False, "msg": "分类不对，只能是 top / bottom / shoes"}
        if not name:
            return {"ok": False, "msg": "衣服名字不能为空"}
        if swim:
            # 泳装只能整套穿：面板里选整套，不接受单件添加，避免出现「有上衣没泳裤」
            return {"ok": False, "msg": "泳装只能整套穿，不能单件添加"}
        with self._lock:
            data = self._load()
            for item in data["items"]:
                if (item.get("category") == category and item.get("name") == name
                        and bool(item.get("sleep")) == sleep
                        and bool(item.get("hotel")) == hotel
                        and bool(item.get("swim")) == swim):
                    return {"ok": True, "item": dict(item), "dup": True, "combos": 0}
            item = {
                "id": _new_id("i-"),
                "category": category,
                "name": name,
                "source": source or "manual",
                "gift_id": gift_id or "",
                "created_at": _amo_clock.now_ts(),
            }
            if sleep:
                item["sleep"] = True
            if hotel:
                item["hotel"] = True
            if swim:
                item["swim"] = True
            data["items"].append(item)
            combos = self._combos_for(data, item)
            if len(data["outfits"]) > MAX_OUTFITS:
                data["outfits"] = data["outfits"][-MAX_OUTFITS:]
            self._save(data)
            self._today_cache = None
        return {"ok": True, "item": dict(item), "combos": combos}

    def delete_item(self, item_id):
        item_id = str(item_id or "").strip()
        if not item_id:
            return {"ok": False, "msg": "缺少衣服 id"}
        with self._lock:
            data = self._load()
            before = len(data["items"])
            deleted = [item for item in data["items"] if item.get("id") == item_id]
            data["items"] = [item for item in data["items"] if item.get("id") != item_id]
            if len(data["items"]) == before:
                return {"ok": False, "msg": "没找到这件衣服"}
            # 衣服删了，它的参考图也一起删掉（只删这件自己那张，不动同名不同分类的）
            if deleted and _item_ref_path is not None:
                try:
                    ref = _item_ref_path(self.plugin, deleted[0])
                    if ref and os.path.isfile(ref):
                        os.remove(ref)
                        logger.info("[amo] 服装参考图已删除：%s" % ref)
                except Exception as _amo_exc:
                    _note_swallowed("wardrobe.Wardrobe.delete_item.ref", _amo_exc)
            gone = [outfit["id"] for outfit in data["outfits"]
                    if item_id in (outfit.get("coat"), outfit.get("top"),
                                   outfit.get("bottom"), outfit.get("skirt"),
                                   outfit.get("shoes"))]
            data["outfits"] = [outfit for outfit in data["outfits"]
                               if outfit.get("id") not in gone]
            for day, entry in list((data.get("daily") or {}).items()):
                if entry.get("outfit") in gone:
                    data["daily"].pop(day, None)
                elif entry.get("swim") in gone:
                    data["daily"][day].pop("swim", None)
            self._ensure_day(data)
            self._refill_sleep_combos(data)
            self._save(data)
            self._today_cache = None
        return {"ok": True, "deleted": deleted[0] if deleted else {}, "outfits": len(gone)}

    def _combos_for(self, data, item):
        """新加一件衣服时，和现有互补单品组几套（同色系/同材质、外套最多一件）。"""
        sleep = bool(item.get("sleep"))
        hotel = bool(item.get("hotel"))
        swim = bool(item.get("swim"))
        by_cat = {key: [entry for entry in data["items"]
                        if entry.get("category") == key
                        and bool(entry.get("sleep")) == sleep
                        and bool(entry.get("hotel")) == hotel
                        and bool(entry.get("swim")) == swim]
                  for key in CATEGORY_KEYS}
        combos = _combo_candidates(item, by_cat, swim)

        def combo_names(entry):
            return tuple(str(part.get("name") or "") if part else "" for part in entry)

        if not swim:
            # 泳装不需要同色系/同材质：一套泳衣本来就是配好的
            combos = [entry for entry in combos if combo_compatible(*combo_names(entry))]
        random.Random("amo-wardrobe-combo:" + str(item.get("id") or "")).shuffle(combos)
        combos = combos[:4]
        existing = {tuple(outfit.get(key) or "" for key in
                          ("coat", "top", "bottom", "skirt", "shoes"))
                    for outfit in data["outfits"]}
        added = 0
        for coat, top, bottom, skirt, shoe in combos:
            key = (coat["id"] if coat else "", top["id"] if top else "",
                   bottom["id"] if bottom else "", skirt["id"] if skirt else "",
                   shoe["id"] if shoe else "")
            if key in existing:
                continue
            existing.add(key)
            data["outfits"].append({
                "id": _new_id("o-"),
                "coat": key[0], "top": key[1], "bottom": key[2],
                "skirt": key[3], "shoes": key[4],
                "source": item.get("source") or "manual",
                "sleep": True if sleep else False,
                "hotel": True if hotel else False,
                "swim": True if swim else False,
                "combo": True,
            })
            added += 1
        return added

    # ---------- 礼物 ----------
    def add_from_gift(self, item, gift_id=""):
        """他送的衣服自动进衣柜；睡衣单独归到晚上衣柜，不是衣服就不动。"""
        name = clean_name(item)
        sleep_category = classify_sleep_item(name)
        if sleep_category:
            result = self.add_item(sleep_category, name, source="gift", gift_id=gift_id, sleep=True)
            if result.get("ok"):
                result["category"] = sleep_category
                result["sleep"] = True
            return result
        category = classify_item(name)
        if not category:
            return None
        result = self.add_item(category, name, source="gift", gift_id=gift_id)
        if result.get("ok"):
            result["category"] = category
        return result

    # ---------- 面板 / 我能永远定格你 ----------
    def _current_snapshot(self, data=None, activity=None, at_home=None, at_hotel=None, swim=None):
        current = self.current(data=data, activity=activity, at_home=at_home,
                               at_hotel=at_hotel, swim=swim)
        if current.get("swim"):
            current["wearing_swim"] = True
        return current

    def snapshot(self):
        data = self._load()
        try:
            now_clock = _clock_now()
            hour = int(now_clock.hour)
            minute = int(now_clock.minute)
        except Exception:
            hour = 20
            minute = 0
        day_text = today_str()
        # P2：一次快照只读一次 current_activity，_at_swim/_at_home/_at_hotel 共用；
        # data 也从 snapshot 传下去，避免同一次刷新里重复 _load。
        try:
            activity = dict(self.plugin.state.current_activity() or {})
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe.snapshot.activity", _amo_exc)
            activity = {}
        swim_now = self.is_swim_activity(activity)
        swim_window = self._swim_window(day_text)
        at_home = self._at_home(activity)
        at_hotel = self._at_hotel(activity)
        day_state, night_state, night_out, reason = _outfit_slots(hour, swim_now, at_home, at_hotel)
        if day_state == "ready":
            today = self.today(day_text, data=data)
        else:
            today = {"pending": True, "state": day_state,
                     "text": "8 点自动生成"}
        if night_state == "ready":
            night = self.night(day_text, hour=hour, hotel=at_hotel, data=data)
        elif night_state == "outing":
            night = {"pending": True, "state": "outing", "text": "临时外出"}
        elif night_state == "swim":
            night = {"pending": True, "state": "swim", "text": "正在使用泳装"}
        else:
            night = {"pending": True, "state": "pending20",
                     "text": "20:00 后自动生成"}
        logger.info(
            "[OUTFIT_DEBUG] time=%02d:%02d schedule=%s day_slot=%s night_slot=%s "
            "swim_slot=%s reason=%s",
            hour, minute,
            ("swim" if swim_now else ("outing" if night_out else "normal")),
            day_state, night_state,
            "ready" if swim_now else "pending", reason,
        )

        # 泳装格：正在玩水就显示实际泳装；否则显示待泳/未来时段。
        if swim_now:
            swim_data = self.swim_outfit(day_text, data=data)
            swim_state = "ready"
        else:
            swim_data = {
                "pending": True,
                "swim": True,
                "state": swim_window.get("state") or "none",
                "text": ("%02d:%02d 游泳 / 海边 / 水上乐园，到点自动换上"
                         % divmod(int(swim_window.get("start") or 0), 60))
                        if swim_window.get("state") == "later"
                        else "游泳 / 海边 / 水上乐园 自动生成",
            }
            swim_state = "pending"
        return {
            "categories": _wardrobe_category_list(data, False, False, False),
            "sleep_categories": _wardrobe_category_list(data, True, False, False),
            "hotel_categories": _wardrobe_category_list(data, True, True, False),
            # 泳装只有上衣（泳衣）和裤子（泳裤），鞋是横线
            "swim_categories": _wardrobe_category_list(data, False, False, True),
            "swim_sets": self.swim_sets(data),
            "swim": swim_data,
            "at_swim": swim_now,
            "today": today,
            "day": day_text,
            "day_state": day_state,
            "current": self._current_snapshot(
                data=data, activity=activity, at_home=at_home, at_hotel=at_hotel,
                swim=swim_now),
            "night": night,
            "night_state": night_state,
            "swim_state": swim_state,
            "hour": hour,
            "outfit_count": len([outfit for outfit in data.get("outfits", [])
                                 if is_day_outfit(outfit)]),
            "sleep_outfit_count": len(self._sleep_outfits(data)),
            "hotel_sleep_outfit_count": len([outfit for outfit in data.get("outfits", [])
                                             if outfit.get("hotel")]),
            "swim_outfit_count": len(self._swim_outfits(data)),
            "day_seed_count": len([outfit for outfit in data.get("outfits", [])
                                   if is_day_outfit(outfit) and not outfit.get("combo")]),
            "sleep_seed_count": len([outfit for outfit in data.get("outfits", [])
                                     if outfit.get("sleep") and not outfit.get("hotel")
                                     and not outfit.get("combo")]),
            "hotel_seed_count": len([outfit for outfit in data.get("outfits", [])
                                     if outfit.get("hotel") and not outfit.get("combo")]),
            "path": self.path,
        }

    @staticmethod
    def _outfit_lock_text(result):
        """sidecar 只写实际单品：omnidraw 会把它当 OUTFIT LOCK 原文喂给副脑。

        不能写「在家穿的居家便服：…」这种泛化标签——副脑会翻成模板
        「casual homewear」，把 action 里点名的「宽大卫衣」直接盖掉（B2b）。
        """
        parts = []
        for key in ("coat", "top", "skirt", "bottom", "shoes"):
            name = str(result.get(key) or "").strip()
            if name and name not in parts:
                parts.append(name)
        return "、".join(parts)

    def _write_sidecar_payload(self, text, result=None):
        """原子写 sidecar；text 为空 = 明确告诉 omnidraw「这次没有可锁的穿搭」。"""
        result = result if isinstance(result, dict) else {}
        payload = {
            "day": str(result.get("day") or today_str()),
            "outfit": result.get("outfit") or "",
            "coat": result.get("coat") or "",
            "top": result.get("top") or "",
            "bottom": result.get("bottom") or "",
            "skirt": result.get("skirt") or "",
            "shoes": result.get("shoes") or "",
            "text": str(text or ""),
            "ts": _amo_clock.now_ts(),
        }
        try:
            folder = os.path.dirname(self.sidecar_path)
            if not os.path.isdir(folder):
                return False
            raw = json.dumps(payload, ensure_ascii=False)
            try:
                with open(self.sidecar_path, encoding="utf-8") as fh:
                    if fh.read() == raw:
                        return True
            except FileNotFoundError:
                pass  # cleanup
            tmp = self.sidecar_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(raw)
            os.replace(tmp, self.sidecar_path)
            return True
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._write_sidecar", _amo_exc)
            return False

    def suppress_outfit_lock(self, reason=""):
        """标记「本请求 action 点名了衣服」，返回 ContextVar token。

        不能再用 self 上的计数器：两个会话并发生图时，A 的 suppress 会把 B
        也当成 suppress，B 的默认自拍就“少锁一次”（B2c）。ContextVar 按
        asyncio task / to_thread 隔离。共享 sidecar 不再清空——omnidraw 侧按
        action 是否点名衣服决定要不要采用 OUTFIT LOCK。
        """
        depth = _outfit_lock_depth()
        token = _OUTFIT_LOCK_DEPTH.set(depth + 1)
        if depth == 0:
            logger.info("[OUTFIT_DEBUG] sidecar_lock=request_scoped reason=%s",
                        str(reason or "explicit_outfit"))
        return token

    def resume_outfit_lock(self, reason="", token=None):
        depth = _outfit_lock_depth()
        if token is not None:
            try:
                _OUTFIT_LOCK_DEPTH.reset(token)
            except ValueError:
                # 跨 context 退出（例如线程池回调）时按层数兜底，别把深度卡住。
                _OUTFIT_LOCK_DEPTH.set(max(0, depth - 1))
        else:
            _OUTFIT_LOCK_DEPTH.set(max(0, depth - 1))
        depth = _outfit_lock_depth()
        if depth == 0:
            logger.info("[OUTFIT_DEBUG] sidecar_lock=request_scoped_off reason=%s",
                        str(reason or "explicit_outfit"))
        return depth

    def outfit_lock_is_suspended(self):
        """本请求是否被标记为「点名了衣服」；并发里的其他会话不受影响。"""
        return _outfit_lock_depth() > 0

    @property
    def _lock_suspended(self):
        """兼容旧读法的只读视图；真实状态在 ContextVar 里。"""
        return _outfit_lock_depth()

    @contextlib.contextmanager
    def outfit_lock_suspended(self, reason=""):
        token = self.suppress_outfit_lock(reason)
        try:
            yield
        finally:
            self.resume_outfit_lock(reason, token)

    def _sync_sidecar(self, result):
        """把今天实际穿的单品写给我能永远定格你；没点名衣服的请求始终保留这把锁。

        B2c：不再因为别的请求正在 suppress 就跳过写入（旧全局计数器会漏写，
        让另一个并发会话“少锁一次”）。sidecar 始终是当天真实穿搭；点名衣服
        的请求由 omnidraw 按 action 判断是否采用 OUTFIT LOCK。
        """
        day = str(result.get("day") or "")
        text = self._outfit_lock_text(result)
        if not day or not text:
            return
        key = day + "|" + str(result.get("outfit") or "") + "|" + text
        if key == self._synced:
            return
        if self._write_sidecar_payload(text, result):
            self._synced = key
