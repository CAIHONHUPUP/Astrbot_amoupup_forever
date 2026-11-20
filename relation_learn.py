# -*- coding: utf-8 -*-
"""从聊天里认关系：他说「小明是我同事」，就记一条关系边。

只做**显式陈述**的抽取，不做推理：
- 必须出现「X 是我…」「我和 X 是…」这种句式；
- 关系词必须在白名单里，归一成面板用的那几类；
- 说话人自己、机器人、以及「你/他/她」这种指代一律不算。

解析是纯函数，方便单测；写入由主程序负责。
"""

from __future__ import annotations

import re

# 面板/数据插件用的关系分类
RELATION_CATEGORIES = ("家人", "恋人", "朋友", "同事", "同学", "熟人", "陌生人", "仇人")

# 关系词 -> 分类（长的放前面，避免「男朋友」被「朋友」先吃掉）
RELATION_WORDS = (
    ("男朋友", "恋人"), ("女朋友", "恋人"), ("对象", "恋人"), ("老公", "恋人"),
    ("老婆", "恋人"), ("男友", "恋人"), ("女友", "恋人"), ("未婚夫", "恋人"),
    ("未婚妻", "恋人"), ("爱人", "恋人"), ("恋人", "恋人"),
    ("爸爸", "家人"), ("妈妈", "家人"), ("哥哥", "家人"), ("姐姐", "家人"),
    ("弟弟", "家人"), ("妹妹", "家人"), ("儿子", "家人"), ("女儿", "家人"),
    ("爷爷", "家人"), ("奶奶", "家人"), ("外公", "家人"), ("外婆", "家人"),
    ("爸", "家人"), ("妈", "家人"), ("哥", "家人"), ("姐", "家人"),
    ("弟", "家人"), ("妹", "家人"), ("爹", "家人"), ("娘", "家人"),
    ("亲戚", "家人"), ("家人", "家人"),
    ("同事", "同事"), ("领导", "同事"), ("老板", "同事"), ("上司", "同事"),
    ("同学", "同学"), ("学长", "同学"), ("学姐", "同学"), ("室友", "同学"),
    ("闺蜜", "朋友"), ("兄弟", "朋友"), ("好友", "朋友"), ("朋友", "朋友"),
    ("熟人", "熟人"),
    ("仇人", "仇人"), ("敌人", "仇人"),
)

_REL = "|".join(word for word, _ in RELATION_WORDS)
_NAME = r"[\u4e00-\u9fffA-Za-z0-9_·]{1,12}"

# 小明是我同事 / 小明是我大学同学（只取「是」后的关系词）
_PATTERNS = (
    re.compile(r"(?P<who>" + _NAME + r")(?:是我|就是我的?|也是我的?)(?P<rel>" + _REL + r")"),
    re.compile(r"(?:和|跟)(?P<who>" + _NAME + r")(?:是|算是)(?P<rel>" + _REL + r")"),
)

# 这些「谁」不是真人，不能记
_BAD_WHO = {
    "我", "你", "他", "她", "它", "咱", "咱们", "我们", "你们", "他们", "她们",
    "这", "那", "这个", "那个", "谁", "人家", "自己", "大家",
}

# 这些词出现在「谁」里，说明抽错了（把动词当人名）
_BAD_CHARS = ("是", "的", "了", "吗", "呢", "吧", "啊", "把", "被", "和", "跟")


def normalize_relation(word):
    word = str(word or "").strip()
    for name, category in RELATION_WORDS:
        if word == name:
            return category
    for name, category in RELATION_WORDS:
        if name in word:
            return category
    return ""


def parse_relations(text):
    """返回 [(谁, 关系分类, 原文片段), ...]，只取明确陈述。"""
    body = str(text or "").strip()
    if not body:
        return []
    found = []
    seen = set()
    for pattern in _PATTERNS:
        for match in pattern.finditer(body):
            who = str(match.group("who") or "").strip()
            if not who or who in _BAD_WHO:
                continue
            if who[0] in "我你他她它":
                continue
            if any(char in who for char in _BAD_CHARS):
                continue
            if len(who) < 2 and not who.isascii():
                # 单字人名（「张」）多半是切错了
                continue
            relation = normalize_relation(match.group("rel"))
            if not relation:
                continue
            key = (who, relation)
            if key in seen:
                continue
            seen.add(key)
            start = max(0, match.start() - 4)
            end = min(len(body), match.end() + 4)
            found.append((who, relation, body[start:end].strip()))
    return found
