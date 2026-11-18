# -*- coding: utf-8 -*-
"""B 方案：LLM 只在语气池候选里选序号（默认关闭，不生成新文本）。

本模块只做「异步选择」这一层；候选生成/轮换/事实守卫仍复用
amo_reply_pipeline 的单一实现。同步守卫路径通过 pending 桥把冲突记录
交给装饰阶段的 async 钩子，保证 runtime 的同步方法体不被改写。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import asyncio
import json
import logging
import re

try:  # 包模式优先，按文件直接加载时退回绝对导入
    from .amo_shared import logger
except Exception:  # 单测/独立加载环境
    try:
        from amo_shared import logger
    except Exception:  # pragma: no cover - 兜底
        logger = logging.getLogger("amo")


def _pipeline():
    """延迟导入，避免 amo_reply_pipeline <-> amo_tone_pick 循环。"""
    try:
        from . import amo_reply_pipeline as pipeline
    except ImportError:  # 直接按文件加载时
        import amo_reply_pipeline as pipeline
    return pipeline


def _tone_pick_prompt(rule, sentence, candidates, plugin, activity):
    """给选择器的只读上下文 + 编号候选；不让它改写。"""
    pipeline = _pipeline()
    mood = pipeline._tone_mood(plugin)[:40] or "平常"
    relation = pipeline._tone_relation(plugin)[:20] or "普通"
    scene = pipeline._tone_scene(activity)
    lines = [
        "冲突类型：" + str(rule or ""),
        "原句：" + str(sentence or "")[:80].replace("\n", " "),
        "心情：%s；关系：%s；场景：%s" % (mood, relation, scene),
        "候选（只选序号，不要改写）：",
    ]
    for index, item in enumerate(candidates):
        lines.append("%d. %s" % (index, item))
    return "\n".join(lines)


def _parse_tone_index(answer, count):
    """解析 {"index": n} 或裸数字；越界/非数字返回 None。"""
    text = str(answer or "").strip()
    if not text:
        return None
    value = None
    match = re.search(r"\{[^{}]*\}", text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict):
            value = data.get("index", data.get("pick"))
    if value is None:
        match = re.search(r"\b(\d{1,2})\b", text)
        if match:
            value = match.group(1)
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < int(count) else None


async def _llm_pick_index(plugin, rule, sentence, candidates, activity):
    """让 LLM 从候选里选序号；任何失败返回 None（调用方回退 A）。"""
    pipeline = _pipeline()
    llm = getattr(plugin, "llm", None)
    chat = getattr(llm, "chat", None)
    if not callable(chat):
        pipeline.TONE_LLM_STATS["no_llm"] += 1
        return None
    pipeline.TONE_LLM_STATS["calls"] += 1
    system = ("你是角色台词的语气选择器。只能从候选里选一句最贴合语境的，"
              "不得改写、拼接、增加事实；只输出 JSON：{\"index\": 数字}。")
    prompt = _tone_pick_prompt(rule, sentence, candidates, plugin, activity)
    try:
        answer = await asyncio.wait_for(
            chat("", system, prompt, limit=40),
            timeout=pipeline._tone_llm_timeout(plugin))
    except asyncio.TimeoutError:
        pipeline.TONE_LLM_STATS["timeout"] += 1
        logger.debug("[reply_guard] B 方案超时，回退语气池")
        return None
    except Exception as exc:  # cleanup: LLM 失败绝不拦发送
        pipeline.TONE_LLM_STATS["error"] += 1
        logger.debug("[reply_guard] B 方案调用失败: %s", exc)
        return None
    index = _parse_tone_index(answer, len(candidates))
    if index is None:
        pipeline.TONE_LLM_STATS["invalid"] += 1
        logger.debug("[reply_guard] B 方案返回非法序号: %r", answer)
        return None
    pipeline.TONE_LLM_STATS["picked"] += 1
    return index


def _remember_tone_choice(plugin, rule, middle, activity, chosen, candidates):
    """把 LLM 选中的序号写回轮换状态，避免下一轮立刻重复。"""
    pipeline = _pipeline()
    try:
        index = candidates.index(chosen)
    except ValueError:
        return
    state = pipeline._tone_state(plugin)
    state[str(pipeline._tone_key(rule, middle, plugin, activity))] = index


def _fact_guard_ok(text, context):
    """选中的候选再跑一次事实守卫；有冲突就不采用。"""
    try:
        try:
            from . import reply_fact_guard as _fact_guard
        except ImportError:
            import reply_fact_guard as _fact_guard
        _fixed, violations = _fact_guard.guard(str(text or ""), context)
        return not violations
    except Exception as exc:  # cleanup: 校验失败按通过处理（候选本身来自池）
        logger.debug("[reply_guard] B 方案事实复核失败: %s", exc)
        return True


async def _llm_naturalize(fixed, violations, plugin, activity, now):
    """逐条冲突让 LLM 选候选；返回 (文本, 选中数, 改动数)。"""
    pipeline = _pipeline()
    result = fixed
    cursor = 0
    picked = 0
    changed = 0
    for item in violations or ():
        if not isinstance(item, dict):
            continue
        rule = str(item.get("rule") or "")
        sentence = str(item.get("sentence") or "")
        replacement = str(item.get("replacement") or "")
        if not rule or not replacement:
            continue
        position = result.find(replacement, cursor)
        if position < 0:
            position = result.find(replacement)
        if position < 0:
            continue
        prefix, middle, suffix = pipeline._changed_segment(sentence, replacement)
        item_variants = item.get("variants") or ()
        if not middle and not (rule == "place" and len(item_variants) >= 2):
            cursor = position + len(replacement)
            continue
        candidates = pipeline._tone_candidates(
            rule, prefix, middle, suffix, plugin, activity,
            variants=item_variants)
        choices = candidates[1:] if len(candidates) > 1 else candidates
        clause = ""
        if len(choices) >= 2:
            index = await _llm_pick_index(plugin, rule, sentence, choices, activity)
            if index is not None:
                clause = choices[index]
                picked += 1
                _remember_tone_choice(plugin, rule, middle, activity,
                                      clause, candidates)
        if not clause:
            pipeline.TONE_LLM_STATS["fallback"] += 1
            seed = "%s|%s|%s" % (rule, now, result[:position])
            clause = pipeline._natural_clause(
                rule, prefix, middle, suffix, sentence, replacement,
                plugin, activity, seed, variants=item.get("variants"))
        if not clause or clause == replacement:
            cursor = position + len(replacement)
            continue
        result = result[:position] + clause + result[position + len(replacement):]
        cursor = position + len(clause)
        changed += 1
        item["natural"] = clause
    return result, picked, changed


async def apply_fact_guard_async(text, plugin, activity=None, now=None):
    """可开关 B：默认走 A；开关打开且命中冲突时让 LLM 在候选里选序号。"""
    pipeline = _pipeline()
    value, fixed, violations, context = pipeline._guard_once(
        text, plugin, activity, now)
    if not value:
        return value
    for item in violations:
        logger.debug("[reply_guard] rule=%s said=%s -> %s | %s",
                     item.get("rule"), item.get("sentence"),
                     item.get("replacement"), item.get("reason"))
    if not violations or not pipeline.tone_llm_pick_enabled(plugin):
        return pipeline._naturalize_safe(fixed, violations, plugin, activity, now)
    result, picked, changed = await _llm_naturalize(
        fixed, violations, plugin, activity, now)
    if changed and _fact_guard_ok(result, context):
        for item in violations:
            if item.get("natural"):
                logger.debug("[reply_guard] B 选中 rule=%s -> %s",
                             item.get("rule"), item.get("natural"))
        return result
    if picked:
        pipeline.TONE_LLM_STATS["fact_fallback"] += 1
        logger.debug("[reply_guard] B 结果未通过事实复核，回退语气池")
    return pipeline._naturalize_safe(fixed, violations, plugin, activity, now)


async def apply_pending_tone_pick_async(text, plugin):
    """消费同步守卫留下的冲突记录：让 LLM 在候选里选，替换 A 的确定性说法。"""
    pipeline = _pipeline()
    pending = getattr(plugin, "_tone_pick_pending", None)
    try:
        setattr(plugin, "_tone_pick_pending", None)
    except Exception as exc:  # cleanup: 极简 fake 不能清 pending
        logger.debug("[reply_guard] B pending 清理失败: %s", exc)
    value = str(text or "")
    if not isinstance(pending, dict) or not value or not pipeline.tone_llm_pick_enabled(plugin):
        return value
    violations = pending.get("violations") or []
    context = pending.get("context") or {}
    activity = pending.get("activity")
    picked = 0
    cursor = 0
    for item in violations:
        if not isinstance(item, dict):
            continue
        natural = str(item.get("natural") or "")
        if not natural or natural not in value:
            continue
        position = value.find(natural, cursor)
        if position < 0:
            position = value.find(natural)
        if position < 0:
            continue
        rule = str(item.get("rule") or "")
        sentence = str(item.get("sentence") or "")
        replacement = str(item.get("replacement") or "")
        if not rule or not replacement:
            continue
        prefix, middle, suffix = pipeline._changed_segment(sentence, replacement)
        candidates = pipeline._tone_candidates(
            rule, prefix, middle, suffix, plugin, activity,
            variants=item.get("variants"))
        choices = candidates[1:] if len(candidates) > 1 else candidates
        if len(choices) < 2:
            continue
        index = await _llm_pick_index(plugin, rule, sentence, choices, activity)
        if index is None:
            pipeline.TONE_LLM_STATS["fallback"] += 1
            continue
        chosen = choices[index]
        # 按位置替换，别误伤前面同内容的另一小句
        value = value[:position] + chosen + value[position + len(natural):]
        cursor = position + len(chosen)
        _remember_tone_choice(plugin, rule, middle, activity, chosen, candidates)
        picked += 1
    if picked and _fact_guard_ok(value, context):
        return value
    if picked:
        pipeline.TONE_LLM_STATS["fact_fallback"] += 1
        logger.debug("[reply_guard] B pending 结果未通过事实复核，保留 A")
    return str(text or "")
