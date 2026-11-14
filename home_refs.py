# -*- coding: utf-8 -*-
"""家的场景参考图（2026-11-14）：客厅 / 卧室各一张，入住或搬家时生成。

- 生成走万象画卷 generate_images_for_plugin(prompt=...)；
- 不出现人物，只拍房间，风格/主色/家具按 housing 里的固定属性；
- 已有图就复用，搬家/大件家具/装修才 force 重生成；
- 万象画卷没装、超时、返回坏图都只返回空，聊天继续用文字描述，不阻塞。
"""
from __future__ import annotations

import asyncio
import os

try:
    from .amo_image_common import find_omni
    from .outfit_refs import _as_png, _decode_item, _download
except ImportError:  # 直接按文件加载时
    from amo_image_common import find_omni  # type: ignore[no-redef]
    from outfit_refs import _as_png, _decode_item, _download  # type: ignore[no-redef]

ROOMS = (("living", "客厅"), ("bedroom", "卧室"))


def build_room_prompt(home, room_label="客厅", now=None):
    """按房间动态拼生图 Prompt：房型/家具/风格/主色/季节 + 风格协调约束。"""
    home = home or {}
    try:
        from .housing import build_room_image_prompt, room_of_item
    except ImportError:
        from housing import build_room_image_prompt, room_of_item
    key = "bedroom" if "卧" in str(room_label) else "living"
    items = (home.get("room_furniture") or {}).get(key)
    if not items:
        items = [item for item in (home.get("furniture") or [])
                 if room_of_item(item) == key]
    prompt, _negative = build_room_image_prompt(
        room_label, home.get("style") or "简约风",
        home.get("colors") or "米白色", items, now)
    return prompt


def room_path(housing, room):
    return os.path.join(housing.image_dir(), "%s-%s.png" % (housing.current().get("id") or "home", room))


async def ensure_room_images(plugin, housing=None, force=False, session=None):
    """生成/复用当前家的客厅、卧室场景图，返回 {room: path}。"""
    housing = housing or getattr(plugin, "housing", None)
    if housing is None:
        return {}
    root = housing.image_dir()
    try:
        os.makedirs(root, exist_ok=True)
    except Exception:
        return {}
    if not force and not housing.can_redraw():
        housing.mark_image_failed()
        return {}
    omni = find_omni(plugin, plugin, "home_refs.ensure_room_images") if find_omni is not None else None
    if omni is None:
        return {}
    out = {}
    attempted = 0
    failed = 0
    for room, label in ROOMS:
        path = room_path(housing, room)
        if os.path.isfile(path) and os.path.getsize(path) > 0 and not force:
            housing.set_room_image(room, path)
            out[room] = path
            continue
        prompt, negative = housing.room_prompt(label)
        attempted += 1
        try:
            result = await asyncio.wait_for(
                omni.generate_images_for_plugin(prompt=prompt, count=1, record_usage=False,
                                                negative_prompt=negative),
                timeout=120,
            )
        except TypeError:
            try:
                result = await asyncio.wait_for(
                    omni.generate_images_for_plugin(prompt=prompt, count=1, record_usage=False),
                    timeout=120,
                )
            except Exception:
                failed += 1
                continue
        except Exception:
            failed += 1
            continue
        images = result.get("images") if isinstance(result, dict) else None
        if not images:
            failed += 1
            continue
        first = images[0]
        data = _decode_item(first)
        if not data and isinstance(first, dict):
            data = await _download(str(first.get("url") or first.get("image_url") or ""), session)
        data = _as_png(data)
        if not data:
            failed += 1
            continue
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except Exception:
            continue
        if os.path.getsize(path) <= 0:
            continue
        housing.set_room_image(room, path)
        out[room] = path
    if out and not failed:
        housing.mark_image_ready()
    elif failed:
        housing.mark_image_failed()
    return out
