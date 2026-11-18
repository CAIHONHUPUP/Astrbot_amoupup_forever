# -*- coding: utf-8 -*-
"""日记落盘 / 目录汇总内核（2026-11-18 从 diary_engine.py 搬进 domains/diary）。

只做文件与目录：解析目录、读写 txt、列树、导入旧目录、统计；不碰人格/模型。
根模块 diary_engine.py 保留提示词/结尾语等适配层，并 re-export 本模块的名字。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("amo")

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
            except (OSError, ValueError) as exc:
                logger.debug("[amo] optional step failed: %r", exc)
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
        except (OSError, ValueError) as exc:
            logger.debug("[amo] optional step failed: %r", exc)
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
    except OSError as exc:
        logger.debug("[amo] optional step failed: %r", exc)
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
    except OSError as exc:
        logger.debug("[amo] optional step failed: %r", exc)
    return ""
def append_note(root: str, day: date, note: str) -> str:
    """在已定稿日记末尾追加“补记”，绝不覆盖正文。"""
    body = str(note or "").strip()
    path = diary_path(root, day)
    current = read_diary(root, day)
    if not current or not body:
        return ""
    content = current.rstrip() + "\n\n【补记】" + body + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(content)
    os.replace(tmp, path)
    return path
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
        except ValueError as exc:
            logger.debug("[amo] optional step failed: %r", exc)
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
def stats(root: str) -> Dict[str, Any]:
    tree = list_tree(root)
    return {"total": tree["total"], "latest": tree["latest"], "root": tree["root"]}
