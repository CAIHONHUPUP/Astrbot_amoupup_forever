# -*- coding: utf-8 -*-
"""回复与当前地点/状态一致性（2026-11-21）。

线上截图：她人在路上，却说「刚到家、脱外套、自己煮面」，还问「笑什么、也在刷视频」。
这里做确定性收口：
- 不在家：删掉居家动作/到家句，只保留「还在路上」；
- 在家：删掉「还在路上/快到了」；
- 他分享视频/图片：不许角色反转让他看自己发的，不许换动物；
- 他刚说笑死：不许再问「笑什么」。
"""
from __future__ import annotations

import re

_HOME_ACTION = re.compile(
    r"(?:我|自己|等会儿|等会|回去).{0,8}"
    r"(?:煮.{0,3}面|吃.{0,2}(?:面|饭)|做(?:个|点|顿)?饭|洗碗|洗衣服|换.{0,3}(?:衣服|鞋)|脱.{0,4}外套|上.{0,2}床)")
_ARRIVAL = re.compile(r"(?:刚到家|到家了|到家|刚进门|进家门|进屋|鞋都换了|换好鞋|穿上拖鞋)")
_ROAD_CLAIM = re.compile(r"(?:还在路上|在路上|快到了|马上到|地铁上|公交上|在车上)")
_SHARE_ROLE = re.compile(r"[^。！？\n]*(?:你快看|你看看这个|给你看看|快看这个)[^。！？\n]*[。！？]?")
_LAUGH_ASK = re.compile(r"[^。！？\n]*(?:笑什么|笑啥)[^。！？\n]*[。！？]?")
_FAKE_SHARE = re.compile(r"[^。！？\n]*(?:你快看这个视频|快看这个视频|给你看这个视频|我刚刷到一个视频)[^。！？\n]*[。！？]?")
_BAD_COOK = re.compile(r"[^。！？\n]*(?:弄点吃的[^。！？\n]*煮.{0,2}面|煮.{0,2}面[^。！？\n]*弄点吃的)[^。！？\n]*[。！？]?")
_ANIMALS = (("猫", ("猫", "猫猫", "小猫")), ("狗", ("狗", "狗狗", "狗子", "小狗")),
            ("兔", ("兔子", "小兔")), ("鸟", ("小鸟", "鸟")), ("仓鼠", ("仓鼠",)))


def _scene(plugin):
    try:
        act = plugin.state.current_activity() or {}
    except Exception:
        act = {}
    place = str(act.get("place") or "")
    zone = str(act.get("zone") or "")
    on_road = zone in ("road", "outside") or any(
        word in place for word in ("路上", "地铁", "公交", "车", "街", "外面"))
    home = zone.startswith("home") or ("家" in place and "路上" not in place)
    return {"home": home, "road": on_road and not home}


def _sentences(text):
    return [part for part in re.split(r"(?<=[。！？!?\n])", str(text or "")) if part.strip()]


def _animals(text):
    body = str(text or "")
    hit = set()
    for name, words in _ANIMALS:
        if any(word in body for word in words):
            hit.add(name)
    return hit


def rewrite(text, plugin, user_text=""):
    body = str(text or "")
    if not body.strip():
        return body
    scene = _scene(plugin)
    user = str(user_text or "")
    # 括号里的居家动作/到家，先整段拿掉（不在家时）
    if scene["road"]:
        body = re.sub(r"（[^）]*(?:刚到家|到家|进门|脱.{0,4}外套|换鞋|煮.{0,3}面|做饭|洗碗)[^）]*）", "", body)
    out = []
    for sentence in _sentences(body):
        if scene["road"]:
            if _ARRIVAL.search(sentence):
                out.append("我还在路上呢，快到了")
                continue
            if _HOME_ACTION.search(sentence):
                continue
        elif scene["home"]:
            if _ROAD_CLAIM.search(sentence) and not _ARRIVAL.search(sentence):
                out.append("我在家呢")
                continue
        out.append(sentence)
    body = "".join(out).strip()
    # 同一件事不要重复“新分享”：她自己刚发过视频，就接着聊
    if any(word in user for word in ("视频", "图片", "刷到", "看到")):
        _ctx_animals = _animals(user)
        _topic = sorted(_ctx_animals)[0] if _ctx_animals else "视频"
        if _FAKE_SHARE.search(body):
            body = _FAKE_SHARE.sub("就是刚才那个%s，" % _topic, body).strip()
        body = _SHARE_ROLE.sub("", body).strip()
    # 把「弄点吃的…煮的面」这种硬拼句改顺
    if _BAD_COOK.search(body):
        body = _BAD_COOK.sub("我等会儿煮点面吃。", body).strip()
    user_animals = _animals(user)
    if user_animals:
        keep = sorted(user_animals)[0]
        for name, words in _ANIMALS:
            if name in user_animals:
                continue
            for word in words:
                body = body.replace(word, keep)
    # 他刚说笑死/好笑：不许反问笑什么
    if any(word in user for word in ("笑死", "好笑", "笑喷", "哈哈哈哈")) and _LAUGH_ASK.search(body):
        body = _LAUGH_ASK.sub("", body).strip()
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if body:
        return body
    return "我还在路上呢，快到了。" if scene["road"] else "嗯。"
