# -*- coding: utf-8 -*-
"""P22 房间参考图 A/B：3 张带房间 ref + 3 张不带，只差 refs 的最后一个。

不参与默认链路：由显式脚本调用，图片写 backup 目录，不写 plugin_data。
"""
from __future__ import annotations

import base64
import os
import tempfile
import time
import urllib.request

ROOM_AB_ROUNDS = 3
AB_DIR_ENV = "AMO_PHOTO_AB_DIR"


def ab_output_dir(base="", ts=None):
    """A/B 输出目录：调用方给 base，其次环境变量，最后临时目录；不写死作者路径。"""
    ts = ts or time.strftime("%Y%m%d-%H%M%S")
    root = str(base or os.environ.get(AB_DIR_ENV, "") or tempfile.mkdtemp(prefix="amo-photo-room-ab-"))
    path = os.path.join(root, "photo-room-ab-%s" % ts)
    os.makedirs(path, exist_ok=True)
    return path


def _image_ext(data):
    """按文件头挑扩展名：我能永远定格你常回 jpg，落盘不能硬写 .png。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _save_item(item, target):
    """把我能永远定格你返回的 item 落成 png：file_path / data_url / url 三种。"""
    if not isinstance(item, dict):
        return ""
    data = b""
    path = str(item.get("file_path") or "")
    if path and os.path.isfile(path):
        with open(path, "rb") as handle:
            data = handle.read()
    if not data and str(item.get("data_url") or "").startswith("data:image"):
        data = base64.b64decode(str(item["data_url"]).split(",", 1)[1])
    if not data:
        url = str(item.get("url") or item.get("image_url") or "")
        if url.startswith("http"):
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
    if not data:
        return ""
    target = os.path.splitext(target)[0] + _image_ext(data)
    with open(target, "wb") as handle:
        handle.write(data)
    return target


async def run_room_ab(engine, out_dir, prompt, refs, room_ref, negative="",
                      rounds=ROOM_AB_ROUNDS, scene_lock="", pose_lock=""):
    """跑 A/B：同一提示词、同一套基础 refs；A 组末尾加房间 ref，B 组不加。"""
    omni = engine._find_omni()
    if omni is None:
        return {"dir": out_dir, "files": [], "errors": ["没找到我能永远定格你"]}
    base = [str(item) for item in (refs or []) if item]
    room_ref = str(room_ref or "")
    saved, errors = [], []
    for tag, extra in (("A_room", [room_ref]), ("B_plain", [])):
        call_refs = base + [item for item in extra if item]
        args = {"prompt": prompt, "count": 1, "record_usage": False, "mode": "selfie",
                "negative_prompt": negative, "scene_lock": scene_lock, "pose_lock": pose_lock}
        if call_refs:
            args["refs"] = call_refs
            args["allow_local_refs"] = True
        for index in range(1, int(rounds) + 1):
            try:
                result = await omni.generate_images_for_plugin(**args)
            except Exception as exc:
                errors.append("%s_%d: %s" % (tag, index, exc))
                continue
            images = result.get("images") if isinstance(result, dict) else None
            target = os.path.join(out_dir, "%s_%d.png" % (tag, index))
            try:
                path = _save_item((images or [None])[0], target) if images else ""
            except Exception as exc:
                path = ""
                errors.append("%s_%d download: %s" % (tag, index, exc))
            if path:
                saved.append(path)
            else:
                errors.append("%s_%d: 没拿到图片（%s）" % (tag, index, (result or {}).get("message", "")))
    return {"dir": out_dir, "files": saved, "errors": errors}
