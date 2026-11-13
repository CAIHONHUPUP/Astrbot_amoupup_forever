# -*- coding: utf-8 -*-
"""服装参考图：每件衣服生成一张平铺图，生图时当参考图用。

目录结构（按衣服名字保存，备份 plugin_data 时一起带走）：
``outfit_refs/白天搭配/<上衣（外套）|裤子|裙子|鞋子>/<衣服名字>.png``
``outfit_refs/晚上搭配/...``、``outfit_refs/酒店睡衣/...``
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import shutil

from astrbot.api import logger

try:
    from .amo_image_common import find_omni
except Exception:  # pragma: no cover
    try:
        from amo_image_common import find_omni
    except Exception:
        find_omni = None  # type: ignore[assignment]

REF_DIR_NAME = "outfit_refs"
SCOPE_FOLDERS = (
    ("day", "白天搭配"),
    ("night", "晚上搭配"),
    ("hotel", "酒店睡衣"),
    ("swim", "泳装"),
)
CATEGORY_FOLDERS = {
    "coat": "上衣（外套）",
    "top": "上衣（外套）",
    "bottom": "裤子",
    "skirt": "裙子",
    "shoes": "鞋子",
}
_SAFE_RE = re.compile(r'[\\/:*?"<>|\r\n]+')
_batch_running = False
_batch_lock = asyncio.Lock()
_batch_state = {"running": False, "total": 0, "done": 0, "current": "", "failed": []}


def base_dir(plugin):
    base = ""
    try:
        base = os.path.dirname(str(getattr(plugin.db, "path", "") or ""))
    except Exception:
        base = ""
    if not base:
        base = str(getattr(plugin, "plugin_dir", "") or ".")
    return os.path.join(base, REF_DIR_NAME)


def safe_name(name):
    text = _SAFE_RE.sub("_", str(name or "").strip())
    return text[:60].strip(" .") or "outfit"


def scope_of(item):
    item = item if isinstance(item, dict) else {}
    if item.get("swim"):
        return "swim"
    if item.get("hotel"):
        return "hotel"
    if item.get("sleep"):
        return "night"
    return "day"


def category_of(item):
    item = item if isinstance(item, dict) else {}
    category = str(item.get("category") or "").strip()
    return category if category in CATEGORY_FOLDERS else "top"


def item_dir(plugin, item):
    scope = scope_of(item)
    folder = dict(SCOPE_FOLDERS)[scope]
    return os.path.join(base_dir(plugin), folder, CATEGORY_FOLDERS[category_of(item)])


def item_ref_path(plugin, item):
    return os.path.join(item_dir(plugin, item), safe_name(item.get("name")) + ".png")


def find_ref(plugin, name):
    """在所有搭配/分类里找同名参考图。"""
    filename = safe_name(name) + ".png"
    for _, scope_folder in SCOPE_FOLDERS:
        for category_folder in CATEGORY_FOLDERS.values():
            path = os.path.join(base_dir(plugin), scope_folder, category_folder, filename)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return path
    return ""


def _decode_item(item):
    if not isinstance(item, dict):
        return b""
    data_url = str(item.get("data_url") or "")
    if data_url.startswith("data:image"):
        try:
            return base64.b64decode(data_url.split(",", 1)[1])
        except Exception:
            return b""
    for key in ("file_path", "image_url"):
        path = str(item.get(key) or "")
        if path and os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    return fh.read()
            except Exception:
                return b""
    return b""


def _as_png(data):
    """万象画卷有时返回 JPEG 却按 .png 保存；转成真正的 PNG，避免参考图校验失败。"""
    if not data:
        return b""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        image.load()
        out = io.BytesIO()
        image.convert("RGB").save(out, "PNG")
        return out.getvalue()
    except Exception as exc:
        logger.debug("[amo] 参考图转 PNG 失败: %s" % exc)
        return b""


async def _download(url, session=None):
    if not url:
        return b""
    try:
        import aiohttp
        own = session is None
        session = session or aiohttp.ClientSession()
        try:
            async with session.get(url, timeout=60) as resp:
                if resp.status == 200:
                    return await resp.read()
        finally:
            if own:
                await session.close()
    except Exception as exc:
        logger.debug("[amo] 服装参考图下载失败: %s" % exc)
    return b""


async def ensure_item_ref(plugin, item, session=None, force=False):
    """生成/复用一件衣服的参考图，返回路径；失败返回空串。"""
    item = item if isinstance(item, dict) else {}
    name = str(item.get("name") or "").strip()
    if not name:
        return ""
    path = item_ref_path(plugin, item)
    if os.path.isfile(path) and os.path.getsize(path) > 0 and not force:
        return path
    existing = find_ref(plugin, name)
    if existing and not force:
        if os.path.abspath(existing) != os.path.abspath(path):
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                shutil.copyfile(existing, path)
            except Exception:
                return existing
        return path
    omni = find_omni(plugin, plugin, "outfit_refs.ensure_item_ref") if find_omni is not None else None
    if omni is None:
        return ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    prompt = (
        "服装平铺参考图：" + name + "。纯白背景，只拍这一件衣服，正面平铺，"
        "不出现人、不出现模特、不出现衣架，颜色和款式准确，布料质感清楚。"
    )
    try:
        result = await asyncio.wait_for(
            omni.generate_images_for_plugin(prompt=prompt, count=1, record_usage=False),
            timeout=90,
        )
    except asyncio.TimeoutError:
        logger.warning("[amo] 服装参考图生成超时（%s）" % name)
        return ""
    except Exception as exc:
        logger.warning("[amo] 服装参考图生成失败（%s）: %s" % (name, exc))
        return ""
    images = result.get("images") if isinstance(result, dict) else None
    if not images:
        return ""
    data = _decode_item(images[0])
    if not data:
        data = await _download(str(images[0].get("url") or images[0].get("image_url") or ""), session)
    data = _as_png(data)
    if not data:
        return ""
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        if os.path.getsize(path) <= 0:
            return ""
        logger.info("[amo] 服装参考图已生成：%s" % path)
        return path
    except Exception as exc:
        logger.warning("[amo] 服装参考图保存失败（%s）: %s" % (name, exc))
        return ""


async def ensure_ref(plugin, name, session=None, force=False):
    """按名字生成/复用参考图；不在库里的名字按普通上衣处理。"""
    name = str(name or "").strip()
    if not name:
        return ""
    existing = find_ref(plugin, name)
    if existing and not force:
        return existing
    category = "top"
    try:
        from .wardrobe import classify_item, classify_sleep_item
    except Exception:
        classify_item = classify_sleep_item = None  # type: ignore[assignment]
    if classify_sleep_item is not None:
        sleep_category = classify_sleep_item(name)
        if sleep_category:
            category = sleep_category
        elif classify_item is not None:
            category = classify_item(name) or "top"
    item = {"name": name, "category": category}
    return await ensure_item_ref(plugin, item, session=session, force=force)


async def ensure_many(plugin, names, session=None, limit=4):
    refs = []
    for name in list(names or [])[:limit]:
        path = await ensure_ref(plugin, name, session=session)
        if path:
            refs.append(path)
    return refs


async def ensure_batch(plugin, items, session=None, progress=None):
    """逐件生成：生成并确认落盘后再做下一件；已有文件直接跳过。"""
    global _batch_running
    async with _batch_lock:
        if _batch_running:
            return {"ok": False, "msg": "已经在生成了", "state": batch_status()}
        _batch_running = True
        _batch_state.update({"running": True, "total": len(items), "done": 0,
                             "current": "", "failed": []})
    try:
        total = len(items)
        done = 0
        failed = []
        for index, item in enumerate(items, start=1):
            name = str(item.get("name") or "")
            _batch_state["current"] = name
            path = await ensure_item_ref(plugin, item, session=session)
            if path:
                done += 1
                logger.info("[amo] 参考图进度 %d/%d：%s" % (index, total, name))
            else:
                failed.append(name)
                logger.warning("[amo] 参考图失败 %d/%d：%s" % (index, total, name))
            _batch_state["done"] = done
            _batch_state["failed"] = list(failed)
            if progress:
                try:
                    progress(index, total, name)
                except Exception:
                    pass
            await asyncio.sleep(0.5)
        return {"ok": True, "done": done, "failed": failed, "total": total}
    finally:
        async with _batch_lock:
            _batch_running = False
            _batch_state["running"] = False
            _batch_state["current"] = ""


def batch_status():
    return dict(_batch_state)


def pending_items(plugin, items):
    """只返回还没生成过参考图的衣服。"""
    pending = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        path = item_ref_path(plugin, item)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            continue
        if find_ref(plugin, name):
            continue
        pending.append(item)
    return pending


def batch_items(wardrobe):
    try:
        data = wardrobe._load()
    except Exception:
        return []
    return list(data.get("items") or [])
