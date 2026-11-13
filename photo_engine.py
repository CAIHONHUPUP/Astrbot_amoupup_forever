# -*- coding: utf-8 -*-
"""照片分享引擎：旅游 / 逛街 / 周末 / 日常 / 早起穿搭。

她按日程过日子，这里只做一件事：在合适的「场景时刻」按配置概率，用万象画卷
（OmniDraw）生成一张照片发给他——要么是当地的街景/风景，要么是她自己的自拍。

- 概率都是 0-100 的整数，面板「照片」页可调；
- 每类时刻每天只掷一次骰子（``photo_roll:<日期>:<类别>:<地点>``），不会每 30 秒重掷；
- 每天总数受 ``photo_daily_cap`` 限制；
- 穿搭只在工作日早上出门那段时间触发，并且**一定是自拍**；
- 旅游/逛街/周末/日常按 ``photo_selfie_ratio`` 决定这张是自拍还是街景。

发送不依赖聊天事件：用 ``context.send_message(umo, chain)`` 主动发到最近活跃的会话。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import random
import time
import urllib.error
import urllib.request

from astrbot.api import logger

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
    from .amo_shared import now_ts, quiet_now, today_str
    from .clock import now as _clock_now
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed
    try:  # 用插件统一时区（服务器常在 UTC 跑）
        from .clock import now as _clock_now
    except Exception:  # pragma: no cover
        try:
            from clock import now as _clock_now
        except Exception:
            from datetime import datetime as _dt

            def _clock_now(*args, **kwargs):
                return _dt.now()
    try:
        from .amo_shared import now_ts, quiet_now, today_str
    except Exception:  # pragma: no cover
        try:
            from amo_shared import now_ts, quiet_now, today_str
        except Exception:
            import time as _time

            def now_ts():
                return _time.time()

            def today_str():
                return _clock_now().date().isoformat()

            def quiet_now(_spec):
                return False

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_image_common import (
        MessageChain,
        apply_persona_config,
        cfg,
        clean_place,
        find_omni,
        image_component,
        spawn,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_image_common import (
            MessageChain,
            apply_persona_config,
            cfg,
            clean_place,
            find_omni,
            image_component,
            spawn,
        )
    except ImportError:  # 直接按文件加载时
        from amo_image_common import (
            MessageChain,
            apply_persona_config,
            cfg,
            clean_place,
            find_omni,
            image_component,
            spawn,
        )

try:  # 视角后缀：她出镜=自拍视角，她拍环境=她的拍摄视角（空镜）
    from .photo_view import SCENERY_ONLY, VIEW_POV, VIEW_SELFIE
except ImportError:  # 直接按文件加载时
    from photo_view import SCENERY_ONLY, VIEW_POV, VIEW_SELFIE

try:  # 场景规则表（照片严格跟随日程的唯一真值）
    from .photo_rules import (
        locks_for_plan,
        pick_pose_for_plan,
        resolve_plan,
    )
except ImportError:  # 直接按文件加载时
    from photo_rules import (
        locks_for_plan,
        pick_pose_for_plan,
        resolve_plan,
    )


def _light_text(engine, plan=None):
    """按场景取光线描述：室外夜景不能写“室内暖灯”（测试桩只收 self 时回退）。"""
    outdoor = False
    if plan is not None:
        outdoor = not str(plan.zone or "").startswith(
            ("home_", "office", "restaurant", "mall", "hotel", "station", "hospital", "unknown")
        )
    getter = getattr(engine, "_time_light", None)
    if not callable(getter):
        return "自然光"
    try:
        return getter(outdoor=outdoor)
    except TypeError:
        try:
            return getter()
        except Exception:
            return "自然光"
    except Exception:
        return "自然光"


def _log_photo_debug(engine, activity, plan, final_prompt, pose_text=""):
    """生图链路调试日志（模块级：测试里的假引擎也能用）。"""
    try:
        enabled = bool(getattr(engine, "debug_prompt", False))
    except Exception:
        enabled = False
    if not enabled:
        return
    data = activity if isinstance(activity, dict) else {}
    act_text = " | ".join(filter(None, [
        str(data.get("activity") or ""),
        str(data.get("place") or ""),
        str(data.get("category") or ""),
        str(data.get("transit") or ""),
        str(data.get("vehicle") or ""),
    ]))
    logger.info(
        "[PHOTO_DEBUG] 日程=%s zone=%s pose_rule=%s guard=%s final_prompt=%s"
        % (act_text or "-", plan.zone, plan.pose_rule or pose_text or "-",
           plan.guard, final_prompt)
    )
    if plan.source not in ("schedule",) or plan.explicit_conflict or plan.repairs:
        logger.info(
            "[PHOTO_DEBUG] source=%s reason=%s explicit_place=%s conflict=%s repairs=%s"
            % (plan.source, plan.reason or "-", plan.explicit_place or "-",
               plan.explicit_conflict, plan.repairs or [])
        )
    scene_en, pose_en = locks_for_plan(plan)
    if scene_en or pose_en:
        logger.info(
            "[PHOTO_DEBUG] locks scene_lock=%s pose_lock=%s"
            % (scene_en or "-", pose_en or "-")
        )


#: 户外/非居家场景的兜底姿势（姿势库那 9 个都是床上的，出门照搬会穿帮）
OUTDOOR_POSES = {
    "road": "站在车厢里，一只手扶着头顶的扶手或吊环，身体微微侧着看镜头",
    "street": "站在路边，身体侧一点，回头看向镜头",
    "mall": "站在橱窗前，一只手自然垂着，微微侧身",
    "office": "站在工位旁，一只手搭在椅背上，微微侧身",
    "scenic": "站在景点前，抬手挡了一下阳光，头发被风吹起",
    "restaurant": "坐在餐桌旁，双手放在桌上，正对镜头",
    "station": "站在候车厅里，一只手扶着行李箱拉杆",
    "hospital": "坐在候诊椅上，双手放在腿上，正对镜头",
    "hotel": "坐在床边，一只手撑在身侧，身体微微后仰看镜头",
}


def outdoor_pose_line(zone):
    """不在家时用的姿势句子。"""
    return OUTDOOR_POSES.get(str(zone or ""), "站着，身体微微侧向镜头")


#: 场景限制（结构化）：提示词和测试都从这里取，别去断言提示词字符串。
SCENE_POLICY = {
    "road": {
        "poses": ["standing", "hold_strap"],
        "banned_postures": ["kneel", "sit_on_seat", "cross_leg"],
        "banned_props": ["bedding", "instant_noodles", "thermos", "tableware", "food"],
        "seat": "hard_bench",
    },
    "default": {
        "poses": ["standing", "sit"],
        "banned_postures": [],
        "banned_props": ["bedding", "tableware"],
        "seat": "",
    },
}

_PROP_WORDS = {"bedding": "床铺被褥", "instant_noodles": "泡面", "thermos": "保温杯",
               "tableware": "餐具", "food": "食物"}
_POSTURE_WORDS = {"kneel": "跪坐", "sit_on_seat": "坐在座位上", "cross_leg": "盘腿坐"}



# ---- 2026-11-12：路上再细分（地铁/公交/私家车），姿势跟着交通工具走 ----
_RIDE_WORDS = {
    "car": ("开车", "打车", "taxi", "网约", "车里", "驾驶", "副驾", "车上"),
    "bus": ("公交", "大巴", "巴士"),
    "subway": ("地铁", "subway", "metro", "车厢"),
}


def road_ride(activity):
    """这一小时在路上是坐什么：car / bus / subway / ''。"""
    data = activity if isinstance(activity, dict) else {}
    act = str(data.get("activity") or "")
    transit = str(data.get("transit") or "")
    vehicle = str(data.get("vehicle") or "")
    text = act + transit + vehicle
    for kind, words in _RIDE_WORDS.items():
        if any(word in text for word in words):
            return kind
    return ""


def pose_line_for(zone, activity):
    """不靠姿势库时的姿势句：酒店坐床边、私家车系安全带、地铁扶杆、其余站立。"""
    zone = str(zone or "")
    if zone == "road":
        ride = road_ride(activity)
        if ride == "car":
            return "坐在座位上系好安全带，一只手举手机自拍，不要站起来"
        if ride == "bus":
            return "坐在公交车硬质座椅上，一只手扶着前座靠背，身体微微侧向镜头"
        return "站在车厢里，一只手扶着头顶的扶手或吊环，身体微微侧着看镜头"
    return OUTDOOR_POSES.get(zone, "站着，身体微微侧向镜头")

def scene_policy(zone):
    """这个场景允许/禁止什么（结构化；测试断言字段，不测提示词）。"""
    return SCENE_POLICY.get(str(zone or ""), SCENE_POLICY["default"])


def scene_guard(zone):
    """把 scene_policy 翻成提示词里的限制。

    2026-11-12：路上不再写“不要X”——万象画卷的副脑只收正向描述、会重写提示词，
    “不要泡面”容易被它理解成“有泡面”。改成把环境写死（只存在什么），让它没得发挥。
    """
    if str(zone or "") == "road":
        return (
            "画面里只有车厢或路面上本来就有的东西：座椅、扶手、吊环、车窗、街景、乘客；"
            "地面和座椅干净，只有这些东西，没有别的物件"
        )
    policy = scene_policy(zone)
    parts = []
    for key in policy.get("banned_postures") or []:
        parts.append("不要" + _POSTURE_WORDS.get(key, key))
    for key in policy.get("banned_props") or []:
        parts.append("不要出现" + _PROP_WORDS.get(key, key))
    if not parts:
        return "画面里只出现这个地点本来就有的东西"
    return "；".join(parts) + "；画面里只出现这个地点本来就有的东西"

try:  # 场景：日程地点 → 背景（在家=家里、逛街=商场、旅游=景区）
    from .photo_scene import (
        OUTFIT_CATEGORY,
        SHOPPING_WORDS,
        TRAVEL_WORDS,
        matches_kind,
        scene_for,
        zone_of,
    )
except ImportError:  # 直接按文件加载时
    from photo_scene import (
        OUTFIT_CATEGORY,
        SHOPPING_WORDS,
        TRAVEL_WORDS,
        matches_kind,
        scene_for,
        zone_of,
    )

try:  # 自拍姿势库：随机挑一个姿势进提示词
    from .pose_library import pose_line
except ImportError:  # 直接按文件加载时
    try:
        from pose_library import pose_line
    except Exception:  # pragma: no cover
        def pose_line(pose):
            return ""

# 生图可能卡住/返回超大图：都设上限，别把事件循环和数据库拖死
GENERATE_TIMEOUT_SECONDS = 180
_MAX_IMAGE_BYTES = 16 * 1024 * 1024


# 同一天所有自拍都用同一套穿搭；按日期稳定挑选，换场景也不换衣服
OUTFIT_POOL = (
    "米色针织衫配牛仔裤",
    "白色衬衫配深色半裙",
    "浅灰卫衣配运动裤",
    "奶油色开衫配直筒裤",
    "黑色小高领配卡其色半裙",
    "牛仔外套配白色T恤和长裤",
    "浅蓝衬衫配米白阔腿裤",
    "粉色卫衣配牛仔裙",
    "燕麦色毛衣配深色半裙",
    "条纹长袖配浅色牛仔裤",
)


def _int_in(value, low, high, default):
    try:
        number = int(float(value))
    except Exception:
        return default
    return max(low, min(high, number))


PHOTO_INTENT_WORDS = (
    "拍照", "拍张", "拍个", "拍给我", "拍来看", "照片", "自拍",
    "看看你", "看看你在", "你在哪", "在哪呢",
)
PHOTO_PLACE_WORDS = (
    "公园", "商场", "咖啡", "学校", "公司", "家里", "街上", "海边", "餐厅",
    "超市", "地铁", "床上", "办公室", "图书馆", "电影院", "游乐园", "动物园",
    "植物园", "江边", "河边", "山上", "健身房", "教室", "宿舍", "路边", "小区",
)


class PhotoEngine:
    VIEWS = ("front", "side", "full")
    VIEW_LABELS = {"front": "正面", "side": "侧面", "full": "全身"}
    def __init__(self, plugin):
        self.plugin = plugin
        self._busy = False
        self._omni = None
        self._omni_checked = 0.0
        self._last_check = 0.0
        self._outfit_day = ""
        self._outfit_text = ""
        self._stats = {"sent": 0, "failed": 0, "rolled": 0}

    def _spawn(self, coro):
        return spawn(self.plugin, coro, "photo_engine.PhotoEngine._spawn")

    # ---------- 配置 ----------

    def _cfg_bool(self, key, default=False):
        return cfg(self.plugin, key, default, bool, "photo_engine.PhotoEngine._cfg_bool")

    def _cfg_int(self, key, default=0):
        return cfg(self.plugin, key, default, int, "photo_engine.PhotoEngine._cfg_int")

    def _cfg_str(self, key, default=""):
        return cfg(self.plugin, key, default, str, "photo_engine.PhotoEngine._cfg_str")

    @property
    def enabled(self):
        return self._cfg_bool("photo_enable", False)

    @property
    def debug(self):
        return self._cfg_bool("photo_debug", False)

    @property
    def debug_prompt(self):
        """生图链路调试开关：debug_prompt 或旧 photo_debug 任一打开都打 [PHOTO_DEBUG]。"""
        if self._cfg_bool("debug_prompt", False):
            return True
        return bool(self.debug)

    def _debug_log(self, activity, plan, final_prompt, pose_text=""):
        if not self.debug_prompt:
            return
        data = activity if isinstance(activity, dict) else {}
        act_text = " | ".join(filter(None, [
            str(data.get("activity") or ""),
            str(data.get("place") or ""),
            str(data.get("category") or ""),
            str(data.get("transit") or ""),
            str(data.get("vehicle") or ""),
        ]))
        logger.info(
            "[PHOTO_DEBUG] 日程=%s zone=%s pose_rule=%s guard=%s final_prompt=%s"
            % (act_text or "-", plan.zone, plan.pose_rule or pose_text or "-",
               plan.guard, final_prompt)
        )
        if plan.source not in ("schedule",) or plan.explicit_conflict or plan.repairs:
            logger.info(
                "[PHOTO_DEBUG] source=%s reason=%s explicit_place=%s conflict=%s repairs=%s"
                % (plan.source, plan.reason or "-", plan.explicit_place or "-",
                   plan.explicit_conflict, plan.repairs or [])
            )

    def _prob(self, key, default):
        return _int_in(self._cfg_int(key, default), 0, 100, default)

    # ---------- 日期 / 计数 ----------

    def _count_key(self, day):
        return "photo_count:%s" % day

    def today_count(self):
        try:
            return int(self.plugin.db.kv_get(self._count_key(today_str()), 0) or 0)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.today_count", _amo_exc)
            return 0

    def _bump_count(self):
        try:
            self.plugin.db.kv_set(self._count_key(today_str()), self.today_count() + 1)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._bump_count", _amo_exc)

    # ---------- 收件人 ----------

    def _owner_uid_ok(self, uid, profile):
        raw = self._cfg_str("photo_owner_ids", "")
        owners = {item.strip() for item in raw.replace("，", ",").split(",") if item.strip()}
        if owners:
            # 明确配了名单就只认名单，不再按关系放行
            return bool(uid) and uid in owners
        try:
            if uid and uid in getattr(self.plugin, "admin_uids", set()):
                return True
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._owner_uid_ok", _amo_exc)
        return str((profile or {}).get("relation") or "") == "相恋"

    def _profiles(self):
        try:
            rows = self.plugin.db.proactive_profiles(limit=50) or []
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._profiles", _amo_exc)
            rows = []
        picked = []
        for profile in rows:
            uid = str(profile.get("uid") or "")
            if not uid:
                continue
            if self._owner_uid_ok(uid, profile):
                picked.append(profile)
        if picked:
            return picked
        # 明确配了 photo_owner_ids 但一个都没匹配上：宁可不发，也不发给别人
        if self._cfg_str("photo_owner_ids", "").strip():
            return []
        # 没配名单、也没有相恋/管理员：只发她的主页私聊，不要发给陌生私聊
        home_umo = ""
        try:
            getter = getattr(self.plugin, "_first_private_umo", None)
            if callable(getter):
                home_umo = str(getter() or "")
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._profiles", _amo_exc)
        if not home_umo:
            return []
        return [row for row in rows if str(row.get("private_umo") or "") == home_umo]

    def _target_umo(self, profile):
        try:
            return str(self.plugin._pick_proactive_umo(profile) or "")
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._target_umo", _amo_exc)
            return str(profile.get("private_umo") or "")

    # ---------- 时刻判断 ----------

    def _now_parts(self):
        now = _clock_now()
        return now.weekday(), now.hour * 60 + now.minute

    def _activity(self):
        try:
            return self.plugin.state.current_activity() or {}
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._activity", _amo_exc)
            return {}

    def _worn_text(self):
        gift = getattr(self.plugin, "gift", None)
        getter = getattr(gift, "worn_text", None) if gift is not None else None
        if callable(getter):
            try:
                return str(getter() or "")
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.PhotoEngine._worn_text", _amo_exc)
        return ""

    def _candidates(self):
        """当前这一刻有哪些可分享的场景；返回 [(kind, scene, prob, key)]。"""
        activity = self._activity()
        act = str(activity.get("activity") or "")
        place = str(activity.get("place") or "")
        # 用 zone_of 而不是裸 zone：0 点/兜底 dict 没 zone 时也能靠地点反推
        zone = zone_of(activity)
        category = str(activity.get("category") or "")
        weekday, minutes = self._now_parts()
        blob = act + place + zone
        out = []

        if weekday < 5 and 7 * 60 <= minutes <= 9 * 60 + 30 and (
            zone == "road" or category in OUTFIT_CATEGORY or "上班" in blob or "通勤" in blob
        ):
            out.append(("outfit", "", self._prob("photo_outfit_prob", 70), "outfit"))

        scenic = zone == "scenic" or any(word in blob for word in TRAVEL_WORDS)
        mall = zone == "mall" or any(word in blob for word in SHOPPING_WORDS)
        if scenic:
            # scene 留空：真正的地点由 scene_for(activity) 从日程里取，
            # 以前拼的「地点·活动」会把「逛景区：看猴子」这种动作塞进空镜
            out.append(("travel", "", self._prob("photo_travel_prob", 60),
                        "travel:" + (place or act)))
        elif mall:
            out.append(("shopping", "", self._prob("photo_shopping_prob", 40),
                        "shopping:" + (place or act)))

        if weekday >= 5 and 10 * 60 <= minutes <= 20 * 60:
            out.append(("weekend", "", self._prob("photo_weekend_prob", 50), "weekend"))

        if 9 * 60 <= minutes <= 21 * 60:
            # 有明确场景（旅游/逛街/周末）时不再补一张泛泛的日常照
            if not out:
                out.append(("daily", "", self._prob("photo_daily_prob", 25), "daily"))
        return out

    def _roll_key(self, kind, key):
        return "photo_roll:%s:%s:%s" % (today_str(), kind, key or "-")

    # ---------- 主循环 ----------

    def _cache_dir(self):
        base = os.path.dirname(str(getattr(self.plugin.db, "path", "") or "")) or os.getcwd()
        path = os.path.join(base, "photo_cache")
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._cache_dir", _amo_exc)
        return path

    def cleanup_cache(self):
        """只清理生图缓存；模板、聊天记录不动。"""
        days = self._cfg_int("photo_cache_days", 7)
        if days <= 0:
            return 0
        try:
            last = float(self.plugin.db.kv_get("photo_cache_cleanup", 0) or 0)
        except Exception:
            last = 0
        if time.time() - last < 3600:
            return 0
        cutoff = time.time() - days * 86400
        removed = 0
        cache_dir = self._cache_dir()
        try:
            for name in os.listdir(cache_dir):
                path = os.path.join(cache_dir, name)
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except Exception:
                    pass
            self.plugin.db.kv_set("photo_cache_cleanup", time.time())
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.cleanup_cache", _amo_exc)
        if removed:
            logger.info("[amo] 清理旧生图缓存 %d 张（保留 %d 天）" % (removed, days))
        return removed

    def on_schedule_changed(self, day="", version=0, reason="schedule_changed"):
        """日程变更事件：生图下一次会重新读 current_activity；这里留可验收日志。"""
        logger.info("[PHOTO_DEBUG] reason=schedule_changed day=%s version=%s source=%s",
                    day or "-", version or "-", reason or "-")

    async def tick(self):
        try:
            self.cleanup_cache()
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.tick", _amo_exc)
        if not self.enabled or self._busy:
            logger.debug("[PHOTO] tick skip reason=%s",
                         "busy" if self._busy else "photo_enable=false")
            return
        now = time.monotonic()
        if now - self._last_check < 20:
            return
        self._last_check = now
        try:
            if quiet_now(self._cfg_str("photo_quiet_hours", "23:00-07:00")):
                logger.debug("[PHOTO] tick skip reason=quiet_hours")
                return
            cap = max(0, self._cfg_int("photo_daily_cap", 4))
            if self.today_count() >= cap:
                logger.debug("[PHOTO] tick skip reason=daily_cap count=%s", cap)
                return
            profiles = self._profiles()
            if not profiles:
                logger.debug("[PHOTO] tick skip reason=no_owner_profile")
                return
            for kind, scene, prob, key in self._candidates():
                roll_key = self._roll_key(kind, key)
                try:
                    if self.plugin.db.kv_get(roll_key, 0):
                        continue
                    self.plugin.db.kv_set(roll_key, 1)
                except Exception as _amo_exc:
                    _note_swallowed("photo_engine.PhotoEngine.tick", _amo_exc)
                    continue
                self._stats["rolled"] += 1
                if random.random() * 100 >= prob:
                    if self.debug:
                        logger.info("[amo] 照片：%s 掷骰没中（%d%%）" % (kind, prob))
                    continue
                profile = random.choice(profiles)
                self._busy = True
                # 生图要几十秒，不能卡在 30 秒一轮的 tick 里；发完再放开
                if self._spawn(self._run_share(kind, scene, profile)) is None:
                    self._busy = False
                return
        except Exception as exc:
            logger.debug("[amo] 照片任务失败: %s" % exc)

    async def _run_share(self, kind, scene, profile):
        try:
            if not self.enabled:
                return
            cap = max(0, self._cfg_int("photo_daily_cap", 4))
            if self.today_count() >= cap:
                return
            ok = await self._share(kind, scene, profile)
            if not ok:
                await self._notify_failure(self._target_umo(profile), "share_failed")
        finally:
            self._busy = False

    async def _notify_failure(self, umo, reason=""):
        """按日程分享失败时补一句；同一会话 10 分钟只提醒一次。"""
        if not umo or MessageChain is None:
            return
        try:
            store = getattr(self, "_failure_notice_at", None)
            if not isinstance(store, dict):
                store = {}
                self._failure_notice_at = store
            now = time.monotonic()
            last = store.get(umo)
            if last is not None and now - last < 600:
                return
            store[umo] = now
            logger.warning("[PHOTO] failure_notice=1 reason=%s", str(reason or "")[:80])
            try:
                from astrbot.api.message_components import Plain
            except Exception:
                from astrbot.api.event.components import Plain
            await self.plugin.context.send_message(
                umo, MessageChain([Plain(text="这次照片没拍成，等会儿再给你拍。")])
            )
        except Exception as exc:
            logger.warning("[PHOTO] failure_notice send failed: %s", exc)

    # ---------- 生成 / 发送 ----------

    @staticmethod
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

    def _prompt_for(self, kind, scene, activity):
        """非自拍：她拍的环境。一律空镜 + 她的拍摄视角，画面里不出现她本人。

        以前这里会把「她在做什么」原样拼进去（比如「边对边打哈欠」），
        那句话要求画面里有个人，和「不出现她」打架，模型只能自己现编一张脸
        —— 2026-11-11 那批「长相画风全变」的照片就是这么来的。

        地点统一走 ``scene_for()``：日程说在家就出家里的陈设，说逛街就出商场，
        旅游就出景区（带景点名），不再拿「他桌面上」这种词去糊。
        2026-11-12：守卫词也进空镜提示词（以前只有自拍带，空镜完全没约束）。
        """
        prefix = self._cfg_str("photo_prompt_prefix", "手机随手拍，真实自然，生活感，")
        activity = dict(activity or {})
        try:
            plan = resolve_plan(activity, kind=kind, mode="photo", scene_hint=str(scene or ""))
        except Exception:  # 规则表异常也不能让照片链路挂掉
            plan = None
        where = str(scene or "").strip() or scene_for(activity, kind)
        if not where and plan is not None:
            where = plan.scene
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
        # 她拍的空镜里她本人不出镜，但最多露出的手/袖口仍要跟今天的穿搭一致
        outfit_line = "她今天穿的是%s，画面边缘露出的手或袖口必须和这套一致" % self.outfit_today()
        extras = [head, light, outfit_line]
        if plan is not None:
            extras.append(plan.guard)
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

    def _selfie_prompt_for(self, kind, scene, activity, pose=None, plan=None, pose_text=""):
        activity = dict(activity or {})
        if plan is None:
            try:
                plan = resolve_plan(activity, kind=kind, mode="photo", scene_hint=str(scene or ""))
            except Exception:  # noqa: BLE001
                plan = None
        # 地点跟着日程走：在家=家里的客厅/卧室，逛街=商场，旅游=景区（带景点名）
        where = str(scene or "").strip() or scene_for(activity, kind) or (plan.scene if plan else "")
        act = str(activity.get("activity") or "")
        # 姿势：只能由场景规则决定（居家才允许用姿势库）
        library = getattr(getattr(self, "plugin", None), "pose", None)
        if not pose_text:
            if pose is not None:
                pose_text = pose_line(pose)
            elif plan is not None:
                used_pose, line = pick_pose_for_plan(plan, library)
                if used_pose is not None:
                    pose = used_pose
                pose_text = line or ""
        if not pose_text and plan is None and activity.get("zone"):
            # 规则表意外不可用时的最小兜底（仍按 zone，不回随机床姿）
            pose_text = pose_line_for(activity.get("zone"), activity)
        # 视角：她本人出镜 → 自拍视角（前置摄像头、手臂伸出、人物占主体）
        light = _light_text(self, plan)
        # 自拍最容易出的问题是两只手都入镜、手腕错位；动作里要用手时也让另一只手自然放好
        if any(word in act for word in ("扎", "摸", "揉", "拿", "吃", "喝", "抱", "捧", "举",
                                        "抓", "捏", "擦", "洗", "穿", "脱", "整理", "拨", "翻",
                                        "打字", "梳")):
            act = "刚换好衣服，随手拍"
        motion = pose_text or act or "随手拍"
        hand_line = ("只有一只手举手机，另一只手自然垂下、不要出现在镜头前，不要多手或手腕错位；"
                     "手机本体和摄像头不要入镜，不要出现手机背面或镜头对着画面")
        where_line = "在%s，背景要能看出这个地方、不要换成别的场景" % (where or "不指定具体地点")
        extras = [where_line, motion, hand_line, light]
        if plan is not None:
            extras.append(plan.guard)
        else:
            extras.append(scene_guard(activity.get("zone")))
        extras.append(VIEW_SELFIE)
        prompt = "自拍，穿着%s，衣服就这一套、不要换成别的，%s，生活感" % (
            self.outfit_today(), "，".join(part for part in extras if part)
        )
        if plan is not None:
            _log_photo_debug(self, activity, plan, prompt, pose_text)
        return prompt

    def _find_omni(self):
        return find_omni(self.plugin, self, "photo_engine.PhotoEngine._find_omni")

    def _selfie_ready(self, omni):
        """她能不能出「自拍」：看万象画卷里有没有她的形象。

        不同版本的 OmniDraw 把形象放在不同地方（踩过坑：只认老字段时，
        新版照样有形象，但这里判成「没有」→ 自拍被降级成街景）：
        - 老版：``plugin_config.persona_ref_image`` / ``persona_base_prompt``；
        - 新版：``plugin_config.personas``（每项带 ``ref_images`` / 基底提示词），
          配置里落成 JSON 的 ``persona_config``；
        - 都没有时，看我们自己 ``templates/`` 里有没有三视图。
        """
        try:
            conf = getattr(omni, "plugin_config", None)
            if conf is not None:
                for key in ("persona_ref_image", "persona_base_prompt"):
                    if str(getattr(conf, key, "") or "").strip():
                        return True
                for profile in (getattr(conf, "personas", None) or []):
                    for key in ("ref_images", "persona_ref_image", "ref_image"):
                        value = getattr(profile, key, None)
                        if isinstance(value, (list, tuple, set)):
                            if any(str(item).strip() for item in value):
                                return True
                        elif str(value or "").strip():
                            return True
                    if str(getattr(profile, "persona_base_prompt", "") or "").strip():
                        return True
                raw = str(getattr(conf, "persona_config", "") or "").strip()
                if raw:
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = {}
                    if isinstance(data, dict):
                        active = str(data.get("active_persona_id") or "")
                        for profile in (data.get("profiles") or []):
                            if not isinstance(profile, dict):
                                continue
                            if active and str(profile.get("id") or "") not in ("", active):
                                continue
                            if (str(profile.get("persona_ref_image") or "").strip()
                                    or str(profile.get("persona_base_prompt") or "").strip()):
                                return True
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._selfie_ready", _amo_exc)
        # 最后看我们自己的模板：OmniDraw 那边没配形象时，三视图仍然是可用的参考
        try:
            return bool(self.persona_refs())
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._selfie_ready", _amo_exc)
            return False

    def _pick_selfie(self, kind):
        # outfit/selfie 是明确要她出镜；不要走概率抽到街景
        if kind in ("outfit", "selfie"):
            return True
        ratio = self._prob("photo_selfie_ratio", 50)
        return random.random() * 100 < ratio

    async def _generate(self, omni, prompt, selfie, pose=None, plan=None):
        refs = []
        pose_ref = ""
        if selfie:
            try:
                outfit: dict[Any, Any] = {}
                wardrobe = getattr(self.plugin, "wardrobe", None)
                if wardrobe is not None:
                    outfit = wardrobe.current() or {}
                names = [outfit.get(key) for key in ("coat", "top", "skirt", "bottom", "shoes")
                         if outfit.get(key)]
                if not names:
                    text = str(self.outfit_today() or "")
                    names = [part.strip() for part in text.split("，") if part.strip()]
                if names:
                    from .outfit_refs import ensure_many
                    refs = await ensure_many(self.plugin, names, limit=4)
            except Exception as exc:
                logger.debug("[amo] 服装参考图准备失败: %s" % exc)
                refs = []
            # 姿势参考图：面板里生成过的才有，放在服装参考图前面（它是「动作」参考）
            try:
                pose_ref = self._pose_ref(pose)
            except Exception as exc:
                logger.debug("[amo] 姿势参考图准备失败: %s" % exc)
                pose_ref = ""
            if pose_ref:
                refs = [pose_ref] + list(refs)
        args = {"prompt": prompt, "count": 1, "record_usage": True}
        if refs:
            args["refs"] = refs
            args["allow_local_refs"] = True
            hint = "（服装参考：参考图最后几张是今天这套衣服的平铺图，衣服颜色和款式必须和它一致，不能改；长相和发型仍按人设三视图。"
            if pose_ref:
                hint += "参考图第一张是姿势参考，只学动作和手的位置，不要照搬那张的脸和衣服。"
            args["prompt"] = prompt + "\n" + hint + "）"
        if selfie:
            args["mode"] = "selfie"
        # 场景/姿势锁：交给万象画卷副脑，解析后强制回填，避免它把日程场景改写掉
        scene_lock = pose_lock = ""
        if plan is not None:
            try:
                scene_lock, pose_lock = locks_for_plan(plan)
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.PhotoEngine._generate.locks", _amo_exc)
                scene_lock = pose_lock = ""
        if scene_lock:
            args["scene_lock"] = scene_lock
        if pose_lock:
            args["pose_lock"] = pose_lock
        try:
            result = await asyncio.wait_for(
                omni.generate_images_for_plugin(**args), timeout=GENERATE_TIMEOUT_SECONDS)
        except TypeError as exc:
            # 旧版万象画卷没有 scene_lock/pose_lock：去掉锁参数重试一次，别让照片直接失败
            had_scene = args.pop("scene_lock", None) is not None
            had_pose = args.pop("pose_lock", None) is not None
            if had_scene or had_pose:
                logger.warning("[amo] 万象画卷不支持场景锁，已退回普通提示词: %s" % exc)
                result = await asyncio.wait_for(
                    omni.generate_images_for_plugin(**args), timeout=GENERATE_TIMEOUT_SECONDS)
            else:
                raise
        except asyncio.TimeoutError:
            logger.warning("[amo] 照片生成超时（%d 秒）" % GENERATE_TIMEOUT_SECONDS)
            return None
        except Exception as exc:
            logger.warning("[amo] 照片生成失败（%s）: %s" % ("自拍" if selfie else "街景", exc))
            return None
        if isinstance(result, dict) and result.get("success") and result.get("images"):
            return result["images"][0]
        message = ""
        if isinstance(result, dict):
            message = str(result.get("message") or "")
        logger.warning("[amo] 照片没出来：%s" % (message or "未知原因"))
        return None

    @staticmethod
    def _image_component(item):
        return image_component(item)

    async def _send(self, umo, item):
        if not umo or MessageChain is None:
            return False
        component = self._image_component(item)
        if component is None:
            return False
        try:
            await self.plugin.context.send_message(umo, MessageChain([component]))
            return True
        except Exception as exc:
            logger.warning("[amo] 照片发送失败: %s" % exc)
            return False

    async def _share(self, kind, scene, profile, target_umo="", lock_scene=True):
        activity = self._activity()
        omni = self._find_omni()
        if omni is None:
            logger.warning("[amo] 没有万象画卷，照片发不出去。")
            return False
        # 一次生图先把“日程 → zone → 姿势/守卫”定型，提示词和姿势参考图共用同一份计划
        try:
            plan = resolve_plan(activity, kind=kind, mode="photo", scene_hint=str(scene or ""))
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._share.plan", _amo_exc)
            plan = None
        selfie = self._pick_selfie(kind)
        if selfie and not self._selfie_ready(omni):
            selfie = False
        # 自拍：姿势由场景规则决定（居家才可能用姿势库），公共场合不会随机到床姿
        pose = None
        pose_text = ""
        if selfie and plan is not None:
            library = getattr(getattr(self, "plugin", None), "pose", None)
            pose, pose_text = pick_pose_for_plan(plan, library)
        prompt = (self._selfie_prompt_for(kind, scene, activity, pose=pose, plan=plan,
                                          pose_text=pose_text) if selfie
                  else self._prompt_for(kind, scene, activity))
        if self._cfg_bool("photo_dry_run", False):
            logger.info("[amo] 照片（dry-run）%s：%s" % ("自拍" if selfie else "街景", prompt))
            return False
        started = time.monotonic()
        item = await self._generate(omni, prompt, selfie, pose=pose,
                                    plan=plan if lock_scene else None)
        if not item and selfie:
            # 自拍链路失败（没参考图/节点不支持）→ 退回空镜，别让这一次白掷；
            # 注意不能退回 selfie 提示词，那样会画出一张「没有她的自拍」
            item = await self._generate(omni, self._prompt_for("scenery", scene, activity), False)
        if not item:
            self._stats["failed"] += 1
            return False
        localized = await self._localize(item)
        if localized is not item:
            item = localized
        logger.info("[amo] 照片耗时 %.1fs（生成+本地化，%s）" % (
            time.monotonic() - started, "自拍" if selfie else "街景"))
        umo = str(target_umo or "") or self._target_umo(profile)
        if not await self._send(umo, item):
            self._stats["failed"] += 1
            return False
        self._bump_count()
        self._stats["sent"] += 1
        try:
            remember = getattr(self.plugin, "_remember_bot_media", None)
            if callable(remember):
                scene_text = ""
                if isinstance(scene, dict):
                    scene_text = str(scene.get("place") or scene.get("zone") or "")
                marker = "[我发的照片] %s%s" % (
                    "自拍" if selfie else "随手拍",
                    ("，背景：" + scene_text) if scene_text else "",
                )
                await remember(umo, marker)
        except Exception as exc:
            logger.debug("[amo] 记录照片失败: %s" % exc)
        logger.info("[amo] 照片已分享（%s/%s）：%s" % (kind, "自拍" if selfie else "街景", prompt))
        return True

    @staticmethod
    def _scene_for_kind(kind, place="", activity=None):
        """测试/手动发图时按类别给明确场景，避免“旅游”沿用当前在家的地点。

        自动发照片那条链路不传 ``scene``，地点完全由日程决定；这里是面板
        「发一张旅游照/逛街照」用的：类别和当前日程对不上时，给该类别的固定场景。
        """
        kind = str(kind or "daily")
        data = dict(activity) if isinstance(activity, dict) else {}
        if place and not data.get("place"):
            data["place"] = place
        if kind in ("travel", "shopping", "weekend") and not matches_kind(kind, data):
            return scene_for({}, kind)
        return scene_for(data, kind)

    async def share_now(self, kind="daily", umo=""):
        """面板「测试」/``/amo photo test``：无视概率，立刻按类别发一张。"""
        if not self.enabled:
            return "照片功能没开（photo_enable=false）。"
        if self._busy:
            return "还在画上一张，等它出来。"
        profiles = self._profiles()
        if not profiles:
            return "没有可发送的会话（先和他聊一句）。"
        activity = self._activity()
        kind = str(kind or "daily").strip() or "daily"
        scene = self._scene_for_kind(kind, str(activity.get("place") or ""), activity)
        if self._cfg_bool("photo_dry_run", False):
            if kind == "outfit":
                prompt = self._selfie_prompt_for(kind, scene, activity)
            else:
                prompt = self._prompt_for(kind, scene, activity)
            logger.info("[amo] 照片（dry-run）%s：%s" % (kind, prompt))
            return "🧪 dry-run：%s" % prompt
        self._busy = True
        try:
            # 手动「发一张旅游照/逛街照」且和当前日程对不上时，是刻意覆盖场景，别再用日程锁
            lock_scene = True
            if kind in ("travel", "shopping", "weekend"):
                lock_scene = bool(matches_kind(kind, activity))
            ok = await self._share(kind, scene, random.choice(profiles), target_umo=umo,
                                   lock_scene=lock_scene)
        finally:
            self._busy = False
        return "✅ 发了一张（%s）" % kind if ok else "❌ 没发出去，看日志"

    def template_dir(self):
        """角色模板保存目录：默认插件目录下 templates/（跟着插件备份）。"""
        raw = self._cfg_str("photo_template_dir", "").strip()
        if raw:
            path = raw if os.path.isabs(raw) else os.path.join(str(self.plugin.plugin_dir), raw)
        else:
            path = os.path.join(str(self.plugin.plugin_dir), "templates")
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.template_dir", _amo_exc)
        return path

    def _cfg_json(self, key):
        raw = self._cfg_str(key, "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _template_map(self):
        """三个视角各自的模板：{front/side/full: path}。"""
        data = self._cfg_json("photo_persona_refs")
        if not data:
            legacy = self._cfg_str("photo_persona_ref", "").strip()
            if legacy and os.path.exists(legacy):
                data = {"front": legacy}
        result = {
            view: path for view, path in data.items()
            if view in self.VIEWS and path and os.path.exists(path)
        }
        if result:
            return result
        # 配置里的文件丢了：按文件名把剩下的模板认回来，别把侧/全身当成正面
        recovered: dict[Any, Any] = {}
        try:
            root = self.template_dir()
            for name in os.listdir(root):
                lower = name.lower()
                if not lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    continue
                if "_side" in lower:
                    view = "side"
                elif "_full" in lower:
                    view = "full"
                elif "_front" in lower or lower.startswith("amo_persona."):
                    view = "front"
                else:
                    continue
                recovered.setdefault(view, os.path.join(root, name))
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._template_map", _amo_exc)
        return recovered

    def _save_template_map(self, mapping):
        try:
            self.plugin.config["photo_persona_refs"] = json.dumps(mapping, ensure_ascii=False)
            first = mapping.get("front") or next(iter(mapping.values()), "")
            self.plugin.config["photo_persona_ref"] = first
            if hasattr(self.plugin.config, "save_config"):
                self.plugin.config.save_config()
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._save_template_map", _amo_exc)

    def _enabled_map(self):
        data = self._cfg_json("photo_persona_enabled")
        result = {view: True for view in self.VIEWS}
        for view in self.VIEWS:
            result[view] = bool(data.get(view, True))
        return result

    def _save_enabled(self, mapping):
        try:
            self.plugin.config["photo_persona_enabled"] = json.dumps(mapping, ensure_ascii=False)
            if hasattr(self.plugin.config, "save_config"):
                self.plugin.config.save_config()
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._save_enabled", _amo_exc)

    def _kv_json(self, key):
        try:
            raw = self.plugin.db.kv_get(key, "{}") or "{}"
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_kv_json(self, key, data):
        try:
            self.plugin.db.kv_set(key, json.dumps(data, ensure_ascii=False))
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._save_kv_json", _amo_exc)

    def _stored_candidates(self):
        data = self._kv_json("photo_candidates")
        result = {}
        for view, item in (data or {}).items():
            if view not in self.VIEWS or not isinstance(item, dict):
                continue
            path = str(item.get("file") or "")
            if not path or not os.path.exists(path):
                continue
            result[view] = {
                "url": str(item.get("url") or ""),
                "file": path,
                "preview": self.preview_data_url({"file_path": path}),
            }
        return result

    def candidate(self, view):
        return self._stored_candidates().get(str(view or "").strip().lower()) or {}

    def set_candidate(self, view, item):
        if not isinstance(item, dict):
            return
        view = str(view or "").strip().lower()
        if view not in self.VIEWS:
            return
        data = self._kv_json("photo_candidates")
        data[view] = {
            "url": str(item.get("url") or ""),
            "file": str(item.get("file_path") or ""),
            "ts": time.time(),
        }
        self._save_kv_json("photo_candidates", data)

    def clear_candidate(self, view):
        view = str(view or "").strip().lower()
        data = self._kv_json("photo_candidates")
        if view in data:
            data.pop(view, None)
            self._save_kv_json("photo_candidates", data)

    def template_info(self):
        root = self.template_dir()
        plugin_dir = os.path.abspath(str(getattr(self.plugin, "plugin_dir", "") or ""))
        inside = bool(plugin_dir) and os.path.abspath(root).startswith(plugin_dir)
        files = []
        try:
            files = [name for name in os.listdir(root) if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
        except Exception:
            files = []
        mapping = self._template_map()
        enabled = self._enabled_map()
        templates = {}
        for view in self.VIEWS:
            path = mapping.get(view, "")
            templates[view] = {
                "path": path,
                "preview": self.preview_data_url({"file_path": path}) if path else "",
                "enabled": bool(enabled.get(view, True)),
            }
        current = self.persona_ref()
        info = {
            "root": root, "inside_plugin": inside, "count": len(files),
            "current": current, "templates": templates,
            "candidates": self._stored_candidates(),
        }
        info["preview"] = templates.get("front", {}).get("preview", "")
        return info

    def persona_refs(self):
        mapping = self._template_map()
        if not mapping:
            # 老版本只存了一张：按「正面」迁移
            try:
                root = self.template_dir()
                files = [
                    os.path.join(root, name) for name in os.listdir(root)
                    if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                ]
                if files:
                    mapping = {"front": max(files, key=os.path.getmtime)}
                    self._save_template_map(mapping)
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.PhotoEngine.persona_refs", _amo_exc)
        enabled = self._enabled_map()
        refs = []
        for view in self.VIEWS:
            path = mapping.get(view)
            if path and enabled.get(view, True):
                refs.append(path)
        return refs

    def persona_ref(self):
        refs = self.persona_refs()
        return refs[0] if refs else ""

    def preview_data_url(self, item, size=512):
        """给面板预览用的小图（data URL），避免 <img> 直接连中转站。"""
        if not isinstance(item, dict):
            return ""
        path = str(item.get("file_path") or "")
        if not path or not os.path.exists(path):
            return ""
        try:
            from PIL import Image as PILImage

            with PILImage.open(path) as image:
                image.thumbnail((size, size))
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="JPEG", quality=82)
            return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.preview_data_url", _amo_exc)
            return ""

    async def _localize(self, item):
        """把远端图片先落到本地，避免发送时 task1.65535.space 偶发连不上。"""
        if not isinstance(item, dict):
            return item
        if item.get("data_url"):
            return item
        path = str(item.get("file_path") or "")
        if path and os.path.exists(path):
            return item
        url = str(item.get("url") or item.get("image_url") or "")
        if not url.startswith("http"):
            return item
        cache_dir = self._cache_dir()
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._localize", _amo_exc)
            return item
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        for _ in range(2):
            try:
                payload, content_type = await asyncio.to_thread(self._fetch_image, url)
                if "png" in content_type or payload[:8] == b"\x89PNG\r\n\x1a\n":
                    ext = ".png"
                elif "webp" in content_type or payload[:4] == b"RIFF":
                    ext = ".webp"
                else:
                    ext = ".jpg"
                target = os.path.join(cache_dir, digest + ext)
                with open(target, "wb") as handle:
                    handle.write(payload)
                copied = dict(item)
                copied["file_path"] = target
                return copied
            except urllib.error.HTTPError as _amo_exc:
                # 403/404 重试也没用，直接走原图发送
                _note_swallowed("photo_engine.PhotoEngine._localize", _amo_exc)
                break
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.PhotoEngine._localize", _amo_exc)
                await asyncio.sleep(1)
        return item

    @staticmethod
    def _fetch_image(url):
        """线程里把图片读完再回来；事件循环里 read() 会把整条链路卡住。"""
        # 部分生图 CDN 会对没有 UA 的 urllib 直接 403，带上浏览器 UA 再取。
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*",
        })
        with urllib.request.urlopen(request, timeout=20) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            payload = response.read(_MAX_IMAGE_BYTES + 1)
        if len(payload) > _MAX_IMAGE_BYTES:
            raise ValueError("图片超过 %d MB，丢弃" % (_MAX_IMAGE_BYTES // (1024 * 1024)))
        return payload, content_type

    async def generate_custom(self, prompt, refs=None):
        """按提示词生成一张（可选带角色模板参考图），不发送。"""
        omni = self._find_omni()
        prompt = str(prompt or "").strip()
        if omni is None or not prompt:
            return None
        if refs is None:
            ref = self.persona_ref()
            refs = [ref] if ref else []
        refs = [item for item in refs if item]
        args = {"prompt": prompt, "count": 1, "event": None, "record_usage": False}
        if refs:
            args["refs"] = refs
            args["allow_local_refs"] = True
        try:
            result = await omni.generate_images_for_plugin(**args)
        except Exception as exc:
            logger.warning("[amo] 自定义生图失败: %s" % exc)
            result = None
        if not isinstance(result, dict) or not result.get("success"):
            # 中转偶发 502：等 3 秒重试一次
            await asyncio.sleep(3)
            try:
                result = await omni.generate_images_for_plugin(**args)
            except Exception as exc:
                logger.warning("[amo] 自定义生图重试失败: %s" % exc)
                return None
        if not isinstance(result, dict) or not result.get("success"):
            message = result.get("message") if isinstance(result, dict) else ""
            logger.warning("[amo] 自定义生图没出图: %s" % (message or "未知原因"))
            return None
        images = result.get("images") or []
        return await self._localize(images[0]) if images else None

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
        """日程里她现在这个地方（具体场景，比如「家里的客厅，沙发、抱枕和茶几」）。"""
        try:
            activity = self.plugin.state.current_activity() or {}
        except Exception:
            return ""
        return scene_for(activity)

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
        plan = resolve_plan(activity, mode="photo", explicit_place=explicit)
        where = scene_for(activity)
        if not where and plan.source == "explicit_place":
            where = explicit
        if not where and plan.source in ("no_schedule", "unknown_zone"):
            # 日程不明时才看他最近说过的地方（不是覆盖日程，只是兜底）
            where = self._recent_place() or ""
        where = where or plan.scene
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
        locked_today = False
        if text and not any(word in text for word in (
            "穿", "衣服", "裙", "衬衫", "毛衣", "外套", "卫衣", "裤", "围巾", "帽子",
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
        item = await self.generate_custom(text)
        if item and send:
            profiles = self._profiles()
            umo = self._target_umo(profiles[0]) if profiles else ""
            if umo:
                await self._send(umo, item)
        return item

    async def persona_sheet(self, view):
        """角色三视图：正面 / 侧面 / 全身。"""
        prompts = {
            "front": "角色设定图·正面：同一角色的正面站姿半身，二次元动漫风格，纯白背景，"
                     "五官清晰对称，蓝发蓝眼，鲸鱼鳍耳，日常服装，正对镜头",
            "side": "角色设定图·侧面：同一角色的侧面站姿半身，二次元动漫风格，纯白背景，"
                    "侧脸清晰，蓝发蓝眼，鲸鱼鳍耳，日常服装",
            "full": "角色设定图·全身：同一角色的正面全身站姿，二次元动漫风格，纯白背景，"
                    "从头到脚完整可见，蓝发蓝眼，鲸鱼鳍耳，日常服装",
        }
        refs = self.persona_refs()
        return await self.generate_custom(prompts.get(str(view), prompts["front"]), refs=refs)

    def _sync_omni_refs(self):
        """把三视图同步给万象画卷。

        以前这里直接读/写它的 ``config_path`` 和 AstrBot 的
        ``astrbot_plugin_omnidraw_config.json``，而且把 list 写进了它期望字符串的
        ``persona_ref_image`` 键——它一改 schema 就会写坏/写空。现在只走它自己的
        配置入口（``_apply_runtime_config`` + ``_persist_config``），格式和校验都由它负责。
        """
        refs = self.persona_refs()
        omni = self._find_omni()
        if omni is None:
            return
        if not apply_persona_config(omni, refs, "photo_engine.PhotoEngine._sync_omni_refs"):
            logger.debug("[amo] 万象画卷没有可用的配置入口，本次只在内存里更新参考图")

    async def adopt_persona(self, url, view="front"):
        """把选中的三视图存成对应视角的模板，以后生图都用它。"""
        url = str(url or "").strip()
        if not url:
            return "缺少图片地址"
        omni = self._find_omni()
        if omni is None:
            return "没找到万象画卷"
        target_dir = self.template_dir()
        if url.startswith("http"):
            try:
                with await asyncio.to_thread(lambda: urllib.request.urlopen(url, timeout=90)) as response:
                    payload = response.read()
                    content_type = str(response.headers.get("Content-Type") or "").lower()
            except Exception as exc:
                return "下载模板失败：%s" % exc
        elif os.path.isfile(url):
            try:
                payload = await asyncio.to_thread(lambda: open(url, "rb").read())
            except Exception as exc:
                return "读取本地模板失败：%s" % exc
            content_type = ""
        else:
            return "只支持 http 或本地图片"
        if "png" in content_type or payload[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif "webp" in content_type or payload[:4] == b"RIFF":
            ext = ".webp"
        else:
            ext = ".jpg"
        view = str(view or "front").strip().lower()
        if view not in self.VIEWS:
            view = "front"
        target = os.path.join(target_dir, "amo_persona_%s%s" % (view, ext))
        temp = target + ".tmp"
        try:
            with open(temp, "wb") as handle:
                handle.write(payload)
            for name in os.listdir(target_dir):
                if (name.startswith("amo_persona_" + view) and not name.endswith(".tmp")
                        and os.path.join(target_dir, name) != target):
                    try:
                        os.remove(os.path.join(target_dir, name))
                    except Exception:
                        pass
            os.replace(temp, target)
        except Exception as exc:
            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except Exception:
                pass
            return "保存模板失败：%s" % exc
        mapping = self._template_map()
        mapping[view] = target
        self._save_template_map(mapping)
        enabled = self._enabled_map()
        enabled[view] = True
        self._save_enabled(enabled)
        self._sync_omni_refs()
        label = self.VIEW_LABELS.get(view, view)
        return "✅ 已保存%s模板：%s" % (label, target)

    def set_template_enabled(self, view, enabled):
        view = str(view or "").strip().lower()
        if view not in self.VIEWS:
            return "未知视角"
        if view not in self._template_map():
            return "这个视角还没有模板"
        flags = self._enabled_map()
        flags[view] = bool(enabled)
        self._save_enabled(flags)
        self._sync_omni_refs()
        label = self.VIEW_LABELS.get(view, view)
        return "✅ %s模板已%s" % (label, "启用" if enabled else "停用")

    async def delete_persona(self, view=""):
        """删除某个视角（或全部）的角色模板。"""
        view = str(view or "").strip().lower()
        mapping = self._template_map()
        removed = []
        if view in self.VIEWS:
            targets = [mapping.pop(view, "")]
        else:
            targets = list(mapping.values())
            mapping = {}
        for path in targets:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
                    removed.append(path)
            except Exception:
                pass
        self._save_template_map(mapping)
        enabled = self._enabled_map()
        if view in self.VIEWS:
            enabled[view] = False
        else:
            enabled = {item: False for item in self.VIEWS}
        self._save_enabled(enabled)
        self._sync_omni_refs()
        label = self.VIEW_LABELS.get(view, "全部")
        return "✅ 已删除%s模板" % label if removed else "当前没有可删除的%s模板" % label

    def status_text(self):
        lines = [
            "📷 照片：%s%s" % ("开" if self.enabled else "关", "（dry-run）" if self._cfg_bool("photo_dry_run", False) else ""),
            "概率：穿搭 %d%%｜旅游 %d%%｜逛街 %d%%｜周末 %d%%｜日常 %d%%" % (
                self._prob("photo_outfit_prob", 70), self._prob("photo_travel_prob", 60),
                self._prob("photo_shopping_prob", 40), self._prob("photo_weekend_prob", 50),
                self._prob("photo_daily_prob", 25),
            ),
            "自拍占比：%d%%｜每天上限：%d｜今天已发：%d" % (
                self._prob("photo_selfie_ratio", 50), self._cfg_int("photo_daily_cap", 4), self.today_count(),
            ),
            "今日穿搭：" + self.outfit_today(),
            "万象画卷：%s" % ("已连接" if self._find_omni() is not None else "没找到"),
            "统计：发 %d｜失败 %d｜掷骰 %d" % (
                self._stats["sent"], self._stats["failed"], self._stats["rolled"],
            ),
        ]
        return "\n".join(lines)


# ---- 2026-11-12 按场景细化：一个 default 会误伤家里/餐厅/酒店 ----
# 只在模块加载后覆盖对应 key；scene_policy()/scene_guard() 都读同一个 dict。
SCENE_POLICY.update({
    "home": {"poses": ["library"], "banned_postures": [], "banned_props": [], "seat": "soft"},
    "restaurant": {"poses": ["sit"], "banned_postures": [], "banned_props": [], "seat": "chair"},
    "hotel": {"poses": ["sit_edge", "stand"], "banned_postures": [], "banned_props": [], "seat": "soft"},
    "office": {"poses": ["stand", "sit"], "banned_postures": [], "banned_props": ["bedding"], "seat": "chair"},
    "mall": {"poses": ["stand", "walk"], "banned_postures": [],
             "banned_props": ["bedding", "instant_noodles", "tableware"], "seat": "bench"},
    "station": {"poses": ["stand"], "banned_postures": [],
                "banned_props": ["bedding", "instant_noodles", "tableware"], "seat": "bench"},
    "scenic": {"poses": ["stand", "walk"], "banned_postures": [],
               "banned_props": ["bedding", "instant_noodles", "tableware"], "seat": "bench"},
    "street": {"poses": ["stand", "walk"], "banned_postures": [],
               "banned_props": ["bedding", "instant_noodles", "tableware"], "seat": "bench"},
    "hospital": {"poses": ["sit"], "banned_postures": [], "banned_props": ["bedding"], "seat": "chair"},
})


# ---- 2026-11-12 场景“该有什么”：只靠禁词会让画面空、道具乱，给每个场景一句正向要素 ----
SCENE_ALLOWED = {
    "home": "家里该有的东西：沙发或床、抱枕、毯子、水杯、窗边的光",
    "office": "工位该有的东西：办公桌、显示器、键盘、马克杯、文件、办公椅",
    "mall": "商场该有的东西：店铺橱窗、扶梯、地砖、指示牌、来往的人",
    "scenic": "景区该有的东西：景点建筑或山水、栏杆、游客、路牌、自然光",
    "restaurant": "餐厅该有的东西：餐桌、碗筷餐盘、菜单、杯子、店里的灯",
    "hotel": "酒店房间该有的东西：床和枕头、床头灯、行李、窗帘、地毯",
    "station": "车站该有的东西：候车座椅、指示牌、行李箱、时刻表、行人",
    "hospital": "医院该有的东西：候诊椅、叫号屏、走廊、扶手、消毒水味的环境",
    "road": "路上该有的东西：扶手或安全带、车窗、座椅、窗外街景或隧道",
    "street": "街边该有的东西：路面、行道树、店面、路牌、走过的行人",
}


def scene_allowed_line(zone):
    """这个场景里应该出现什么（和禁词配合，避免画面空荡或乱加道具）。"""
    return SCENE_ALLOWED.get(str(zone or ""), "只出现这个地点本来就有的东西")
