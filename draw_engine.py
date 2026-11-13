# -*- coding: utf-8 -*-
"""画图引擎（接「万象画卷」OmniDraw）。

玩法：她正常回完话之后，按配置判断这轮要不要顺手画一张给她发过去。

- 触发方式（``draw_trigger_mode``）：
  - ``topic``：他这句话里出现 ``draw_topic_keywords`` 里的词（默认：吃什么/吃啥/在干嘛…），
    就用她刚才的回复当画面描述；
  - ``explicit``：他明确说「画一张 / 给我画 / 发张图 / 自拍」才画，用他这句话当描述；
  - ``both``：两种都算。
- 生成走 OmniDraw 的 ``generate_images_for_plugin()``（只返回结果，不自己发图），
  拿到图之后再通过 ``event.send()`` 补一条图片消息，所以她的文字一定先到，图后到。
- 每次生成都在后台任务里跑，不阻塞她说话；失败只记日志，不会把回复吞掉。

和主脑的关系：
- ``maybe_after_reply()`` 由 ``on_decorating_result()`` 调用（只负责判断 + 起任务）；
- ``/amo draw 描述`` 是给测试用的手动入口（不依赖触发词）。

数据只在内存里（冷却、每小时/每天计数），重启清零；不写数据库。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time

from astrbot.api import logger

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
    from .amo_shared import logger as _amo_logger
    from .amo_shared import today_str as _today_str
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed
    try:
        from .amo_shared import today_str as _today_str
    except Exception:  # pragma: no cover
        try:
            from amo_shared import today_str as _today_str
        except Exception:
            import datetime as _dt

            def _today_str():
                return _dt.datetime.now().date().isoformat()
    try:  # 正常包模式
        from .amo_shared import logger as _amo_logger  # noqa: F401  (保持和主模块同一 logger)
    except Exception:  # pragma: no cover - 只在极端导入方式下走到
        _amo_logger = logger

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_image_common import (
        MessageChain,
        cfg,
        clean_place,
        find_omni,
        image_component,
        spawn,
        split_keywords,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_image_common import (
            MessageChain,
            cfg,
            clean_place,
            find_omni,
            image_component,
            spawn,
            split_keywords,
        )
    except ImportError:  # 直接按文件加载时
        from amo_image_common import (
            MessageChain,
            cfg,
            clean_place,
            find_omni,
            image_component,
            spawn,
            split_keywords,
        )

try:  # 视角后缀：她出镜=自拍视角，她拍环境=她的拍摄视角（空镜）
    from .photo_view import SCENERY_ONLY, looks_like_her, view_suffix
except ImportError:  # 直接按文件加载时
    from photo_view import SCENERY_ONLY, looks_like_her, view_suffix

try:  # 场景：日程地点 → 背景（在家=家里、逛街=商场、旅游=景区）
    from .photo_scene import PLACE_HINTS, scene_for
except ImportError:  # 直接按文件加载时
    from photo_scene import PLACE_HINTS, scene_for

try:  # 自拍姿势库：随机挑一个姿势
    from .pose_library import pose_line
except ImportError:  # 直接按文件加载时
    try:
        from pose_library import pose_line
    except Exception:  # pragma: no cover
        def pose_line(pose):
            return ""

try:  # 场景规则表（照片严格跟随日程）
    from .photo_rules import locks_for_plan, pick_pose_for_plan, resolve_plan
except ImportError:  # 直接按文件加载时
    from photo_rules import locks_for_plan, pick_pose_for_plan, resolve_plan

# 生图可能卡死；最多等 3 分钟就放弃这一次
GENERATE_TIMEOUT_SECONDS = 180
# data_url 是 base64，超过这个长度就不进 kv（否则数据库会被撑大）
_MAX_CACHED_DATA_URL = 200000

DEFAULT_TOPIC_KEYWORDS = (
    "吃什么,吃啥,吃点什么,想吃什么,去哪吃,在干嘛,在做什么,做什么,好饿,饿了,"
    "想喝,喝什么,喝啥,想玩,在忙什么"
)

DEFAULT_EXPLICIT_KEYWORDS = (
    "画一张,画个,画一下,画一幅,画张,给我画,帮我画,替我画,来一张,来张,发张图,发个图,"
    "发一张,拍张,拍一张,拍照给我看看,拍给我看,照片给我看看,发张照片,自拍,画我,来点图"
)

_EXPLICIT_CLEAN_RE = re.compile(
    r"(请|麻烦)?(你)?(给我|帮我|替我)?"
    r"(画一张|画一个|画一幅|画一下|画张|画个|来一张|来张|发张图|发个图|发一张|"
    r"拍照给我看看|拍给我看|照片给我看看|发张照片|拍张|拍一张|自拍|给我看看|画|发张|发个|来张)"
)
_TAIL_PUNCT_RE = re.compile(r"[\s，,。.！!？?~～…]+$")
_FILLER_TAIL_RE = re.compile(r"(吧|呀|啊|呢|啦|哦|哈|嘛|咯)+$")

QUESTION_WORDS = (
    "吗", "呢", "怎么样", "如何", "在干吗", "干嘛", "在做什么", "做什么", "开心",
    "好不好", "是不是", "在不在", "在哪", "玩得", "有没有", "什么", "哪里",
)

FILLER_WORDS = (
    "开心", "在呀", "好呀", "是呀", "对呀", "还行", "还好", "哈哈", "嘿嘿",
    "呀", "啊", "啦", "哦", "嗯", "呢", "吧", "嘛",
)

# 用来做「同一场景」缓存的词；命中越多越容易被当成同一张图
SCENE_KEYWORDS = (
    "公园", "狗", "猫", "咖啡", "奶茶", "面条", "火锅", "烧烤", "蛋糕", "海边", "沙滩",
    "山", "古镇", "街", "商场", "书店", "花", "樱花", "雨", "雪", "夕阳", "夜景",
    "电影院", "地铁", "公交", "办公室", "家", "厨房", "阳台", "床", "餐桌", "餐厅",
    "学校", "操场", "湖", "河", "跑步", "散步", "蛋糕", "冰淇淋", "水果", "衣服",
    "裙子", "毛衣", "围巾", "帽子", "电脑", "书", "电影", "音乐", "旅行",
)


def _split_keywords(text):
    return split_keywords(text)


def clean_explicit_prompt(text):
    """「给我画一碗面」→「一碗面」。"""
    text = str(text or "").strip()
    cleaned = _EXPLICIT_CLEAN_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _TAIL_PUNCT_RE.sub("", cleaned)


def clean_topic_prompt(text, max_chars=80):
    """她刚说的话 → 画面描述。去掉换行/引号/末尾语气词，截断到 max_chars。"""
    text = str(text or "").replace("\n", " ").strip()
    text = text.strip("\"'“”‘’「」『』 ")
    text = _TAIL_PUNCT_RE.sub("", text)
    text = _FILLER_TAIL_RE.sub("", text).strip()
    max_chars = max(12, int(max_chars or 80))
    if len(text) > max_chars:
        text = text[:max_chars].rstrip("，,。.、 ")
    return text


class DrawEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self._last_draw = {}      # uid -> monotonic
        self._draw_times = {}     # uid -> [monotonic, ...]
        self._inflight = set()    # umo
        self._last_prompt = {}    # uid -> (prompt, monotonic)
        self._omni = None
        self._omni_checked = 0.0
        self._stats = {"scheduled": 0, "sent": 0, "failed": 0, "skipped": 0, "cached": 0}
        self._last_error = ""
        self._missing_notice_at = {}   # umo -> monotonic：缺万象画卷时多久提醒一次

    # ---------- 配置 ----------

    def _cfg_bool(self, key, default=False):
        return cfg(self.plugin, key, default, bool, "draw_engine.DrawEngine._cfg_bool")

    def _cfg_int(self, key, default=0):
        return cfg(self.plugin, key, default, int, "draw_engine.DrawEngine._cfg_int")

    def _cfg_float(self, key, default=0.0):
        return cfg(self.plugin, key, default, float, "draw_engine.DrawEngine._cfg_float")

    def _cfg_str(self, key, default=""):
        return cfg(self.plugin, key, default, str, "draw_engine.DrawEngine._cfg_str")

    @property
    def enabled(self):
        return self._cfg_bool("draw_enable", False)

    @property
    def dry_run(self):
        return self._cfg_bool("draw_dry_run", False)

    @property
    def debug(self):
        return self._cfg_bool("draw_debug", False)

    @property
    def debug_prompt(self):
        return bool(self._cfg_bool("debug_prompt", False) or self.debug)

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

    def _mode(self):
        mode = self._cfg_str("draw_trigger_mode", "topic").strip().lower()
        return mode if mode in ("topic", "explicit", "both") else "topic"

    def _topic_keywords(self):
        raw = self._cfg_str("draw_topic_keywords", DEFAULT_TOPIC_KEYWORDS)
        return _split_keywords(raw) or _split_keywords(DEFAULT_TOPIC_KEYWORDS)

    def _explicit_keywords(self):
        raw = self._cfg_str("draw_explicit_keywords", DEFAULT_EXPLICIT_KEYWORDS)
        return _split_keywords(raw) or _split_keywords(DEFAULT_EXPLICIT_KEYWORDS)

    # ---------- 权限 / 限额 ----------

    def is_owner(self, event):
        """谁能触发画图：draw_owner_ids（配了只认名单）> 管理员 > 关系「相恋」。"""
        raw = self._cfg_str("draw_owner_ids", "")
        owners = {item for item in _split_keywords(raw.replace(" ", ","))}
        uid = ""
        try:
            uid = str(self.plugin._canonical_uid(event) or "")
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine.is_owner", _amo_exc)
        if owners:
            return bool(uid) and uid in owners
        try:
            if self.plugin._is_admin(event):
                return True
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine.is_owner", _amo_exc)
        if uid:
            try:
                profile = self.plugin.db.get_profile(uid) or {}
                if str(profile.get("relation") or "") == "相恋":
                    return True
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.DrawEngine.is_owner", _amo_exc)
        return False

    def _check_limits(self, uid):
        """返回 None 表示可以画；否则返回跳过原因。"""
        now = time.monotonic()
        cooldown = max(0, self._cfg_int("draw_cooldown_minutes", 30)) * 60
        last = self._last_draw.get(uid, 0.0)
        if cooldown and last and now - last < cooldown:
            return "冷却中（%d 秒）" % int(cooldown - (now - last))
        times = list(self._draw_times.get(uid, []))
        hour = [ts for ts in times if now - ts < 3600]
        max_hour = max(0, self._cfg_int("draw_max_per_hour", 3))
        if max_hour <= 0:
            return "画图限额为 0（已关闭）"
        if len(hour) >= max_hour:
            return "这一小时画满了"
        # 以前这里从 hour 里再筛 86400，等于"每天"只看最近一小时，日限额根本拦不住
        day = [ts for ts in times if now - ts < 86400]
        max_day = max(0, self._cfg_int("draw_max_per_day", 10))
        if max_day <= 0:
            return "画图限额为 0（已关闭）"
        if len(day) >= max_day:
            return "今天画满了"
        return None

    def _record(self, uid):
        now = time.monotonic()
        self._last_draw[uid] = now
        times = [ts for ts in self._draw_times.get(uid, []) if now - ts < 86400]
        times.append(now)
        self._draw_times[uid] = times

    # ---------- 触发判断 ----------

    # ---------- 场景缓存（同一场景在哪个平台问，都发同一张） ----------

    @staticmethod
    def _cache_key(prompt, keywords=None, selfie=False, residual=""):
        parts = [_today_str(), "selfie" if selfie else "scene"]
        if keywords:
            parts.append(",".join(sorted(set(keywords))))
        if residual:
            parts.append(hashlib.sha1(str(residual).encode("utf-8")).hexdigest()[:8])
        if not keywords and not residual:
            parts.append(hashlib.sha1(str(prompt or "").encode("utf-8")).hexdigest())
        return "draw_cache:" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]

    def _cache_get(self, key):
        minutes = self._cfg_int("draw_cache_minutes", 1440)
        if minutes <= 0:
            return None
        try:
            item = self.plugin.db.kv_get(key, {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine._cache_get", _amo_exc)
            return None
        if not isinstance(item, dict):
            return None
        try:
            ts = float(item.get("ts") or 0)
        except Exception:
            ts = 0.0
        if not ts or time.time() - ts > minutes * 60:
            return None
        path = str(item.get("file_path") or "")
        if path and not os.path.exists(path):
            path = ""
        url = str(item.get("url") or "")
        data_url = str(item.get("data_url") or "")
        if not path and not url and not data_url:
            return None
        return {"file_path": path, "url": url, "data_url": data_url}

    def _cache_put(self, key, item):
        if self._cfg_int("draw_cache_minutes", 1440) <= 0 or not isinstance(item, dict):
            return
        file_path = str(item.get("file_path") or "")
        url = str(item.get("url") or item.get("image_url") or "")
        data_url = str(item.get("data_url") or "")
        if not (file_path or url or data_url):
            return
        # 有本地文件/外链就够了；data_url 是 base64，存进 kv 会把库撑到几百 MB
        if data_url and (file_path or url or len(data_url) > _MAX_CACHED_DATA_URL):
            data_url = ""
        if not (file_path or url or data_url):
            return
        payload = {
            "ts": time.time(),
            "file_path": file_path,
            "url": url,
            "data_url": data_url,
        }
        try:
            self.plugin.db.kv_set(key, payload)
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine._cache_put", _amo_exc)

    @staticmethod
    def _concrete_len(text):
        """去掉问句词和语气词后还剩多少“实词”，用来判断哪句话更有画面。"""
        body = str(text or "")
        for word in QUESTION_WORDS + FILLER_WORDS:
            body = body.replace(word, "")
        body = re.sub(r"[\s，,。.！!？?~～…]+", "", body)
        return len(body)

    def _is_vague_request(self, text):
        """「你在公园吗」「公园玩的开心吗」这类问句，不能拿它本身当画面。"""
        cleaned = clean_explicit_prompt(text)
        if not cleaned:
            return True
        return any(word in text for word in QUESTION_WORDS)

    def _richer(self, *texts):
        best = ""
        for text in texts:
            text = str(text or "").strip()
            if self._concrete_len(text) > self._concrete_len(best):
                best = text
        return best

    def _activity(self):
        """她此刻在日程里那条（拿不到就空 dict）。"""
        try:
            return self.plugin.state.current_activity() or {}
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine._activity", _amo_exc)
            return {}

    def _place_text(self):
        activity = self._activity()
        return "%s %s" % (activity.get("place") or "", activity.get("activity") or "")

    @staticmethod
    def _clean_place(place):
        return clean_place(place)

    def _current_scene(self):
        """她此刻真实在哪、在干嘛（来自日程），地点换成一整句具体场景。"""
        activity = self._activity()
        where = scene_for(activity)
        act = str(activity.get("activity") or "").strip()
        if act and act not in where:
            return "%s，%s" % (where, act) if where else act
        return where or act

    def _scene_keywords(self, text):
        text = str(text or "")
        words = list(SCENE_KEYWORDS)
        extra = self._cfg_str("draw_scene_keywords", "")
        for item in extra.replace("，", ",").split(","):
            item = item.strip()
            if item and item not in words:
                words.append(item)
        found = []
        for word in words:
            if word in text and word not in found:
                found.append(word)
        return found

    @staticmethod
    def _scene_residual(text, keywords):
        """场景词以外的具体细节（喂鸽子/放风筝…），有就参与缓存键，避免被当成同一张。"""
        body = str(text or "")
        for word in keywords:
            body = body.replace(word, "")
        for word in QUESTION_WORDS + FILLER_WORDS:
            body = body.replace(word, "")
        for word in ("在", "了", "着", "和", "跟", "我", "你", "他", "她", "它", "的",
                     "是", "有", "去", "来", "那边", "这里", "那里", "一起", "看到",
                     "看见", "今天", "现在", "还", "就", "也", "都", "很", "一个",
                     "到", "玩", "逛", "走", "看", "坐", "吃", "喝", "聊", "待", "呆",
                     "转", "跑", "睡", "躺", "站", "拍", "买"):
            body = body.replace(word, "")
        body = re.sub(r"[\s，,。.！!？?~～…]+", "", body)
        return body if len(body) >= 3 else ""

    def _last_bot_line(self, event, reply_text):
        """她上一条说过的话（「拍照给我看看」时拿它当画面描述）。

        先按 uid 跨平台找（QQ 说过的话，飞书那边也认），再退回当前会话。
        """
        current = str(reply_text or "").strip()
        rows = []
        uid = ""
        try:
            uid = str(self.plugin._canonical_uid(event) or "")
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine._last_bot_line", _amo_exc)
        if uid:
            try:
                rows = self.plugin.db.messages_by_uid(uid, 12) or []
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.DrawEngine._last_bot_line", _amo_exc)
                rows = []
        if not rows:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if umo:
                try:
                    rows = self.plugin.db.recent_messages(umo, 8) or []
                except Exception as _amo_exc:
                    _note_swallowed("draw_engine.DrawEngine._last_bot_line", _amo_exc)
                    rows = []
        for row in reversed(rows):
            if str(row.get("role") or "") != "bot":
                continue
            content = str(row.get("content") or "").strip()
            if not content or content == current:
                continue
            return content
        return ""

    def _skip(self, reason):
        logger.debug("[DRAW] skip reason=%s" % reason)
        return None

    def wants_photo(self, event):
        """他这轮是不是明确在要照片（给主脑提示用：别一口拒绝）。"""
        if not self.enabled:
            return False
        try:
            if self._cfg_bool("draw_private_only", True) and not event.is_private_chat():
                return False
            if not self.is_owner(event):
                return False
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine.wants_photo", _amo_exc)
            return False
        if self._mode() == "topic":
            return False
        text = str(getattr(event, "message_str", "") or "")
        return any(kw and kw.lower() in text.lower() for kw in self._explicit_keywords())

    def decide(self, event, reply_text):
        """返回 dict(prompt, mode, reason) 或 None。"""
        if not self.enabled:
            return self._skip("draw_enable=false")
        if self._cfg_bool("draw_private_only", True) and not event.is_private_chat():
            return self._skip("不是私聊")
        if not self.is_owner(event):
            return self._skip("不是主人/相恋关系")
        text = str(getattr(event, "message_str", "") or "").strip()
        reply_text = str(reply_text or "").strip()
        if not text:
            return self._skip("空消息")
        selfie = "自拍" in text
        mode = self._mode()
        lowered = text.lower()
        explicit_hit = mode in ("explicit", "both") and any(
            kw and kw.lower() in lowered for kw in self._explicit_keywords()
        )
        topic_hit = mode in ("topic", "both") and any(
            kw and kw in text for kw in self._topic_keywords()
        )
        if not explicit_hit and not topic_hit:
            return self._skip("没命中触发词")
        if not reply_text:
            return self._skip("没有回复文本")
        if explicit_hit and any(word in reply_text for word in (
            "画好", "画了", "给你画", "这就画", "马上画", "稍等", "等我画", "发给你",
        )):
            # 模型已经自己调了画图工具，别重复画
            return self._skip("模型回复里已经在画")
        if explicit_hit:
            source = "explicit"
            cleaned = clean_explicit_prompt(text)
            max_chars = self._cfg_int("draw_prompt_max_chars", 80)
            if self._is_vague_request(text) or len(cleaned) < 2:
                # 问「你在公园吗/你在干嘛」：她到底在哪由日程决定，图也按日程里的真实地点画
                scene = self._current_scene()
                if scene:
                    subject = scene
                else:
                    previous = self._last_bot_line(event, reply_text)
                    best = self._richer(reply_text, previous)
                    subject = clean_topic_prompt(best, max_chars) if best else cleaned
            else:
                subject = cleaned
                if len(subject) < 4:
                    previous = self._last_bot_line(event, reply_text)
                    if previous:
                        subject = clean_topic_prompt(previous, max_chars)
        else:
            subject = clean_topic_prompt(reply_text, self._cfg_int("draw_prompt_max_chars", 80))
            source = "topic"
        if not subject:
            subject = clean_topic_prompt(reply_text, self._cfg_int("draw_prompt_max_chars", 80))
        if not subject and selfie:
            subject = "自拍"
        if not subject:
            return self._skip("没有可画的画面")
        if explicit_hit and self._is_vague_request(text):
            # 「你在公园吗/拍照给我看看」：拍她自己，用固定形象参考图
            selfie = True
        if not selfie and looks_like_her(text):
            # 「拍张你在工位的照片」也要拍她本人 → 自拍视角、带固定形象；
            # 「你看到的海/你眼里的窗外」是她的视角，人不出镜，保持空镜
            selfie = True
        photo_plan = None
        if selfie:
            # 照片语义：地点/姿势/守卫全部由日程计划决定；他说的地点只在日程不明时兜底
            activity = self._activity()
            explicit_place = ""
            # 只认他这句话里说的地点；不要从她上一条/日程场景句里抠（「石板路」不是地点冲突）
            for word in PLACE_HINTS:
                if word and word in str(text or ""):
                    explicit_place = word
                    break
            plan = resolve_plan(activity, mode="photo", explicit_place=explicit_place)
            photo_plan = plan
            if plan.explicit_conflict and self.debug_prompt:
                logger.info(
                    "[PHOTO_DEBUG] conflict=explicit_place:%s schedule_zone:%s → schedule_wins"
                    % (explicit_place, plan.base_zone)
                )
            where = scene_for(activity) or (explicit_place if plan.source == "explicit_place" else "")
            # 「发张自拍」：默认穿今天固定的那一套；他点名要别的衣服才按他的来
            parts = ["自拍"]
            outfit = ""
            photo = getattr(self.plugin, "photo", None)
            getter = getattr(photo, "outfit_today", None) if photo is not None else None
            if callable(getter):
                try:
                    outfit = str(getter() or "")
                except Exception as _amo_exc:
                    _note_swallowed("draw_engine.DrawEngine.decide", _amo_exc)
                    outfit = ""
            explicit_clothes = any(word in str(subject or "") for word in (
                "穿", "衣服", "裙", "衬衫", "毛衣", "外套", "卫衣", "裤", "鞋", "围巾", "帽子",
            ))
            locked_today = bool(outfit) and not explicit_clothes
            if locked_today:
                parts.append("穿着" + outfit)
            if subject and subject != "自拍" and not plan.explicit_conflict:
                # 他说的细节保留（衣服/话题），但和日程冲突的地点不写进画面
                parts.append(subject)
            if where:
                parts.append("在" + where + "，背景要能看出这个地方、不要换成别的场景")
            # 姿势：由场景规则决定（居家才允许用姿势库；公共场合不会随机到床姿）
            library = getattr(self.plugin, "pose", None)
            _pose, pose_text = pick_pose_for_plan(plan, library)
            if pose_text and "姿势" not in str(subject or ""):
                parts.append(pose_text)
            parts.append(plan.guard)
            parts.extend(["自然光", view_suffix(True), "生活感"])
            if locked_today:
                parts.append("人物长相参考模板，衣服锁定为上面的今日穿搭，不要换别的衣服，也不要照搬模板里的衣服")
            else:
                parts.append("人物长相参考模板，衣服颜色和款式以文字为准，不要照搬模板里的衣服")
            prompt = "，".join(part for part in parts if part)
            self._debug_log(activity, plan, prompt, pose_text)
        else:
            # 她拍的环境：空镜 + 她的视角，画面里不出现她本人
            prefix = self._cfg_str("photo_prompt_prefix", "自然生活照，手机随手拍，")
            prompt = (prefix + subject + "，" + SCENERY_ONLY + "，" + view_suffix(False)).strip("，")
        scene_text = " ".join(filter(None, [subject, reply_text, self._place_text()]))
        keywords = self._scene_keywords(scene_text)
        residual = self._scene_residual(scene_text, keywords)
        cache_key = self._cache_key(prompt, keywords, selfie, residual)
        uid = str(self.plugin._canonical_uid(event) or "")
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        cached = self._cache_get(cache_key)
        if cached:
            # 同一个场景在别的平台再问：直接发同一张，不重新画、也不吃冷却
            return {"prompt": prompt, "mode": source, "reason": "cache",
                    "cached": cached, "cache_key": cache_key}
        if umo and umo in self._inflight:
            return self._skip("上一张还在画")
        reason = self._check_limits(uid)
        if reason:
            if self.debug:
                logger.info("[amo] 画图跳过：%s（uid=%s）" % (reason, uid))
            return None
        last = self._last_prompt.get(uid)
        if last and last[0] == prompt and time.monotonic() - last[1] < 120:
            return self._skip("120 秒内同一句")
        return {"prompt": prompt, "mode": source, "reason": "explicit" if explicit_hit else "topic",
                "cache_key": cache_key, "selfie": selfie, "plan": photo_plan}

    def maybe_after_reply(self, event, reply_text):
        """on_decorating_result 里调用：判断 + 起一个后台任务，不阻塞。"""
        plan = self.decide(event, reply_text)
        if not plan:
            return None
        # 明确要图、但本机根本没装万象画卷：别一声不吭（用户只会以为她不理人），
        # 回一句人话说明缺什么；同一会话 10 分钟只提醒一次。
        notice = self.missing_omni_notice(event)
        if notice:
            self._spawn(self._send_text(event, notice))
            return None
        self._schedule(event, plan["prompt"], plan["reason"], plan.get("cached"),
                       plan.get("cache_key", ""), plan.get("selfie", False),
                       plan.get("plan"))
        return plan

    def missing_omni_notice(self, event):
        """这一轮该不该解释「没装画图插件」。返回要说的话，或空字符串。"""
        try:
            if not self.wants_photo(event):
                return ""
            if self._find_omni() is not None:
                return ""
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            store = getattr(self, "_missing_notice_at", None)
            if not isinstance(store, dict):
                store = {}
                self._missing_notice_at = store
            now = time.monotonic()
            last = store.get(umo)
            if last is not None and now - last < 600:
                return ""
            store[umo] = now
            logger.info("[amo] 他这轮要照片，但本机没装万象画卷：已回复说明")
            return "我这台还没装画图插件（万象画卷），装了才能给你拍照片；等我弄好再给你拍。"
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine.missing_omni_notice", _amo_exc)
            return ""

    async def _notify_failure(self, event, reason=""):
        """明确要图但生成/发送失败时，给一句人话；同一会话 10 分钟只提醒一次。"""
        try:
            if event is None:
                return
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            store = getattr(self, "_failure_notice_at", None)
            if not isinstance(store, dict):
                store = {}
                self._failure_notice_at = store
            now = time.monotonic()
            last = store.get(umo) if umo else None
            if umo and last is not None and now - last < 600:
                return
            if umo:
                store[umo] = now
            logger.warning("[DRAW] failure_notice=1 reason=%s", str(reason or "")[:80])
            await self._send_text(
                event, "这次没拍成，等会儿再给你拍；不是不理你。"
            )
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine._notify_failure", _amo_exc)

    async def _send_text(self, event, text):
        """补一句纯文字（缺插件时用；发送失败只记日志，不影响正常回复）。"""
        if MessageChain is None or not text:
            return False
        try:
            try:
                from astrbot.api.message_components import Plain
            except Exception:
                from astrbot.api.event.components import Plain
        except Exception as exc:
            logger.warning("[amo] 缺画图插件提示发不出去（拿不到 Plain）: %s" % exc)
            return False
        try:
            await event.send(MessageChain([Plain(text)]))
            return True
        except Exception as exc:
            logger.warning("[amo] 缺画图插件提示发送失败: %s" % exc)
            return False

    def manual(self, event, text):
        """``/amo draw 描述``：跳过触发词，直接画（给测试用）。"""
        if not self.enabled:
            return "画图没开（draw_enable=false）。"
        if not self.is_owner(event):
            return "这个只给主人用哦。"
        text = str(text or "").strip()
        if not text:
            return self.diagnostics()
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if umo and umo in self._inflight:
            return "上一张还在画，等它出来。"
        selfie = looks_like_her(text)
        prefix = self._cfg_str("photo_prompt_prefix", "自然生活照，手机随手拍，")
        body = text if ("自拍" in text or not selfie) else "自拍，" + text
        view = view_suffix(selfie) if selfie else (SCENERY_ONLY + "，" + view_suffix(False))
        prompt = (prefix + body + "，" + view).strip("，")
        self._schedule(event, prompt, "manual", selfie=selfie)
        tail = "（dry-run，只记提示词）" if self.dry_run else ""
        return "🎨 开始画：%s%s（画好会单独发一张）" % (text, tail)

    # ---------- 任务 ----------

    def _spawn(self, coro):
        return spawn(self.plugin, coro, "draw_engine.DrawEngine._spawn")

    def _schedule(self, event, prompt, reason, cached=None, cache_key="", selfie=False, photo_plan=None):
        uid = str(self.plugin._canonical_uid(event) or "")
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if umo:
            self._inflight.add(umo)
        self._last_prompt[uid] = (prompt, time.monotonic())
        self._stats["scheduled"] += 1
        if self.debug:
            logger.info("[amo] 画图排队（%s%s）：%s" % (reason, "/缓存" if cached else "", prompt))
        if self._spawn(self._run(event, prompt, uid, umo, cached, cache_key, selfie,
                                 photo_plan)) is None:
            if umo:
                self._inflight.discard(umo)

    async def _run(self, event, prompt, uid, umo, cached=None, cache_key="", selfie=False,
                   photo_plan=None):
        try:
            delay = max(0.0, self._cfg_float("draw_delay_seconds", 1.5))
            if delay:
                await asyncio.sleep(delay)
            if not self.enabled:
                return
            if cached:
                if await self._send_image(event, cached):
                    self._stats["cached"] += 1
                return
            if self.dry_run:
                logger.info("[amo] 画图（dry-run）不调用模型（%s）：%s" % (
                    "自拍视角" if selfie else "她的拍摄视角", prompt))
                self._stats["skipped"] += 1
                return
            image = await self._generate(event, prompt, selfie, plan=photo_plan)
            if not image:
                self._stats["failed"] += 1
                await self._notify_failure(event, self._last_error or "生成失败")
                return
            photo_engine = getattr(self.plugin, "photo", None)
            localizer = getattr(photo_engine, "_localize", None)
            if callable(localizer):
                try:
                    image = await localizer(image)
                except Exception as _amo_exc:
                    _note_swallowed("draw_engine.DrawEngine._run", _amo_exc)
            self._cache_put(cache_key, image)
            if await self._send_image(event, image):
                self._record(uid)
                self._stats["sent"] += 1
            else:
                self._stats["failed"] += 1
                await self._notify_failure(event, "发送失败")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._stats["failed"] += 1
            self._last_error = str(exc)
            logger.warning("[amo] 画图失败: %s" % exc)
            await self._notify_failure(event, str(exc))
        finally:
            if umo:
                self._inflight.discard(umo)

    # ---------- OmniDraw ----------

    def _find_omni(self):
        """拿万象画卷插件实例；没装/没启用返回 None。"""
        return find_omni(self.plugin, self, "draw_engine.DrawEngine._find_omni")

    async def _generate(self, event, prompt, selfie=False, plan=None):
        omni = self._find_omni()
        if omni is None:
            logger.warning("[amo] 没找到万象画卷（astrbot_plugin_omnidraw），画不了。")
            return None
        args = {"prompt": prompt, "count": 1, "event": event, "record_usage": True}
        if selfie:
            args["mode"] = "selfie"
        if plan is not None:
            try:
                scene_lock, pose_lock = locks_for_plan(plan)
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.DrawEngine._generate.locks", _amo_exc)
                scene_lock = pose_lock = ""
            if scene_lock:
                args["scene_lock"] = scene_lock
            if pose_lock:
                args["pose_lock"] = pose_lock
        try:
            result = await asyncio.wait_for(
                omni.generate_images_for_plugin(**args), timeout=GENERATE_TIMEOUT_SECONDS)
        except TypeError as exc:
            had_scene = args.pop("scene_lock", None) is not None
            had_pose = args.pop("pose_lock", None) is not None
            if had_scene or had_pose:
                logger.warning("[amo] 万象画卷不支持场景锁，已退回普通提示词: %s" % exc)
                result = await asyncio.wait_for(
                    omni.generate_images_for_plugin(**args), timeout=GENERATE_TIMEOUT_SECONDS)
            else:
                raise
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._last_error = "生成超时（%d 秒）" % GENERATE_TIMEOUT_SECONDS
            logger.warning("[amo] 万象画卷生成超时（%d 秒）" % GENERATE_TIMEOUT_SECONDS)
            return None
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("[amo] 万象画卷生成失败: %s" % exc)
            return None
        if not isinstance(result, dict) or not result.get("success"):
            message = ""
            if isinstance(result, dict):
                message = str(result.get("message") or "")
            self._last_error = message or "万象画卷没出图"
            logger.warning("[amo] 万象画卷没出图：%s" % (message or "未知原因"))
            return None
        images = result.get("images") or []
        if not images:
            return None
        return images[0]

    # ---------- 发送 ----------

    @staticmethod
    def _image_component(item):
        return image_component(item)

    async def _send_image(self, event, item):
        component = self._image_component(item)
        if component is None:
            logger.warning("[amo] 画图结果没有可发送的图片字段: %s" % list(item.keys()))
            return False
        if MessageChain is None:
            logger.warning("[amo] 画图发送失败：拿不到 MessageChain")
            return False
        try:
            await event.send(MessageChain([component]))
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("[amo] 画图发送失败: %s" % exc)
            return False
        return True

    # ---------- 状态 ----------

    def diagnostics(self):
        """没收到图时用来自查：开关、冷却、节点、上次报错。"""
        omni = self._find_omni()
        node_count = 0
        model = ""
        base = ""
        try:
            cfg = getattr(omni, "plugin_config", None) if omni is not None else None
            nodes = list(getattr(cfg, "providers", []) or [])
            node_count = len(nodes)
            if nodes:
                model = str(getattr(nodes[0], "model", "") or "")
                base = str(getattr(nodes[0], "base_url", "") or "")
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine.diagnostics", _amo_exc)
        lines = [self.status_text()]
        lines.append("万象画卷：" + ("已连接" if omni is not None else "没找到（插件没装/没启用）"))
        lines.append("生图节点：%d 个%s" % (
            node_count,
            ("｜%s @ %s" % (model, base)) if node_count else "（还没配 key）",
        ))
        lines.append("上次错误：" + (self._last_error or "无"))
        return "\n".join(lines)

    def status_text(self):
        omni = self._find_omni()
        mode = self._mode()
        mode_text = {"topic": "话题触发", "explicit": "明确要图", "both": "话题+明确"}.get(mode, mode)
        return "\n".join([
            "🎨 画图：%s%s" % ("开" if self.enabled else "关", "（dry-run）" if self.dry_run else ""),
            "触发：" + mode_text,
            "冷却：%d 分钟｜上限 %d/小时、%d/天" % (
                self._cfg_int("draw_cooldown_minutes", 30),
                self._cfg_int("draw_max_per_hour", 3),
                self._cfg_int("draw_max_per_day", 10),
            ),
            "万象画卷：" + ("已连接" if omni is not None else "没找到（插件没装/没启用）"),
            "本轮统计：排 %d｜发 %d｜复用 %d｜失败 %d｜跳过 %d" % (
                self._stats["scheduled"], self._stats["sent"], self._stats["cached"],
                self._stats["failed"], self._stats["skipped"],
            ),
        ])
