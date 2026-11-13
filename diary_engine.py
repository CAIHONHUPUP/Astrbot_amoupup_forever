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
        from diary_lines import DIARY_ENDING_HINTS, pick_hints

# 上架规范：持久化数据只能写在 data/plugin_data/<plugin_name> 下，
# 所以默认目录不再写死作者本机路径，留空由 resolve_dir() 落到插件数据目录。
DEFAULT_DIR = ""
DEFAULT_SUBDIR = "diary"
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
TITLE_PREFIX = "我的日记"
_FILE_RE = re.compile(r"^我的日记(\d{4})年(\d{2})月(\d{2})日\.txt$")


def resolve_dir(setting: Any, data_root: str) -> str:
    """把配置里的日记目录解析成合规路径（只允许落在插件数据目录下）。

    - 留空：``<data_root>/diary``，这是默认行为；
    - 相对路径：挂在 ``<data_root>/`` 下，例如 ``日记`` -> ``<data_root>/日记``；
    - 绝对路径：只有位于 ``data_root`` 内部才采用，写到别处一律回退默认目录；
    - 老版本默认值（Windows 盘符路径 ``D:\\...``）一律回退默认目录，避免升级后
      还在往插件数据目录外面写文件。
    """
    root = str(data_root or "").strip()
    default = os.path.join(root, DEFAULT_SUBDIR) if root else ""
    text = str(setting or "").strip()
    if not text:
        return default
    # 作者本机路径一类的旧默认值：不管当前平台认不认，直接回退。
    # 但 Windows 上用户自己填的、确实落在数据目录内的绝对路径要保留
    # （以前一律当旧默认值丢掉，等于面板里填的目录不生效）。
    if _WIN_ABS_RE.match(text):
        if os.name == "nt" and root:
            try:
                resolved = os.path.abspath(text)
                resolved_root = os.path.abspath(root)
                if resolved == resolved_root or resolved.startswith(resolved_root + os.sep):
                    return resolved
            except (OSError, ValueError):
                pass
        return default
    if not os.path.isabs(text):
        if not root:
            return text
        joined = os.path.abspath(os.path.join(root, text))
        resolved_root = os.path.abspath(root)
        if joined == resolved_root or joined.startswith(resolved_root + os.sep):
            return joined
        return default
    if root:
        try:
            resolved_root = os.path.abspath(root)
            resolved = os.path.abspath(text)
            if resolved == resolved_root or resolved.startswith(resolved_root + os.sep):
                return resolved
        except (OSError, ValueError):
            pass
    return default


def parse_day(value: Any) -> Optional[date]:
    """把 '2026-11-08' / '2026/11/8' / date / datetime 统一成 date。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("/", "-").replace(".", "-").replace("年", "-").replace("月", "-").replace("日", "")
    parts = [part for part in text.split("-") if part]
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (TypeError, ValueError):
        return None


def day_str(day: date) -> str:
    return day.strftime("%Y-%m-%d")


def title_for(day: date) -> str:
    return "%s%d年%02d月%02d日" % (TITLE_PREFIX, day.year, day.month, day.day)


def diary_root(root: str = "") -> str:
    return str(root or DEFAULT_DIR)


def diary_path(root: str, day: date) -> str:
    base = diary_root(root)
    return os.path.join(
        base, "%04d" % day.year, "%02d" % day.month, "%02d" % day.day,
        "%s.txt" % title_for(day),
    )


def write_diary(root: str, day: date, text: str) -> str:
    """写入一天的日记（原子替换），返回文件路径。"""
    body = str(text or "").strip()
    path = diary_path(root, day)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = title_for(day) + "\n\n" + body + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(content)
    os.replace(tmp, path)
    return path


def read_diary(root: str, day: date) -> str:
    path = diary_path(root, day)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return fp.read()
    except OSError:
        pass
    # 兜底：拷贝过来的日记如果文件名被改过，就按 年/月/日 目录找第一份 txt
    day_dir = os.path.dirname(path)
    try:
        for file_name in sorted(os.listdir(day_dir), reverse=True):
            if not file_name.endswith(".txt") or file_name.endswith(".tmp"):
                continue
            try:
                with open(os.path.join(day_dir, file_name), "r", encoding="utf-8") as fp:
                    return fp.read()
            except OSError:
                continue
    except OSError:
        pass
    return ""


def list_tree(root: str) -> Dict[str, Any]:
    """按 年 → 月 → 日 汇总已写的日记。"""
    base = diary_root(root)
    years: List[Dict[str, Any]] = []
    total = 0
    latest = ""
    try:
        names = os.listdir(base)
    except OSError:
        names = []
    for year_name in sorted([n for n in names if re.match(r"^\d{4}$", n)], reverse=True):
        year_dir = os.path.join(base, year_name)
        if not os.path.isdir(year_dir):
            continue
        months: List[Dict[str, Any]] = []
        for month_name in sorted([n for n in os.listdir(year_dir) if re.match(r"^\d{2}$", n)], reverse=True):
            month_dir = os.path.join(year_dir, month_name)
            if not os.path.isdir(month_dir):
                continue
            days: List[Dict[str, Any]] = []
            for day_name in sorted([n for n in os.listdir(month_dir) if re.match(r"^\d{2}$", n)], reverse=True):
                day_dir = os.path.join(month_dir, day_name)
                if not os.path.isdir(day_dir):
                    continue
                day_files = []
                for file_name in sorted(os.listdir(day_dir), reverse=True):
                    if not file_name.endswith(".txt") or file_name.endswith(".tmp"):
                        continue
                    match = _FILE_RE.match(file_name)
                    if match:
                        day_date = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                    else:
                        # 兜底：只要放在 年/月/日 目录里的 txt 都算，
                        # 这样换电脑整个文件夹拷过来、哪怕文件名被改过也能读。
                        try:
                            day_date = date(int(year_name), int(month_name), int(day_name))
                        except ValueError:
                            continue
                    path = os.path.join(day_dir, file_name)
                    try:
                        size = os.path.getsize(path)
                        with open(path, "r", encoding="utf-8") as fp:
                            text = fp.read()
                    except OSError:
                        continue
                    day_files.append((day_date, path, text, size))
                seen_dates = set()
                for day_date, path, text, size in day_files:
                    day_text = day_str(day_date)
                    if day_text in seen_dates:
                        continue
                    seen_dates.add(day_text)
                    total += 1
                    if day_text > latest:
                        latest = day_text
                    days.append({
                        "date": day_text,
                        "day": day_date.day,
                        "title": title_for(day_date),
                        "chars": len(text),
                        "size": size,
                        "path": path,
                    })
            if days:
                months.append({"month": int(month_name), "days": days})
        if months:
            years.append({"year": int(year_name), "months": months})
    return {"total": total, "latest": latest, "years": years, "root": base}


def _date_from_path(path: str, base: str) -> Optional[date]:
    """从 年/月/日 文件夹或文件名解析日期。"""
    match = _FILE_RE.match(os.path.basename(path))
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    try:
        rel = os.path.relpath(os.path.dirname(path), base).split(os.sep)
    except ValueError:
        rel = []
    for index, part in enumerate(rel):
        if not re.match(r"^\d{4}$", part):
            continue
        if index + 2 >= len(rel):
            continue
        month, day_part = rel[index + 1], rel[index + 2]
        if re.match(r"^\d{2}$", month) and re.match(r"^\d{2}$", day_part):
            try:
                return date(int(part), int(month), int(day_part))
            except ValueError:
                continue
    return None


def import_folder(source: str, root: str) -> Dict[str, Any]:
    """把别处的日记文件夹合并进来：按 年/月/日 或文件名解析日期，已存在就跳过。"""
    src = str(source or "").strip()
    result: Dict[str, Any] = {
        "ok": False, "source": src, "root": diary_root(root),
        "copied": 0, "skipped": 0, "failed": 0,
    }
    if not src or not os.path.isdir(src):
        result["error"] = "文件夹不存在"
        return result
    for dirpath, _dirnames, filenames in os.walk(src):
        for name in filenames:
            if not name.lower().endswith(".txt") or name.endswith(".tmp"):
                continue
            path = os.path.join(dirpath, name)
            day = _date_from_path(path, src)
            if not day:
                result["failed"] += 1
                continue
            dest = diary_path(root, day)
            if os.path.abspath(dest) == os.path.abspath(path) or os.path.exists(dest):
                result["skipped"] += 1
                continue
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(path, dest)
                result["copied"] += 1
            except OSError:
                result["failed"] += 1
    result["ok"] = True
    return result


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
    except Exception:
        pass
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


def build_prompt(day: date, chat_lines, hourly_lines) -> str:
    """拼日记 prompt（纯函数，方便测试）。"""
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
        "请写今天这篇日记：\n"
        "1. 标题不用你写，文件里已经有了；直接写正文；\n"
        "2. 以「这一天的生活小日记」为骨架写日常：几点在哪、在做什么、吃了什么、怎么通勤、天气怎样；"
        "这就是你的日程，日记要和它一致；\n"
        "3. 聊天里和主人的对话可以写进去，写他说过的话、你的感受；\n"
        "4. 可以很长，也可以只有一两句，甚至只写一句想他、爱他；\n"
        "5. 口吻和人格设定完全一致，可以有情绪、有小脾气、有细节；\n"
        "6. 不许编：日程里没有的活动、聊天里没说过的事，都不要写成事实；拿不准就不写；\n"
        + ending_rule
        + "8. 文中的“我”是 bot 本人；用户是“他”，不要把“我”写成用户；\n"
        "9. 不要提「日程」「AI」「模型」「插件」这些词，不要分点，不要写日期标题；\n"
        "直接输出日记正文。"
    )


def stats(root: str) -> Dict[str, Any]:
    tree = list_tree(root)
    return {"total": tree["total"], "latest": tree["latest"], "root": tree["root"]}
