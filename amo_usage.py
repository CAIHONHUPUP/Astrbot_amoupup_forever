# -*- coding: utf-8 -*-
"""UsageMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

from typing import TYPE_CHECKING

import asyncio
import os
import time

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        USAGE_DEFAULT_PRICES, _now, fetch_deepseek_balance, fetch_provider_balance, logger, to_float,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            USAGE_DEFAULT_PRICES, _now, fetch_deepseek_balance, fetch_provider_balance, logger, to_float,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            USAGE_DEFAULT_PRICES, _now, fetch_deepseek_balance, fetch_provider_balance, logger, to_float,
        )


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class UsageMixin(_MixinBase):
    def _usage_db_path(self):
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            return os.path.join(str(get_astrbot_data_path()), "data_v4.db")
        except Exception as _amo_exc:
            _note_swallowed("amo_usage.HumanCorePlugin._usage_db_path", _amo_exc)
            try:
                data_dir = os.path.dirname(os.path.dirname(os.path.dirname(str(self.db.path))))
                return os.path.join(data_dir, "data_v4.db")
            except Exception as _amo_exc:
                _note_swallowed("amo_usage.HumanCorePlugin._usage_db_path", _amo_exc)
                return ""

    def _deepseek_api_key(self):
        key = str(self.cfg_str("deepseek_api_key", "") or "").strip()
        if key:
            return key
        for name in ("DEEPSEEK_API_KEY", "COMPANION_V2_BALANCE_API_KEY"):
            value = str(os.environ.get(name, "") or "").strip()
            if value:
                return value
        return ""

    def _usage_prices(self):
        prices = {}
        for key in ("hit_peak", "hit_idle", "miss_peak", "miss_idle", "out_peak", "out_idle"):
            prices[key] = self.cfg_float("usage_price_" + key, USAGE_DEFAULT_PRICES[key])
        return prices

    def _usage_start_of_today(self):
        now = _now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp()

    def _provider_inventory(self):
        """读取 AstrBot 已配置的对话供应商；只返回脱敏信息，绝不回传密钥。"""
        items = []
        try:
            providers = self.context.get_all_providers() or []
        except Exception as _amo_exc:
            _note_swallowed("amo_usage.HumanCorePlugin._provider_inventory", _amo_exc)
            providers = []
        for provider in providers:
            config = getattr(provider, "provider_config", None) or {}
            pid = str(config.get("id") or "")
            if not pid:
                continue
            model = ""
            try:
                model = str(provider.get_model() or "")
            except Exception as _amo_exc:
                _note_swallowed("amo_usage.HumanCorePlugin._provider_inventory", _amo_exc)
                model = str(config.get("model") or "")
            api_base = str(config.get("api_base") or config.get("url") or "").strip()
            key = config.get("key") or ""
            if isinstance(key, list):
                has_key = any(str(item or "").strip() for item in key)
            else:
                has_key = bool(str(key or "").strip())
            items.append({
                "id": pid,
                "model": model,
                "type": str(config.get("type") or ""),
                "enable": config.get("enable", True) is not False,
                "has_key": has_key,
                "api_base": api_base,
            })
        return items

    def _judge_provider_id(self):
        try:
            if self.heartflow is not None:
                return str(self.heartflow.judge_provider_info().get("id") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_usage.HumanCorePlugin._judge_provider_id", _amo_exc)
        return ""

    def _using_provider_id(self):
        try:
            provider = self.context.get_using_provider()
            return str((getattr(provider, "provider_config", None) or {}).get("id") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_usage.HumanCorePlugin._using_provider_id", _amo_exc)
            return ""

    @staticmethod
    def _balance_probe_kind(api_base):
        base = str(api_base or "").lower()
        if "deepseek" in base:
            return "deepseek"
        if "siliconflow" in base:
            return "siliconflow"
        return "openai_bill"

    async def _provider_balances_cached(self, providers):
        """逐个探测供应商余额；60 秒缓存，失败不阻塞面板。"""
        now = time.time()
        signature = "|".join(sorted(
            item["id"] + ":" + item["api_base"] + ":" + str(item["has_key"]) for item in providers
        ))
        cache = getattr(self, "_provider_balance_cache", None)
        if (
            isinstance(cache, dict)
            and now - to_float(cache.get("at"), 0) < 60
            and cache.get("signature") == signature
        ):
            return cache.get("data") or []
        enabled = [item for item in providers if item["enable"] and item["has_key"] and item["api_base"]]

        def probe(item):
            kind = self._balance_probe_kind(item["api_base"])
            key = self._provider_api_key(item)
            result = {"id": item["id"], "model": item["model"], "kind": kind}
            if not key:
                result.update({"ok": False, "error": "未配置密钥"})
                return result
            data = fetch_provider_balance(key, item["api_base"], kind)
            data["id"] = item["id"]
            data["model"] = item["model"]
            data["kind"] = kind
            return data

        results = []
        if enabled:
            def run_all():
                return [probe(item) for item in enabled]

            try:
                results = await asyncio.to_thread(run_all)
            except Exception as exc:
                logger.debug("[amo] 供应商余额探测失败: %s" % exc)
                results = []
        self._provider_balance_cache = {"at": now, "signature": signature, "data": results}
        return results

    def _provider_api_key(self, item):
        """从 AstrBot 供应商配置里取密钥；只在内存中使用，不写日志不回传。"""
        config = {}
        try:
            for provider in (self.context.get_all_providers() or []):
                candidate = getattr(provider, "provider_config", None) or {}
                if str(candidate.get("id") or "") == str(item.get("id") or ""):
                    config = candidate
                    break
        except Exception as _amo_exc:
            _note_swallowed("amo_usage.HumanCorePlugin._provider_api_key", _amo_exc)
            config = {}
        key = config.get("key") or ""
        if isinstance(key, list):
            key = next((str(item or "").strip() for item in key if str(item or "").strip()), "")
        return str(key or "").strip()

    async def _balance_cached(self):
        """余额 60 秒内复用，避免面板每刷一次就打一次外网。"""
        now = time.time()
        cache = getattr(self, "_balance_cache", None)
        if isinstance(cache, dict) and now - float(cache.get("at") or 0) < 60:
            return cache.get("data") or {"ok": False, "error": "查询失败"}
        key = self._deepseek_api_key()
        data = await asyncio.to_thread(fetch_deepseek_balance, key)
        self._balance_cache = {"at": now, "data": data}
        return data
