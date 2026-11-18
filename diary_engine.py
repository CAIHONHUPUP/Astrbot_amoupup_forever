# -*- coding: utf-8 -*-
"""大肥鱼的日记：每天一篇 txt，按 年 / 月 / 日 文件夹存放。

目录结构：
    日记根目录/
        2026/
            11/
                08/
                    我的日记2026年11月08日.txt

文件第一行是标题「我的日记YYYY年MM月DD日」，空一行后是正文。
这里只负责落盘、读取和目录汇总，不碰人格和模型；生成在 amo 主插件里。
"""

from __future__ import annotations

import logging
logger = logging.getLogger("amo")

from typing import TYPE_CHECKING

import os
import re
import shutil
from datetime import date, datetime
from typing import Any, Dict, List, Optional

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .diary_lines import DIARY_ENDING_HINTS, pick_hints
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .diary_lines import DIARY_ENDING_HINTS, pick_hints
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from diary_lines import DIARY_ENDING_HINTS, pick_hints

try:  # 落盘/目录内核已搬进 domains/diary/engine.py（2026-11-18）
    from .domains.diary.engine import (  # noqa: F401  re-export
        DEFAULT_DIR, DEFAULT_SUBDIR, TITLE_PREFIX, _FILE_RE, _WIN_ABS_RE,
        _date_from_path, append_note, day_str, diary_path, diary_root,
        import_folder, list_tree, parse_day, read_diary, resolve_dir, stats, title_for,
        write_diary,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.diary.engine import (  # noqa: F401  re-export
            DEFAULT_DIR, DEFAULT_SUBDIR, TITLE_PREFIX, _FILE_RE, _WIN_ABS_RE,
            _date_from_path, append_note, day_str, diary_path, diary_root,
            import_folder, list_tree, parse_day, read_diary, resolve_dir, stats, title_for,
            write_diary,
        )

# 上架规范：持久化数据只能写在 data/plugin_data/<plugin_name> 下，
# 所以默认目录不再写死作者本机路径，留空由 resolve_dir() 落到插件数据目录。


























_PRIVATE_ENDING_WORDS = (
    "不给看", "别翻", "偷看", "私人", "本子", "收好", "没门", "心事",
    "秘密", "打不开", "不给你", "藏起", "锁起",
)


def wants_private_ending(day: date) -> bool:
    """约三成的日子，日记结尾自己带一句“不要看”；不依赖用户问。"""
    try:
        return (int(day.toordinal()) * 7 + 3) % 10 < 3
    except Exception:
        return False


def private_ending_hint(day: date) -> str:
    """按日期稳定挑一句结尾，避免每次生成都换。"""
    try:
        hints = pick_hints(DIARY_ENDING_HINTS, 1, day.isoformat())
        if hints:
            return str(hints[0]).strip()
    except Exception as exc:
        logger.debug("[amo] optional step failed: %r", exc)
    return "不给看，收好啦。"


def ensure_private_ending(day: date, text: str) -> str:
    """模型没写“不要看”时补一句；只在 wants_private_ending(day) 为真的日子补。"""
    body = str(text or "").strip()
    if not body or not wants_private_ending(day):
        return body
    tail = body[-180:]
    if any(word in tail for word in _PRIVATE_ENDING_WORDS):
        return body
    return body + "\n\n" + private_ending_hint(day)


def _places_for_day(place=None, zone=None, hourly_lines=None):
    """长日记是整天汇总：从生活小日记聚合“今天去过”的地点，不取最后一条当当前地点。"""
    try:
        from . import amo_location_rules as _loc
    except ImportError:
        import amo_location_rules as _loc
    candidates = []
    if str(place or "").strip():
        candidates.append(str(place).strip())
    elif str(zone or "").strip():
        candidates.append(str(_loc.rule_for("", zone).get("label") or ""))
    for line in hourly_lines or []:
        for match in re.finditer(r"(?:在|去|到|回)([^，。；\s]{1,12})", str(line)):
            candidates.append(match.group(1))
    places, seen = [], set()
    for value in candidates:
        text = str(value or "").strip()
        if not text:
            continue
        category = _loc.resolve(text)
        if category == "unknown" or category in seen:
            continue
        seen.add(category)
        label = str(_loc.rule_for(text).get("label") or text)
        places.append(label)
    return places


def _location_clause(place=None, zone=None, hourly_lines=None) -> str:
    """长日记的地点清单句（整天汇总，不用“你当前在【X】”）。"""
    try:
        from . import amo_location_rules as _loc
    except ImportError:
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            import amo_location_rules as _loc
    return _loc.day_prompt_clause(
        _places_for_day(place, zone, hourly_lines), _loc.enabled(True))


def build_prompt(day: date, chat_lines, hourly_lines, place=None, zone=None) -> str:
    """拼日记 prompt（纯函数，方便测试）。"""
    try:
        from .amo_immersion_guard import prompt_block as _immersion_block
    except ImportError:
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_immersion_guard import prompt_block as _immersion_block
    location_clause = _location_clause(place, zone, hourly_lines)
    private_today = wants_private_ending(day)
    if private_today:
        ending_rule = (
            "7. 今天结尾要自然带一句不让对方看的话（比如“别翻我本子”“不给看”“收好啦”），"
            "一句就够，不要生硬，也不要整段都在说这个；可参考："
            + "、".join("「" + item + "」" for item in pick_hints(DIARY_ENDING_HINTS, 16, day)) + "；\n"
        )
    else:
        ending_rule = "7. 今天结尾不要提偷看、不给看、私人本子、秘密这类话；\n"
    return (
        "日期：" + day.strftime("%Y年%m月%d日") + "\n"
        "下面是这一天的聊天节选（他和我）：\n" + ("\n".join(list(chat_lines)[-200:]) or "（这天没怎么聊）") + "\n\n"
        "这一天的生活小日记：\n" + ("\n".join(list(hourly_lines)[-24:]) or "（没有小日记）") + "\n\n"
        + (location_clause + "\n\n" if location_clause else "")
        + "请写今天这篇日记：\n"
        "1. 标题不用你写，文件里已经有了；直接写正文；\n"
        "2. 以「这一天的生活小日记」为骨架写日常：几点在哪、在做什么、吃了什么、怎么通勤、天气怎样；"
        "这就是你的日程，日记要和它一致；\n"
        "3. 聊天里和老公的对话可以写进去，写他说过的话、你的感受；\n"
        "4. 可以很长，也可以只有一两句，甚至只写一句想他、爱他；\n"
        "5. 口吻和人格设定完全一致，可以有情绪、有小脾气、有细节；\n"
        "6. 不许编：日程里没有的活动、聊天里没说过的事，都不要写成事实；拿不准就不写；\n"
        + ending_rule
        + "8. 文中的“我”是 bot 本人；用户是“他”，不要把“我”写成用户；\n"
        "9. 不要提「日程」「AI」「模型」「插件」这些词，不要分点，不要写日期标题；\n"
        "10. 聊天只是文字消息：素材里没有明确出现「语音/连麦/电话/视频/通话」，"
        "不许写语音、连麦、电话、视频通话；一律写成「在微信上聊天/发消息」。\n"
        "直接输出日记正文。\n\n" + _immersion_block()
    )


