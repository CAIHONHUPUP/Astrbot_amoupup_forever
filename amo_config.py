# -*- coding: utf-8 -*-
"""ConfigMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

try:  # 兼容层：旧插件名 / 旧数据目录 / 旧配置名的唯一来源
    from . import amo_compat as _compat
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        import amo_compat as _compat

import json
import os
from datetime import date

try:  # 配置模型（Phase 4）：schema 是唯一真相
    from .amo_config_model import ConfigModel, coerce_value, mask_secret, run_migrations
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_config_model import ConfigModel, coerce_value, mask_secret, run_migrations

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        LEGACY_DEFAULT_ADMIN, SegmentEngine, logger, to_float, to_int,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            LEGACY_DEFAULT_ADMIN, SegmentEngine, logger, to_float, to_int,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                LEGACY_DEFAULT_ADMIN, SegmentEngine, logger, to_float, to_int,
            )


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class ConfigMixin(_MixinBase):
    _config_mtime: float
    segment_engine: Any

    def _get_config_model(self):
        """当前 config 对象对应的配置模型；config 被整体替换时自动重建。"""
        config = getattr(self, "config", None)
        model = self.__dict__.get("_config_model_cache")
        if model is not None and getattr(model, "config", None) is config:
            return model
        model = ConfigModel.from_plugin_dir(
            config, getattr(self, "plugin_dir", ""), error_logger=_note_swallowed,
        )
        self.__dict__["_config_model_cache"] = model
        return model

    def cfg(self, key, default=None):
        return self._get_config_model().get(key, default)

    def cfg_list(self, key, default=None):
        """读列表型配置（template_list 存的是对象数组）；读到的不是 list 就当空。"""
        return self._get_config_model().get_list(key, default)

    def cfg_bool(self, key, default=False):
        return self._get_config_model().get_bool(key, default)

    def cfg_int(self, key, default=0):
        return self._get_config_model().get_int(key, default)

    def cfg_float(self, key, default=0.0):
        return self._get_config_model().get_float(key, default)

    def cfg_str(self, key, default=""):
        return self._get_config_model().get_str(key, default)

    def _core_decor_flags(self, umo=""):
        """AstrBot 核心会不会在插件 hook 之后再加「引用 / @」。

        上架要求不能改 AstrBot 核心；但核心的 platform_settings.reply_with_quote /
        reply_with_mention 是全局的，会在 on_decorating_result 之后无条件装饰结果，
        把本插件的私聊不引用、群聊按概率引用覆盖掉。这里只读配置，检测到开了就由
        插件自己发送，核心就没机会再补。
        """
        try:
            config = self.context.get_config() or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_config.HumanCorePlugin._core_decor_flags", _amo_exc)
            config = {}
        try:
            settings = config.get("platform_settings") or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_config.HumanCorePlugin._core_decor_flags", _amo_exc)
            settings = {}
        return {
            "quote": bool(settings.get("reply_with_quote", False)),
            "mention": bool(settings.get("reply_with_mention", False)),
        }

    def _migrate_legacy_config(self):
        """把旧版配置文件（旧名见 amo_compat.py）合并到当前插件的配置文件。"""
        path = str(getattr(self.config, "config_path", "") or "")
        if not path:
            return
        base = os.path.dirname(path)
        legacy_candidates = _compat.legacy_config_paths(base)
        legacy = next((item for item in legacy_candidates if os.path.exists(item)), "")
        if not legacy:
            return
        try:
            import shutil as _shutil
            with open(path, encoding="utf-8-sig") as stream:
                current = json.load(stream)
            with open(legacy, encoding="utf-8-sig") as stream:
                old = json.load(stream)
            if not isinstance(current, dict) or not isinstance(old, dict):
                return
            schema: dict[Any, Any] = {}
            try:
                with open(os.path.join(self.plugin_dir, "_conf_schema.json"), encoding="utf-8-sig") as stream:
                    schema = json.load(stream) or {}
            except Exception as _amo_exc:
                _note_swallowed("amo_config.HumanCorePlugin._migrate_legacy_config", _amo_exc)
                schema = {}
            merged: dict[str, Any] = {}
            for key, value in old.items():
                meta = schema.get(key)
                if not isinstance(meta, dict):
                    meta = {}
                default = meta.get("default")
                if key not in current or current.get(key) == default:
                    current[key] = value
                    merged[key] = value
            if not merged:
                return
            backup = path + ".bak-4.0.0"
            if os.path.exists(path) and not os.path.exists(backup):
                _shutil.copy2(path, backup)
            self.config.update(current)
            if hasattr(self.config, "save_config"):
                self.config.save_config()
            logger.info(
                "[amo] 已迁移旧版 amo_forever 配置: %s（%d 项）"
                % (legacy, len(merged))
            )
            logger.info("[amo] 升级提示：插件标识现为 Astrbot_amoupup_forever；旧 amo_forever 配置/数据已自动迁移。")
        except Exception as exc:
            logger.warning("[amo] 旧版 amo_forever 配置迁移失败: %s" % exc)

    def _reload_config_if_changed(self):
        """AstrBot 原生插件配置页保存后，运行中的 self.config 可能不是新对象。

        读取配置文件 mtime，变化时把新值合并进当前配置，保证天气城市、
        管理员、主动私聊等动态配置不需要重启插件也能生效。
        """
        path = getattr(self.config, "config_path", "")
        if not path:
            return
        try:
            mtime = os.path.getmtime(path)
        except Exception as _amo_exc:
            _note_swallowed("amo_config.HumanCorePlugin._reload_config_if_changed", _amo_exc)
            return
        if mtime <= self._config_mtime:
            return
        try:
            with open(path, encoding="utf-8-sig") as stream:
                data = json.load(stream)
            if not isinstance(data, dict):
                return
            old_engine_config = {
                key: value for key, value in self.config.items()
                if key.startswith("seg_")
            }
            self.config.update(data)
            new_engine_config = {
                key: value for key, value in self.config.items()
                if key.startswith("seg_")
            }
            if old_engine_config and old_engine_config != new_engine_config:
                self._spawn(self._apply_runtime_config())
            self._config_mtime = mtime
            if data.get("bot_name"):
                self.bot_name = str(data.get("bot_name"))
            if data.get("bot_aliases") is not None:
                self.bot_aliases = [
                    item.strip() for item in str(data.get("bot_aliases")).split(",") if item.strip()
                ]
            self._persona_name_cache.clear()
            raw_admins = data.get("admin_uids", None)
            if raw_admins is not None:
                self.admin_uids = {
                    item.strip() for item in str(raw_admins).split(",") if item.strip()
                }
            start = str(data.get("period_start") or "").strip()
            if start and self.state is not None and start != self.state.period_start:
                try:
                    date.fromisoformat(start)
                    self.state.set_period_start(start)
                except Exception as _amo_exc:
                    _note_swallowed("amo_config.HumanCorePlugin._reload_config_if_changed", _amo_exc)
            try:
                self._sync_weather_city_config()
            except Exception as _amo_exc:
                _note_swallowed("amo_config.HumanCorePlugin._reload_config_if_changed", _amo_exc)
            logger.debug("[amo] 检测到插件配置变更，已重新加载")
        except Exception as exc:
            logger.debug("[amo] 重新加载插件配置失败: %s" % exc)

    def _seg_config(self):
        try:
            return {key[4:]: value for key, value in self.config.items() if key.startswith("seg_")}
        except Exception as _amo_exc:
            _note_swallowed("amo_config.HumanCorePlugin._seg_config", _amo_exc)
            return {}

    def _migrate_scenic_kb_name(self):
        try:
            if self.db.kv_get("scenic_kb_name_migrated_502", False):
                return
            current = self.cfg_str("scenic_kb_name", "").strip()
            if current in ("", "中国景区知识库", "景区知识库"):
                self.config["scenic_kb_name"] = "旅游知识库-风景区"
                if hasattr(self.config, "save_config"):
                    self.config.save_config()
            self.db.kv_set("scenic_kb_name_migrated_502", True)
        except Exception as exc:
            logger.debug("[amo] 迁移景区知识库名称失败: %s" % exc)

    def _migrate_typing_defaults(self):
        """2.6.2 起把旧的 55ms/字默认值迁移到更真实的 400ms/字。"""
        try:
            if self.db.kv_get("typing_realistic_migrated", False):
                return
            changes: dict[str, Any] = {}
            if to_int(self.cfg("typing_base_ms", 0), 0) == 600:
                changes["typing_base_ms"] = 700
            if to_int(self.cfg("typing_per_char_ms", 0), 0) == 55:
                changes["typing_per_char_ms"] = 400
            if to_int(self.cfg("typing_max_ms", 0), 0) == 4500:
                changes["typing_max_ms"] = 20000
            if abs(to_float(self.cfg("seg_per_char_delay_seconds", 0), 0) - 0.08) < 0.0001:
                changes["seg_per_char_delay_seconds"] = 0.4
            for key, value in changes.items():
                self.config[key] = value
            if changes:
                if hasattr(self.config, "save_config"):
                    self.config.save_config()
                if self.segment_engine is not None and "seg_per_char_delay_seconds" in changes:
                    self.segment_engine.per_char_delay_seconds = 0.4
                logger.debug("[amo] 打字延迟默认值已迁移为 400ms/字（18字约8秒）")
            self.db.kv_set("typing_realistic_migrated", True)
        except Exception as exc:
            logger.debug("[amo] 迁移打字延迟失败: %s" % exc)

    def _migrate_delay_default(self):
        """2.4.7 起默认按回复字数动态延迟；旧的 fixed 配置迁移一次。"""
        try:
            if self.cfg_str("seg_delay_type", "").strip().lower() != "fixed":
                return
            if self.db.kv_get("delay_dynamic_migrated", False):
                return
            self.config["seg_delay_type"] = "dynamic"
            if self.segment_engine is not None:
                self.segment_engine.delay_type = "dynamic"
            if hasattr(self.config, "save_config"):
                self.config.save_config()
            self.db.kv_set("delay_dynamic_migrated", True)
            logger.debug("[amo] 分段延迟已从 fixed 迁移为 dynamic（按回复字数）")
        except Exception as exc:
            logger.debug("[amo] 迁移分段延迟失败: %s" % exc)

    def _migrate_legacy_admin_default(self):
        """旧版本把 3261748099 写成了默认管理员；2.4.0 只清理一次。"""
        try:
            if self.cfg_str("admin_uids", "").strip() != LEGACY_DEFAULT_ADMIN:
                return
            if self.db.kv_get("admin_default_cleared", False):
                return
            parts = [
                item.strip() for item in self.cfg_str("admin_uids", "").split(",")
                if item.strip() and item.strip() != LEGACY_DEFAULT_ADMIN
            ]
            self.config["admin_uids"] = ",".join(parts)
            if hasattr(self.config, "save_config"):
                self.config.save_config()
            self.admin_uids = set(parts)
            self.db.kv_set("admin_default_cleared", True)
            logger.debug("[amo] 已移除旧默认管理员 %s，请在配置里填写自己的 ID" % LEGACY_DEFAULT_ADMIN)
        except Exception as exc:
            logger.debug("[amo] 清理旧默认管理员失败: %s" % exc)

    @staticmethod
    def _coerce_config_value(kind, value):
        return coerce_value(kind, value)

    def _schema_map(self):
        return dict(self._get_config_model().schema)

    @staticmethod
    def _mask_secret(value):
        return mask_secret(value)

    def _config_schema_items(self):
        schema = self._schema_map()
        items = []
        for key, meta in schema.items():
            if not isinstance(meta, dict):
                continue
            if key.startswith("draw_") or key.startswith("photo_"):
                group = "照片"
            elif key in ("schedule_day_types", "schedule_change_enabled", "schedule_change_owner_ids"):
                group = "日程"
            elif key.startswith("seg_"):
                group = "分段"
            elif key.startswith("voice_"):
                group = "语音"
            elif key.startswith("companion_") or key.startswith("memory_") or key in ("admin_uids",):
                group = "记忆"
            elif key.startswith("proactive_") or key.startswith("delay_") or key.startswith("disturb_") or key.startswith("birthday_") or key in ("quiet_hours", "auto_active_msg"):
                group = "私聊"
            elif key.startswith("period_") or key.startswith("enable_period") or key.startswith("enable_sick"):
                group = "状态"
            else:
                group = "基础"
            items.append({
                "key": key,
                "type": meta.get("type", "string"),
                "description": meta.get("description") or key,
                "hint": meta.get("hint", ""),
                "default": meta.get("default"),
                "options": meta.get("options") or [],
                "special": meta.get("_special", ""),
                "group": group,
                "value": (
                    self._mask_secret(self.cfg(key, meta.get("default")))
                    if key in self.SENSITIVE_KEYS
                    else self.cfg(key, meta.get("default"))
                ),
                "configured": (
                    bool(str(self.cfg(key, "") or "").strip())
                    if key in self.SENSITIVE_KEYS
                    else True
                ),
                "sensitive": key in self.SENSITIVE_KEYS,
            })
        return items

    def run_config_migrations(self, boot=False):
        """迁移统一由 ConfigModel 编排；迁移方法本体仍留在各 mixin。"""
        return run_migrations(self, boot=boot, logger=logger)

    def config_health(self):
        """配置健康检查：未知键 / 类型错 / 越界 / 旧键；只报告，不改配置。"""
        issues = self._get_config_model().health()
        if issues:
            logger.info("[amo] 配置健康检查: %d 项（首个 %s: %s）",
                        len(issues), issues[0]["key"], issues[0]["message"])
        return issues

    async def _apply_runtime_config(self):
        self._admin_cache = {}
        self.bot_name = self.cfg_str("bot_name", self.bot_name) or "陪伴"
        self._persona_name_cache.clear()
        self.bot_aliases = [
            item.strip() for item in self.cfg_str("bot_aliases", "").split(",") if item.strip()
        ]
        self.admin_uids = {
            item.strip() for item in self.cfg_str("admin_uids", "").split(",") if item.strip()
        }
        if self.state is not None:
            start = self.cfg_str("period_start", "").strip()
            if start and start != self.state.period_start:
                try:
                    date.fromisoformat(start)
                    self.state.set_period_start(start)
                except Exception as _amo_exc:
                    _note_swallowed("amo_config.HumanCorePlugin._apply_runtime_config", _amo_exc)
            try:
                # 启动/重载不算「用户手动刷新」：走每天 3 次的时段额度，避免每次重启烧额度
                await self.state.refresh_weather()
            except Exception as _amo_exc:
                _note_swallowed("amo_config.HumanCorePlugin._apply_runtime_config", _amo_exc)
        if SegmentEngine is not None:
            old = self.segment_engine
            if old is not None:
                for task in list(getattr(old, "_tasks", set())):
                    try:
                        task.cancel()
                    except Exception as _amo_exc:
                        _note_swallowed("amo_config.HumanCorePlugin._apply_runtime_config", _amo_exc)
            try:
                seg_config = self._seg_config()
                seg_config.setdefault("protect_paired_symbols", True)
                self.segment_engine = SegmentEngine(self.context, seg_config, plugin=self)
            except Exception as exc:
                logger.error("[amo] 应用分段配置失败: %s" % exc)
