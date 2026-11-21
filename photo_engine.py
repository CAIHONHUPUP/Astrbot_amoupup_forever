# -*- coding: utf-8 -*-
"""照片分享引擎：旅游 / 逛街 / 周末 / 日常 / 早起穿搭。

她按日程过日子，这里只做一件事：在合适的「场景时刻」按配置概率，用我能永远定格你
（OmniDraw）生成一张照片发给他——要么是当地的街景/风景，要么是她自己的自拍。

- 概率都是 0-100 的整数，面板「照片」页可调；
- 每类时刻每天只掷一次骰子（``photo_roll:<日期>:<类别>:<地点>``），不会每 30 秒重掷；
- 每天总数受 ``photo_daily_cap`` 限制；
- 穿搭只在工作日早上出门那段时间触发，并且**一定是自拍**；
- 旅游/逛街/周末/日常按 ``photo_selfie_ratio`` 决定这张是自拍还是街景。

发送不依赖聊天事件：用 ``context.send_message(umo, chain)`` 主动发到最近活跃的会话。
"""

from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import asyncio
import shutil
import base64
import hashlib
import io
import json
import os
import random
import time
import urllib.error
import urllib.request

try:  # 纯内核：缓存清理 / 模板目录选择（域内只用标准库）
    from .domains.photo import cache as _photo_cache
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.photo import cache as _photo_cache

from astrbot.api import logger

from typing import Any

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
    from .amo_shared import now_ts, quiet_now, today_str
    from .clock import now as _clock_now
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
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
                return _amo_clock.now_ts()

            def today_str():
                return _clock_now().date().isoformat()

            def quiet_now(_spec):
                return False

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_image_common import (
        MessageChain,
        apply_persona_config,
        canonical_template_dir,
        cfg,
        clean_place,
        find_omni,
        find_template_file,
        image_component,
        persona_refs_of,
        resolve_template_path,
        spawn,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_image_common import (
            MessageChain,
            apply_persona_config,
            canonical_template_dir,
            cfg,
            clean_place,
            find_omni,
            find_template_file,
            image_component,
            persona_refs_of,
            resolve_template_path,
            spawn,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_image_common import (
                MessageChain,
                apply_persona_config,
                canonical_template_dir,
                cfg,
                clean_place,
                find_omni,
                find_template_file,
                image_component,
                persona_refs_of,
                resolve_template_path,
                spawn,
            )

try:  # 视角后缀：她出镜=自拍视角，她拍环境=她的拍摄视角（空镜）
    from .photo_view import (ROOM_NO_BODY, SCENERY_ONLY, SELFIE_FRAMING,
                             SELFIE_NEGATIVE, SELFIE_STYLE, VIEW_POV, VIEW_SELFIE)
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_view import (ROOM_NO_BODY, SCENERY_ONLY, SELFIE_FRAMING,
                                SELFIE_NEGATIVE, SELFIE_STYLE, VIEW_POV, VIEW_SELFIE)

try:  # 生图计划：一次生成只解析一次，锁 / 姿势 / 参考图策略都从 plan 取
    from .photo_plan import (
        build_plan,
        locks_for_plan,
        pick_pose_for_plan,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_plan import (
            build_plan,
            locks_for_plan,
            pick_pose_for_plan,
        )

#: 兼容：老名字叫 resolve_plan（外部脚本 / 既有测试按这个名字取）
resolve_plan = build_plan

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
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
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
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from pose_library import pose_line
        except Exception:  # pragma: no cover
            def pose_line(pose):
                return ""

try:
    from .photo_data import (
    home_room_for_plan,
    _light_text,
    _light_value,
    selfie_state_hint,
    _log_photo_debug,
    OUTDOOR_POSES,
    outdoor_pose_line,
    SCENE_ALLOWED,
    SCENE_POLICY,
    _PROP_WORDS,
    _POSTURE_WORDS,
    _RIDE_WORDS,
    road_ride,
    pose_line_for,
    scene_policy,
    scene_guard,
    GENERATE_TIMEOUT_SECONDS,
    _MAX_IMAGE_BYTES,
    OUTFIT_POOL,
    _int_in,
    PHOTO_INTENT_WORDS,
    PHOTO_PLACE_WORDS,
    scene_allowed_line,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_data import (
            home_room_for_plan,
            _light_text,
            _light_value,
            selfie_state_hint,
            _log_photo_debug,
            OUTDOOR_POSES,
            outdoor_pose_line,
            SCENE_ALLOWED,
            SCENE_POLICY,
            _PROP_WORDS,
            _POSTURE_WORDS,
            _RIDE_WORDS,
            road_ride,
            pose_line_for,
            scene_policy,
            scene_guard,
            GENERATE_TIMEOUT_SECONDS,
            _MAX_IMAGE_BYTES,
            OUTFIT_POOL,
            _int_in,
            PHOTO_INTENT_WORDS,
            PHOTO_PLACE_WORDS,
            scene_allowed_line,
        )

try:
    from .photo_prompt import PhotoPromptMixin
    from .photo_generate import PhotoGenerateMixin
    from .photo_share import PhotoShareMixin
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_prompt import PhotoPromptMixin
        from photo_generate import PhotoGenerateMixin
        from photo_share import PhotoShareMixin


class PhotoEngine(PhotoPromptMixin, PhotoGenerateMixin, PhotoShareMixin):
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
        self._activity_cache = None  # (天 时:分, 日程快照)；日程变更/换天即失效
        self._persona_refs_signature: Any = None
        self._persona_synced_ok = False
        # 启动：先把三视图路径从旧 omnidraw 迁到生图插件目录（旧路径只读兜底），
        # 再排一次后台握手把参考图补进生图插件；任何一步失败都不阻塞加载。
        try:
            self._migrate_template_config()
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.__init__.migrate", _amo_exc)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # 没有事件循环（单测/同步加载）：首次生图时握手自愈兜底
        try:
            self._spawn(self._bootstrap_persona_refs())
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.__init__.bootstrap", _amo_exc)

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
        """当前日程快照：同一天同一分钟只读一次（候选/生成/状态共用）。

        P2-4：一次出图链路以前会反复调 state.current_activity()（内部要读日程）；
        这里按分钟缓存，日程变更事件（on_schedule_changed）和换天会失效，不脏读。
        """
        stamp = None
        try:
            now = _clock_now()
            stamp = "%s %02d:%02d" % (today_str(), int(now.hour), int(now.minute))
        except Exception:
            stamp = None
        if stamp is not None:
            cached = getattr(self, "_activity_cache", None)
            if cached and cached[0] == stamp:
                return dict(cached[1])
        try:
            activity = self.plugin.state.current_activity() or {}
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._activity", _amo_exc)
            return {}
        if stamp is not None:
            self._activity_cache = (stamp, dict(activity))
        return activity

    def invalidate_activity(self):
        """日程变更/换天：丢掉此刻日程缓存，下一次出图重新读。"""
        self._activity_cache = None

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
        if _amo_clock.now_ts() - last < 3600:
            return 0
        cutoff = _amo_clock.now_ts() - days * 86400
        cache_dir = self._cache_dir()
        removed, failed = _photo_cache.purge_old_files(cache_dir, cutoff)
        if failed:
            _note_swallowed("photo_engine.PhotoEngine.cleanup_cache",
                            OSError("%d 个缓存文件删除失败" % failed))
        try:
            self.plugin.db.kv_set("photo_cache_cleanup", _amo_clock.now_ts())
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.cleanup_cache", _amo_exc)
        if removed:
            logger.info("[amo] 清理旧生图缓存 %d 张（保留 %d 天）" % (removed, days))
        return removed

    def template_dir(self):
        """角色模板保存目录：生图插件 amo_templates（新名）；旧的只读兜底。

        生图插件只接受自己 data_dir 下的本地人设参考图（它会按 data_dir 做
        安全校验，目录外的绝对路径会被静默丢掉），所以新存的模板要落在
        plugin_data/Astrbot_amoupup_photo/amo_templates；老 omnidraw 目录只在
        读的时候兜底，不再往里写。
        """
        raw = self._cfg_str("photo_template_dir", "").strip()
        canonical = canonical_template_dir(self.plugin)
        plugin_dir = str(getattr(self.plugin, "plugin_dir", "") or "") or os.getcwd()
        chosen = _photo_cache.choose_template_dir(raw, plugin_dir, canonical)
        try:
            os.makedirs(chosen, exist_ok=True)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.template_dir", _amo_exc)
        return chosen

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
        """三个视角各自的模板：{front/side/full: path}；顺手把旧路径迁到新插件目录。"""
        data = self._cfg_json("photo_persona_refs")
        if not data:
            legacy = self._cfg_str("photo_persona_ref", "").strip()
            if legacy and os.path.exists(legacy):
                data = {"front": legacy}
        result: dict[Any, Any] = {}
        changed = False
        for view in self.VIEWS:
            current = str(data.get(view) or "")
            path = resolve_template_path(view, current, self.plugin)
            if not path:
                continue
            result[view] = path
            if path != current:
                changed = True
        if not result:
            # 配置里的文件丢了：按文件名把剩下的模板认回来，别把侧/全身当成正面
            for view in self.VIEWS:
                path = resolve_template_path(view, "", self.plugin)
                if not path:
                    path = find_template_file(view, self.template_dir())
                if path:
                    result[view] = path
                    changed = True
        if changed and result:
            self._save_template_map(result)
        return self._migrate_persona_templates(result)

    def _migrate_template_config(self):
        """启动迁移：三视图/模板目录指到新生图插件目录，旧路径只读兜底。

        改名后老配置还指着旧 omnidraw 的 plugin_data/amo_templates：
        文件还在，但生图插件只收自己 data_dir 下的图，会静默丢掉 → 0 张参考图。
        这里把能迁的迁到 plugin_data/Astrbot_amoupup_photo/amo_templates。
        """
        before = self._cfg_json("photo_persona_refs")
        before_dir = self._cfg_str("photo_template_dir", "").strip()
        mapping = self._template_map()
        changed = False
        if mapping and {str(k): str(v) for k, v in mapping.items()} != {
                str(k): str(v) for k, v in before.items() if k in self.VIEWS}:
            changed = True
        canonical = canonical_template_dir(self.plugin)
        if canonical and os.path.isdir(canonical) and before_dir:
            if os.path.normcase(os.path.abspath(before_dir)) != os.path.normcase(os.path.abspath(canonical)):
                try:
                    self.plugin.config["photo_template_dir"] = canonical
                    changed = True
                except Exception as _amo_exc:
                    _note_swallowed("photo_engine.PhotoEngine._migrate_template_config", _amo_exc)
        if changed:
            self._save_template_map(mapping)
            logger.info("[amo] 三视图路径已迁到生图插件目录：%s",
                        json.dumps(mapping, ensure_ascii=False))
        return mapping

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
            "ts": _amo_clock.now_ts(),
        }
        self._save_kv_json("photo_candidates", data)

    def clear_candidate(self, view):
        view = str(view or "").strip().lower()
        data = self._kv_json("photo_candidates")
        if view in data:
            data.pop(view, None)
            self._save_kv_json("photo_candidates", data)

    def template_info(self):
        root = self.persona_template_dir()
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


    # ---------- 用户三视图（2026-11-21：合拍用） ----------

    _USER_VIEW_FILES = {"front": "用户正面.png", "side": "用户侧面.png", "full": "用户全身.png"}
    _PERSONA_VIEW_FILES = {"front": "角色正面.png", "side": "角色侧面.png", "full": "角色全身.png"}

    def persona_template_dir(self):
        """角色三视图模板目录：plugin_data/outfit_refs/角色三视图。"""
        try:
            try:
                from .amo_image_common import plugin_data_root
            except ImportError:
                from amo_image_common import plugin_data_root
            root = os.path.join(plugin_data_root(self.plugin), "outfit_refs", "角色三视图")
            os.makedirs(root, exist_ok=True)
            return root
        except Exception as exc:
            logger.debug("[amo] 角色三视图目录创建失败: %r", exc)
            return self.template_dir()

    def _migrate_persona_templates(self, mapping):
        """把旧的 amo_templates/amo_persona_*.png 迁到 outfit_refs/角色三视图/角色*.png。"""
        changed = False
        new_dir = self.persona_template_dir()
        for view, path in list((mapping or {}).items()):
            name = os.path.basename(str(path or ""))
            if not name.startswith("amo_persona_"):
                continue
            target = os.path.join(new_dir, self._PERSONA_VIEW_FILES.get(view, "角色正面.png"))
            try:
                if str(path) != target and os.path.isfile(path):
                    shutil.copy2(path, target)
                if os.path.isfile(target):
                    mapping[view] = target
                    changed = True
            except Exception as exc:
                logger.debug("[amo] 角色三视图迁移失败: %r", exc)
        if changed:
            self._save_template_map(mapping)
            try:
                self._sync_omni_refs()
            except Exception as exc:
                logger.debug("[amo] 角色三视图迁移后同步生图插件失败: %r", exc)
        return mapping

    def user_template_dir(self):
        """用户三视图模板目录：plugin_data/outfit_refs/用户三视图。"""
        try:
            try:
                from .amo_image_common import plugin_data_root
            except ImportError:
                from amo_image_common import plugin_data_root
            root = os.path.join(plugin_data_root(self.plugin), "outfit_refs", "用户三视图")
            os.makedirs(root, exist_ok=True)
            return root
        except Exception as exc:
            logger.debug("[amo] 用户三视图目录创建失败: %r", exc)
            return self.template_dir()

    def _user_map(self):
        data = self._kv_json("photo_user_templates")
        mapping = {view: str(data.get(view) or "") for view in self.VIEWS if str(data.get(view) or "")}
        if mapping:
            return mapping
        # 老数据/手动放入：按文件名认回来
        try:
            root = self.user_template_dir()
            for view, name in self._USER_VIEW_FILES.items():
                path = os.path.join(root, name)
                if os.path.isfile(path):
                    mapping[view] = path
        except Exception as exc:
            logger.debug("[amo] 用户三视图认回失败: %r", exc)
        return mapping

    def _save_user_map(self, mapping):
        self._save_kv_json("photo_user_templates", mapping)

    def _user_enabled(self):
        data = self._kv_json("photo_user_enabled")
        return {view: bool(data.get(view, True)) for view in self.VIEWS}

    def _save_user_enabled(self, mapping):
        self._save_kv_json("photo_user_enabled", mapping)

    def user_candidate(self, view):
        data = self._kv_json("photo_user_candidates")
        return data.get(str(view or "").strip().lower()) or {}

    def set_user_candidate(self, view, item):
        view = str(view or "").strip().lower()
        if view not in self.VIEWS or not isinstance(item, dict):
            return
        data = self._kv_json("photo_user_candidates")
        data[view] = {"url": str(item.get("url") or ""), "file": str(item.get("file_path") or ""),
                      "preview": self.preview_data_url(item), "ts": _amo_clock.now_ts()}
        self._save_kv_json("photo_user_candidates", data)

    def user_template_info(self):
        mapping = self._user_map()
        enabled = self._user_enabled()
        templates = {}
        for view in self.VIEWS:
            path = mapping.get(view, "")
            templates[view] = {"path": path,
                               "preview": self.preview_data_url({"file_path": path}) if path else "",
                               "enabled": bool(enabled.get(view, True))}
        ref_path = self.user_ref_path()
        return {"templates": templates,
                "candidates": self._kv_json("photo_user_candidates"),
                "ref": {"has_ref": bool(ref_path),
                        "preview": self.user_ref_preview() if ref_path else ""}}

    def user_refs(self):
        mapping = self._user_map()
        enabled = self._user_enabled()
        return [mapping[view] for view in self.VIEWS
                if mapping.get(view) and enabled.get(view, True)]

    def _ensure_png_file(self, path):
        """把 JPEG/WebP 统一转成真 PNG，避免生图插件「内容与声明类型不匹配」。"""
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            try:
                try:
                    from .outfit_refs import _as_png
                except ImportError:
                    from outfit_refs import _as_png
                png = _as_png(data)
            except Exception:
                png = b""
            if png and png != data:
                with open(path, "wb") as fh:
                    fh.write(png)
            return path
        except Exception as exc:
            logger.debug("[amo] 参考图转 PNG 失败: %r", exc)
            return path

    def user_ref_path(self):
        try:
            root = self.template_dir()
            path = os.path.join(root, "amo_user_ref.png")
            if not os.path.isfile(path):
                return ""
            return self._ensure_png_file(path)
        except Exception:
            return ""

    def user_ref_preview(self):
        path = self.user_ref_path()
        return self.preview_data_url({"file_path": path}) if path else ""

    def set_user_ref(self, raw):
        raw = str(raw or "").strip()
        if not raw:
            return ""
        try:
            root = self.template_dir()
            os.makedirs(root, exist_ok=True)
            target = os.path.join(root, "amo_user_ref.png")
            if raw.startswith("data:"):
                import base64
                _head, _sep, body = raw.partition(",")
                payload = base64.b64decode(body)
                with open(target, "wb") as fh:
                    fh.write(payload)
            elif os.path.isfile(raw):
                import shutil
                shutil.copy2(raw, target)
            else:
                return ""
            return self._ensure_png_file(target)
        except Exception as exc:
            logger.debug("[amo] 用户参考图保存失败: %s", exc)
            return ""

    def clear_user_ref(self):
        try:
            path = os.path.join(self.template_dir(), "amo_user_ref.png")
            if os.path.isfile(path):
                os.remove(path)
        except Exception as exc:
            logger.debug("[amo] 用户参考图删除失败: %s", exc)
        return ""

    async def adopt_user(self, url, view="front"):
        """把选中的用户三视图存成模板（outfit_refs/用户三视图/用户正面.png）。"""
        url = str(url or "").strip()
        if not url:
            return "缺少图片地址"
        try:
            if url.startswith("data:"):
                import base64
                _head, _sep, body = url.partition(",")
                payload = base64.b64decode(body)
            elif url.startswith("http"):
                def _download():
                    with urllib.request.urlopen(url, timeout=90) as response:
                        return response.read()
                payload = await asyncio.to_thread(_download)
            elif os.path.isfile(url):
                payload = open(url, "rb").read()
            else:
                return "只支持 http / 本地 / data URL"
        except Exception as exc:
            return "读取图片失败：%s" % exc
        view = str(view or "front").strip().lower()
        if view not in self.VIEWS:
            view = "front"
        target_dir = self.user_template_dir()
        target = os.path.join(target_dir, self._USER_VIEW_FILES.get(view, "用户正面.png"))
        try:
            with open(target, "wb") as fh:
                fh.write(payload)
            self._ensure_png_file(target)
        except Exception as exc:
            return "保存模板失败：%s" % exc
        mapping = self._user_map()
        mapping[view] = target
        self._save_user_map(mapping)
        enabled = self._user_enabled()
        enabled[view] = True
        self._save_user_enabled(enabled)
        return "✅ 用户%s模板已保存" % self.VIEW_LABELS.get(view, view)

    def set_user_enabled(self, view, enabled):
        view = str(view or "").strip().lower()
        if view not in self.VIEWS:
            return "未知视角"
        if view not in self._user_map():
            return "这个视角还没有模板"
        flags = self._user_enabled()
        flags[view] = bool(enabled)
        self._save_user_enabled(flags)
        return "✅ 用户%s模板已%s" % (self.VIEW_LABELS.get(view, view), "启用" if enabled else "停用")

    async def delete_user(self, view=""):
        view = str(view or "").strip().lower()
        mapping = self._user_map()
        removed = []
        if view in self.VIEWS:
            targets = [mapping.pop(view, "")]
        else:
            targets = list(mapping.values())
            mapping = {}
        for path in targets:
            try:
                if path and os.path.isfile(path):
                    os.remove(path)
                    removed.append(path)
            except Exception as exc:
                logger.debug("[amo] 用户模板删除失败: %s", exc)
        self._save_user_map(mapping)
        enabled = self._user_enabled()
        if view in self.VIEWS:
            enabled[view] = False
        else:
            enabled = {item: False for item in self.VIEWS}
        self._save_user_enabled(enabled)
        return "✅ 已删除用户%s模板" % self.VIEW_LABELS.get(view, "全部") if removed else "当前没有可删除的用户模板"

    # ---------- 合拍 ----------

    def couple_pose(self):
        try:
            library = getattr(self.plugin, "pose", None)
            if library is None:
                return None
            return library.random_pose("home", "", usage="couple")
        except Exception:
            return None

    def couple_refs(self, pose=None):
        refs = list(self.persona_refs()) + list(self.user_refs())
        try:
            library = getattr(self.plugin, "pose", None)
            if pose is not None and library is not None:
                path = library.ref_path(pose)
                if path and os.path.isfile(path):
                    refs.append(path)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.couple_refs", _amo_exc)
        return refs

    def couple_prompt(self, scene="", pose=None, text=""):
        name = str((pose or {}).get("name") or "")
        note = str((pose or {}).get("note") or "")
        where = str(scene or "家里")
        return ("两人合照：画面里两个真实人物——她（参考图前三张是她的三视图）和用户本人"
                "（参考图中间几张是他的三视图），两个人在一起，场景：%s。"
                "姿势：%s（%s）。第三人称虚空相机，全身构图，两个人从头到脚完整入镜，"
                "不出现手机、不出现拿手机的手；自然光，日常穿搭，表情自然。"
                "参考图只借长相和姿势，不要照搬背景和衣服。"
                % (where, name or "自然站在一起", note or "并排看镜头"))

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
            except Exception as exc:
                _note_swallowed("photo_engine.py.1861", exc)
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
            "我能永远定格你：%s" % ("已连接" if self._find_omni() is not None else "没找到"),
            "统计：发 %d｜失败 %d｜掷骰 %d" % (
                self._stats["sent"], self._stats["failed"], self._stats["rolled"],
            ),
        ]
        return "\n".join(lines)
