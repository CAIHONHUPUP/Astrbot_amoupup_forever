# -*- coding: utf-8 -*-
"""独立一致性检查器（不 import 生产守卫的规则逻辑，只借地点词表思路自建）。

输入：锁定的快照（地点/zone/动作/同伴）+ 回复文本 + 探针类型。
输出：问题列表，code ∈ {degenerate, stale_scene, companion_conflict, place_conflict,
action_conflict, meal_conflict, health_conflict, probe_unanswered, meta_leak}。
"""
from __future__ import annotations

import re

PLACE_GROUPS = (
    ("home", ("家里", "家中", "客厅", "卧室", "沙发", "厨房", "阳台", "被窝", "床上",
              "卫生间", "浴室", "小区", "家")),
    ("office", ("写字楼", "办公室", "会议室", "工位", "公司", "单位")),
    ("mall", ("步行街", "专柜", "逛街", "商场", "超市", "购物", "试衣间")),
    ("restaurant", ("咖啡店", "咖啡馆", "奶茶店", "小吃街", "餐厅", "饭店", "饭馆", "食堂")),
    ("road", ("车上", "车里", "打车", "出租车", "网约车", "地铁", "公交", "车厢")),
    ("street", ("街上", "街边", "街头", "路边", "路上", "外面")),
    ("scenic", ("游乐园", "动物园", "植物园", "风景区", "爬山", "海边", "沙滩",
                "景区", "景点", "公园", "山上", "古镇")),
    ("hotel", ("酒店", "宾馆", "民宿", "旅馆", "客栈")),
    ("hospital", ("医院", "诊所")),
    ("gym", ("健身房",)),
    ("school", ("图书馆", "教室", "学校", "宿舍", "操场")),
)

_WORD_GROUP = {}
for _group, _words in PLACE_GROUPS:
    for _word in _words:
        _WORD_GROUP.setdefault(_word, _group)
_ALL_WORDS = sorted(_WORD_GROUP, key=len, reverse=True)

ZONE_ALLOWED = {
    "home": {"home"},
    "office": {"office"},
    "mall": {"mall"},
    "restaurant": {"restaurant"},
    "scenic": {"scenic", "playground"},
    "hotel": {"hotel"},
    "hospital": {"hospital"},
    "gym": {"gym"},
    "school": {"school"},
    "road": {"road", "street"},
    "street": {"street", "road"},
    "station": {"road"},
    "playground": {"scenic"},
}

PAST_MARKERS = ("昨天", "昨晚", "前天", "以前", "之前", "上周", "上次", "那次", "那时候",
                "当年", "大学", "去年", "前阵子", "前两天")
FUTURE_MARKERS = ("明天", "后天", "下次", "待会", "等会", "以后", "周末", "改天", "回头",
                  "想", "打算", "准备", "要不要", "要是", "如果", "下班后", "下了班",
                  "明年", "一会儿")

CLAUSE_SPLIT = re.compile(r"[，,。！？!?；;、\n]+")
PARTICLES = set("嗯啊哦呀呢吧哈嘿唉诶喔噢")

WORK_RE = re.compile(r"(?:我|俺)?(?:还|正|刚)?在(?:公司|单位|工位|办公室|会议室)?"
                     r"(?:上班|加班|开会)|(?:我|俺)(?:今天|刚才)?(?:上班|加班|开会)"
                     r"|刚开完会|还在改方案|在赶方案|对着电脑改")
SLEEP_RE = re.compile(r"(?:我|俺)?在(?:睡觉|睡午觉|午睡|补觉|赖床)|躺床上|赖床|刚睡醒|睡着")
COOK_RE = re.compile(r"(?:在|正在)?(?:做|煮|炒|炖|弄)(?:饭|菜|面)|洗碗|洗衣服|拖地|收拾屋子|晾衣服|煮面")
SHOP_RE = re.compile(r"在?(?:逛街|逛商场|逛超市|试衣服)")
TRAVEL_RE = re.compile(r"在(?:旅游|景区|爬山|古镇|海边)|在酒店")
TRANSIT_RE = re.compile(r"在(?:地铁上|公交上|车上|打车|地铁|公交)")

TOGETHER_RES = (
    re.compile(r"他在?我旁边|在我旁边|他?搂着我|他?抱着我|他刚抱我|抱了我"),
    re.compile(r"他刚亲我|他亲了我|亲了我一下|他靠在我肩|靠在我肩"),
    re.compile(r"他坐我边上|坐我边上|手(?:还)?搭在我(?:腰|肩)上"),
    re.compile(r"我们在一起|他在我身边|他陪我"),
)
SICK_WORDS = ("发烧", "低烧", "感冒", "生病", "不舒服", "咳嗽", "拉肚子", "头晕", "嗓子疼")

EAT_RE = re.compile(r"(?:刚|正在|刚刚|才|在)吃(?:完|了|过|着)?|吃过了|刚扒了|在吃")
FOOD_WORDS = ("吃", "饭", "面", "外卖", "粥", "面包", "零食", "奶茶", "咖啡", "饿", "饱")

VERB_WORDS = ("工作", "上班", "加班", "开会", "吃饭", "吃", "做", "煮", "洗", "收拾", "刷",
              "看", "追剧", "睡", "躺", "窝", "逛", "走", "坐", "等", "聊", "玩", "写",
              "改", "健身", "跑", "散", "爬", "休息", "忙", "敷", "护肤", "化妆", "拍",
              "喝", "练", "洗", "宅", "瘫")

PERSON_WORDS = ("他", "老公", "同事", "朋友", "一个人", "自己", "没人", "大家", "妈", "爸",
                "姐", "哥", "同学", "室友", "闺蜜", "领导")

TIME_WORDS = ("点", "分钟", "一会儿", "马上", "晚点", "快了", "下班", "天黑", "今晚",
              "回", "到家", "在家", "回去", "六点", "七点", "八点", "九点", "十点")

META_WORDS = ("AI", "人工智能", "语言模型", "提示词", "系统指令", "作为一个模型")


def clauses(text):
    return [part for part in CLAUSE_SPLIT.split(str(text or "")) if part.strip()]


def content_length(text):
    body = re.sub(r"[\s，,。！？!?；;、…~～·\"'“”‘’()（）【】\[\]（）]", "", str(text or ""))
    return len([char for char in body if char not in PARTICLES])


def match_place(text):
    value = str(text or "")
    best = ("", "")
    for word in _ALL_WORDS:
        if word in value and len(word) > len(best[1]):
            best = (_WORD_GROUP[word], word)
    return best if best[1] else (None, "")


def is_self_claim(clause):
    clause = str(clause or "")
    if "我" in clause or "俺" in clause:
        return True
    if re.match(r"^\s*(?:还|刚|正|就|才)?在", clause):
        return True
    if re.search(r"(?:到家|进门|回到|回到)", clause):
        return True
    return False


def _marked(clause):
    if any(marker in clause for marker in PAST_MARKERS):
        return True
    if any(marker in clause for marker in FUTURE_MARKERS):
        return True
    return False


def check_degenerate(text):
    body = str(text or "").strip()
    if not body:
        return [{"code": "degenerate", "subtype": "empty", "detail": "回复为空"}]
    if body in ("嗯", "哦", "好", "行", "对", "是", "嗯嗯", "好呀"):
        return []
    if content_length(body) == 0:
        return [{"code": "degenerate", "subtype": "fragment",
                 "detail": "去掉语气词后没有内容（只剩「%s」）" % body[:8]}]
    return []


def check_place(text, snap):
    zone = str((snap or {}).get("zone") or "")
    allowed = ZONE_ALLOWED.get(zone)
    if not allowed:
        return []
    issues = []
    for clause in clauses(text):
        if _marked(clause) or not is_self_claim(clause):
            continue
        # 「18:00 差不多到家」是预计/未来，不是在说此刻位置
        if ("到家" in clause and re.search(r"(?:\d|点|差不多|晚点|一会儿|等会|就)", clause)
                and not re.search(r"(?:了|刚|已经|正在)", clause)):
            continue
        # 「（你）到家跟我说一声」是在对他说，不是她的位置
        if re.search(r"到家(?:了)?(?:跟|给|告诉|说|发|记得|打)", clause):
            continue
        # 「通勤回家 / 回公司 / 去商场」是目的地/在途，不是此刻所在
        if re.search(r"(?:回|去|往|赶去|赶到)(?:家|公司|商场|餐厅|酒店|医院|景区|学校|机场|健身房)", clause):
            continue
        group, word = match_place(clause)
        if not group or group in allowed:
            continue
        if "从" + word in clause or ("离开" in clause and word in clause):
            continue
        issues.append({"code": "place_conflict", "subtype": group,
                       "detail": "此刻 zone=%s，回复说「%s」" % (zone, clause[:30])})
        break
    return issues


def check_action(text, snap):
    zone = str((snap or {}).get("zone") or "")
    category = str((snap or {}).get("category") or "")
    issues = []
    for clause in clauses(text):
        if _marked(clause):
            continue
        for pattern, subtype, allowed in (
                (WORK_RE, "work", category in ("work", "study") or zone == "office"),
                (SLEEP_RE, "sleep", zone == "home" and category in ("sleep", "rest", "health")),
                (COOK_RE, "cook", zone == "home"),
                (SHOP_RE, "shop", zone == "mall"),
                (TRAVEL_RE, "travel", zone in ("scenic", "hotel")),
                (TRANSIT_RE, "transit", zone in ("road", "street", "station"))):
            match = pattern.search(clause)
            if not match or allowed:
                continue
            if not _self_subject(clause, match.start()):
                continue
            issues.append({"code": "action_conflict", "subtype": subtype,
                           "detail": "category=%s/zone=%s 却说「%s」" % (category, zone, clause[:30])})
            return issues
    return issues


def _self_subject(clause, index):
    """隐含主语也算她本人：句首没有第三人称、且不是「他/你...」在前。"""
    if "我" in clause or "俺" in clause:
        return True
    prefix = clause[:max(0, index)]
    for third in ("他", "你", "她", "朋友", "同事", "妈", "爸", "闺蜜", "他们", "她们"):
        if third in prefix:
            return False
    return True


def check_companion(text, snap, together):
    if together:
        return []
    for clause in clauses(text):
        if _marked(clause):
            continue
        for pattern in TOGETHER_RES:
            match = pattern.search(clause)
            if not match:
                continue
            if "不在" in clause[max(0, match.start() - 3):match.start()]:
                continue
            return [{"code": "companion_conflict", "subtype": match.group(0),
                     "detail": "此刻没在一起，却说「%s」" % clause[:30]}]
    return []


def check_meal(text, snap, meal_now):
    if meal_now:
        return []
    for clause in clauses(text):
        if _marked(clause):
            continue
        if re.search(r"(没|没有|不|还没)\s*(?:吃|饿)", clause):
            continue
        match = EAT_RE.search(clause)
        if not match:
            continue
        if not _self_subject(clause, match.start()):
            continue
        return [{"code": "meal_conflict", "subtype": "eat",
                 "detail": "非饭点却说「%s」" % clause[:30]}]
    return []


def check_health(text, snap):
    health = (snap or {}).get("health") or {}
    if health.get("sick") or str(health.get("status") or "健康") not in ("健康", "疲惫"):
        return []
    for clause in clauses(text):
        if re.search(r"(没|没有|不|别)\s*(?:再)?\s*(?:发烧|烧|病|感冒|咳嗽)", clause):
            continue
        for word in SICK_WORDS:
            if word in clause:
                if not _self_subject(clause, clause.find(word)):
                    continue
                return [{"code": "health_conflict", "subtype": word,
                         "detail": "健康状态却称「%s」" % clause[:30]}]
    return []


def check_stale(text, snap, prev_snap):
    if not prev_snap:
        return []
    prev_zone = str(prev_snap.get("zone") or "")
    zone = str((snap or {}).get("zone") or "")
    prev_place = str(prev_snap.get("place") or "")
    if prev_zone == zone and prev_place == str((snap or {}).get("place") or ""):
        return []
    prev_allowed = set(ZONE_ALLOWED.get(prev_zone) or {prev_zone})
    current_allowed = set(ZONE_ALLOWED.get(zone) or {zone})
    if prev_allowed & current_allowed:
        return []  # 同一场景组（家→家、路上→街上），不算旧场景
    prev_verb = str(prev_snap.get("verb") or "").strip()
    prev_word = ""
    for group, words in PLACE_GROUPS:
        if group in prev_allowed:
            prev_word = words[-1]
            break
    for clause in clauses(text):
        if not is_self_claim(clause):
            continue
        group, word = match_place(clause)
        if group and group in prev_allowed and group not in current_allowed:
            return [{"code": "stale_scene", "subtype": "old_place",
                     "detail": "已从%s切到%s，仍说「%s」" % (prev_zone, zone, clause[:30])}]
        if prev_verb and len(prev_verb) >= 2 and prev_verb in clause:
            return [{"code": "stale_scene", "subtype": "old_action",
                     "detail": "已从%s切到%s，仍说「%s」" % (prev_zone, zone, clause[:30])}]
    return []


def check_probe(text, probe, snap):
    if not probe:
        return []
    body = str(text or "")
    answered = False
    if probe == "where":
        group, _word = match_place(body)
        answered = bool(group) or any(word in body for word in ("外面", "到家", "出门", "秘密", "不告诉", "猜"))
    elif probe == "doing":
        group, _word = match_place(body)
        answered = bool(group) or any(word in body for word in VERB_WORDS) or "忙" in body
    elif probe == "who":
        answered = any(word in body for word in PERSON_WORDS) or "跟你" in body
    elif probe == "eaten":
        answered = any(word in body for word in FOOD_WORDS)
    elif probe == "when_return":
        answered = any(word in body for word in TIME_WORDS) or bool(re.search(r"\d", body))
    if answered:
        return []
    return [{"code": "probe_unanswered", "subtype": probe,
             "detail": "问「%s」，回复没答：%s" % (probe, body[:30])}]


def check_meta(text):
    body = str(text or "")
    for word in META_WORDS:
        if word in body:
            return [{"code": "meta_leak", "subtype": word, "detail": body[:30]}]
    return []


def evaluate(text, turn_ctx):
    """返回 (issues, codes)。issues 里的顺序即严重度优先级。"""
    snap = turn_ctx.get("snap") or {}
    prev_snap = turn_ctx.get("prev_snap")
    issues = []
    issues.extend(check_degenerate(text))
    issues.extend(check_stale(text, snap, prev_snap))
    issues.extend(check_companion(text, snap, bool(turn_ctx.get("together"))))
    issues.extend(check_place(text, snap))
    issues.extend(check_action(text, snap))
    issues.extend(check_meal(text, snap, bool(turn_ctx.get("meal_ok"))))
    issues.extend(check_health(text, snap))
    issues.extend(check_probe(text, turn_ctx.get("probe"), snap))
    issues.extend(check_meta(text))
    codes = []
    for item in issues:
        if item["code"] not in codes:
            codes.append(item["code"])
    return issues, codes
