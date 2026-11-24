# -*- coding: utf-8 -*-
"""守卫健康检查（2026-11-24 第 1 刀）。

2026-11-23 的真实教训：收件人守卫文件被精简掉后，4 个调用点全部
静默降级，线上看起来完全正常，守卫其实已经没了。这里在启动时把必须存在的
守卫模块/属性点一遍，缺了就 ERROR，并给 /status 一个 guards 字段。
"""
from __future__ import annotations

try:  # 吞异常留痕（2026-11-24）
    from .amo_observability import note as _note_swallowed
except ImportError:
    from amo_observability import note as _note_swallowed

import importlib
import logging

logger = logging.getLogger("amo.guard_health")

#: 模块 -> 必须存在的属性。只列缺了会静默出事的守卫，不追求覆盖所有模块。
CRITICAL_GUARDS = {
    "amo_persona_guard": ("prepare_outbound", "strip_action_narration", "contextual_fallback"),
    "amo_transition": ("start", "finish", "guard_road_home"),
    "place_guard": ("conflicting_place", "allowed_groups"),
    "reply_fact_guard": ("guard", "has_violation"),
    "amo_food_delivery": ("pending_order", "consume_arrival", "today_facts"),
    "amo_runtime_context_agenda": ("AgendaContextMixin",),
    "amo_voice": ("clean_voice_text",),
    "amo_outbound": ("send", "send_media"),
    "segment_engine": ("SegmentEngine",),
    # 事实单一源：缺了它们，所有读侧纠偏都会静默失效。
    "domains.schedule.place": ("resolve_zone", "repair_zone_rows"),
    "domains.reply.repetition_guard": ("contextual_fallback",),
    # 场景/居住单一源：缺了它们，聊天/日记/照片会各自再猜一套「她在不在家」。
    "amo_scene_ledger": ("scene_at", "write", "constraints"),
    "amo_household": ("scene_at", "scene_now", "wording"),
}


def _import(name):
    package = __package__
    if package:
        return importlib.import_module("." + name, package)
    return importlib.import_module(name)


def check_guards(critical=None):
    """返回 {ok, checked, missing}；绝不抛异常。"""
    table = CRITICAL_GUARDS if critical is None else critical
    missing = []
    checked = 0
    for module_name, attrs in table.items():
        checked += 1
        try:
            module = _import(module_name)
        except Exception as exc:
            missing.append({"module": module_name, "attr": "<module>",
                            "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        for attr in attrs:
            if not hasattr(module, attr):
                missing.append({"module": module_name, "attr": attr, "error": "missing"})
    return {"ok": not missing, "checked": checked, "missing": missing}


def assert_guards(plugin=None, critical=None):
    """启动时调用：缺守卫打 ERROR，并把报告挂到 plugin._guard_health。"""
    report = check_guards(critical)
    try:
        if plugin is not None:
            plugin._guard_health = report
    except Exception as exc:  # cleanup: 挂不上也不能影响启动
        logger.error("[amo] 守卫报告挂载失败: %s", exc)
    if report["ok"]:
        logger.info("[amo] 守卫健康检查通过（%d 组）", report["checked"])
    else:
        logger.error("[amo] 守卫健康检查失败：missing=%s", report["missing"])
    return report

