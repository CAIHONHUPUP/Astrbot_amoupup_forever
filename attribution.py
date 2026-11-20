# -*- coding: utf-8 -*-
"""归属判定：他说的经历属于他，不能安到自己身上。

用户跟她说「我在外面吃了碗面」，她回「我也在外面吃了碗面」——这就是「幻视」。
这里只做两件事：

- ``first_person_experience(text)``：这句话是不是他在讲自己的经历；
- ``attribution_leak(user_text, reply)``：她的回复有没有把他的经历接成自己的。

纯函数、无依赖，方便单测。
"""

from __future__ import annotations

import re

# 第一人称 + 经历动词（身体伤痛也算经历：脸疼、被打、住院…）
_EXPERIENCE = re.compile(
    r"我(?:今天|刚才|刚|昨天|昨晚|上午|中午|下午|晚上|现在)?[^，。！？!?；;\n]{0,12}"
    r"(?:吃|喝|去|到|在|买|看|逛|玩|加班|上班|睡|住|点|回"
    r"|疼|痛|受伤|被打|被扇|挨打|住院|发烧|生病|吐|哭)"
)

# 「吃了碗面」→ 碗面 ；「在楼下那家面馆吃了」→ 楼下那家面馆
_FOOD = re.compile(r"(?:吃|喝)了?([\u4e00-\u9fff]{2,6})")
_PLACE = re.compile(r"在([\u4e00-\u9fff]{2,8}?)(?:吃|喝|逛|玩|待|上班|加班|睡)")
# 身体/遭遇类：他被打、脸疼、受伤，回复里出现「我 + 这些词」就是把他安到自己身上
_BODY = re.compile(r"(脸|头|手|脚|腿|腰|背|肚子|身上|眼睛|牙|嗓子|喉咙|胸口|心口)"
                   r"(?:好|很|太|超|挺|也|还|都|又|就|有点|有些|特别|不太|不)?"
                   r"(?:疼|痛|受伤)")
_EVENT = re.compile(r"(被打|被扇|挨打|受伤|住院|被骂)")

_STOPWORDS = {"什么", "这个", "那个", "一下", "一点", "的时", "时候", "东西", "地方"}


def first_person_experience(text):
    """他是不是在讲自己的经历（我吃了/我在哪/我刚去…）。"""
    body = str(text or "")
    if not body:
        return False
    return bool(_EXPERIENCE.search(body))


def _tokens(text):
    body = str(text or "")
    out = set()
    # _BODY 只取「部位」太泛（一个「脸」字会误伤「我看看你的脸」），
    # 伤痛归属交给 attribution_leak 里的「部位 + 疼/痛/伤」组合判。
    for pattern in (_FOOD, _PLACE, _EVENT):
        for match in pattern.finditer(body):
            token = match.group(1).strip()
            token = re.sub(r"^(个|了)", "", token)
            if len(token) >= 2 and token not in _STOPWORDS:
                out.add(token)
    return out


def _body_parts(text):
    return {match.group(1) for match in _BODY.finditer(str(text or ""))}


def _pain_sentence(sentence, pain_parts, event_hit):
    """这一句是不是把他的伤痛接成了自己的。"""
    if "我" not in sentence:
        return False
    if not any(word in sentence for word in ("疼", "痛", "受伤")):
        return False
    # 「我心疼你」是安慰，不是“我也疼”，别误伤
    if "心疼" in sentence and not any(part in sentence for part in pain_parts):
        return False
    return bool(event_hit or any(part in sentence for part in pain_parts))


def attribution_leak(user_text, reply):
    """回复里是否出现了「我 + 他的食物/地点/伤痛」，也就是把他的经历安到自己身上。"""
    body = str(user_text or "")
    pain_parts = _body_parts(body)
    event_hit = bool(_EVENT.search(body))
    if pain_parts or event_hit:
        # 伤痛要单独判：他说「脸疼」，她回「我脸不疼了」——中间夹个「不」，
        # 按 token 子串匹配会漏掉，所以按「部位 + 疼/痛/伤」的组合判。
        for sentence in re.split(r"[。！？!?；;\n]", str(reply or "")):
            if _pain_sentence(sentence, pain_parts, event_hit):
                return True
    tokens = _tokens(body)
    if not tokens:
        return False
    for sentence in re.split(r"[。！？!?；;\n]", str(reply or "")):
        if "我" not in sentence:
            continue
        for token in tokens:
            if token in sentence:
                return True
    return False


def leak_tokens(user_text, reply):
    """返回命中的那些词，方便打日志定位。"""
    body = str(user_text or "")
    hit = []
    for sentence in re.split(r"[。！？!?；;\n]", str(reply or "")):
        if "我" not in sentence:
            continue
        for token in _tokens(body):
            if token in sentence and token not in hit:
                hit.append(token)
    if hit:
        return hit
    pain_parts = _body_parts(body)
    event_hit = bool(_EVENT.search(body))
    for sentence in re.split(r"[。！？!?；;\n]", str(reply or "")):
        if not _pain_sentence(sentence, pain_parts, event_hit):
            continue
        for part in sorted(pain_parts):
            if part in sentence and part not in hit:
                hit.append(part)
        if event_hit and not hit:
            match = _EVENT.search(body)
            if match:
                hit.append(match.group(1))
    return hit
