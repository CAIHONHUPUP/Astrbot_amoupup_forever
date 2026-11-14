# -*- coding: utf-8 -*-
"""我能永远陪着你 / 我能永远陪伴你 5.4.2
一个尽量“像人”的 AstrBot 智械人类核心：
生理期 / 九维心情 / 世界观与日程 / 天气 / 健康 / 群聊插话阈值 /
私聊判定 / 主动私聊 / 长期记忆与遗忘 / 防抖 / 分段与打字延迟 / 引用回复 / AI 润色"""

from typing import TYPE_CHECKING, Any


PLUGIN_VERSION = "7.1.0"

try:
    from .amo_feature_api import FeatureApiMixin
except ImportError:
    from amo_feature_api import FeatureApiMixin

try:  # 包模式优先
    from .compat import load
except ImportError:  # 按文件直接加载
    from compat import load

_note_swallowed = load(".amo_observability", "amo_observability", "note")
_watch_handshake = load(".amo_social_bridge", "amo_social_bridge", "handshake")

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
    for _mod, _name in (
        ("amo_api", "ApiMixin"), ("amo_commands", "CommandMixin"), ("amo_config", "ConfigMixin"),
        ("amo_kb", "KbMixin"), ("amo_llm", "LlmMixin"), ("amo_memory", "MemoryMixin"),
        ("amo_message", "MessageMixin"), ("amo_proactive", "ProactiveMixin"),
        ("amo_runtime", "RuntimeMixin"), ("amo_schedule", "ScheduleMixin"),
        ("amo_social", "SocialMixin"), ("amo_travel", "TravelMixin"), ("amo_usage", "UsageMixin"),
    ):
        globals()[_name] = load("." + _mod, _mod, _name)
    _clock_configure = load(".clock", "clock", "configure")
    _clock_name = load(".clock", "clock", "current_name")

    _n = "Context"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "GiftEngine"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "HeartflowEngine"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "SegmentEngine"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "Star"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "_now"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "clamp"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "has_question"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "logger"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "looks_like_plain_command"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "now_ts"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "overlap"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "quiet_now"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "sentiment"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "to_float"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "to_int"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "today_str"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "tokens"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "truncate"; globals()[_n] = load(".amo_shared", "amo_shared", _n)
    _n = "web_request"; globals()[_n] = load(".amo_shared", "amo_shared", _n)

# ---- 三个大类（原先在 main.py，现各自独立成模块；这里再导出，外部 import 不变）----
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_db import AmoDB
    from .amo_human_state import HumanState
    from .amo_llm_service import LLMService
    from .amo_wallet import Wallet
else:  # 运行时：包模式优先，按文件直接导入时退回绝对导入
    AmoDB = load(".amo_db", "amo_db", "AmoDB")
    HumanState = load(".amo_human_state", "amo_human_state", "HumanState")
    HungerEngine = load(".amo_hunger", "amo_hunger", "HungerEngine")
    LLMService = load(".amo_llm_service", "amo_llm_service", "LLMService")
    Wallet = load(".amo_wallet", "amo_wallet", "Wallet")

# ---- 画图 / 照片分享（本地功能，接万象画卷 OmniDraw）----
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .draw_engine import DrawEngine
    from .photo_engine import PhotoEngine
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    DrawEngine = load(".draw_engine", "draw_engine", "DrawEngine")
    PhotoEngine = load(".photo_engine", "photo_engine", "PhotoEngine")

# ---- 穿搭衣柜（面板「穿搭」页 / 每日锁定一套）----
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .wardrobe import Wardrobe
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    Wardrobe = load(".wardrobe", "wardrobe", "Wardrobe")

# ---- 进程内事件总线（日程/媒体等稳定事件）----
EventBus = load(".amo_events", "amo_events", "EventBus")
ScheduleChanged = load(".amo_events", "amo_events", "ScheduleChanged")

# ---- 自拍姿势库（面板「姿势库」/ 自拍随机挑姿势）----
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .pose_library import PoseLibrary
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    PoseLibrary = load(".pose_library", "pose_library", "PoseLibrary")


# ---------------------------------------------------------------------------
# 插话判定（ReplyGate 独立成模块，这里只再导出，外部 import 不变）
# ---------------------------------------------------------------------------
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_reply_gate import ReplyGate
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    ReplyGate = load(".amo_reply_gate", "amo_reply_gate", "ReplyGate")
    _n = "WebRouteMixin"; globals()[_n] = load(".amo_routes", "amo_routes", _n)


# ---------------------------------------------------------------------------
# 插件主体
# ---------------------------------------------------------------------------

# 面板写操作路由：只注册 POST + 套 _web_guard。
# 以前守卫按「methods 里有没有 GET」判断，而写接口全被注册成 GET+POST，于是守卫一次都没执行过。
# 面板写路由 / handler 路径归一（独立模块）
WRITE_ROUTES = load(".amo_routes", "amo_routes", "WRITE_ROUTES")
_align_handler_module_paths = load(".amo_routes", "amo_routes", "align_handler_module_paths")
_align_handler_module_paths(__name__)


class HumanCorePlugin(MemoryMixin, CommandMixin, ConfigMixin, MessageMixin, SocialMixin, TravelMixin, RuntimeMixin, KbMixin, UsageMixin, ApiMixin, ScheduleMixin, ProactiveMixin, LlmMixin, WebRouteMixin, Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self._init_basic(config)
        self._init_engines()
        self._init_runtime()
        self._register_web_apis()
        logger.info("[amo] 我能永远陪伴你 %s loaded; stale web routes purged" % PLUGIN_VERSION)
        self._spawn(self._log_omni_handshake())
        self._boot_task = self._spawn(self._ensure_ready())

    def _init_basic(self, config):
        """配置/事件/时区/各种内存状态（原 __init__ 前半段）。"""
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

    def _init_engines(self):
        """db / 钱包 / 各引擎（原 __init__ 中段）。"""
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

    def _init_runtime(self):
        """引用守卫 / 运行标记 / 计数器（原 __init__ 后半段）。"""
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

    def _omnidraw_self_use_hint(self):
        """提醒：万象画卷要装作者仓库的「能陪自用版」，上游版一次自拍可能发两张。"""
        try:
            ctx = getattr(self, "context", None)
            getter = getattr(ctx, "get_registered_star", None) if ctx else None
            meta = getter("astrbot_plugin_omnidraw") if callable(getter) else None
            if meta is None:
                return
            version = str(getattr(meta, "version", "") or "")
            name = str(getattr(meta, "display_name", "") or "")
            if version.startswith(("3.4.3", "3.4.4")) or "自用" in name:
                return
            logger.warning(
                "[amo] 提醒：万象画卷不是「能陪自用版」（当前 %s）。上游版一次自拍可能发两张；"
                "请安装作者仓库 CAIHONHUPUP/astrbot_plugin_omnidraw-amoupup 的 v3.4.3-amoupup。",
                version or "未知版本")
        except Exception as _amo_exc:
            _note_swallowed("amo.main.omnidraw_hint", _amo_exc)

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
            self.hunger = HungerEngine(self)
            try:
                await self._sync_with_data_plugin()
            except Exception as exc:
                logger.debug("[amo] 与数据插件同步失败: %s" % exc)
            self._ready = True
            self._omnidraw_self_use_hint()
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
        否则停用「能记」之后，记忆仍会往它身上写，用户却看不到任何数据。
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


    # ---- 主动消息多平台轮换 ----


    # ---------- 指令 ----------


    # ---------- 心流完整版指令 ----------


    # ---------- Web 面板 ----------
        # 不注册无前缀的旧路由，避免和其他插件或 AstrBot 自带路由冲突。


    # 这些值绝不能随 /config 明文下发（GET 无 body、无 CSRF 校验）
    SENSITIVE_KEYS = {"hefeng_key", "deepseek_api_key"}


    # ---- 中文城市名 → LocationID（离线表，见 amo_city_index.py） ----


    # ---- 用量 / 余额 ----


    # ---- 供应商识别 / 多模型用量 ----




# 把对外状态/事件 API 挂到插件类上（避免多继承影响 Star 初始化）
for _feat_name in dir(FeatureApiMixin):
    if _feat_name.startswith("_"):
        continue
    try:
        setattr(HumanCorePlugin, _feat_name, getattr(FeatureApiMixin, _feat_name))
    except Exception:
        pass
