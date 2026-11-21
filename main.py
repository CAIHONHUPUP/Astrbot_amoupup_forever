# -*- coding: utf-8 -*-
"""我能永远陪着你 / 我能永远陪伴你 5.4.2
一个尽量“像人”的 AstrBot 智械人类核心：
生理期 / 九维心情 / 世界观与日程 / 天气 / 健康 / 群聊插话阈值 /
私聊判定 / 主动私聊 / 长期记忆与遗忘 / 防抖 / 分段与打字延迟 / 引用回复 / AI 润色"""

from typing import Any, TYPE_CHECKING

try:  # 兼容层：旧插件名 / 旧数据目录 / 旧配置名的唯一来源
    from . import amo_compat as _compat
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        import amo_compat as _compat

import hashlib
import os
import random  # 测试会 patch main.random（群聊回复概率）

try:  # Phase 17：结构化健康快照（只读、降级不抛；读库细节在 observability）
    from .observability import plugin_health
except ImportError:  # 按文件直接加载（离线单测）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from observability import plugin_health

try:  # Phase 17：统一时钟（源码守护禁止生产代码裸调 time.time）
    from .clock import now_ts as _obs_now_ts
except ImportError:  # 按文件直接加载（离线单测）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from clock import now_ts as _obs_now_ts

#: 插件载入时刻，用来给 /status 的 obs_health.uptime_s 当起点
_OBS_STARTED_AT = _obs_now_ts()

PLUGIN_VERSION = "7.6.1"

try:  # 包模式优先（AstrBot 正常加载）
    from .amo_bootstrap import (
        AmoDB, ApiMixin, CommandMixin, ConfigMixin, Context,
        DrawEngine, EventBus, FeatureApiMixin, GiftEngine, HeartflowEngine,
        HandshakeMixin, Housing, KbMixin, LLMService,
        InitMixin, LifecycleMixin, LlmMixin, MemoryMixin, MessageMixin, MicroEventEngine, PhotoEngine,
        PoseLibrary, ProactiveMixin, ReplyGate, RuntimeMixin, ScheduleChanged,
        ScheduleMixin, SegmentEngine, SocialLearnEngine, SocialMixin, Star,
        TravelMixin, UsageMixin, Wallet, Wardrobe,
        WebRouteMixin, _align_handler_module_paths, _clock_configure, _clock_name, _note_swallowed,
        logger, looks_like_plain_command,
    )
except ImportError:  # 按文件直接加载（离线单测 / 老加载方式）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_bootstrap import (
            AmoDB, ApiMixin, CommandMixin, ConfigMixin, Context,
            DrawEngine, EventBus, FeatureApiMixin, GiftEngine, HeartflowEngine,
            HandshakeMixin, Housing, KbMixin, LLMService,
            InitMixin, LifecycleMixin, LlmMixin, MemoryMixin, MessageMixin, MicroEventEngine, PhotoEngine,
            PoseLibrary, ProactiveMixin, ReplyGate, RuntimeMixin, ScheduleChanged,
            ScheduleMixin, SegmentEngine, SocialLearnEngine, SocialMixin, Star,
            TravelMixin, UsageMixin, Wallet, Wardrobe,
            WebRouteMixin, _align_handler_module_paths, _clock_configure, _clock_name, _note_swallowed,
            logger, looks_like_plain_command,
        )

try:  # 兼容旧导入：测试 / 外部扩展仍会 from main import HumanState / HungerEngine
    from .amo_bootstrap import HumanState, HungerEngine  # noqa: F401
except ImportError:  # 按文件直接加载
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_bootstrap import HumanState, HungerEngine  # noqa: F401

try:  # 就绪/握手/日程事件：2026-11-17 从 main.py 拆到 amo_readiness
    from .amo_readiness import ReadinessMixin
except ImportError:  # 按文件直接加载
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_readiness import ReadinessMixin

try:  # 提示词注册表（Phase 4）：LEAN_PARTS 不再手写第二份
    from . import amo_prompt_registry
except ImportError:  # 按文件直接加载（顶层 sys.path 模式）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_prompt_registry

try:  # Phase 16：统一后台任务管理器（受管 spawn / 快照都在 amo_jobs）
    from .amo_jobs import JobManager, report_jobs, spawn_background
except ImportError:  # 按文件直接加载（离线单测 / 老加载方式）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_jobs import JobManager, report_jobs, spawn_background

try:  # 运行期 glue：启动预热 / 我能永远定格你自用版提醒
    from .amo_image_common import self_use_hint
    from .amo_runtime import warm_up
except ImportError:  # 按文件直接加载（离线单测）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_image_common import self_use_hint
        from amo_runtime import warm_up

# handler 装饰器必须留在 main.py：AstrBot 按 handler_module_path 派发，
# 搬走会让 handler 静默失效（2026-11-11 那次 20 个 handler 全灭）。
_align_handler_module_paths(__name__)

#: 跨插件 feature API 协议版本（Phase 19）：守护只读 protocol_info() 判断兼容性。
PROTOCOL_NAME = "amo-feature"
PROTOCOL_VERSION = "1.0"


def feature_api_names():
    """feature API 公开方法名（排序后作为 api_hash 的输入，只读）。"""
    return sorted(name for name in dir(FeatureApiMixin) if not name.startswith("_"))


def feature_api_hash():
    """feature API 指纹：方法名排序后 sha256 前 12 位。"""
    payload = "\n".join(feature_api_names()).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


class HumanCorePlugin(MemoryMixin, CommandMixin, ConfigMixin, MessageMixin, SocialMixin, TravelMixin, RuntimeMixin, KbMixin, UsageMixin, ApiMixin, ScheduleMixin, ProactiveMixin, LlmMixin, WebRouteMixin, HandshakeMixin, InitMixin, LifecycleMixin, ReadinessMixin, Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self._init_basic(config)
        self._init_engines()
        self._init_runtime()
        self._register_web_apis()
        logger.info("[amo] 我能永远陪伴你 %s loaded; stale web routes purged" % PLUGIN_VERSION)
        self._spawn(self._log_omni_handshake())

    # 初始化装配（_init_basic / _init_engines / _init_runtime）：2026-11-17 拆到 amo_init.InitMixin


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
            try:
                from .amo_tts_guard import wrap_tts_providers
            except ImportError:
                from amo_tts_guard import wrap_tts_providers
            try:
                wrap_tts_providers(self.context, logger)
            except Exception as exc:
                logger.debug("[amo] TTS 动作过滤接管失败: %s" % exc)
            try:   # 姿势库新增了种子：后台把缺的参考图补齐（拍脚姿势第一次用到就能直接用）
                from .pose_library import ensure_batch as _pose_batch
                from .pose_library import pending_poses as _pending_poses
                _library = getattr(self, "pose", None)
                _pending = _pending_poses(_library) if _library is not None else []
                if _pending:
                    self._spawn(_pose_batch(self, _pending))
                    logger.info("[amo] 姿势参考图缺 %d 张，后台补齐", len(_pending))
            except Exception as exc:
                logger.debug("[amo] 姿势参考图补齐失败: %s" % exc)
            try:
                from .laundry_engine import register_buy_appliance
                manager = getattr(getattr(self.context, "provider_manager", None), "llm_tools", None)
                if register_buy_appliance(self, manager):
                    logger.info("[amo] LLM tool registered: buy_appliance")
            except Exception as exc:
                logger.debug("[amo] 注册 buy_appliance 工具失败: %s" % exc)
            try:
                from .draw_engine import register_send_photo
                manager = getattr(getattr(self.context, "provider_manager", None), "llm_tools", None)
                if register_send_photo(self, manager):
                    logger.info("[amo] LLM tool registered: send_photo")
                from .amo_schedule_tools import register_schedule_tools
                if register_schedule_tools(self, manager):
                    logger.info("[amo] LLM tools registered: update_schedule/continue/set_cost")
            except Exception as exc:
                logger.debug("[amo] 注册 send_photo 工具失败: %s" % exc)

    # 受管 spawn / 任务快照 / 启动预热 / 生图插件提醒：实现都在各自模块，
    # 这里只留类属性别名（self._spawn(...) 等调用点一个都不用改）。
    _spawn = spawn_background
    _jobs_report = report_jobs
    _warm_up = warm_up
    _omnidraw_self_use_hint = self_use_hint

    def protocol_info(self):
        """Phase 19：只读协议摘要（守护 compat() 用来判断跨插件协议版本）。

        纯内存 + 源码常量，不读库、不联网；字段只增不改。
        """
        try:  # 包模式优先
            from . import amo_db_schema as _schema
        except ImportError:  # 按文件直接加载
            import amo_db_schema as _schema
        return {
            "plugin": "Astrbot_amoupup_forever",
            "protocol": PROTOCOL_NAME,
            "protocol_version": PROTOCOL_VERSION,
            "api_hash": feature_api_hash(),
            "schema_version": int(getattr(_schema, "LATEST_VERSION", 0) or 0),
            "version": PLUGIN_VERSION,
        }

    def _obs_health(self):
        """Phase 17：结构化健康快照（/status 的 obs_health 字段；读库细节在 observability）。"""
        return plugin_health(self, version=PLUGIN_VERSION,
                             started_at=_OBS_STARTED_AT, now=_obs_now_ts())


    # 生命周期（initialize / terminate / stop）：2026-11-17 拆到 amo_lifecycle.LifecycleMixin


    # ---- 跨平台账号合并 ----


    # ---------- 管理员 ----------

    # ---------- 消息学习 ----------


    # ---------- 近聊上下文（内存缓冲 + 记忆终端钩子） ----------
    CONTEXT_BUFFER_LIMIT = 60


    # ---------- 配套数据插件（Astrbot_amoupup_data / 我能永远记住你） ----------
    DATA_PLUGIN_NAMES = _compat.DATA_PLUGIN_NAMES


    # ---------- 记忆能力层（memory_hooks）：陪伴只判定，数据交给挂载插件 ----------


    # 配套插件发现 / 数据插件缺失提醒 / 平台标签：2026-11-17 拆到 amo_handshake.HandshakeMixin


# ---------- livingmemory（只读直连） ----------


    # ---------- 消息入口 ----------


    # ---------- 发送 ----------


    # ---------- LLM 注入 ----------


    #: lean 压缩文案由注册表统一生成（不再手写第二份）
    LEAN_PARTS = amo_prompt_registry.REGISTRY.lean_parts_map()

    # ---------- 后台循环 ----------



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




# 把共同活动（聊天 → 日程 → 事实）挂到插件类上，原因同上
try:  # 测试里 main 是当顶层模块导入的，两种都要能吃
    from .amo_shared_activity import SharedActivityMixin  # noqa: E402
except ImportError:  # pragma: no cover
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared_activity import SharedActivityMixin  # noqa: E402

for _sa_name in dir(SharedActivityMixin):
    # 私有辅助（_sa_kv/_sa_start/_sa_finish…）也要一起搬，公开方法里都在调它们
    if _sa_name.startswith("_") and not _sa_name.startswith("_sa_"):
        continue
    try:
        setattr(HumanCorePlugin, _sa_name, getattr(SharedActivityMixin, _sa_name))
    except Exception as exc:
        _note_swallowed("main.py.shared_activity", exc)


# 把对外状态/事件 API 挂到插件类上（避免多继承影响 Star 初始化）
#: 对外 API 依赖的私有辅助：默认不挂下划线方法，但 push_to_owner / hunger_help
#: 第一行就是 self._owner_umos()，不挂就是 AttributeError —— 守护侧的逛街炫耀、
#: 宵夜「我饿了」、约会事故推送会全部静默失败（2026-11-20 定位）。
FEATURE_PRIVATE_HELPERS = ("_owner_umos", "_relation_overrides")

for _feat_name in dir(FeatureApiMixin):
    if _feat_name.startswith("_") and _feat_name not in FEATURE_PRIVATE_HELPERS:
        continue
    try:
        setattr(HumanCorePlugin, _feat_name, getattr(FeatureApiMixin, _feat_name))
    except Exception as exc:
        _note_swallowed("main.py.765", exc)
