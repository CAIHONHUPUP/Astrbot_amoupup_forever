# -*- coding: utf-8 -*-
"""我能永远陪着你 / 我能永远陪伴你 5.4.2
一个尽量“像人”的 AstrBot 智械人类核心：
生理期 / 九维心情 / 世界观与日程 / 天气 / 健康 / 群聊插话阈值 /
私聊判定 / 主动私聊 / 长期记忆与遗忘 / 防抖 / 分段与打字延迟 / 引用回复 / AI 润色"""

from typing import TYPE_CHECKING, Any



PLUGIN_VERSION = "7.0.0"

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_social_bridge import handshake as _watch_handshake
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_social_bridge import handshake as _watch_handshake
    except ImportError:  # 直接按文件加载时
        from amo_social_bridge import handshake as _watch_handshake

import asyncio
import os
import random
import re

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_api import ApiMixin
    from .amo_commands import CommandMixin
    from .amo_config import ConfigMixin
    from .amo_kb import KbMixin
    from .amo_llm import LlmMixin
    from .amo_memory import MemoryMixin
    from .amo_message import MessageMixin
    from .amo_proactive import ProactiveMixin
    from .amo_runtime import RuntimeMixin
    from .amo_schedule import ScheduleMixin
    from .amo_social import SocialMixin
    from .amo_travel import TravelMixin
    from .amo_usage import UsageMixin
    from .clock import configure as _clock_configure
    from .clock import current_name as _clock_name
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_api import ApiMixin
        from .amo_commands import CommandMixin
        from .amo_config import ConfigMixin
        from .amo_kb import KbMixin
        from .amo_llm import LlmMixin
        from .amo_memory import MemoryMixin
        from .amo_message import MessageMixin
        from .amo_proactive import ProactiveMixin
        from .amo_runtime import RuntimeMixin
        from .amo_schedule import ScheduleMixin
        from .amo_social import SocialMixin
        from .amo_travel import TravelMixin
        from .amo_usage import UsageMixin
        from .clock import configure as _clock_configure
        from .clock import current_name as _clock_name
    except ImportError:  # 直接按文件加载时
        from amo_api import ApiMixin
        from amo_commands import CommandMixin
        from amo_config import ConfigMixin
        from amo_kb import KbMixin
        from amo_llm import LlmMixin
        from amo_memory import MemoryMixin
        from amo_message import MessageMixin
        from amo_proactive import ProactiveMixin
        from amo_runtime import RuntimeMixin
        from amo_schedule import ScheduleMixin
        from amo_social import SocialMixin
        from amo_travel import TravelMixin
        from amo_usage import UsageMixin
        from clock import configure as _clock_configure
        from clock import current_name as _clock_name

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        Context,
        GiftEngine,
        HeartflowEngine,
        SegmentEngine,
        Star,
        _now,
        clamp,
        has_question,
        logger,
        looks_like_plain_command,
        now_ts,
        overlap,
        quiet_now,
        sentiment,
        to_float,
        to_int,
        today_str,
        tokens,
        truncate,
        web_request,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            Context,
            GiftEngine,
            HeartflowEngine,
            SegmentEngine,
            Star,
            _now,
            clamp,
            has_question,
            logger,
            looks_like_plain_command,
            now_ts,
            overlap,
            quiet_now,
            sentiment,
            to_float,
            to_int,
            today_str,
            tokens,
            truncate,
            web_request,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            Context,
            GiftEngine,
            HeartflowEngine,
            SegmentEngine,
            Star,
            _now,
            clamp,
            has_question,
            logger,
            looks_like_plain_command,
            now_ts,
            overlap,
            quiet_now,
            sentiment,
            to_float,
            to_int,
            today_str,
            tokens,
            truncate,
            web_request,
        )

# ---- 三个大类（原先在 main.py，现各自独立成模块；这里再导出，外部 import 不变）----
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_db import AmoDB
    from .amo_human_state import HumanState
    from .amo_llm_service import LLMService
    from .amo_wallet import Wallet
else:  # 运行时：包模式优先，按文件直接导入时退回绝对导入
    try:
        from .amo_db import AmoDB
        from .amo_human_state import HumanState
        from .amo_llm_service import LLMService
        from .amo_wallet import Wallet
    except ImportError:  # 直接按文件加载时
        from amo_db import AmoDB
        from amo_human_state import HumanState
        from amo_llm_service import LLMService
        from amo_wallet import Wallet

# ---- 画图 / 照片分享（本地功能，接万象画卷 OmniDraw）----
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .draw_engine import DrawEngine
    from .photo_engine import PhotoEngine
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .draw_engine import DrawEngine
        from .photo_engine import PhotoEngine
    except ImportError:  # 直接按文件加载时
        try:
            from draw_engine import DrawEngine
            from photo_engine import PhotoEngine
        except Exception:  # 没装/导入失败时不影响主脑启动
            DrawEngine = None  # type: ignore[assignment,misc]
            PhotoEngine = None  # type: ignore[assignment,misc]

# ---- 穿搭衣柜（面板「穿搭」页 / 每日锁定一套）----
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .wardrobe import Wardrobe
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .wardrobe import Wardrobe
    except ImportError:  # 直接按文件加载时
        try:
            from wardrobe import Wardrobe
        except Exception:  # 没装/导入失败时不影响主脑启动
            Wardrobe = None  # type: ignore[assignment,misc]

# ---- 进程内事件总线（日程/媒体等稳定事件）----
try:
    from .amo_events import EventBus, ScheduleChanged
except ImportError:  # 直接按文件加载时
    from amo_events import EventBus, ScheduleChanged

# ---- 自拍姿势库（面板「姿势库」/ 自拍随机挑姿势）----
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .pose_library import PoseLibrary
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .pose_library import PoseLibrary
    except ImportError:  # 直接按文件加载时
        try:
            from pose_library import PoseLibrary
        except Exception:  # 没装/导入失败时不影响主脑启动
            PoseLibrary = None  # type: ignore[assignment,misc]



# ---------------------------------------------------------------------------
# 插话判定
# ---------------------------------------------------------------------------

# 关系 → 私聊回复的基础概率：恋人必回，其它按亲疏分档（配置 private_reply_prob 只管没列出来的关系）
RELATION_REPLY_PROB = {
    "相恋": 1.0, "恋人": 1.0, "爱人": 1.0,
    "家人": 0.9, "亲人": 0.9,
    "熟识": 0.7, "朋友": 0.7, "同学": 0.7, "同事": 0.7,
    "普通": 0.5, "陌生人": 0.3,
    "抵触": 0.3, "仇恨": 0.1,
}


class ReplyGate:
    def __init__(self, plugin):
        self.p = plugin

    def activity(self, group_id):
        if not group_id:
            return 0.0
        cutoff = now_ts() - 300
        times = [item for item in self.p._group_times.get(group_id, []) if item >= cutoff]
        self.p._group_times[group_id] = times
        return len(times) / 5.0

    def score_group(self, umo, group_id, text, recent, directed, profile, snap):
        rec = [m for m in recent if m.get("role") == "user"][-6:]
        rec_text = " ".join([m.get("content", "") for m in rec])
        relevance = overlap(tokens(text), tokens(rec_text)) * 100.0
        if has_question(text):
            relevance += 12
        if "你" in text or any(alias and alias in text for alias in self.p.bot_aliases):
            relevance += 10
        if directed:
            relevance += 28
        relevance = clamp(relevance)

        mood = snap["mood"]
        willingness = 50.0
        willingness += (mood["开心"] - 50) * 0.30
        willingness += (mood["兴奋"] - 50) * 0.22
        willingness += (mood["平静"] - 50) * 0.10
        willingness -= (mood["疲惫"] - 50) * 0.35
        willingness -= (mood["郁闷"] - 50) * 0.28
        willingness -= (mood["生气"] - 50) * 0.22
        willingness += (mood["依恋"] - 50) * 0.15
        relation = profile.get("relation", "普通")
        willingness += {"相恋": 22, "熟识": 10, "抵触": -30, "仇恨": -55}.get(relation, 0)
        phase = snap["period"]["phase"]
        willingness += {"排卵期": 6, "卵泡期": 3, "黄体期": -6, "月经期": -10}.get(phase, 0)
        if snap["health"]["sick"]:
            willingness -= 15
        hour = _now().hour
        if hour < 6:
            willingness -= 25
        last_reply = self.p._last_reply_at.get(umo, 0)
        if last_reply and now_ts() - last_reply < 25:
            willingness -= 22
        willingness = clamp(willingness)

        social = 72.0
        if len(text) < 2:
            social -= 20
        if re.search(r"https?://|www\.", text):
            social -= 35
        if re.search(r"(加群|代购|广告|优惠|返利|扫码|兼职)", text):
            social -= 45
        senti = sentiment(text)
        if senti["insult"]:
            social -= 40
        blocked = [word.strip() for word in self.p.cfg_str("blocked_words", "").split(",") if word.strip()]
        if any(word in text for word in blocked):
            social -= 50
        if directed:
            social += 10
        if has_question(text):
            social += 5
        social = clamp(social)

        timing = 60.0
        dt = now_ts() - last_reply if last_reply else 99999
        if dt < 15:
            timing -= 40
        elif dt < 45:
            timing -= 20
        elif dt < 120:
            timing -= 5
        elif dt > 300:
            timing += 15
        category = snap["activity"].get("category")
        if category == "sleep":
            timing -= 55
        elif category in ("work", "study"):
            timing -= 18
        rate = self.activity(group_id)
        if 2.0 <= rate <= 12.0:
            timing += 10
        elif rate > 20.0:
            timing -= 12
        if quiet_now(self.p.cfg_str("quiet_hours", "23:30-07:30")):
            timing -= 25
        timing = clamp(timing)

        last_two = [m for m in recent if m.get("role") == "user"][-2:]
        coherence = overlap(
            tokens(text), tokens(" ".join([m.get("content", "") for m in last_two]))
        ) * 100.0
        if directed:
            coherence += 18
        if re.search(r"(这个|那个|刚才|刚刚|上面|你说|你刚才)", text):
            coherence += 10
        if len(text) < 4:
            coherence -= 10
        coherence = clamp(coherence)

        weights = [
            self.p.cfg_float("gate_w_relevance", 0.30),
            self.p.cfg_float("gate_w_willingness", 0.25),
            self.p.cfg_float("gate_w_social", 0.15),
            self.p.cfg_float("gate_w_timing", 0.15),
            self.p.cfg_float("gate_w_coherence", 0.15),
        ]
        total_weight = sum(weights) or 1.0
        total = (
            relevance * weights[0] + willingness * weights[1]
            + social * weights[2] + timing * weights[3] + coherence * weights[4]
        ) / total_weight
        return {
            "relevance": round(relevance, 1),
            "willingness": round(willingness, 1),
            "social": round(social, 1),
            "timing": round(timing, 1),
            "coherence": round(coherence, 1),
            "total": round(clamp(total), 1),
        }

    def dynamic_threshold(self, activity, snap, profile):
        base = self.p.cfg_float("reply_threshold", 0)
        if base <= 0:
            legacy = self.p.cfg_float("base_reply_threshold", 0.55)
            base = legacy * 100 if legacy <= 1 else legacy
        base = clamp(base, 0, 100)

        if activity < 1:
            activity_delta = -8
        elif activity < 4:
            activity_delta = -2
        elif activity < 9:
            activity_delta = 3
        elif activity < 18:
            activity_delta = 8
        else:
            activity_delta = 12

        mood = snap["mood"]
        positive = (mood["开心"] + mood["兴奋"] + mood["平静"] + mood["依恋"]) / 4.0
        negative = (mood["郁闷"] + mood["疲惫"] + mood["生气"] + mood["委屈"] + mood["焦虑"]) / 5.0
        mood_score = clamp(50 + (positive - negative) * 0.8)
        mood_delta = (50 - mood_score) * 0.25

        relation = profile.get("relation", "普通")
        relation_delta = {"相恋": -10, "熟识": -5, "抵触": 16, "仇恨": 36}.get(relation, 0)
        health_delta = 8 if snap["health"]["sick"] else 0
        phase = snap["period"]["phase"]
        period_delta = {"月经期": 6, "黄体期": 4, "排卵期": -3, "卵泡期": -1}.get(phase, 0)
        time_delta = 10 if _now().hour in (23, 0, 1, 2, 3, 4, 5) else 0
        weather_delta = 3 if snap["weather"].get("tag") in ("rain", "storm", "snow") else 0

        value = clamp(
            base + activity_delta + mood_delta + relation_delta
            + health_delta + period_delta + time_delta + weather_delta,
            20, 92,
        )
        return {
            "value": round(value, 1),
            "base": round(base, 1),
            "activity_delta": activity_delta,
            "mood_delta": round(mood_delta, 1),
            "relation_delta": relation_delta,
            "health_delta": health_delta,
            "period_delta": period_delta,
            "time_delta": time_delta,
            "weather_delta": weather_delta,
        }

    async def should_reply_group(self, umo, group_id, text, recent, directed, profile, snap):
        scores = self.score_group(umo, group_id, text, recent, directed, profile, snap)
        activity = self.activity(group_id)
        threshold = self.dynamic_threshold(activity, snap, profile)
        diff = scores["total"] - threshold["value"]

        if diff >= 0:
            probability = 0.92
        elif diff > -8:
            probability = 0.55 + diff * 0.045
        elif diff > -20:
            probability = 0.18 + (diff + 20) * 0.046
        else:
            probability = 0.03
        probability = clamp(probability, 0.01, 0.98)
        reply = random.random() < probability

        bot_recent = [m for m in recent[-5:] if m.get("role") == "bot"]
        if not directed and len(bot_recent) >= 2 and diff < 20:
            reply = False

        used_llm = False
        band = self.p.cfg_float("llm_gate_band", 10)
        if self.p.cfg_bool("enable_llm_gate", True) and abs(diff) <= band:
            question = (
                "群聊里刚刚有人说『" + truncate(text, 80) + "』。"
                "角色现在的心情是" + snap["dominant"] + "，正在" + snap["activity"].get("activity", "休息") + "，"
                "和说话人的关系是" + profile.get("relation", "普通") + "。"
                "角色应该自然地插话回复吗？"
            )
            verdict = await self.p.llm.decide(
                umo, question,
                {
                    "recent": [m.get("content", "") for m in recent[-6:]],
                    "mood": snap["mood"],
                    "activity": snap["activity"],
                },
            )
            used_llm = True
            if verdict is not None:
                reply = verdict

        return {
            "reply": reply,
            "probability": round(probability, 2),
            "diff": round(diff, 1),
            "scores": scores,
            "threshold": threshold,
            "activity": round(activity, 2),
            "used_llm": used_llm,
            "directed": directed,
        }

    def private_probability(self, snap, profile):
        relation = str(profile.get("relation") or "普通")
        # 关系决定基础档：恋人必回；其它关系 9 成 / 7 成 / 5 成 / 3 成 / 1 成
        base = RELATION_REPLY_PROB.get(relation)
        if base is None:
            base = clamp(self.p.cfg_float("private_reply_prob", 0.5), 0.0, 1.0)
        if base >= 1.0:
            # 恋人：不看日程、心情、生病，一律回复
            return 1.0
        probability = base
        category = snap["activity"].get("category")
        probability *= {
            "sleep": 0.02, "work": 0.42, "study": 0.50,
            "commute": 0.60, "meal": 0.85,
        }.get(category, 1.0)
        probability *= {
            "感冒": 0.55, "低烧": 0.35, "轻微不适": 0.70, "疲惫": 0.70,
        }.get(snap["health"]["status"], 1.0)
        probability *= {
            "月经期": 0.80, "黄体期": 0.90, "排卵期": 1.05,
        }.get(snap["period"]["phase"], 1.0)
        mood = snap["mood"]
        if mood["生气"] > 60:
            probability *= 0.70
        if mood["郁闷"] > 60:
            probability *= 0.80
        if mood["开心"] > 65:
            probability *= 1.05
        try:
            if profile.get("uid") and self.p._depressed_active(str(profile.get("uid"))):
                probability *= 0.35
        except Exception as _amo_exc:
            _note_swallowed("main.ReplyGate.private_probability", _amo_exc)
        return clamp(probability, 0.0, 1.0)


# ---------------------------------------------------------------------------
# 插件主体
# ---------------------------------------------------------------------------

# 面板写操作路由：只注册 POST + 套 _web_guard。
# 以前守卫按「methods 里有没有 GET」判断，而写接口全被注册成 GET+POST，于是守卫一次都没执行过。
WRITE_ROUTES = frozenset({
    "/config/save", "/config_save",
    "/profile/save", "/profile_save",
    "/profile/delete", "/profile_delete",
    "/relation/save", "/relation_save",
    "/relation/delete", "/relation_delete",
    "/memory/add", "/memory_add",
    "/memory/delete", "/memory_delete",
    "/mood/update", "/mood_update",
    "/schedule/regen", "/schedule_regen",
    "/kb/regen", "/kb_regen",
    "/scenic_kb/regen", "/scenic_kb_regen",
    "/worldview/regen", "/worldview_regen",
    "/say",
    "/city/set",
    "/photo/test",
    "/photo/prompt",
    "/photo/prompt_saved",
    "/photo/sheet",
    "/photo/persona",
    "/photo/persona/delete",
    "/photo/persona/toggle",
    "/photo/template_dir",
    "/photo/template_open",
    "/draw/test",
    "/draw/decide",
    "/wardrobe/add",
    "/wardrobe/delete",
    "/wardrobe/reroll",
    "/wardrobe/today",
    "/wardrobe/night",
    "/poses/save",
    "/poses/refs",
    "/diary/write", "/diary/import", "/diary/open",
    "/refresh_status",
})


def _align_handler_module_paths(main_module):
    """把 amo_*.py 子模块里注册的 handler 归到插件主模块。

    AstrBot 的 StarHandlerRegistry 只认 `handler_module_path == 插件主模块路径` 的 handler：
    加载时按这个路径取 handler 做实例绑定（`get_handlers_by_module_name`），派发时又用
    `star_map[handler_module_path].activated` 判断插件是否启用。模块化重构后 handler 都定义在
    amo_commands / amo_runtime / amo_llm 里，路径对不上 → 20 个 handler 被静默丢掉：
    `on_message` 不跑、`/amo` 命令无效、`<amo_state>` 不注入，只有核心 LLM 还在回复。
    """
    try:
        from astrbot.core.star.star_handler import star_handlers_registry
    except Exception as exc:  # 单测/非 AstrBot 环境
        logger.debug("[amo] 跳过 handler 路径归一: %s" % exc)
        return 0
    prefix = str(main_module).rsplit(".", 1)[0] + "."
    fixed = 0
    try:
        for md in list(star_handlers_registry):
            path = str(getattr(md, "handler_module_path", "") or "")
            if path == main_module or not path.startswith(prefix):
                continue
            old_full = str(getattr(md, "handler_full_name", "") or "")
            md.handler_module_path = main_module
            new_full = str(main_module) + "_" + str(getattr(md, "handler_name", "") or "")
            md.handler_full_name = new_full
            mapping = getattr(star_handlers_registry, "star_handlers_map", None)
            if isinstance(mapping, dict) and old_full in mapping:
                mapping[new_full] = mapping.pop(old_full)
            fixed += 1
    except Exception as exc:
        logger.warning("[amo] handler 路径归一失败: %s" % exc)
        return 0
    if fixed:
        logger.info("[amo] handler 路径归一完成：%d 个（%s → 主模块）" % (fixed, prefix[:-1]))
    return fixed


_align_handler_module_paths(__name__)


class HumanCorePlugin(MemoryMixin, CommandMixin, ConfigMixin, MessageMixin, SocialMixin, TravelMixin, RuntimeMixin, KbMixin, UsageMixin, ApiMixin, ScheduleMixin, ProactiveMixin, LlmMixin, Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.events = EventBus()
        self.events.subscribe(ScheduleChanged, self._on_schedule_changed)
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._migrate_legacy_config()
        self.bot_name = self.cfg_str("bot_name", "") or "陪伴"
        # 时区：服务器常在 UTC 跑，必须按用户时区算（否则北京时间早上 10 点会被当成凌晨 2 点）
        try:
            self.timezone_name = _clock_configure(self.cfg_str("timezone", "Asia/Shanghai"))
            logger.info("[amo] 时区: %s（系统: %s）" % (self.timezone_name, _clock_name()))
        except Exception as exc:
            self.timezone_name = ""
            logger.warning("[amo] 时区初始化失败: %s" % exc)
        self.persona_name = ""
        self._persona_name_cache: dict[str, Any] = {}
        self._last_affect: dict[str, Any] = {}
        self._disturb_until: dict[str, float] = {}
        self._disturb_last: dict[str, float] = {}
        self._last_pending_check = 0.0
        self._last_proactive_activity: dict[str, Any] = {}
        self._last_bot_saved: dict[str, float] = {}
        # 近聊上下文缓冲（只在内存里；本地聊天可以不落库，用记忆终端补）
        self._ctx_buffer: dict[str, list] = {}
        self._ctx_hydrated: set[str] = set()
        self._attribution_alert: dict[str, float] = {}
        self._last_schedule_save = ""
        self._disturb_cooldown: dict[str, float] = {}
        self._disturb_reset_at: dict[str, float] = {}
        self.bot_aliases = [item.strip() for item in self.cfg_str("bot_aliases", "").split(",") if item.strip()]
        self.admin_uids = set()
        for item in self.cfg_str("admin_uids", "").split(","):
            if item.strip():
                self.admin_uids.add(item.strip())
        self.db = AmoDB(self.plugin_dir)
        # 钱包：amo 是资产权威，金融插件只读余额 / 调 credit、debit（不做 UI）
        self.wallet = Wallet(self.db)
        self.wardrobe = None
        if Wardrobe is not None:
            try:
                self.wardrobe = Wardrobe(self)
            except Exception as exc:
                logger.warning("[amo] 穿搭衣柜初始化失败: %s" % exc)
        self.pose = None
        if PoseLibrary is not None:
            try:
                self.pose = PoseLibrary(self)
            except Exception as exc:
                logger.warning("[amo] 姿势库初始化失败: %s" % exc)
        self.state: Any = None
        self.watch_info = None
        self.llm = LLMService(self)
        self.gate = ReplyGate(self)
        self.gift = None
        if GiftEngine is not None:
            try:
                self.gift = GiftEngine(self)
            except Exception as exc:
                logger.warning("[amo] 礼物引擎初始化失败: %s" % exc)
        self.draw = None
        if DrawEngine is not None:
            try:
                self.draw = DrawEngine(self)
            except Exception as exc:
                logger.warning("[amo] 画图引擎初始化失败: %s" % exc)
        self.photo = None
        if PhotoEngine is not None:
            try:
                self.photo = PhotoEngine(self)
            except Exception as exc:
                logger.warning("[amo] 照片引擎初始化失败: %s" % exc)
        if self.draw is not None:
            logger.info("[amo] 画图引擎已加载（%s）" % ("开" if self.draw.enabled else "关"))
        if self.photo is not None:
            logger.info("[amo] 照片引擎已加载（%s）" % ("开" if self.photo.enabled else "关"))
        try:
            logger.info(
                "[CONFIG] private=%s group=%s draw=%s photo=%s gift=%s diary=%s "
                "heartflow=%s schedule_change=%s",
                self.cfg_bool("enable_private_reply", True),
                self.cfg_bool("enable_group_reply", True),
                self.cfg_bool("draw_enable", False),
                self.cfg_bool("photo_enable", False),
                self.cfg_bool("gift_enable", False),
                self.cfg_bool("diary_enabled", True),
                self.cfg_bool("hf_enable_heartflow", True),
                self.cfg_bool("schedule_change_enabled", True),
            )
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin.__init__.config_summary", _amo_exc)
        self.heartflow = None
        self.segment_engine = None
        try:
            if HeartflowEngine is not None:
                hf_config = self._hf_config()
                hf_config.setdefault("enable_heartflow", True)
                hf_config.setdefault("enable_dynamic_threshold", True)
                hf_config.setdefault("judge_provider_name", "")
                self.heartflow = HeartflowEngine(self.context, hf_config)
                self.heartflow.set_identity(self.bot_name, self.bot_aliases)
            if SegmentEngine is not None:
                seg_config = self._seg_config()
                seg_config.setdefault("protect_paired_symbols", True)
                self.segment_engine = SegmentEngine(self.context, seg_config, plugin=self)
        except Exception as exc:
            logger.error("[amo] 心流/分段引擎初始化失败: %s" % exc)
        # 飞书适配器默认把每条回复都发成「引用回复」；私聊不该引用（群聊按概率）
        try:
            self._install_lark_quote_guard()
        except Exception as exc:
            logger.debug("[amo] 飞书引用守卫安装失败: %s" % exc)
        try:
            self._install_qq_quote_guard()
        except Exception as exc:
            logger.debug("[amo] QQ 引用守卫安装失败: %s" % exc)
        self._ready = False
        self._init_lock = asyncio.Lock()
        self._running = True
        self._tasks: set[Any] = set()
        # 数据插件（Astrbot_amoupup_data）可能比 amo 后加载：用这几个标记做一次性补同步，不轮询
        self._data_synced = False
        self._data_sync_lock = asyncio.Lock()
        self._last_data_sync_try = 0.0
        self._data_found_logged = False
        self._data_missing_logged = False
        self._data_warn_scheduled = False
        self._group_times: dict[str, float] = {}
        self._private_buffers: dict[str, list] = {}
        self._last_reply_at: dict[str, float] = {}
        self._last_umo = ""
        self._last_group = ""
        self._last_purge = 0.0
        self._last_auto_learn = 0.0
        self._proactive_last = 0.0
        self._last_ritual = 0.0
        self._last_relation_decay = 0.0
        self._last_relation_sediment = 0.0
        self._bot_initiated: dict[str, float] = {}
        self._prefix_cache: Any = None
        self._loop_task = None
        self._config_mtime = 0.0
        self._register_web_apis()
        logger.info("[amo] 我能永远陪伴你 %s loaded; stale web routes purged" % PLUGIN_VERSION)
        self._spawn(self._log_omni_handshake())
        self._boot_task = self._spawn(self._ensure_ready())

    # ---------- 配置 ----------














    def _command_prefixes(self):
        if self._prefix_cache is not None:
            return self._prefix_cache
        prefixes = []
        try:
            cfg = self.context.get_config()
            raw = cfg.get("wake_prefix", []) if cfg else []
            if isinstance(raw, str):
                raw = [raw]
            prefixes = [str(item) for item in (raw or []) if item]
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._command_prefixes", _amo_exc)
        self._prefix_cache = prefixes
        return prefixes

    def _looks_like_command(self, text):
        if looks_like_plain_command(text):
            return True
        first = text.split()[0].lower() if text.split() else ""
        if first in ("amo", "陪伴", "c_status", "c_profile", "c_setrel", "learn_and_gate"):
            return True
        return any(prefix and text.startswith(prefix) for prefix in self._command_prefixes())

    _on_astrbot_loaded_hook = getattr(filter, "on_astrbot_loaded", None)

    if _on_astrbot_loaded_hook is not None:
        @_on_astrbot_loaded_hook()
        async def on_astrbot_loaded(self):
            """所有插件加载完：数据插件要是比 amo 后加载，这里补做一次同步。"""
            try:
                await self._ensure_ready()
                if not self._data_synced and self._data_connected():
                    await self._sync_with_data_plugin()
            except Exception as exc:
                logger.debug("[amo] 加载完成补同步失败: %s" % exc)

    def _spawn(self, coro):
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            try:
                coro.close()
            except Exception as _amo_exc:
                _note_swallowed("main.HumanCorePlugin._spawn", _amo_exc)
            return None
        self._tasks.add(task)

        def _report(done):
            self._tasks.discard(done)
            if done.cancelled():
                return
            try:
                exc = done.exception()
            except Exception as _amo_exc:
                _note_swallowed("main.HumanCorePlugin._spawn._report", _amo_exc)
                exc = None
            if exc is not None:
                logger.warning("[amo] 后台任务异常: %s", exc, exc_info=exc)

        task.add_done_callback(_report)
        return task

    async def _ensure_ready(self):
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            try:
                await asyncio.to_thread(self.db.init)
            except Exception as exc:
                logger.error("[amo] 数据库初始化失败: %s" % exc)
            try:
                await asyncio.to_thread(self.wallet.ensure_schema)
            except Exception as exc:
                logger.error("[amo] 钱包初始化失败: %s" % exc)
            self._migrate_delay_default()
            self._migrate_proactive_timing()
            self._migrate_typing_defaults()
            self._migrate_legacy_admin_default()
            self._migrate_scenic_kb_name()
            self._migrate_schedule_defaults()
            self._migrate_diary_continuity()
            await asyncio.to_thread(self._merge_alias_profiles)
            self.state = HumanState(self)
            try:
                await self._sync_with_data_plugin()
            except Exception as exc:
                logger.debug("[amo] 与数据插件同步失败: %s" % exc)
            self._ready = True
            try:
                retry = getattr(self, "_retry_web_api_routes", None)
                if callable(retry):
                    retry()
            except Exception as _amo_exc:
                _note_swallowed("main.HumanCorePlugin._ensure_ready.web_retry", _amo_exc)
            self._spawn(self._log_omni_handshake())
            self._spawn(self._watch_handshake())
            self._loop_task = self._spawn(self._background_loop())
            self._spawn(self._warm_up())





    async def _warm_up(self):
        try:
            await asyncio.sleep(3)
            umo = self._last_umo or self._first_private_umo()
            await self._sync_persona_name(umo or "")
            await self._ensure_dialect_kb()
            await self._ensure_scenic_kb()
            await self.state.ensure_worldview(umo)
            await self.state.ensure_schedule(umo)
        except Exception as exc:
            logger.debug("[amo] 预热失败: %s" % exc)

    async def _log_omni_handshake(self):
        """启动时主动和万象画卷握手一次；没装也不报错，生图前还会再找。"""
        try:
            from .amo_image_common import find_omni
        except Exception:
            try:
                from amo_image_common import find_omni
            except Exception:
                return
        try:
            engine = getattr(self, "photo", None) or getattr(self, "draw", None)
            if engine is None:
                return
            omni = None
            for attempt in range(8):
                omni = find_omni(self, engine, "main._log_omni_handshake", force=True)
                if omni is not None:
                    break
                await asyncio.sleep(2)
            if omni is None:
                logger.info(
                    "[amo] 没装「万象画卷」(astrbot_plugin_omnidraw)：照片/画图暂时用不了，"
                    "用户要图时会回一句说明；装好并启用后会自动接上，不用重启"
                )
                return
            cfg = getattr(omni, "plugin_config", None)
            nodes = list(getattr(cfg, "providers", []) or [])
            model = str(getattr(nodes[0], "model", "") or "") if nodes else ""
            logger.info("[amo] 已握手万象画卷，可直接生图（生图节点 %d 个%s）" % (
                len(nodes), ("，模型 " + model) if model else ""))
        except Exception as exc:
            logger.debug("[amo] 万象画卷握手检查失败: %s" % exc)

    async def _watch_handshake(self):
        """和「我能永远守护你」（Astrbot_amoupup_watch）握一次手。

        只做发现 + describe()：没装 / 停用 / 加载失败 / 版本不匹配都只记日志，
        不影响聊天。社会逻辑全在 watch 自己的插件里，amo 一行都不碰。
        """
        try:
            info = await _watch_handshake(self)
        except Exception as exc:
            _note_swallowed("main.HumanCorePlugin._watch_handshake", exc)
            return None
        self.watch_info = info
        return info

    def _on_schedule_changed(self, event):
        """日程事件订阅者：日记失效/取消 + 穿搭失效 + 生图刷新日志。"""
        day = str(getattr(event, "day", "") or "")
        version = int(getattr(event, "version", 0) or 0)
        start_hour = int(getattr(event, "start_hour", -1) or -1)
        cancelled: list[Any] = []
        state = getattr(self, "state", None)
        if state is not None and start_hour >= 0:
            try:
                state.db.mark_diaries_stale(day, start_hour, version)
            except Exception as exc:
                logger.warning("[EVENT] diary_stale failed: %s", exc)
            cancel = getattr(self, "_cancel_diary_tasks", None)
            if callable(cancel):
                try:
                    cancelled = cancel(day, start_hour) or []
                except Exception as exc:
                    logger.warning("[EVENT] diary_cancel failed: %s", exc)
            daily_mark = getattr(self, "_mark_daily_diary_stale", None)
            if callable(daily_mark):
                try:
                    daily_mark(day, version)
                except Exception as exc:
                    logger.warning("[EVENT] daily_diary_stale failed: %s", exc)
        wardrobe = getattr(self, "wardrobe", None)
        if wardrobe is not None:
            invalidate = getattr(wardrobe, "invalidate", None)
            if callable(invalidate):
                try:
                    invalidate(day, reason="schedule_changed")
                except Exception as exc:
                    logger.warning("[EVENT] wardrobe_invalidate failed: %s", exc)
        photo = getattr(self, "photo", None)
        hook = getattr(photo, "on_schedule_changed", None) if photo is not None else None
        if callable(hook):
            try:
                hook(day, version, "schedule_changed")
            except Exception as exc:
                logger.warning("[EVENT] photo_refresh failed: %s", exc)
        logger.debug("[EVENT] schedule_changed day=%s version=%s start_hour=%s cancelled=%s",
                     day, version, start_hour, len(cancelled))
        return cancelled

    async def initialize(self):
        await self._ensure_ready()

    async def terminate(self):
        self._running = False
        if self.heartflow is not None:
            try:
                await self.heartflow.terminate()
            except Exception as _amo_exc:
                _note_swallowed("main.HumanCorePlugin.terminate", _amo_exc)
        if self.segment_engine is not None:
            for task in list(getattr(self.segment_engine, "_tasks", set())):
                try:
                    task.cancel()
                except Exception as _amo_exc:
                    _note_swallowed("main.HumanCorePlugin.terminate", _amo_exc)
        for task in list(self._tasks):
            try:
                task.cancel()
            except Exception as _amo_exc:
                _note_swallowed("main.HumanCorePlugin.terminate", _amo_exc)
        if self.state is not None:
            try:
                self.state.save()
            except Exception as _amo_exc:
                _note_swallowed("main.HumanCorePlugin.terminate", _amo_exc)
        # 不在这里关闭数据库：AstrBot 重载插件时旧实例注册的 Web API
        # 可能仍被调用，关闭连接会导致 "Cannot operate on a closed database"。
        # 连接由进程退出时回收；AmoDB.conn 也会在需要时自动重连。

    async def stop(self):
        await self.terminate()

    # ---- 跨平台账号合并 ----


    # ---------- 管理员 ----------

    # ---------- 消息学习 ----------




    # ---------- 近聊上下文（内存缓冲 + 记忆终端钩子） ----------
    CONTEXT_BUFFER_LIMIT = 60














    # ---------- 配套数据插件（Astrbot_amoupup_data / 我能永远记住你） ----------
    DATA_PLUGIN_NAMES = ("Astrbot_amoupup_data", "Astr_Amo_data", "astrbot_plugin_amo_data", "AstrAmoData")


    # ---------- 记忆能力层（memory_hooks）：陪伴只判定，数据交给挂载插件 ----------


    def _plugin_by_names(self, names):
        """按插件名找实例；不缓存，reload 之后拿到的是新实例。

        停用的插件还挂在内存里（`activated=False`），这里必须当它不存在：
        否则停用「想记」之后，记忆仍会往它身上写，用户却看不到任何数据。
        """
        for name in names:
            try:
                meta = self.context.get_registered_star(name)
            except Exception as _amo_exc:
                _note_swallowed("main.HumanCorePlugin._plugin_by_names", _amo_exc)
                meta = None
            if getattr(meta, "activated", True) is False:
                continue
            star = self._star_instance(meta)
            if star is not None:
                return star
        try:
            wanted = [str(item).lower() for item in names]
            for meta in self.context.get_all_stars():
                if getattr(meta, "activated", True) is False:
                    continue
                module = str(getattr(meta, "module_path", "") or "").lower()
                label = str(getattr(meta, "name", "") or "").lower()
                if any(word in module or word in label for word in wanted):
                    star = self._star_instance(meta)
                    if star is not None:
                        return star
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._plugin_by_names", _amo_exc)
        return None


    def _note_data_missing(self):
        """第一次没找到数据插件：记一次日志，并在 60 秒后再确认一次（只提醒一次）。"""
        if not self._data_missing_logged:
            self._data_missing_logged = True
            logger.info(
                "[amo] 未检测到数据插件 Astrbot_amoupup_data（没装或已停用）："
                "这期间聊天正常，但记忆不会写入，面板记忆页是空的；装好并启用后一分钟内会自动接上"
            )
        if not self._data_warn_scheduled:
            self._data_warn_scheduled = True
            self._spawn(self._warn_data_missing_later())

    async def _warn_data_missing_later(self, delay=60.0):
        try:
            await asyncio.sleep(max(1.0, float(delay)))
        except asyncio.CancelledError:
            return
        if not self._running or self._data_synced or self._data_connected():
            return
        logger.warning(
            "[amo] 数据插件未连接：去「插件」页看 Astrbot_amoupup_data 是不是被停用/加载报错；"
            "装好并启用后一分钟内会自动接上，不用重启"
        )


    # 平台名 -> 显示名。Amo 在 QQ 和飞书上都要认得出同一个人。
    PLATFORM_LABELS = {
        "aiocqhttp": "QQ", "qq": "QQ", "qq_official": "QQ", "onebot": "QQ", "napcat": "QQ",
        "lark": "飞书", "feishu": "飞书",
        "wecom": "企业微信", "wework": "企业微信", "dingtalk": "钉钉",
        "telegram": "Telegram", "discord": "Discord", "slack": "Slack",
        "webchat": "网页", "wechat": "微信", "wx": "微信",
    }

    def _platform_of(self, event=None):
        """当前消息来自哪个平台（aiocqhttp / lark …），失败就退回 umo 前缀。"""
        name = ""
        try:
            name = str(event.get_platform_name() or "")
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._platform_of", _amo_exc)
            name = ""
        if not name:
            try:
                name = str(event.unified_msg_origin or "").split(":", 1)[0]
            except Exception as _amo_exc:
                _note_swallowed("main.HumanCorePlugin._platform_of", _amo_exc)
                name = ""
        name = name.strip().lower()
        if name:
            seen = getattr(self, "_seen_platforms", None)
            if seen is None:
                seen = set()
                self._seen_platforms = seen
            seen.add(name)
        return name

    def _platform_label(self, platform=""):
        key = str(platform or "").strip().lower()
        if not key:
            return "这里"
        return self.PLATFORM_LABELS.get(key, str(platform).strip())

    @staticmethod
    def _event_message_id(event):
        """取平台消息 id（QQ 的 message_id / 飞书的 event id），用于聊天记录去重。"""
        for holder in (getattr(event, "message_obj", None), event):
            for attr in ("message_id", "msg_id", "id"):
                try:
                    value = getattr(holder, attr, None)
                except Exception as _amo_exc:
                    _note_swallowed("main.HumanCorePlugin._event_message_id", _amo_exc)
                    value = None
                if value not in (None, "", 0):
                    return str(value)[:128]
        return ""



















    def _cleanup_auto_profiles(self, source, real_uids):
        """删除记忆源自动创建、但不在真实画像列表里的历史空画像。"""
        real = {str(uid) for uid in real_uids}
        removed = 0
        try:
            for row in self.db.list_profiles(limit=1000):
                uid = str(row.get("uid") or "")
                if not uid or uid in real:
                    continue
                if str(row.get("platform") or "") not in ("angel_memory", "memory_companion"):
                    continue
                if to_int(row.get("msg_count"), 0) > 1:
                    continue
                if str(row.get("private_umo") or ""):
                    continue
                self.db.delete_profile(uid)
                removed += 1
            if removed:
                logger.info("[amo] 已清理 %d 个非画像用户（%s）" % (removed, source))
        except Exception as exc:
            logger.debug("[amo] 清理非画像用户失败: %s" % exc)




# ---------- livingmemory（只读直连） ----------












    # ---------- 消息入口 ----------











    # ---------- 发送 ----------






    # ---------- LLM 注入 ----------


    LEAN_PARTS = {
        "【昵称区分】": None,
        "【角色边界】": "【角色边界】他说“我在开车/在酒店/刚逛完”说的是他自己，不是你的经历。",
        "【身体归属】": "【身体归属】他的伤/病/疼/被打都是他的事，不是你的事；别说“我也疼/我脸不疼了”。",
        "【引用规则】": "【引用规则】引用的话按引用头算是谁说的，不要当成自己说过的。",
        "【天气一致】": "【天气一致】天气以【天气】为准，不要说反。",
        "【地点写法】": "【地点写法】地点用口语说，不要念“城市·地点”。",
        "【别编】": "【别编】对方没明说的（进店/点好/买好/在开车）不要替他当成事实。",
        "【别翻旧账】": "【别翻旧账】同一个问题再问就正常再答，不说“刚说过了/问几遍了”。",
        "【问题怎么答】": "【问题怎么答】问吃什么：先说你自己吃什么，再给一两个具体建议。",
        "【事实锁定】": "【事实锁定】地点/在做什么/有没有出门以【正在】为准，不编和地点矛盾的行程；他记错或试探时按日程纠正，别顺着他改口。",
        "【聊天格式】": "【聊天格式】不要复述 @昵称、账号、群号、ID。",
        "【长度】": "【长度】默认 1-2 句，能一句说清就一句，不列点不总结。",
        "【人格】": "【人格】保持人格设定，不要变成客服或通用助手。",
    }




    # ---------- 后台循环 ----------








    def _pending_key(self, uid):
        return "proactive_pending_" + str(uid)


    def _depressed_active(self, uid):
        return to_float(self.db.kv_get("proactive_depressed_until_" + str(uid), 0), 0) > now_ts()

    def _set_depressed(self, uid):
        hours = max(1, self.cfg_int("proactive_depressed_hours", 24))
        self.db.kv_set("proactive_depressed_until_" + str(uid), now_ts() + hours * 3600)


    # 每天的固定问候：kind、开关、时段、默认时段
    RITUAL_SPECS = (
        ("goodnight", "ritual_goodnight", "ritual_goodnight_window", "22:00-02:00"),
        ("morning", "ritual_morning", "ritual_morning_window", "06:30-10:00"),
        ("lunch", "ritual_lunch", "ritual_lunch_window", "11:00-13:30"),
    )

    FOLLOWUP_TEMPLATES = {
        "slack": [
            "在忙吗？怎么不理我。",
            "老公老公，你在忙着什么呀，这么久不理我。",
            "喂——人呢，是不是又把我晾一边了。",
            "忙完了没呀，我在这儿等半天了。",
            "是不是我太吵了？那我安静会儿，你有空回我一下。",
            "你这会儿在忙啥呢，回我一句我就不闹了。",
        ],
        "greet": [
            "？怎么不理我了？",
            "你是不是没看见我呀。",
            "醒醒，在等你呢。",
            "我在这儿呢，你倒是看我一眼呀。",
            "喂喂，人呢？",
        ],
        "meal": [
            "饭吃了没？",
            "到饭点了，别又随便对付一口。",
            "吃了啥呀，说给我听听。",
            "中午吃点什么呀，别又忘了。",
        ],
        "offwork": [
            "今天过得怎么样呀？",
            "你忙完记得回我一句嘛。",
            "你是还在加班吗？",
            "下班没呀，别又拖到很晚。",
        ],
        "comfort": [
            "好点了吗？",
            "还难受吗，要不要跟我说说。",
            "舒服点没有呀。",
            "还疼不疼，别硬撑着。",
        ],
    }






    # ---- 主动消息节流 ----


    def _forced_hour_key(self, uid):
        return "proactive_forced_hour_" + str(uid or "")

    def _forced_sent_this_hour(self, uid):
        """「日常关心」每小时只强制一次；存数据库，热重载/重启都不清空。"""
        mark = today_str() + ":" + str(_now().hour)
        try:
            return str(self.db.kv_get(self._forced_hour_key(uid), "")) == mark
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._forced_sent_this_hour", _amo_exc)
            return self._last_proactive_activity.get(str(uid)) == mark

    def _mark_forced_hour(self, uid):
        mark = today_str() + ":" + str(_now().hour)
        self._last_proactive_activity[str(uid)] = mark
        try:
            self.db.kv_set(self._forced_hour_key(uid), mark)
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._mark_forced_hour", _amo_exc)

    # ---- 主动消息多平台轮换 ----
    def _record_channel(self, uid, umo, platform):
        """记下这个人各平台的会话地址，供主动消息选边。"""
        if not uid or not umo:
            return
        try:
            key = "channels:%s" % uid
            data = self.db.kv_get(key, {}) or {}
            if not isinstance(data, dict):
                data = {}
            data[str(platform or "?")] = {"umo": str(umo), "ts": now_ts()}
            self.db.kv_set(key, data)
        except Exception as exc:
            logger.debug("[amo] 记录通道失败: %s" % exc)





    # ---------- 指令 ----------



    # ---------- 心流完整版指令 ----------








    # ---------- Web 面板 ----------
        # 不注册无前缀的旧路由，避免和其他插件或 AstrBot 自带路由冲突。


    def _safe_register(self, route, handler, methods, desc):
        # 写接口只收 POST：面板的 WRITE_ACTIONS 已经强制带 body 走 POST，
        # 老版本面板桥接没有 apiPost 时会收到 405/403，然后自动退回 fetch POST（前端有兜底）。
        # 读接口不套守卫，避免给状态轮询加额外开销。
        # 注意：route 带插件名前缀（/Astrbot_amoupup_forever/...），WRITE_ROUTES 里存的是不带前缀的
        # 路径，所以要拿 desc（注册时传的 path）来判定，否则所有写路由都会被漏掉。
        is_write = str(desc or "") in WRITE_ROUTES
        if is_write:
            methods = ["POST"]
        if is_write:
            handler = self._web_guard(handler, is_write=True)
        try:
            self.context.register_web_api(route, handler, methods, desc)
            return True
        except TypeError:
            pass
        except Exception as exc:
            logger.warning("[WEB_REG] route=%s ok=0 error=%s", route, exc)
            return False
        try:
            self.context.register_web_api(route, methods, desc, handler)
            return True
        except Exception as exc:
            logger.warning("[WEB_REG] route=%s ok=0 legacy_error=%s", route, exc)
            return False

    def _q(self, key, default=None):
        try:
            if web_request is not None:
                return web_request.query.get(key, default)
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._q", _amo_exc)
        return default

    def _q_int(self, key, default=0):
        return to_int(self._q(key, default), default)

    async def _body(self):
        try:
            if web_request is not None and hasattr(web_request, "json"):
                data = await web_request.json(default={})
                return data if isinstance(data, dict) else {}
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._body", _amo_exc)
        try:
            if web_request is not None:
                data = await web_request.get_json(silent=True)
                return data or {}
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._body", _amo_exc)
        return {}


    # 这些值绝不能随 /config 明文下发（GET 无 body、无 CSRF 校验）
    SENSITIVE_KEYS = {"hefeng_key", "deepseek_api_key"}


    # ---- 中文城市名 → LocationID（离线表，见 amo_city_index.py） ----








    # ---- 用量 / 余额 ----


    # ---- 供应商识别 / 多模型用量 ----




















