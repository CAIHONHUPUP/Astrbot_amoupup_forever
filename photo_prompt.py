# -*- coding: utf-8 -*-
"""照片提示词：穿搭/光线/空镜/自拍提示词装配（从 photo_engine 拆出，行为逐字节不变）。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

import random

from astrbot.api import logger

try:
    from .photo_data import (
    OUTFIT_POOL,
    PHOTO_INTENT_WORDS,
    PHOTO_PLACE_WORDS,
    _light_text,
    _light_value,
    _log_photo_debug,
    home_room_for_plan,
    pose_line_for,
    scene_guard,
    selfie_state_hint,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_data import (
            OUTFIT_POOL,
            PHOTO_INTENT_WORDS,
            PHOTO_PLACE_WORDS,
            _light_text,
            _light_value,
            _log_photo_debug,
            home_room_for_plan,
            pose_line_for,
            scene_guard,
            selfie_state_hint,
        )

try:
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_observability import note as _note_swallowed

try:
    from .amo_shared import today_str
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import today_str

try:
    from .clock import now as _clock_now
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from clock import now as _clock_now


try:
    from .amo_image_common import clean_place
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_image_common import clean_place

try:
    from .photo_view import (
    HEADWEAR_LOCK,
    ROOM_NO_BODY,
    SCENERY_ONLY,
    SELFIE_FRAMING,
    SELFIE_STYLE,
    VIEW_POV,
    VIEW_SELFIE,
    sanitize_selfie_prompt,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_view import (
            HEADWEAR_LOCK,
            ROOM_NO_BODY,
            SCENERY_ONLY,
            SELFIE_FRAMING,
            SELFIE_STYLE,
            VIEW_POV,
            VIEW_SELFIE,
            sanitize_selfie_prompt,
        )

try:
    from .photo_plan import (
    build_plan,
    pick_pose_for_plan,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_plan import (
            build_plan,
            pick_pose_for_plan,
        )

try:
    from .photo_scene import scene_for
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_scene import scene_for

try:
    from .pose_library import pose_line
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from pose_library import pose_line

try:  # P22：在家分房间 + 房间参考图（默认关，A/B 显式开）
    from .photo_room import (
    ROOM_REF_SUBJECT_GUARD,
    room_lock_line,
    room_scene_line,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_room import (
            ROOM_REF_SUBJECT_GUARD,
            room_lock_line,
            room_scene_line,
        )


def _day_view_event(plugin):
    """此刻正在进行的那条事实（build_day_view 合并了块日记/账本）。

    照片场景的地点/活动先读这份唯一事实；读不到就返回空 dict，
    调用方再退回 state.current_activity()（旧口径不动）。
    """
    if plugin is None:
        return {}
    try:
        try:
            from . import amo_fact_timeline as fact_timeline
        except ImportError:  # 直接按文件加载时
            import amo_fact_timeline as fact_timeline
        now = _clock_now()
        view = fact_timeline.build_day_view(plugin, now.strftime("%Y-%m-%d"), now)
        return fact_timeline.current_view_event(view, now) or {}
    except Exception as _amo_exc:
        _note_swallowed("photo_prompt._day_view_event", _amo_exc)
        return {}


class PhotoPromptMixin:
    """提示词装配：穿搭、光线、空镜、自拍。"""
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _cfg_str: Any
        _profiles: Any
        _send: Any
        _target_umo: Any
        _worn_text: Any
        generate_custom: Any
        plugin: Any

    def _clean_place(place):
        """日程里的 place 有时是「他桌面上」这种动作描述，不适合当拍照地点。"""
        return clean_place(place)

    def outfit_today(self):
        """今天的固定穿搭：从「穿搭」衣柜里选一套，同一天所有自拍都穿这一套。"""
        day = today_str()
        # 先问衣柜：面板「换一套」后要立刻生效，不能吃本模块的旧缓存
        wardrobe = getattr(self.plugin, "wardrobe", None)
        if wardrobe is not None:
            try:
                current = wardrobe.current(day)
                text = str(current.get("text") or "").strip()
                if text:
                    self._outfit_day = day
                    self._outfit_text = text
                    return text
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.PhotoEngine.outfit_today", _amo_exc)
        if self._outfit_day == day and self._outfit_text:
            return self._outfit_text
        key = "photo_outfit:" + day
        try:
            cached = str(self.plugin.db.kv_get(key, "") or "")
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.outfit_today", _amo_exc)
            cached = ""
        if cached:
            self._outfit_day = day
            self._outfit_text = cached
            return cached
        chooser = random.Random("amo-outfit:" + day)
        text = chooser.choice(OUTFIT_POOL)
        try:
            temp = float((getattr(self.plugin.state, "weather", {}) or {}).get("temp") or 0)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.outfit_today", _amo_exc)
            temp = 0.0
        if temp and temp <= 12:
            text += "，外面套了件大衣"
        elif temp and temp >= 28:
            text += "，天热穿得清爽"
        worn = self._worn_text()
        if worn:
            text += "，" + worn
        try:
            self.plugin.db.kv_set(key, text)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.outfit_today", _amo_exc)
        self._outfit_day = day
        self._outfit_text = text
        return text

    #: 这些场景在室内，晚上用室内灯光；其余（路上/街/景区）用夜景光
    INDOOR_ZONES = ("home", "office", "restaurant", "mall", "hotel", "station", "hospital", "unknown")

    @staticmethod
    def _light_for_hour(hour, outdoor=False):
        """按当前时间给生图光线，别晚上拍出白天；室外场景不写“室内暖灯”。"""
        try:
            hour = int(hour) % 24
        except (TypeError, ValueError):
            hour = 12
        if 5 <= hour < 7:
            return "清晨，天刚亮，柔和偏冷的晨光"
        if 7 <= hour < 11:
            return "上午，明亮自然光"
        if 11 <= hour < 14:
            return "正午，明亮顶光"
        if 14 <= hour < 17:
            return "下午，暖色斜阳"
        if 17 <= hour < 19:
            return "傍晚黄昏，暖橙色夕阳、光线正在变暗"
        if 19 <= hour < 22:
            if outdoor:
                return "晚上，路灯和车灯亮着，夜色，不要白天阳光"
            return "晚上，室内暖色灯光，窗外是夜色，不要白天阳光"
        if outdoor:
            return "深夜，只有路灯和零星车灯，夜色很深，不要白天阳光"
        return "深夜，室内只开暖黄小灯，窗外漆黑，不要白天阳光"

    def _outdoor_light(self, plan):
        if plan is None:
            return False
        return not str(plan.zone or "").startswith(("home_", "office", "restaurant", "mall",
                                                    "hotel", "station", "hospital", "unknown"))

    def _time_light(self, outdoor=False):
        try:
            hour = int(_clock_now().hour)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._time_light", _amo_exc)
            hour = 12
        return "现在是%02d点，%s" % (hour, self._light_for_hour(hour, outdoor=outdoor))

    def _prompt_for(self, kind, scene, activity, plan=None):
        """非自拍：她拍的环境。一律空镜 + 她的拍摄视角，画面里不出现她本人。

        plan 由 _share 传进来（一次生图只解析一次计划）；没传时（既有三参调用 /
        测试）在这里兜底解析，兼容老签名。

        以前这里会把「她在做什么」原样拼进去（比如「边对边打哈欠」），
        那句话要求画面里有个人，和「不出现她」打架，模型只能自己现编一张脸
        —— 2026-11-11 那批「长相画风全变」的照片就是这么来的。

        地点统一走 ``scene_for()``：日程说在家就出家里的陈设，说逛街就出商场，
        旅游就出景区（带景点名），不再拿「他桌面上」这种词去糊。
        2026-11-12：守卫词也进空镜提示词（以前只有自拍带，空镜完全没约束）。
        """
        prefix = self._cfg_str("photo_prompt_prefix", "手机随手拍，真实自然，生活感，")
        activity = dict(activity or {})
        if plan is None:
            try:
                plan = build_plan(activity, kind=kind, mode="photo", scene=str(scene or ""))
            except Exception:  # 规则表异常也不能让照片链路挂掉
                plan = None
        where = str(scene or "").strip() or scene_for(activity, kind)
        if not where and plan is not None:
            where = plan.scene
        if plan is not None:
            plan.where = where
            plan.debug["shot"] = "empty"
        if kind == "travel":
            head = prefix + (where or "风景区") + "，风景照，背景就是这个地点"
        elif kind == "shopping":
            head = prefix + (where or "商场街边") + "，街景，生活感，背景就是这个地点"
        elif kind == "weekend":
            head = prefix + (where or "周末出门走走") + "，街景，生活感，背景就是这个地点"
        elif kind == "outfit":
            # 自拍失败时的兜底：没有参考图就别硬画脸，只拍今天这套衣服
            head = prefix + "%s，%s的衣服细节，生活感" % (where or "家门口", self.outfit_today())
        else:
            # 日常：只留地点，不带「她在做什么」（否则和空镜打架）
            head = prefix + (where or "日常") + "，生活空镜，生活感，背景就是这个地点"
        light = _light_text(self, plan)
        # 空镜就是空镜：不再允许"画面边缘露出手/袖口"，否则会生成"空房间+一只手"
        outfit_line = ROOM_NO_BODY
        extras = [head, light, outfit_line]
        if plan is not None:
            extras.append(plan.guard)
            if getattr(plan, "room_ref", ""):
                try:
                    housing = getattr(self.plugin, "housing", None)
                    room = home_room_for_plan(plan, activity) or "living"
                    room_line = room_scene_line(housing, room) if housing is not None else ""
                    if room_line:
                        extras.append(room_line)
                    extras.append(room_lock_line(room, housing))
                except Exception as _amo_exc:
                    _note_swallowed("photo_engine.PhotoEngine._prompt_for.room", _amo_exc)
        extras.extend([SCENERY_ONLY, VIEW_POV])
        prompt = "，".join(part for part in extras if part).strip("，")
        if plan is not None:
            _log_photo_debug(self, activity, plan, prompt)
        return prompt

    def _pick_pose(self, zone=None):
        """从姿势库随机挑一个自拍姿势；没有姿势库/条目就返回 None。

        兼容旧调用；新链路（照片严格跟随日程）请用 ``pick_pose_for_plan()``，
        姿势由场景规则决定，公共场合不会随机到床姿。
        """
        library = getattr(getattr(self, "plugin", None), "pose", None)
        if library is None:
            return None
        try:
            return library.random_pose(zone)
        except TypeError:   # 老姿势库/测试桩只收 self
            return library.random_pose()
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._pick_pose", _amo_exc)
            return None

    def _pose_ref(self, pose):
        """姿势参考图路径（面板里生成过才有；没有就空串，不在这里现生成）。"""
        library = getattr(getattr(self, "plugin", None), "pose", None)
        if library is None or not pose:
            return ""
        try:
            return library.ref_for(pose)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._pose_ref", _amo_exc)
            return ""

    def _selfie_outfit_text(self, zone):
        """床/沙发场景只去掉鞋（避免自拍穿鞋上床）；外套必须留着。

        2026-11-17 FIX-3：外套被这里丢掉后，文字只说上衣/下装，和「最后几张
        平铺图」里的外套打架，模型会退回人设三视图的默认服装（米白开衫）。
        """
        worn: dict[str, Any] = {}
        try:
            wardrobe = getattr(self.plugin, "wardrobe", None)
            if wardrobe is not None:
                worn = wardrobe.current() or {}
        except Exception:
            worn: dict[str, Any] = {}
        keys = ("coat", "top", "skirt", "bottom")
        if zone not in ("home_bed", "home_sofa"):
            keys = ("coat", "top", "skirt", "bottom", "shoes")
        parts = [str(worn.get(key) or "").strip() for key in keys]
        return "、".join(part for part in parts if part) or self.outfit_today()

    def _selfie_prompt_for(self, kind, scene, activity, pose=None, plan=None, pose_text="",
                           safe=False):
        activity = dict(activity or {})
        if plan is None:
            try:
                plan = build_plan(activity, kind=kind, mode="photo", scene=str(scene or ""))
            except Exception:  # noqa: BLE001
                plan = None
        # 地点跟着日程走：在家=家里的客厅/卧室，逛街=商场，旅游=景区（带景点名）
        where = str(scene or "").strip() or scene_for(activity, kind) or (plan.scene if plan else "")
        if plan is not None:
            plan.where = where
            plan.debug["shot"] = "selfie"
        act = str(activity.get("activity") or "")
        # 姿势：只能由场景规则决定（居家才允许用姿势库）
        library = getattr(getattr(self, "plugin", None), "pose", None)
        if not pose_text:
            if pose is not None:
                pose_text = pose_line(pose)
            elif plan is not None and getattr(plan, "pose_ready", False):
                # 主链路（_share）已经把姿势写回计划：重试也用它，不再重摇一次
                if pose is None:
                    pose = plan.pose
                pose_text = plan.pose_line or ""
            elif plan is not None:
                used_pose, line = pick_pose_for_plan(plan, library)
                if used_pose is not None:
                    pose = used_pose
                pose_text = line or ""
        if not pose_text and plan is None and activity.get("zone"):
            # 规则表意外不可用时的最小兜底（仍按 zone，不回随机床姿）
            pose_text = pose_line_for(activity.get("zone"), activity)
        # 视角：她本人出镜 → 自拍视角（前置摄像头、人物占主体）
        light = _light_text(self, plan)
        if any(word in act for word in ("扎", "摸", "揉", "拿", "吃", "喝", "抱", "捧", "举",
                                        "抓", "捏", "擦", "洗", "穿", "脱", "整理", "拨", "翻",
                                        "打字", "梳")):
            act = "刚换好衣服，随手拍"
        # 姿势保留，但去掉容易触发内容政策的身体/皮肤描述
        pose_text = sanitize_selfie_prompt(pose_text)
        if safe:
            motion = "自然地看镜头，日常自拍姿态"
        else:
            motion = pose_text or act or "随手拍"
        camera_line = ("手机本体和摄像头不要入镜，不要出现手机背面或镜头对着画面；"
                       "姿态自然放松，不要夸张摆拍")
        where_line = "在%s，背景要能看出这个地方、不要换成别的场景" % (where or "不指定具体地点")
        zone = str(getattr(plan, "zone", "") or "")
        bed = zone in ("home_bed", "home_sofa") or any(
            word in (where + act) for word in ("床", "被窝", "沙发"))
        if hasattr(self, "_selfie_outfit_text"):
            outfit_text = self._selfie_outfit_text(zone if zone else ("home_bed" if bed else ""))
        else:
            outfit_text = self.outfit_today()
        extras = [where_line, motion, camera_line, HEADWEAR_LOCK, light]
        if bed:
            extras.append("她正躺在/坐在床上或沙发上，盖着被子；画面里绝对不要出现鞋")
        elif safe:
            pass
        else:
            try:
                worn = (self.plugin.wardrobe.current() or {}) if getattr(self.plugin, "wardrobe", None) else {}
            except Exception:
                worn: dict[str, Any] = {}
            if not safe and worn.get("sleep"):
                extras.append("穿着松软睡衣，没有外套、没有牛仔裤、没有硬挺的裤子")
            elif not safe and worn.get("homewear"):
                # B2b：具体单品已经写在上面「穿着%s」里；这里只补姿态，不再写第二句
                # 泛化服装——副脑会把它翻成模板「居家便服」，反而和单品打架。
                extras.append("姿态松弛，日常居家")
        if plan is not None:
            extras.append(plan.guard)
            try:
                housing = getattr(self.plugin, "housing", None)
                if housing is not None and str(plan.zone).startswith("home"):
                    room = home_room_for_plan(plan, activity) or "living"
                    room_line = housing.room_scene_line(room)
                    if room_line:
                        extras.append(room_line)
                    if getattr(plan, "room_ref", ""):
                        # A/B 才带房间参考图：锁同一间房的布局/光线，不锁像素
                        extras.append(room_lock_line(room, housing))
                        extras.append(ROOM_REF_SUBJECT_GUARD)
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.PhotoEngine._selfie_prompt_for.room", _amo_exc)
        else:
            extras.append(scene_guard(activity.get("zone")))
        state_line = ""
        if not safe:
            try:
                state_line = sanitize_selfie_prompt(selfie_state_hint(self, activity, plan))
            except Exception:
                state_line = ""
        if safe:
            # 安全重试：只留人设/穿搭/场景/日常姿势，不提浴室水汽和湿发
            safe_where = "家里" if any(word in where for word in
                                        ("浴室", "洗澡", "沐浴", "卫生间")) else where
            safe_where_line = "在%s，背景是真实的生活场景，不要换成别的场景" % (safe_where or "家里")
            safe_parts = [safe_where_line,
                          "日常自拍构图，人物自然出现在镜头前，画面以人物和当前场景为主",
                          "穿着%s，衣服就这一套、不要换成别的" % outfit_text,
                          HEADWEAR_LOCK,
                          light,
                          "自然生活照，生活感，不强调身体细节"]
            prompt = "自拍，手机前置摄像头日常自拍，" + "，".join(
                part for part in safe_parts if part)
        else:
            extras.extend([VIEW_SELFIE, state_line, SELFIE_FRAMING, SELFIE_STYLE])
            prompt = "自拍，第一人称自拍，举着手机自拍，穿着%s，衣服就这一套、不要换成别的，%s，生活感" % (
                outfit_text, "，".join(part for part in extras if part)
            )
        prompt = sanitize_selfie_prompt(prompt, safe=safe)
        if plan is not None:
            _log_photo_debug(self, activity, plan, prompt, pose_text)
        return prompt

    def _selfie_state_hint(self, activity, plan=None):
        return selfie_state_hint(self, activity, plan)

    def _place_words(self):
        words = list(PHOTO_PLACE_WORDS)
        extra = self._cfg_str("draw_scene_keywords", "")
        for item in extra.replace("，", ",").split(","):
            item = item.strip()
            if item and item not in words:
                words.append(item)
        return words

    def _place_from_text(self, text):
        text = str(text or "")
        for word in self._place_words():
            if word in text:
                return word
        return ""

    def _recent_place(self):
        """看最近聊天里她自己说的地点，比如「我在公园散步」。"""
        uid = ""
        try:
            profiles = self._profiles()
            if profiles:
                uid = str(profiles[0].get("uid") or "")
        except Exception:
            uid = ""
        if not uid:
            return ""
        try:
            rows = self.plugin.db.messages_by_uid(uid, 30) or []
        except Exception:
            return ""
        words = self._place_words()
        for row in reversed(rows):
            if str(row.get("role") or "") != "bot":
                continue
            content = str(row.get("content") or "")
            for word in words:
                if word in content:
                    return word
        return ""

    def _scene_place(self):
        """日程里她现在这个地方（具体场景，比如「家里的客厅，沙发、抱枕和茶几」）。

        F1：地点/活动先读 amo_fact_timeline.build_day_view 里正在进行的那条事实
        （块日记/账本已按 event_id 合并），空着的字段才退回 state.current_activity()。
        """
        activity = {}
        try:
            activity = dict(self.plugin.state.current_activity() or {})
        except Exception as _amo_exc:
            _note_swallowed("photo_prompt._scene_place.state", _amo_exc)
            activity = {}
        view_event = _day_view_event(getattr(self, "plugin", None))
        if view_event:
            for key in ("place", "activity"):
                if not str(activity.get(key) or "").strip():
                    activity[key] = str(view_event.get(key) or "")
        return scene_for(activity) if activity else ""

    def smart_prompt(self, text):
        """面板输入像「拍照给我看看」时，自动补上她此刻的地点 + 姿势 + 今天的穿搭。

        照片语义下日程是唯一真值：他输入里写的地点不再覆盖日程（冲突会记日志）；
        只有日程完全不明（0 点空窗且没有昨天可沿用）才用他说的地点兜底。
        """
        raw = str(text or "").strip()
        if not any(word in raw for word in PHOTO_INTENT_WORDS):
            return raw
        concrete = raw
        for word in PHOTO_INTENT_WORDS + ("照片", "给我", "看看", "你", "吧", "呀", "啊", "嘛",
                                          "呢", "吗", "？", "?", "，", ",", "。", "！", "!"):
            concrete = concrete.replace(word, "")
        concrete = concrete.strip()
        if len(concrete) >= 4:
            # 已经描述了具体画面（如「在公园和狗狗玩」）→ 当成自定义画图，不套日程
            return raw
        try:
            activity = self.plugin.state.current_activity() or {}
        except Exception:
            activity = {}
        explicit = self._place_from_text(raw)
        plan = build_plan(activity, mode="photo", explicit_place=explicit)
        where = scene_for(activity)
        if not where and plan.source == "explicit_place":
            where = explicit
        if not where and plan.source in ("no_schedule", "unknown_zone"):
            # 日程不明时才看他最近说过的地方（不是覆盖日程，只是兜底）
            where = self._recent_place() or ""
        where = where or plan.scene
        plan.where = where
        plan.debug["shot"] = "smart"
        library = getattr(getattr(self, "plugin", None), "pose", None)
        _pose, pose_text = pick_pose_for_plan(plan, library)
        parts = ["自拍"]
        outfit = self.outfit_today()
        if outfit:
            parts.append("穿着" + outfit)
        parts.append("在" + (where or "不指定具体地点"))
        parts.extend([
            "背景要能看出这个地方，不要换成别的场景",
            pose_text,
            plan.guard,
            _light_text(self, plan),
            "手机前置摄像头", "生活感",
            "人物长相参考模板，衣服锁定为上面的今日穿搭，不要换别的衣服，也不要照搬模板里的衣服",
        ])
        prompt = "，".join(part for part in parts if part)
        _log_photo_debug(self, activity, plan, prompt, pose_text)
        return prompt

    async def generate_prompt(self, prompt, send=False):
        """面板「按提示词生成」：画一张；send=True 时同时发到最近会话。"""
        text = self.smart_prompt(prompt)
        raw = str(prompt or "")
        # B2b：他点名衣服要用原始输入判断；smart_prompt 自己补的「穿着…」不算。
        explicit_clothes = any(word in raw for word in (
            "穿", "衣服", "裙", "衬衫", "衬衣", "毛衣", "外套", "大衣", "卫衣", "裤",
            "鞋", "靴", "T恤", "泳衣", "泳装", "睡衣", "吊带", "背心", "西装",
            "围巾", "帽子",
        ))
        locked_today = False
        if text and not any(word in text for word in (
            "穿", "衣服", "裙", "衬衫", "衬衣", "毛衣", "外套", "大衣", "卫衣", "裤",
            "鞋", "靴", "T恤", "泳衣", "泳装", "睡衣", "吊带", "背心", "西装",
            "围巾", "帽子",
        )):
            outfit = self.outfit_today()
            if outfit:
                text = "穿着%s，%s" % (outfit, text)
                locked_today = True
        if text and not any(word in text for word in ("二次元", "动漫", "插画")):
            prefix = self._cfg_str("photo_prompt_prefix", "").strip()
            if prefix:
                text = prefix + text
        if text and "不要照搬模板里的衣服" not in text:
            if locked_today:
                # 自动补的是今天锁定那一套，出图就别再换
                text += "；人物长相参考模板，衣服锁定为上面的今日穿搭，不要换别的衣服，也不要照搬模板里的衣服"
            else:
                # 他自己写了穿什么：参考图只借长相
                text += "；人物长相参考模板，衣服颜色和款式以文字描述为准，不要照搬模板里的衣服"
        wardrobe = getattr(self.plugin, "wardrobe", None)
        if (explicit_clothes and wardrobe is not None
                and hasattr(wardrobe, "outfit_lock_suspended")):
            # 他点名了衣服：本次生成期间让 omnidraw 的 OUTFIT LOCK 失效，
            # 否则 sidecar 里的今日穿搭（尤其居家便服）会把文字描述盖掉（B2b）。
            with wardrobe.outfit_lock_suspended("explicit_outfit"):
                item = await self.generate_custom(text)
        else:
            item = await self.generate_custom(text)
        if item and send:
            profiles = self._profiles()
            umo = self._target_umo(profiles[0]) if profiles else ""
            if umo:
                await self._send(umo, item)
        return item
