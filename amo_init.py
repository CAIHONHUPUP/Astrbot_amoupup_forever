# -*- coding: utf-8 -*-
"""初始化装配（2026-11-17 从 main.py 拆出）：配置/事件/引擎/运行标记。

为什么单独成 mixin：main.py 只剩「装配 + 消息入口」，这三段初始化共 170 余行，
和消息处理无关，却让装配文件越来越长。方法体逐字搬运，未改任何文本/默认值。

顺序固定：_init_basic（配置/时区/内存状态）→ _init_engines（db/钱包/引擎）→
_init_runtime（守卫/运行标记/任务管理），由 HumanCorePlugin.__init__ 调用。
"""
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

try:
    from .amo_events import EventBus, ScheduleChanged
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_events import EventBus, ScheduleChanged

try:
    from .clock import configure as _clock_configure, current_name as _clock_name
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from clock import configure as _clock_configure, current_name as _clock_name

try:
    from .amo_db import AmoDB
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_db import AmoDB

try:
    from .wardrobe import Wardrobe
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from wardrobe import Wardrobe

try:
    from .pose_library import PoseLibrary
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from pose_library import PoseLibrary

try:
    from .amo_llm_service import LLMService
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_llm_service import LLMService

try:
    from .micro_events import MicroEventEngine
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from micro_events import MicroEventEngine

try:
    from .housing import Housing
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from housing import Housing

try:
    from .amo_reply_gate import ReplyGate
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_reply_gate import ReplyGate

try:
    from .draw_engine import DrawEngine
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from draw_engine import DrawEngine

try:
    from .photo_engine import PhotoEngine
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from photo_engine import PhotoEngine

try:
    from .amo_shared import GiftEngine, HeartflowEngine, SegmentEngine, logger
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_shared import GiftEngine, HeartflowEngine, SegmentEngine, logger

try:
    from .amo_jobs import JobManager
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_jobs import JobManager

try:
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_observability import note as _note_swallowed


class _NoMoney:
    """钱包已退役：不记账、不扣款、余额不参与判断（保留属性名给未删的调用点）。"""

    # 兼容说明（2026-11-24 收口）：balance / balance_cents / total_assets 只为旧调用点
    # 保留属性名，恒返回 10^9 分；禁止再把它们写进任何提示词或决策分支，
    # 新增代码不要再读余额——读到的永远是常量，只会制造走不到的死分支。

    def __init__(self, db=None):
        self.db = db

    def ensure_schema(self):
        return None

    def balance(self, *args, **kwargs):
        return 10 ** 9

    def balance_cents(self, *args, **kwargs):
        return 10 ** 9

    def total_assets(self, *args, **kwargs):
        return 10 ** 9

    def debit(self, *args, **kwargs):
        return True

    def credit(self, *args, **kwargs):
        return True

    def ledger(self, *args, **kwargs):
        return []


class InitMixin:
    """配置/引擎/运行标记初始化（宿主由 HumanCorePlugin 提供）。"""

    if TYPE_CHECKING:  # 只给类型检查看：宿主提供的成员
        context: Any
        bot_name: str
        bot_aliases: list[str]

        def cfg_str(self, *args: Any, **kwargs: Any) -> str: ...
        def cfg_bool(self, *args: Any, **kwargs: Any) -> bool: ...
        def run_config_migrations(self, *args: Any, **kwargs: Any) -> Any: ...
        def _hf_config(self) -> dict[str, Any]: ...
        def _seg_config(self) -> dict[str, Any]: ...
        def _install_lark_quote_guard(self) -> Any: ...
        def _install_qq_quote_guard(self) -> Any: ...
        def _spawn(self, coro: Any) -> Any: ...
        async def _ensure_ready(self) -> Any: ...
        def _on_schedule_changed(self, event: Any) -> Any: ...
    def _init_basic(self, config):
        """配置/事件/时区/各种内存状态（原 __init__ 前半段）。"""
        self.config = config or {}
        self.events = EventBus()
        self.events.subscribe(ScheduleChanged, self._on_schedule_changed)
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.run_config_migrations(boot=True)
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
        self.wallet = _NoMoney(self.db)
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
        self.llm = LLMService(self)
        try:
            from .amo_heart import HeartEngine
        except ImportError:
            from amo_heart import HeartEngine
        self.heart = HeartEngine()
        self.micro_events = None
        if MicroEventEngine is not None:
            try:
                self.micro_events = MicroEventEngine(self)
                logger.info("[amo] 微型日常事件引擎已加载")
            except Exception as exc:
                logger.warning("[amo] 微型日常事件初始化失败: %s" % exc)
        self.housing = None
        if Housing is not None:
            try:
                self.housing = Housing(self)
                logger.info("[amo] 房屋系统已加载（%s）" % (self.housing.current().get("name") or "家"))
            except Exception as exc:
                logger.warning("[amo] 房屋系统初始化失败: %s" % exc)
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
        # 第 1 刀：缺模块/缺守卫必须显式报错，不再静默降级（2026-11-23 收件人守卫事故）。
        try:
            try:
                from .amo_guard_health import assert_guards
            except ImportError:
                from amo_guard_health import assert_guards
            assert_guards(self)
        except Exception as exc:
            logger.error("[amo] 守卫健康检查自身失败: %s" % exc)

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
        # Phase 16：后台任务统一交给 JobManager（受管 spawn / 单飞 / 超时 / 退避）
        self.jobs = JobManager(logger=logger, tag="amo", startup_delay=4.0)
        self._tasks: set[Any] = self.jobs.spawned
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
        self._proactive_running = False  # 主动消息单飞（2026-11-19）
        self._last_ritual = 0.0
        self._last_relation_decay = 0.0
        self._last_relation_sediment = 0.0
        self._bot_initiated: dict[str, float] = {}
        self._bot_initiated_text: dict[str, str] = {}
        self._prefix_cache: Any = None
        self._loop_task = None
        self._config_mtime = 0.0
        self._boot_task = self._spawn(self._ensure_ready())
