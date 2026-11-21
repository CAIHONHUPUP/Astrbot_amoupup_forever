# -*- coding: utf-8 -*-
"""先理解再回复：意图 / 情绪归属 / 对象（2026-11-21）。

不是再加一条禁语，而是在生成前告诉模型「他到底在说什么」：
- 他在吐槽，不是指责她；
- 他在问，不是在陈述；
- 他在分享，她要做接收者。
"""
from __future__ import annotations

import re

_QUESTION = re.compile(r"[?？]|吗|什么|哪|怎么|为什么|多少|谁|几")
_VENT = re.compile(r"我.{0,8}(?:气|烦|难受|累|崩|无语|火大|炸)")
_SHARE = re.compile(r"视频|图片|照片|刷到|看到|分享")
_REQUEST = re.compile(r"帮我|给我|能不能|可不可以|陪我|讲个|唱个")
_JOKE = re.compile(r"哈哈|笑死|搞笑|段子")
_TARGET = re.compile(r"(?:这|那)?([\u4e00-\u9fffA-Za-z0-9]{1,8})(?:剧|电视|视频|电影|游戏|饭|面|工作|班|人)")


def _act(text):
    if _VENT.search(text):
        return "吐槽"
    if _QUESTION.search(text):
        return "提问"
    if _SHARE.search(text):
        return "分享"
    if _REQUEST.search(text):
        return "请求"
    if _JOKE.search(text):
        return "开玩笑"
    return "陈述"


def _emotion(text):
    if re.search(r"气|烦|火大|炸", text):
        return "生气"
    if re.search(r"难受|委屈|崩", text):
        return "难受"
    if re.search(r"累|困", text):
        return "累"
    if _JOKE.search(text):
        return "开心"
    return "平静"


def _target(text):
    match = _TARGET.search(text)
    if match:
        return (match.group(1) + match.group(0)[len(match.group(1)):])[:12]
    return "他说的这件事"


def build(text, recent=""):
    body = str(text or "").strip()
    act = _act(body)
    card = {
        "speaker": "他",
        "act": act,
        "emotion": _emotion(body),
        "target": _target(body),
        "need": "共情" if act == "吐槽" else ("回答" if act == "提问" else "接住"),
    }
    return card


def prompt_block(card):
    if not isinstance(card, dict) or not card:
        return ""
    return ("【他在说什么】%s在%s；情绪：%s；对象：%s；需要：%s。"
            "先接住他这句，不要防御、不要换主语、不要答非所问。"
            % (card.get("speaker", "他"), card.get("act", "陈述"),
               card.get("emotion", "平静"), card.get("target", "这件事"),
               card.get("need", "接住")))


def issues(card, reply):
    if not isinstance(card, dict):
        return []
    body = str(reply or "")
    if card.get("act") == "吐槽" and re.search(r"我(?:哪|没|才不).{0,4}(?:气|生气|烦)|别给我扣帽子", body):
        return ["把他在吐槽写成你在生气"]
    return []
