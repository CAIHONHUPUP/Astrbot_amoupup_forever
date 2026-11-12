# -*- coding: utf-8 -*-
"""TravelMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

from typing import TYPE_CHECKING

import random
import re
from datetime import date, timedelta

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        _CITY_DEFAULT, _city_detect, _city_pick_spots, _follow_block, _follow_invite, _follow_refuse, _now, _wander_agree, _wander_ask_limit, _wander_assess, _wander_follow_agree_text, _wander_offer_block, _wander_pick, _wander_plan_text, _wander_question, _wander_refuse, _wander_solo_agree_text, _wander_solo_block, _wander_solo_refuse_text, _wander_trip_days, _wander_want, logger, now_ts, to_float, today_str,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            _CITY_DEFAULT, _city_detect, _city_pick_spots, _follow_block, _follow_invite, _follow_refuse, _now, _wander_agree, _wander_ask_limit, _wander_assess, _wander_follow_agree_text, _wander_offer_block, _wander_pick, _wander_plan_text, _wander_question, _wander_refuse, _wander_solo_agree_text, _wander_solo_block, _wander_solo_refuse_text, _wander_trip_days, _wander_want, logger, now_ts, to_float, today_str,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            _CITY_DEFAULT, _city_detect, _city_pick_spots, _follow_block, _follow_invite, _follow_refuse, _now, _wander_agree, _wander_ask_limit, _wander_assess, _wander_follow_agree_text, _wander_offer_block, _wander_pick, _wander_plan_text, _wander_question, _wander_refuse, _wander_solo_agree_text, _wander_solo_block, _wander_solo_refuse_text, _wander_trip_days, _wander_want, logger, now_ts, to_float, today_str,
        )


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class TravelMixin(_MixinBase):
    def _city_module(self):
        """离线城市表模块（懒加载；缺失时返回 None，天气按原样走）。"""
        try:
            try:
                from . import amo_city_index
            except Exception as _amo_exc:
                _note_swallowed("amo_travel.HumanCorePlugin._city_module", _amo_exc)
                import amo_city_index
            return amo_city_index
        except Exception as exc:
            logger.debug("[amo] 城市表不可用: %s" % exc)
            return None

    def _city_lookup(self, text):
        """把「东莞」「北京朝阳」解析成 LocationID；失败返回 None。"""
        module = self._city_module()
        if module is None:
            return None
        try:
            return module.resolve_city(text)
        except Exception as exc:
            logger.debug("[amo] 城市名解析失败: %s" % exc)
            return None

    async def _maybe_follow_city(self, event, uid, profile):
        """他到了某地：她问「我来陪你吧」；同意就跟去（路上约 2 小时），拒绝就闷闷不乐。"""
        if not self.cfg_bool("city_follow_enabled", True):
            return ""
        try:
            if not event.is_private_chat():
                return ""
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._maybe_follow_city", _amo_exc)
            return ""
        if str(profile.get("relation") or "") != "相恋":
            return ""
        text = str(event.message_str or "")
        now = now_ts()
        offer = self.db.kv_get("follow_offer", {}) or {}
        offer = offer if isinstance(offer, dict) else {}
        if offer and now - to_float(offer.get("ts"), 0) > 1800:
            self.db.kv_set("follow_offer", {})
            offer = {}
        travel = self.db.kv_get("travel", {}) or {}
        travel = travel if isinstance(travel, dict) else {}
        if travel.get("city") and re.search(r"(回来吧|回家吧|别玩了|该回来了|我回东莞)", text):
            await self._end_follow_trip(uid)
            return "【回家】他说回来了：自然应一句「好，我这就回」，别拖，也别说还没玩够。"
        if travel.get("city"):
            return ""
        if self.db.kv_get("wander_trip", {}):
            return ""
        if _follow_invite(text):
            city = str(self.db.kv_get("he_city", "") or "").strip()
            if not city:
                return "【邀请】他让你过去，但你不知道他在哪个城市：先问一句他在哪。"
            if city == _CITY_DEFAULT:
                return "【邀请】他让你过去，可你俩都在东莞：自然回一句“我不就在你旁边吗”之类的。"
            self.db.kv_set("follow_declined", {})
            return await self._start_follow_trip(uid, {
                "city": city, "id": "",
                "tomorrow": _now().hour >= 22,
            })
        if offer.get("city"):
            if _wander_agree(text):
                return await self._start_follow_trip(uid, offer)
            if _follow_refuse(text):
                return await self._reject_follow_trip(uid, offer)
        old_he = str(self.db.kv_get("he_city", "") or "").strip()
        old = str(self.db.kv_get("his_city", "") or "").strip()
        hit = _city_detect(text, prefer_city=old or old_he)
        if not hit:
            return ""
        name = str(hit.get("name") or "").strip()
        declined = self.db.kv_get("follow_declined", {}) or {}
        declined_city = str(declined.get("city") or "") if isinstance(declined, dict) else ""
        if not name or name == old_he:
            if declined_city and declined_city == name:
                return "【别重复提】你之前问过要不要去陪他，他没让；别再主动提这事，按他说的上班就好。"
            return ""
        if name == _CITY_DEFAULT:
            self.db.kv_set("he_city", name)
            self.db.kv_set("follow_declined", {})
            return ""
        # 到了新的城市：之前"被拒过"的标记作废，可以再问一次
        self.db.kv_set("follow_declined", {})
        tomorrow = _now().hour >= 22
        self.db.kv_set("he_city", name)
        self.db.kv_set("wander_offer", {})
        self.db.kv_set("follow_offer", {
            "ts": now, "city": name, "id": str(hit.get("id") or ""), "tomorrow": tomorrow,
        })
        return _follow_block(True, name, tomorrow)

    async def _start_follow_trip(self, uid, offer):
        """同意跟去：定出发日/出发时间，当天或次日先坐 2 小时车再玩。"""
        city = str(offer.get("city") or "").strip()
        if not city:
            self.db.kv_set("follow_offer", {})
            return ""
        tomorrow = bool(offer.get("tomorrow"))
        start = (_now().date() + timedelta(days=1)) if tomorrow else _now().date()
        start_hour = 9 if tomorrow else _now().hour
        spots = []
        try:
            spots = _city_pick_spots(self.scenic_spots(), city, start.isoformat())
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._start_follow_trip", _amo_exc)
            spots = []
        trip = {
            "city": city,
            "id": str(offer.get("id") or ""),
            "from": str(self.db.kv_get("his_city", "") or ""),
            "start_day": start.isoformat(),
            "start_hour": start_hour,
            "spots": [dict(x) for x in spots][:4],
            "phase": "journey",
        }
        self.db.kv_set("travel", trip)
        self.db.kv_set("follow_offer", {})
        self.db.kv_set("follow_declined", {})
        try:
            content = "她 %s 坐车去 %s 陪他（路上约 2 小时）" % (start.isoformat(), city)
            if not await self._write_memory_checked(uid, content, "event", 1):
                logger.warning("[amo] 跟随记忆没写进去")
        except Exception as exc:
            logger.warning("[amo] 跟随记忆写入失败: %s" % exc)
        try:
            log = self.db.kv_get("travel_log", []) or []
            if not isinstance(log, list):
                log = []
            log.append({"day": start.isoformat(), "hour": "%02d:00" % start_hour,
                        "city": city, "journey": True})
            self.db.kv_set("travel_log", log[-10:])
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._start_follow_trip", _amo_exc)
        if not tomorrow:
            self.db.kv_set("his_city", city)
            hit = self._city_lookup(city)
            if hit:
                try:
                    await self._switch_weather_city(hit)
                except Exception as exc:
                    logger.warning("[amo] 跟随天气切换失败: %s" % exc)
            try:
                self.state.apply_follow_start(city, start_hour, spots, keep_past=True)
            except Exception as exc:
                logger.warning("[amo] 跟随日程失败: %s" % exc)
            trip["phase"] = "touring"
            self.db.kv_set("travel", trip)
        plan = _wander_follow_agree_text(city, random)
        when = "明天一早" if tomorrow else "现在"
        return (
            "【出发】他同意了：你" + when + "出发去 " + city + " 陪他，路上约 2 小时，到了再找他。"
            "照这个意思说一句（别报公司/上班）：" + plan
        )

    async def _reject_follow_trip(self, uid, offer):
        """他让她好好上班：她闷闷不乐，日程不变，日记记一笔。"""
        city = str(offer.get("city") or "").strip()
        self.db.kv_set("follow_offer", {})
        self.db.kv_set("follow_declined", {"city": city, "day": today_str()})
        try:
            self.state.nudge(委屈=10, 郁闷=8, 开心=-6)
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._reject_follow_trip", _amo_exc)
        try:
            content = "她说想去%s陪他，他让她好好上班，她闷闷不乐" % city
            if not await self._write_memory_checked(uid, content, "event", 1):
                logger.warning("[amo] 被拒记忆没写进去")
        except Exception as exc:
            logger.warning("[amo] 被拒记忆失败: %s" % exc)
        try:
            log = self.db.kv_get("follow_reject_log", []) or []
            if not isinstance(log, list):
                log = []
            log.append({"day": today_str(), "hour": "%02d:00" % _now().hour, "city": city})
            self.db.kv_set("follow_reject_log", log[-10:])
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._reject_follow_trip", _amo_exc)
        return _follow_block(False, city)

    async def _end_follow_trip(self, uid):
        """结束跟随：回家、恢复城市/天气/日程、写回来记忆。"""
        trip = self.db.kv_get("travel", {}) or {}
        city = str(trip.get("city") or "") if isinstance(trip, dict) else ""
        self.db.kv_set("travel", {})
        home = _CITY_DEFAULT
        self.db.kv_set("his_city", home)
        self.db.kv_set("he_city", home)
        hit = self._city_lookup(home)
        if hit:
            try:
                await self._switch_weather_city(hit)
            except Exception as _amo_exc:
                _note_swallowed("amo_travel.HumanCorePlugin._end_follow_trip", _amo_exc)
        try:
            self.state.apply_travel_overlay(home)
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._end_follow_trip", _amo_exc)
        try:
            content = "她 %s 从 %s 回来了" % (today_str(), city)
            await self._write_memory_checked(uid, content, "event", 1)
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._end_follow_trip", _amo_exc)
        try:
            log = self.db.kv_get("travel_log", []) or []
            if isinstance(log, list):
                log.append({
                    "day": today_str(), "hour": "%02d:00" % _now().hour,
                    "city": city, "back": True,
                })
                self.db.kv_set("travel_log", log[-10:])
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._end_follow_trip", _amo_exc)

    async def _maybe_wander(self, event, uid, profile):
        """她自己出去玩：问意愿 → 同意 → 定行程；旅行中让他叫回来。"""
        if not self.cfg_bool("wander_enabled", True):
            return ""
        try:
            if not event.is_private_chat():
                return ""
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._maybe_wander", _amo_exc)
            return ""
        if str(profile.get("relation") or "") != "相恋":
            return ""
        if self.db.kv_get("travel", {}) or self.db.kv_get("follow_offer", {}):
            return ""
        text = str(event.message_str or "")
        now = now_ts()
        trip = self.db.kv_get("wander_trip", {}) or {}
        trip = trip if isinstance(trip, dict) else {}
        if trip.get("city") and re.search(r"(回来吧|回家吧|别玩了|早点回来|该回来了)", text):
            await self._end_solo_trip(uid, "他让你回来")
            return "【旅游】他让你回来：自然应一句“好，我收拾下就回”，别拖，也别说还没玩够。"
        offer = self.db.kv_get("wander_offer", {}) or {}
        offer = offer if isinstance(offer, dict) else {}
        if offer and now - to_float(offer.get("ts"), 0) > 1800:
            self.db.kv_set("wander_offer", {})
            offer = {}
        if _wander_question(text):
            if (
                self.db.kv_get("travel", {})
                or self.db.kv_get("follow_offer", {})
                or self.db.kv_get("wander_trip", {})
            ):
                return ""
            today = today_str()
            count = 0
            if str(self.db.kv_get("wander_asked_day", "") or "") == today:
                try:
                    count = int(self.db.kv_get("wander_ask_count", 0) or 0)
                except (TypeError, ValueError):
                    count = 0
            limit = _wander_ask_limit(self.cfg_bool("wander_ask_three_times", False))
            if count >= limit:
                return (
                    "【旅游提议】今天已经问过 " + str(count) + " 次了（一天最多 " + str(limit)
                    + " 次），别再重新决定、别再报新城市；按之前说的自然接一句。"
                )
            self.db.kv_set("wander_asked_day", today)
            self.db.kv_set("wander_ask_count", count + 1)
            low, tired = _wander_assess(self.state.mood, self.state.health_info())
            want = _wander_want(
                low, tired, random,
                self.cfg_float("wander_prob_low", 0.85),
                self.cfg_float("wander_prob_good", 0.15),
            )
            city, spots = "", []
            if want:
                city, spots = _wander_pick(
                    self.scenic_spots(), random, count=4,
                    exclude_city=str(self.db.kv_get("his_city", "") or ""),
                )
                want = bool(city)
            if not want:
                self.db.kv_set("wander_offer", {"ts": now, "want": False})
            else:
                self.db.kv_set("wander_offer", {
                    "ts": now, "want": True, "city": city,
                    "spots": [str(x.get("name") or "") for x in spots][:4],
                    "spot_rows": spots[:4],
                })
            return _wander_offer_block(bool(want), city, spots)
        if offer.get("want") and _wander_agree(text):
            return await self._start_solo_trip(uid, offer)
        if offer.get("want") and _wander_refuse(text):
            self.db.kv_set("wander_offer", {})
            try:
                self.state.nudge(委屈=5, 郁闷=4)
            except Exception as _amo_exc:
                _note_swallowed("amo_travel.HumanCorePlugin._maybe_wander", _amo_exc)
            return (
                "【不出去了】他说这次不让你去：你有点失落，但别闹，用自己的口吻说一句："
                + _wander_solo_refuse_text(random)
            )
        if trip.get("city") and not trip.get("announced"):
            start = str(trip.get("day") or "")
            ret = str(trip.get("return_day") or start)
            if start and start <= today_str() <= ret:
                try:
                    day_no = (_now().date() - date.fromisoformat(start)).days + 1
                except Exception as _amo_exc:
                    _note_swallowed("amo_travel.HumanCorePlugin._maybe_wander", _amo_exc)
                    day_no = 1
                return _wander_solo_block(str(trip["city"]), trip.get("spots") or [], day_no=day_no)
        return ""

    async def _start_solo_trip(self, uid, offer):
        """确认出发：>=22 点明天走，否则现在就走。"""
        city = str(offer.get("city") or "").strip()
        spots = [x for x in (offer.get("spot_rows") or []) if isinstance(x, dict)]
        if not city:
            self.db.kv_set("wander_offer", {})
            return ""
        tomorrow = _now().hour >= 22
        start = (_now().date() + timedelta(days=1)) if tomorrow else _now().date()
        days = max(1, min(3, _wander_trip_days(random)))
        if days == 1 and not tomorrow and _now().hour >= 17:
            days = 2  # 傍晚才走，别刚出门就回家
        return_day = start + timedelta(days=days - 1)
        return_hour = 21 if days == 1 else 12
        hit = self._city_lookup(city)
        trip = {
            "city": city,
            "id": str((hit or {}).get("id") or ""),
            "day": start.isoformat(),
            "days": days,
            "return_day": return_day.isoformat(),
            "return_hour": return_hour,
            "spots": [str(x.get("name") or "") for x in spots][:4],
            "phase": "planned",
        }
        self.db.kv_set("wander_trip", trip)
        self.db.kv_set("wander_offer", {})
        try:
            content = "她 %s 一个人去 %s 旅游（他同意了，玩 %d 天）" % (start.isoformat(), city, days)
            if not await self._write_memory_checked(uid, content, "event", 1):
                logger.warning("[amo] 旅游记忆没写进去")
        except Exception as exc:
            logger.warning("[amo] 旅游记忆写入失败: %s" % exc)
        try:
            log = self.db.kv_get("travel_log", []) or []
            if not isinstance(log, list):
                log = []
            log.append({
                "day": start.isoformat(), "hour": "%02d:00" % _now().hour,
                "city": city, "solo": True,
            })
            self.db.kv_set("travel_log", log[-10:])
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._start_solo_trip", _amo_exc)
        if not tomorrow:
            if hit:
                try:
                    await self._switch_weather_city(hit)
                except Exception as exc:
                    logger.warning("[amo] 旅游天气切换失败: %s" % exc)
            try:
                self.state.apply_travel_overlay(city)
            except Exception as exc:
                logger.warning("[amo] 旅游日程失败: %s" % exc)
            trip["phase"] = "applied"
            self.db.kv_set("wander_trip", trip)
        agree = _wander_solo_agree_text(city, random)
        plan = _wander_plan_text(city, spots, tomorrow, random)
        when = "明天" if tomorrow else "现在"
        back_text = "第 %d 天中午回来" % days if days > 1 else "当晚回来"
        return (
            "【出发】他同意了：你" + when + "去 " + city + " 旅游（一个人），玩 %d 天，%s。"
            % (days, back_text) + "先照这个说一句：" + agree + " 再说安排：" + plan
            + " 别说成跟他一起；白天自己去景点，晚上住酒店。"
        )

    async def _maybe_solo_return(self):
        """旅游最后一天到点回家：换天气、报平安、写记忆/日记。"""
        trip = self.db.kv_get("wander_trip", {}) or {}
        if not isinstance(trip, dict) or not trip.get("city") or trip.get("announced"):
            return
        ret_day = str(trip.get("return_day") or "")
        if not ret_day or today_str() != ret_day:
            return
        try:
            hour = int(trip.get("return_hour") or 12)
        except (TypeError, ValueError):
            hour = 12
        if _now().hour < hour:
            return
        city = str(trip["city"])
        his = str(self.db.kv_get("his_city", "") or "").strip() or _CITY_DEFAULT
        hit = self._city_lookup(his)
        if hit:
            try:
                await self._switch_weather_city(hit)
            except Exception as exc:
                logger.warning("[amo] 回来天气切换失败: %s" % exc)
        try:
            self.state.apply_travel_overlay(his)
        except Exception as exc:
            logger.warning("[amo] 回来日程失败: %s" % exc)
        # 报平安必须进私聊：以前用 _last_umo，他最后一句是在群里就会把「我回来啦」发进群。
        umo = self._first_private_umo()
        if not umo:
            last = str(getattr(self, "_last_umo", "") or "")
            umo = last if "FriendMessage" in last else ""
        uid = ""
        try:
            rows = self.db.list_profiles(limit=1) or []
            uid = str((rows[0] or {}).get("uid") or "") if rows else ""
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._maybe_solo_return", _amo_exc)
            uid = ""
        try:
            if umo:
                text = random.choice((
                    "我回来啦，刚到家。",
                    "到家啦，你在干嘛呢？",
                    "我回来了，你吃饭了没？",
                ))
                if await self._send_to_umo(umo, text):
                    self._mark_bot_initiated(umo)
                    if uid:
                        self._set_proactive_pending(uid, "travel_back", text, umo=umo)
        except Exception as exc:
            logger.warning("[amo] 回来报平安失败: %s" % exc)
        try:
            if uid:
                if not await self._write_memory_checked(
                    uid, "她 %s 从 %s 旅游回来了" % (today_str(), city), "event", 1,
                ):
                    logger.warning("[amo] 回来记忆没写进去")
        except Exception as exc:
            logger.warning("[amo] 回来记忆失败: %s" % exc)
        try:
            log = self.db.kv_get("travel_log", []) or []
            if isinstance(log, list):
                log.append({
                    "day": today_str(), "hour": "%02d:00" % _now().hour,
                    "city": city, "solo": True, "back": True,
                })
                self.db.kv_set("travel_log", log[-10:])
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._maybe_solo_return", _amo_exc)
        trip["announced"] = True
        self.db.kv_set("wander_trip", trip)
        logger.info("[amo] 旅游回来：%s" % city)

    async def _end_solo_trip(self, uid, reason=""):
        """结束 solo 旅游：清状态、记回来、天气换回他在的城市、日程回家。"""
        trip = self.db.kv_get("wander_trip", {}) or {}
        if not isinstance(trip, dict) or not trip.get("city"):
            return
        city = str(trip["city"])
        self.db.kv_set("wander_trip", {})
        try:
            content = "她 %s 从 %s 旅游回来了" % (today_str(), city)
            if not await self._write_memory_checked(uid, content, "event", 1):
                logger.warning("[amo] 回来记忆没写进去")
        except Exception as exc:
            logger.warning("[amo] 回来记忆写入失败: %s" % exc)
        try:
            log = self.db.kv_get("travel_log", []) or []
            if not isinstance(log, list):
                log = []
            log.append({
                "day": today_str(), "hour": "%02d:00" % _now().hour,
                "city": city, "solo": True, "back": True,
            })
            self.db.kv_set("travel_log", log[-10:])
        except Exception as _amo_exc:
            _note_swallowed("amo_travel.HumanCorePlugin._end_solo_trip", _amo_exc)
        his = str(self.db.kv_get("his_city", "") or "").strip() or _CITY_DEFAULT
        hit = self._city_lookup(his)
        if hit:
            try:
                await self._switch_weather_city(hit)
            except Exception as exc:
                logger.warning("[amo] 回来天气切换失败: %s" % exc)
        try:
            self.state.apply_travel_overlay(his)
        except Exception as exc:
            logger.warning("[amo] 回来日程失败: %s" % exc)
