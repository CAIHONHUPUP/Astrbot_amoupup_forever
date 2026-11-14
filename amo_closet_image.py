# -*- coding: utf-8 -*-
"""买衣服后生成参考图：自定义生图接口（URL/Token 可配置）+ 超时回退文本。"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time


def fallback_text(item):
    item = item or {}
    name = str(item.get("name") or "衣服")
    color = str(item.get("color") or "")
    is_night = str(item.get("time_slot") or "") == "night" or bool(item.get("sleep"))
    slot = "夜间穿搭" if is_night else "白天穿搭"
    return "🎀 [%s] 👗 [%s]（%s）" % (color or "原色", name, slot)


def build_prompt(item):
    item = item or {}
    name = str(item.get("name") or "衣服")
    color = str(item.get("color") or "")
    cat = str(item.get("category") or "top")
    label = {"top": "上衣", "bottom": "裤子", "skirt": "裙子",
             "coat": "外套", "shoes": "鞋子"}.get(cat, "衣物")
    return ("生成一张服装平铺参考图：%s%s，%s，纯白背景，正面平铺，"
            "完整展示款式、颜色、面料与细节，电商商品图风格，无人物、无文字。" % (color, name, label))


def _cfg(plugin, key, default=""):
    try:
        return plugin.cfg_str(key, default)
    except Exception:
        return default


def _request(url, token, prompt, timeout):
    import urllib.request
    body = json.dumps({"prompt": prompt}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + str(token)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


async def generate(plugin, item):
    """返回 {ok, path|fallback}；绝不抛异常。"""
    item = dict(item or {})
    url = _cfg(plugin, "closet_image_api_url", "")
    if not url:
        return {"ok": False, "fallback": fallback_text(item)}
    token = _cfg(plugin, "closet_image_token", "")
    try:
        timeout = float(_cfg(plugin, "closet_image_timeout", "45") or 45)
    except (TypeError, ValueError):
        timeout = 45.0
    prompt = build_prompt(item)
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_request, url, token, prompt, timeout), timeout + 5)
        data = json.loads(raw.decode("utf-8"))
        image = data.get("image_base64") or data.get("b64") or data.get("image") or ""
        if not image and data.get("image_url"):
            image = await asyncio.to_thread(_request_download, data["image_url"], timeout)
        if isinstance(image, str) and image.startswith("http"):
            image = await asyncio.to_thread(_request_download, image, timeout)
        if isinstance(image, str):
            blob = base64.b64decode(image.split(",")[-1])
        else:
            blob = bytes(image)
        from . import outfit_refs
        path = outfit_refs.item_ref_path(plugin, item)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(blob)
        _save_path(plugin, item, path)
        return {"ok": True, "path": path}
    except Exception:
        return {"ok": False, "fallback": fallback_text(item)}


def _request_download(url, timeout):
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def _save_path(plugin, item, path):
    wardrobe = getattr(plugin, "wardrobe", None)
    if wardrobe is None:
        return
    try:
        data = wardrobe._load()
        for row in data.get("items") or []:
            if (row.get("category") == item.get("category")
                    and row.get("name") == item.get("name")):
                row["image_path"] = path
                break
        wardrobe._save(data)
    except Exception:
        pass


async def ensure_after_add(plugin, item):
    result = await generate(plugin, item)
    if not result.get("ok"):
        try:
            plugin.logger.info("[衣橱] 参考图回退：%s" % result.get("fallback"))
        except Exception:
            pass
        try:
            from . import wardrobe
        except ImportError:
            pass
    return result
