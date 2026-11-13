# -*- coding: utf-8 -*-
"""「她在哪」不能跟日程打架（2026-11-12 加入）。

症状：日程写着在写字楼上班，他问一句「你在逛街吧？」，她就顺着说「嗯，在逛街呢」；
或者她在别处待过，隔一小时还在说「我在商场」。日程是唯一事实源，这里做两件事：

1. **他先开口试探**：``conflicting_place()`` 判断他这句话是不是在说她在一个和
   日程对不上的地方，是的话由 ``correction_hint()`` 给主脑一条当场纠正的指令
   （“别顺着承认，按日程否认”）；
2. **她嘴上跑偏**：``rewrite_reply()`` 在回复发出去之前，把她第一人称的地点声明
   （“我在商场呢”/“我刚在酒店”）改回日程里的地点，兜住模型没听话的那几次。

地点按「组」判断：家 / 公司 / 商场 / 景区 / 餐厅 / 交通 / 街边 / 酒店 / 学校 / 医院 / 健身房。
日程说在办公室，她（或他）说在商场、在酒店、在家，都算打架；说在公司、在工位就不动。

这里只做纯文本判断，不碰数据库、不调模型，方便单测。
"""
from __future__ import annotations

import re

try:  # 包模式优先，按文件直接加载时退回绝对导入
    from .photo_scene import place_of, zone_of
except ImportError:  # 直接按文件加载时
    from photo_scene import place_of, zone_of

#: 地点词 → 组（长得像「车站/车上」这种按最长匹配优先）
GROUPS = (
    ("home", ("家里", "家中", "客厅", "卧室", "被窝", "沙发", "厨房", "阳台",
              "卫生间", "洗手台", "家门口", "小区", "家")),
    ("office", ("写字楼", "办公室", "会议室", "工位", "公司", "单位")),
    ("mall", ("步行街", "专柜", "逛街", "商场", "超市", "购物")),
    ("scenic", ("游乐园", "动物园", "植物园", "风景区", "爬山", "海边", "沙滩",
                "景区", "景点", "公园", "山上", "古镇", "旅游")),
    ("restaurant", ("咖啡店", "咖啡馆", "奶茶店", "小吃街", "餐厅", "饭店", "饭馆", "食堂")),
    ("transit", ("车站", "地铁", "公交", "车厢", "车上", "车里", "打车", "高铁",
                 "机场", "站台")),
    ("street", ("街上", "街边", "街头", "路边", "路上", "外面")),
    ("hotel", ("酒店", "宾馆", "民宿", "旅馆", "客栈")),
    ("school", ("图书馆", "教室", "学校", "宿舍", "操场")),
    ("hospital", ("医院", "诊所")),
    ("gym", ("健身房",)),
)

#: 此时此刻这个 zone 允许出现的地点组（别的一律算「说错地方」）
ALLOWED_GROUPS = {
    "home": frozenset(("home",)),
    "office": frozenset(("office",)),
    "mall": frozenset(("mall",)),
    "scenic": frozenset(("scenic",)),
    "restaurant": frozenset(("restaurant",)),
    "road": frozenset(("transit", "street")),
    "hotel": frozenset(("hotel",)),
    "station": frozenset(("transit",)),
}

#: 改口时用的口语地点
TARGET_WORD = {
    "home": "家",
    "office": "公司",
    "mall": "商场",
    "scenic": "景区",
    "restaurant": "餐厅",
    "road": "路上",
    "hotel": "酒店",
    "station": "车站",
}

#: 「大家 / 人家 / 老家」里的「家」不是在说她自己
_NOISE = ("大家", "人家", "国家", "专家", "画家", "科学家", "商家", "店家",
          "一家", "老家", "家族", "顾家")

#: 他这句话带这些词，说的是以后/邀约，不是她现在在哪
_SOFT_MARKERS = ("明天", "下次", "周末", "待会", "等会", "以后", "要不要", "去不去",
                 "想不想", "陪我", "带我", "一起去", "打算", "准备", "以后")

_WORD_GROUP: dict[str, str] = {}
for _group, _words in GROUPS:
    for _word in _words:
        _WORD_GROUP.setdefault(_word, _group)
_ALL_WORDS = sorted(_WORD_GROUP, key=len, reverse=True)


def _clauses(text):
    return [part for part in re.split(r"[，,。.！!？?；;、\s]+", str(text or "")) if part]


def _denoise(text):
    value = str(text or "")
    for word in _NOISE:
        value = value.replace(word, "某某")
    return value


def match_location(text):
    """句子里最长的那个地点词 → (组, 词)；没有就 None。"""
    value = _denoise(text)
    best = ("", "")
    for word in _ALL_WORDS:
        if word in value and len(word) > len(best[1]):
            best = (_WORD_GROUP[word], word)
    return best if best[1] else None


def allowed_groups(activity):
    zone = zone_of(activity)
    return ALLOWED_GROUPS.get(zone)


def conflicting_place(text, activity):
    """他这句话是不是说她在一个和日程对不上的地方。

    返回 ``{"said": 他说的地点, "group": 组}``；对不上才返回，正常/说他自己/说以后都返回 None。
    """
    allowed = allowed_groups(activity)
    if not allowed:
        return None
    for clause in _clauses(text):
        found = match_location(clause)
        if not found:
            continue
        group, word = found
        if group in allowed:
            continue
        index = clause.find(word)
        last_you = clause.rfind("你", 0, index)
        if last_you < 0:
            continue  # 没提「你」：说的是他自己
        if "我" in clause[last_you:index]:
            continue  # 「你以为我在逛街」：说的是他自己
        if any(marker in clause for marker in _SOFT_MARKERS):
            continue  # 「你明天去逛街吗」：说的是以后
        return {"said": word, "group": group}
    return None


def spoken_place(activity):
    """日程地点 → 说话时会用的说法（「莞城区的写字楼」→「公司」）。"""
    zone = zone_of(activity)
    if zone in TARGET_WORD:
        return TARGET_WORD[zone]
    place = place_of(activity)
    if "·" in place:
        place = place.split("·")[-1].strip() or place
    place = re.sub(r"^[\u4e00-\u9fa5]{1,6}?(?:区|县|市)的?", "", place).strip()
    return place or "家"


def correction_hint(activity, said):
    """给主脑的一条硬指令：按日程否认，别顺着他改口。"""
    place = spoken_place(activity)
    act = str((activity or {}).get("activity") or "").strip()
    where = "在" + place + (("、" + act) if act else "")
    word = str((said or {}).get("said") or "")
    return (
        "【地点纠正】他这轮说你在「%s」，但日程里你此刻%s——不要顺着他承认；"
        "先按日程自然否认（比如“没有呀，我在%s呢”），再说别的，也不许改口说自己在别处。"
        % (word, where, place)
    )


def rewrite_reply(text, activity):
    """把回复里和日程冲突的第一人称地点声明改回日程地点（兜底，正常不该触发）。"""
    value = str(text or "")
    allowed = allowed_groups(activity)
    if not value or not allowed:
        return value
    target = TARGET_WORD.get(zone_of(activity)) or place_of(activity)
    if not target:
        return value
    for group, words in GROUPS:
        if group in allowed:
            continue
        for word in sorted(words, key=len, reverse=True):
            pattern = re.escape(word)
            # 我在X / 我刚在X / 我还在X / 我到了X
            value = re.sub(
                r"我(?P<mid>(?:刚|正|一直|还|才|也|这边)?)(?:在|到了|回到)(?P<w>" + pattern + r")",
                lambda match: "我" + (match.group("mid") or "") + "在" + target,
                value,
            )
            # 「嗯，在逛街呢」这种省略主语的短回复
            value = re.sub(
                r"(^|[，,。！!？?\s])在(?P<w>" + pattern + r")(?=[呢啊呀了，,。！!？?\s]|$)",
                lambda match: (match.group(1) or "") + "在" + target,
                value,
            )
    return value


# 2026-11-12 新增 zone（医院/景区/街边）也要有“地点纠正”的目标词
TARGET_WORD.update({
    "hospital": "医院",
    "scenic": "景区",
    "street": "街边",
})
