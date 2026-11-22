# -*- coding: utf-8 -*-
"""回复事实守卫（2026-11-17）：「不矛盾」档。

她聊天里的事实性说法不能和当前日程/状态打架，但**不要求逐条对应**：
日程没写的小细节（吃了什么、说了哪句话、心情如何）一律自由；只在下面五类
「硬事实」冲突时改写：

1. place     地点组冲突（上班说在家、在家说在公司、医院说在家……）；
2. activity  活动类别冲突（休息日说在上班、上班说在睡觉、没旅游说在旅游/酒店）；
3. meal      非饭点（前后 1 小时都没有 meal 块）却说「正在吃/刚吃完」；
4. health    健康状态正常却说发烧/病着，或生病却说「我好了/退烧了」；
5. companion 没在一起（together/date session）却说「他在我旁边/刚抱我」。

输入 guard(text, ctx)，返回 (rewritten_text, violations)；每条 violation 是
{"rule", "sentence", "replacement", "reason"}（规则 / 原句 / 改成什么 / 依据）。

ctx 字段（都可缺省，缺省=不知道就不拦）：

    activity   dict：当前日程块 place/zone/category/activity（可带 start/end）
    health     dict：status + sick（对齐 state.health_info()）
    date       dict 或 bool：together / active（约会状态）
    trip       dict 或 bool：active / phase / hotel（旅游/酒店状态）
    nearby     list：附近日程块，每项含 category + start/end（HH:MM）
               或 start_ts/end_ts；用来判断「前后 1 小时有没有 meal」
    today_chat bool：今天聊过没有（只影响陪伴兜底话术）
    now        datetime / 时间戳；缺省=当前本地时间

地点组不另造一套：直接复用 place_guard 的 match_location /
allowed_groups / TARGET_WORD / spoken_place，只补几个它没收录的口语词。
本模块不 import 插件引擎，纯规则、可单测。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import datetime as _dt
import re

try:  # 包模式优先，按文件直接加载时退回绝对导入
    from .clock import now as _clock_now
    from .place_guard import TARGET_WORD, allowed_groups, match_location, spoken_place
    from .photo_scene import zone_of
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from clock import now as _clock_now
        from place_guard import TARGET_WORD, allowed_groups, match_location, spoken_place
        from photo_scene import zone_of

#: place_guard.GROUPS 没收录、但聊天里很常见的口语地点（只补，不改原表）
_EXTRA_WORDS = {
    "楼下": "home",
    "楼上": "home",
    "门口": "home",
    "阳台上": "home",
}

#: place_guard.ALLOWED_GROUPS 没收的 zone（医院/学校/健身房/交通）在这里补上，
#: 只影响本守卫，不改 place_guard 自己的表。
_EXTRA_ALLOWED = {
    "hospital": frozenset({"hospital"}),
    "school": frozenset({"school"}),
    "gym": frozenset({"gym"}),
    "street": frozenset({"street", "transit"}),
    # zone=transit 与 road 等价：地铁/公交/车里都算在路上
    "transit": frozenset({"transit", "street"}),
}

#: place_guard.TARGET_WORD 没收的 zone 改口词（只影响本守卫）
_EXTRA_TARGET = {"transit": "路上"}

#: 这些词说明说的是以后/愿望，不是「此刻在哪」
_FUTURE = (
    "明天", "后天", "下次", "待会", "待会儿", "等会", "等会儿", "以后", "周末",
    "明年", "想", "打算", "准备", "要不要", "要是", "如果", "改天", "回头",
    "下班后", "下了班",
)

#: 在家才会有的身体动作；地点被改到公司/医院时整句收成模糊说法
_HOME_VERBS = ("躺", "睡", "窝", "赖", "瘫", "宅")

#: 地点词后面的方位/指示后缀：改口要连它一起换，不能只换地点词（否则「在家上」）
_LOCALIZER_SUFFIXES = ("里面", "上面", "这边", "那边", "附近", "旁边", "里头",
                       "楼下", "楼上", "门口", "里", "上", "中", "内")
#: 能跟在地点后面、换地点时还保留的方位词（「我在家这边躲雨」→「我在咖啡店这边躲雨」）。
#: 「上/里」这类不是指示方位，换地点时必须连旧地点一起删掉，否则会拼出「在家上改周报」。
_ORIENT_SUFFIXES = ("这边", "那边", "附近", "旁边")

#: 出现在地点词前面说明是「从某处离开/过来」，不是此刻所在
_ORIGIN_PREFIXES = ("从", "离开")

#: 「从 X 回来/出来/过来」说的是已经离开那儿，不跟此刻地点冲突
_MOTION_DONE = ("回来", "回家", "出来", "过来", "回去")

#: 后续是时间/事情名词时，不硬把地点往里塞（宁可不说地点，不出坏句）
_NOUN_TAILS = ("饭点", "时间", "事情", "东西", "天气", "现在", "今天", "今晚",
               "晚上", "早上", "中午", "下午", "周末", "一会儿", "等会儿")

#: 数量词开头（「工位上一堆事」→「家里一堆事」）
_QUANTIFIER_TAILS = ("一堆", "两堆", "几堆", "好多", "很多", "不少", "点", "些")

#: 能直接跟在地点后面的活动/状态开头（其余按「名词尾巴」处理，删地点短语）
_ACTIVITY_CHARS = frozenset(
    "改写忙做吃看刷躺睡窝歇待坐站等玩聊跟和收看听学念想发收整弄洗煮炒追逛摸加开"
    "上下来回到说讲喝穿脱打扫收拾准备处理琢磨修画写陪带继续练干")

#: 地点改写后的二次校验（task-41）：这些是商场/试衣专属词。地点已经改成
#: 家/公司/餐厅等之后，词还留着就说明这句本质上仍是「在商场」的分享——只换地点
#: 会拼出「在看衣服服」「站在外面扶梯上」这种语义/语法都不成立的句子。
_MALL_ONLY_WORDS = ("商场", "逛街", "试衣", "试穿", "试了", "试完", "购物袋",
                    "扶梯", "专柜", "橱窗", "货架", "收银", "导购",
                    "买衣服", "挑衣服")
#: 这些 zone 里商场/试衣词本来就成立，不做二次校验。
_MALL_ZONES = frozenset({"mall", "street"})

#: 整句都在讲「路上/快到了」：按「已经到达」改整句，别把地点词塞进半句
_TRANSIT_PROGRESS = ("快到了", "马上到", "就到了", "就快到了", "快到家", "堵着",
                     "堵车", "赶路", "在路上", "地铁上", "车上", "进站", "出站")

_SICK_STATUSES = (
    "轻微不适", "感冒", "低烧", "发烧", "重感冒", "不舒服", "生病", "拉肚子",
    "肠胃炎", "咳嗽",
)

#: 「在睡觉」类说法（只和 work/commute 冲突；rest 可放行）
_SLEEP_CLAIMS = (
    "正在睡觉", "在睡午觉", "在午睡", "在补觉", "在睡觉", "睡觉中", "睡着了", "赖床",
)

#: 「在上班」类说法（和 sleep/meal/rest/health 冲突）
_WORK_CLAIMS = (
    "正在上班", "在上班", "上班中", "正在加班", "在加班", "加班中",
    "在开会", "上班", "加班", "开会",
)

#: 进行式的「此刻在工作」口语写法：睡觉/休息/生病/吃饭/逛街时说这个也肯定是矛盾
_WORK_PRESENT_CLAIMS = (
    "还在改方案", "在改方案", "改方案",
    "还在赶方案", "在赶方案", "赶方案", "对着电脑改",
)
#: 近过去的工作说法（刚开完会）：work/commute 下同样按此刻改；sleep/health 下
#: 可以解释成刚发生，保留原文（不把时间语义改坏）
_WORK_EDGE_CLAIMS = (
    "刚开完会", "开完会",
)

#: 「在做饭」类说法：办公室/餐厅/路上说这个同样是活动矛盾
_COOK_CLAIMS = (
    "正在做饭", "在做饭", "做饭中", "在煮饭", "煮饭", "在炒菜", "炒菜",
    "在做菜", "做菜", "在弄饭", "做饭",
)

#: 裸的「上班/加班/开会」在这些字后面说的是以后/否定，不算此刻
_WORK_SKIP_PREFIX = ("下", "去", "回", "赶", "来", "想", "要", "不", "没", "别")

#: 在吃饭的说法 → 非饭点的降级话术（具体吃什么不管）
_EAT_RE = re.compile(
    r"(刚吃完饭|刚吃完|刚吃了|刚吃|正在吃|正吃着|在吃饭|在吃呢|吃过了|吃了点"
    r"|在吃(?![货惊力醋香])|扒了两口(?:饭|菜)?)"
    r"(?:(?:[两几]口|一点|点)[^，。！？\n]{0,6})?",
)
_EAT_REPL = {
    "刚吃完饭": "等会儿弄点吃的",
    "刚吃完": "等会儿弄点吃的",
    "刚吃了": "等会儿弄点吃的",
    "刚吃": "等会儿弄点吃的",
    "正在吃": "待会儿吃",
    "正吃着": "待会儿吃",
    "在吃饭": "待会儿吃饭",
    "在吃呢": "待会儿吃",
    "吃过了": "等会儿弄点吃的",
    "吃了点": "等会儿弄点吃的",
    "在吃": "待会儿吃",
}

_SICK_WORDS = ("还在发烧", "又发烧", "还在烧", "又烧", "发烧", "低烧", "还烧着",
               "烧着", "还烧", "病着", "生病", "感冒", "拉肚子", "肠胃炎",
               "退烧", "刚退", "又该烧", "怕再烧", "怕着凉", "着凉", "发冷",
               "37度", "三十七度", "病还没好", "还没好利索", "好利索", "利索点")
#: 关心/追问句的关键词：出现这些说明在问“他”的病情，不是她的健康声明。
_CARE_QUESTION_RE = re.compile(r"(多久|怎么|吃药|看医生|要不要|疼不疼|好点没|严不严重)")
_RECOVER_WORDS = ("我好了", "已经好了", "病好了", "退烧了", "痊愈了", "全好了",
                  "不难受了", "还没好", "没好")

#: 生病时这些是「症状」不是「痊愈宣言」：不烧了 / 没发烧 / 不再病着。
#: 先判否定，避免「我已经好了，不烧了」被当成两句痊愈、各改一遍。
_SICK_NEGATION_RE = re.compile(r"(?:没|没有|不|不再)\s*(?:再)?\s*(?:发烧|烧|病)")

#: 小句里冲突词前面的否定前缀（「我没发烧」「不难受」）
_NEG_PREFIX_RE = re.compile(r"(?:没|没有|不|不再|别)\s*(?:再)?\s*$")

_TOGETHER_CLAIMS = (
    "他刚抱我", "他抱着我", "他搂着我", "刚抱我", "抱着我", "他抱我", "搂着我",
    "他躺我旁边", "躺我旁边", "坐在我旁边", "他在我旁边", "在我旁边", "他在旁边",
    "刚亲我", "他亲我", "亲了我", "亲了我一下", "抱了我一下", "他刚亲了我",
    "他刚抱了我", "他靠在我肩上", "靠在我肩上", "坐我边上", "坐在我边上",
    "手还搭在我腰上", "手搭在我腰上", "他手还搭在我腰上", "他就坐我边上",
)

_SENTENCE_RE = re.compile(r"([。！!？?；;\n]+)")
_CLAUSE_RE = re.compile(r"([，,、]+)")


def _now(ctx):
    value = (ctx or {}).get("now")
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, (int, float)):
        return _dt.datetime.fromtimestamp(value)
    return _clock_now()


def _match_location(clause):
    """place_guard 的最长地点词 + 本地补充词。"""
    best = match_location(clause)
    for word, group in _EXTRA_WORDS.items():
        if word in clause and (best is None or len(word) > len(best[1])):
            best = (group, word)
    return best


def _self_claim(clause, word):
    """这句话的主语是不是她（不是「他/你/大家」）。"""
    index = clause.find(word)
    if index < 0:
        return False
    head = clause[:index]
    me = head.rfind("我")
    you = head.rfind("你")
    him = head.rfind("他")
    if you >= 0 and you > me:
        return False
    if him >= 0 and him > me:
        return False
    if me >= 0:
        return True
    stripped = head.strip()
    if not stripped:
        return True
    # 「站在商场扶梯上」「坐在商场长椅上」这类省略主语的进行式，主语默认是她；
    # 前面出现「你/他」时上面已经返回 False，不会误改别人（task-41 C3）。
    return bool(re.match(
        r"^(?:刚|正|还|才|也|一直|现在|就|站在|坐在|躺在|靠在|趴在|蹲在|待在|走在|在)+$",
        stripped))


def _residual_place_words(text):
    """地点冲突改写后还残留的商场/试衣专属词（二次校验用）。"""
    body = str(text or "")
    return [word for word in _MALL_ONLY_WORDS if word in body]


def _claimish(clause, word):
    return _self_claim(clause, word) and not any(mark in clause for mark in _FUTURE)


def _clause_hit(rule, clause, new, reason):
    if new == clause:
        return []
    return [{"rule": rule, "sentence": clause, "replacement": new, "reason": reason}]


# ---------------------------------------------------------------------------
# 1) 地点组
# ---------------------------------------------------------------------------


def _unique(items):
    out = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _split_place_clause(clause, word):
    """地点词 -> (前缀, 去掉「上/里/这边」等后缀后的后续, 被去掉的方位后缀)。

    方位后缀要单独带出来：换地点时可以丢掉它，但「我在家这边躲雨」这种
    「地点+方位词+动作」结构，丢掉方位词会把「在哪」的答案一起删掉，
    所以由 _place_orient_variants 保留（task-36）。
    """
    index = clause.find(word)
    lead = clause[:index]
    tail = clause[index + len(word):]
    local = ""
    for suffix in _LOCALIZER_SUFFIXES:
        if tail.startswith(suffix):
            local = suffix
            tail = tail[len(suffix):]
            break
    return lead, tail.lstrip(), local


def _place_target(activity, zone):
    return (TARGET_WORD.get(zone) or _EXTRA_TARGET.get(zone)
            or spoken_place(activity) or "在外面")


def _place_empty_tail_variants(subject, target):
    """只说了地点（没有后续动作）：整句收成「在X呢」一类的完整自然句。"""
    first = ("%s在%s呢" % (subject, target)) if subject else ("在%s呢" % target)
    here = first[:-1] + "这边呢" if first.endswith("呢") else first + "这边"
    return _unique((
        first,
        here,
        "这会儿在%s呢" % target,
        "我就在%s呢" % target,
        "人就在%s呢" % target,
        "还在%s呢" % target,
        "已经在%s了" % target,
    ))


def _place_activity_variants(target, tail, subject=""):
    """「在工位上改周报」→「在家改周报」：删掉方位词，只留活动。"""
    first = (subject + "在%s%s" % (target, tail)) if subject else ("在%s%s" % (target, tail))
    here = (subject + "在%s这边%s" % (target, tail)) if subject else ("在%s这边%s" % (target, tail))
    return _unique((
        first,
        here,
        "这会儿在%s%s" % (target, tail),
        "我就在%s%s" % (target, tail),
        "人就在%s%s" % (target, tail),
        "还在%s%s" % (target, tail),
        "已经在%s%s" % (target, tail),
    ))


def _place_orient_variants(target, local, tail, subject=""):
    """「我在<错地点>这边<动作>」→「我在<正确地点>这边<动作>」：只换地点，保留方位词。"""
    who = subject or ""
    here = "%s%s" % (local, tail)
    first = ("%s在%s%s" % (who, target, here)) if who else ("在%s%s" % (target, here))
    return _unique((
        first,
        ("%s就在%s%s" % (who, target, here)) if who else ("就在%s%s" % (target, here)),
        "这会儿在%s%s" % (target, here),
        ("%s还在%s%s" % (who, target, here)) if who else ("还在%s%s" % (target, here)),
        "人就在%s%s" % (target, here),
    ))


def _place_quantifier_variants(target, tail):
    """「工位上一堆事」→「家里一堆事」。"""
    first = "%s里%s" % (target, tail)
    return _unique((
        first,
        "在%s这边%s" % (target, tail),
        "在%s里%s" % (target, tail),
        "这会儿%s里%s" % (target, tail),
        "我在%s里%s" % (target, tail),
        "就在%s里%s" % (target, tail),
        "现在%s里%s" % (target, tail),
    ))


def _place_drop_variants(lead, tail, target):
    """结构不适合直接塞地点：删掉地点短语，只保留原意（宁可不说地点）。"""
    base = (lead + tail).strip("，,、 ")
    if not base:
        return _place_empty_tail_variants("", target)
    here = ("这边" + tail) if tail else ""
    rest = ("这会儿" + tail) if tail else ""
    mine = ("我这儿" + tail) if tail else ""
    ending = (tail + "呢") if tail and tail[-1] not in "呢啊呀了吧哦嘛" else ""
    now = ("现在" + tail) if tail else ""
    return _unique((base, here, rest, mine, ending, now))


def _looks_like_activity(tail):
    text = str(tail or "")
    if not text:
        return False
    if text.startswith("一起"):
        return True
    if text.startswith(_NOUN_TAILS):
        return False
    return text[0] in _ACTIVITY_CHARS


def _whole_transit_sentence(sentence, ctx):
    """整句都在讲「路上 / 快到了」：直接换成「已经到家了」一类的自然整句。

    只在句子里出现和日程冲突的地点时触发；不能保证通顺就不动。
    """
    activity = ctx.get("activity") or {}
    found = _match_location(sentence)
    if not found:
        return None
    group, word = found
    zone = zone_of(activity) or ""
    allowed = allowed_groups(activity) or _EXTRA_ALLOWED.get(zone)
    if not allowed or group in allowed:
        return None
    if not any(marker in sentence for marker in _TRANSIT_PROGRESS):
        return None
    if not _claimish(sentence, word):
        return None
    target = _place_target(activity, zone)
    if target == "家":
        variants = _unique(("已经到家了", "我刚到家", "在家呢", "这会儿在家呢",
                            "刚进家门", "已经在家了"))
    else:
        variants = _unique(("已经在%s了" % target, "我就在%s" % target,
                            "这会儿在%s" % target, "人就在%s" % target,
                            "刚到%s" % target, "在%s呢" % target))
    reason = "日程此刻在%s（zone=%s，整句是行程/到达说法）" % (
        str(activity.get("place") or target), zone or "?")
    return variants[0], variants, reason


def _place_rule(clause, ctx):
    """地点冲突：按原句结构整句换掉地点短语，绝不把地点词直接塞进「在…上」。"""
    activity = ctx.get("activity") or {}
    found = _match_location(clause)
    if not found:
        return clause, []
    group, word = found
    zone = zone_of(activity) or ""
    allowed = allowed_groups(activity) or _EXTRA_ALLOWED.get(zone)
    if not allowed or group in allowed:
        return clause, []
    if not _claimish(clause, word):
        return clause, []
    lead, tail, local = _split_place_clause(clause, word)
    target = _place_target(activity, zone)
    reason = "日程此刻在%s（zone=%s，category=%s）" % (
        str(activity.get("place") or target), zone or "?",
        str(activity.get("category") or "?"))
    # 1) 「从 X 回来/出来」：说的是已经离开那儿，不是此刻在哪
    if lead.endswith(_ORIGIN_PREFIXES):
        if any(marker in tail for marker in _MOTION_DONE):
            return clause, []
        clean = lead
        for prefix in _ORIGIN_PREFIXES:
            if clean.endswith(prefix):
                clean = clean[:-len(prefix)]
                break
        base = (clean + tail).strip("，,、 ")
        if not base or base == clause:
            return clause, []
        short = tail.strip()
        variants = _unique((
            base,
            ("我" + short) if short else "",
            (clean + "先" + short) if short else "",
            (clean + "就" + short) if short else "",
            (clean + "，先" + short) if short else "",
            ("我这就" + short) if short else "",
            ("我先" + short) if short else "",
        ))
        hits = _clause_hit("place", clause, base, reason)
        if hits:
            hits[0]["variants"] = variants
        return base, hits
    # 2) 「到了 X / 回到 X」
    if lead.endswith(("到了", "回到")):
        if tail:
            return clause, []
        core = lead[:-2]
        base = "%s到%s了" % (core, target)
        variants = _unique((base, "%s已经到%s了" % (core, target),
                            "%s已经到%s啦" % (core, target),
                            "%s在%s呢" % (core, target), "已经到%s了" % target,
                            "在%s呢" % target))
        hits = _clause_hit("place", clause, base, reason)
        if hits:
            hits[0]["variants"] = variants
        return base, hits
    # 3) 身体动作（躺/睡/窝）只在家成立：改到别处时收成「人在公司呢」
    if target != "家" and any(tail.startswith(verb) for verb in _HOME_VERBS):
        tail = ""
    # 4) 地点词前面不是「在」：不硬拼语法，删掉地点短语
    if lead and not lead.endswith("在"):
        variants = _place_drop_variants(lead, tail, target)
    elif not tail or tail[0] in "呢啊呀了吧哦嘛了的":
        variants = _place_empty_tail_variants("我" if "我" in lead else "", target)
    elif tail.startswith(_QUANTIFIER_TAILS) and not tail.startswith("一起"):
        variants = _place_quantifier_variants(target, tail)
    elif local in _ORIENT_SUFFIXES and tail:
        # 「我在家这边躲雨」→「我在咖啡店这边躲雨」：换地点、保留方位词，
        # 不能删成「我在躲雨」把「在哪」的答案删掉（task-36）。
        variants = _place_orient_variants(target, local, tail, "我" if "我" in lead else "")
    elif _looks_like_activity(tail):
        variants = _place_activity_variants(target, tail, "我" if "我" in lead else "")
    else:
        variants = _place_drop_variants(lead, tail, target)
    base = variants[0]
    # 二次校验（task-41）：地点词换掉了，但「试衣服/购物袋/扶梯」这类地点专属词
    # 还留在句子里时，只换地点会拼出「在看衣服服」「站在外面扶梯上」这种坏句。
    # 这时收成只讲地点的自然短句，宁可不说活动细节，也不出坏句。
    if zone not in _MALL_ZONES and _residual_place_words(base):
        safe = _place_empty_tail_variants("我" if "我" in clause else "", target)[0]
        variants = _unique((safe,) + tuple(variants))
        base = safe
    hits = _clause_hit("place", clause, base, reason)
    if hits:
        hits[0]["variants"] = variants
    return base, hits


# ---------------------------------------------------------------------------
# 2) 活动类别 / 旅游
# ---------------------------------------------------------------------------


def _activity_rule(clause, ctx):
    activity = ctx.get("activity") or {}
    category = str(activity.get("category") or "")
    zone = zone_of(activity) or ""
    if category in ("work", "commute"):
        target = "在上班" if category == "work" else "在路上"
        words = list(_SLEEP_CLAIMS) + list(_COOK_CLAIMS)
        if category == "commute":
            # 在路上说「在开会/在加班/刚开完会/还在改方案」同样是活动矛盾
            words += (list(_WORK_CLAIMS) + list(_WORK_EDGE_CLAIMS)
                      + list(_WORK_PRESENT_CLAIMS))
        claims = [(word, target) for word in words]
        reason = "日程此刻是%s（category=%s），不能说在睡觉/做饭%s" % (
            str(activity.get("activity") or category), category,
            "" if category == "work" else "/开会/加班",
        )
    elif category in ("sleep", "meal", "rest", "health"):
        target = {"sleep": "在睡觉", "meal": "在吃饭", "rest": "在休息"}.get(category, "")
        if not target:
            target = "在医院" if zone == "hospital" else "在家歇着"
        words = list(_WORK_CLAIMS) + list(_WORK_PRESENT_CLAIMS)
        if category == "meal":
            # 吃饭时不会「还在开会/改方案」；但睡觉/休息/生病时「刚开完会」很可能是
            # 刚发生的事（近过去），硬改成「刚在睡觉」会把时间语义改坏，保留原文。
            words += list(_WORK_EDGE_CLAIMS)
        if zone != "home":
            # 在餐厅/公司说「在做饭」也是活动矛盾；家=meal 本来就可能真在做饭
            words += list(_COOK_CLAIMS)
        claims = [(word, target) for word in words]
        reason = "日程此刻是%s（category=%s），不能说在上班/做饭" % (
            str(activity.get("activity") or category), category,
        )
    elif category in ("play", "gym", "date", "shop", "life"):
        doing = str(activity.get("activity") or "").strip()
        doing = doing.lstrip("正在")
        target = "在" + (doing or "忙")
        claims = [(word, target) for word in (
            *_WORK_CLAIMS, *_WORK_EDGE_CLAIMS, *_WORK_PRESENT_CLAIMS, *_COOK_CLAIMS,
            "在逛街", "逛街", "在健身", "健身",
            "在买菜", "买菜", "在看电影", "看电影")]
        reason = "日程此刻是%s（category=%s），不能换成别的活动" % (doing or category, category)
    else:
        return _travel_rule(clause, ctx)
    for word, target in claims:
        if word not in clause or not _claimish(clause, word):
            continue
        index = clause.find(word)
        if word in ("上班", "加班", "开会") and index > 0:
            if clause[index - 1] in _WORK_SKIP_PREFIX:
                continue
        rest = clause[index + len(word):]
        if rest.startswith(("附近", "楼下", "门口", "旁边", "族", "路", "时间")):
            continue
        # 「在加班」整体换成「在休息」；裸的「加班」只换成「休息」，别多出一个「在」。
        # 「刚开完会」「还在改方案」这类带时间前缀的写法，把前缀一起保留：
        # 刚/还 + 「在X」→「刚在X」「还在X」，别拼出「刚上班」「刚路上」这类残句。
        if target.startswith("在") and word[0] in "刚还":
            repl = word[0] + target
        else:
            repl = target if word.startswith(("在", "正")) else (
                target[1:] if target.startswith("在") else target)
        new = clause.replace(word, repl, 1)
        return new, _clause_hit("activity", clause, new, reason)
    return _travel_rule(clause, ctx)


def _travel_rule(clause, ctx):
    """没旅游/没酒店却说在旅游、在酒店。"""
    trip = ctx.get("trip") or {}
    if isinstance(trip, bool):
        trip = {"active": trip}
    phase = str(trip.get("phase") or "")
    active = bool(trip.get("active")) or phase in ("outbound", "traveling", "returning")
    hotel = bool(trip.get("hotel")) or bool(ctx.get("hotel")) or phase in ("outbound", "traveling")
    zone = zone_of(ctx.get("activity") or {}) or ""
    outdoor = "在家待着呢" if zone == "home" else "在外面逛逛"
    for word in ("在旅游", "正在旅游", "出来旅游", "在旅行", "正在旅行"):
        if word in clause and not active and _claimish(clause, word):
            new = clause.replace(word, outdoor, 1)
            return new, _clause_hit("activity", clause, new, "没有进行中的 trip（trip.active=false）")
    for word in ("住酒店", "在酒店", "住民宿", "在民宿", "住宾馆", "在宾馆"):
        if word in clause and not hotel and _claimish(clause, word):
            new = clause.replace(word, outdoor, 1)
            return new, _clause_hit("activity", clause, new, "没有酒店/民宿状态（trip.hotel=false）")
    return clause, []


# ---------------------------------------------------------------------------
# 3) 吃饭
# ---------------------------------------------------------------------------


def _hhmm(value, base):
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty time")
    hour, _, minute = text.partition(":")
    return (base.replace(hour=0, minute=0, second=0, microsecond=0)
            + _dt.timedelta(hours=int(hour), minutes=int(minute or 0)))


def _span(item, base):
    start = item.get("start_ts")
    end = item.get("end_ts")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return (_dt.datetime.fromtimestamp(start), _dt.datetime.fromtimestamp(end))
    try:
        start_at = _hhmm(item.get("start"), base)
        end_at = _hhmm(item.get("end"), base)
    except (TypeError, ValueError):
        return None
    if end_at <= start_at:
        end_at += _dt.timedelta(days=1)
    return start_at, end_at


def _meal_allowed(activity, ctx, now):
    if str(activity.get("category") or "") == "meal":
        return True
    if ctx.get("meal_within_hour"):
        return True
    low = now - _dt.timedelta(hours=1)
    high = now + _dt.timedelta(hours=1)
    for item in ctx.get("nearby") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("category") or "") != "meal":
            continue
        span = _span(item, now)
        if span and span[0] <= high and span[1] >= low:
            return True
    return False


def _meal_rule(clause, ctx):
    match = _EAT_RE.search(clause)
    if not match:
        return clause, []
    activity = ctx.get("activity") or {}
    if _meal_allowed(activity, ctx, _now(ctx)):
        return clause, []
    if not _self_claim(clause, match.group(0)):
        return clause, []
    if re.search(r"(没|不|别|没有)\s*$", clause[:match.start()]):
        return clause, []
    word = match.group(0)
    repl = _EAT_REPL.get(word) or "等会儿弄点吃的"
    new = clause[:match.start()] + repl + clause[match.end():]
    reason = "此刻不是饭点（category=%s），前后一小时也没有 meal 块" % (
        str(activity.get("category") or "?"),
    )
    return new, _clause_hit("meal", clause, new, reason)


# ---------------------------------------------------------------------------
# 4) 健康
# ---------------------------------------------------------------------------


def _is_sick(health):
    health = health or {}
    sick = health.get("sick")
    if sick is not None:
        return bool(sick)
    return str(health.get("status") or "健康") in _SICK_STATUSES


def _health_rule(clause, ctx):
    health = ctx.get("health") or {}
    if _is_sick(health):
        if _SICK_NEGATION_RE.search(clause):
            return clause, []
        for word in _RECOVER_WORDS:
            if word in clause and _self_claim(clause, word):
                new = clause.replace(word, "还是有点不舒服", 1)
                reason = "健康状态=%s（sick=true），不能说已经好了" % (
                    str(health.get("status") or "生病"),
                )
                return new, _clause_hit("health", clause, new, reason)
        return clause, []
    # 1b-2：问句/关心句是在问“他”的病情，不是她的健康声明，不能整句改写。
    # 线上 18:22：「咳嗽多久了…药吃了吗」被当成她生病，换成「就是有点累？」。
    if _CARE_QUESTION_RE.search(clause) or clause.rstrip().endswith(("？", "?")):
        return clause, []
    for word in _SICK_WORDS:
        if word not in clause:
            continue
        index = clause.find(word)
        if _NEG_PREFIX_RE.search(clause[:index]):
            continue
        if not _self_claim(clause, word) and any(
                mark in clause for mark in ("他", "你", "她", "朋友", "同事", "妈", "爸")):
            continue
        new = "已经好了，就是还有点累"
        reason = "健康状态=%s（sick=false），不能说还在生病" % (
            str(health.get("status") or "健康"),
        )
        return new, _clause_hit("health", clause, new, reason)
    return clause, []


# ---------------------------------------------------------------------------
# 5) 约会/陪伴
# ---------------------------------------------------------------------------


def _together_ok(ctx):
    if ctx.get("together") or ctx.get("dating"):
        return True
    date = ctx.get("date")
    if isinstance(date, dict):
        return bool(date.get("together") or date.get("active"))
    return bool(date)


def _companion_rule(clause, ctx):
    if _together_ok(ctx):
        return clause, []
    for word in _TOGETHER_CLAIMS:
        if word not in clause or not _self_claim(clause, word):
            continue
        index = clause.find(word)
        before = clause[:index]
        after = clause[index + len(word):]
        if "他" in word:
            subject = "他"
        elif before.rstrip().endswith("他"):
            subject = ""
        elif clause.lstrip().startswith("他"):
            subject = "他"
        else:
            subject = ""
        when = "刚才" if ctx.get("today_chat", True) else ""
        if "旁边" in word:
            repl = subject + "人不在我这边，" + when + "在手机上聊了几句"
            after = ""
        else:
            repl = subject + when + "在手机上聊了几句"
            if after.startswith(("了", "呢")):
                after = after[1:]
        new = before + repl + after
        reason = "约会状态：现在没在一起（together=false，也没有进行中的 date session）"
        return new, _clause_hit("companion", clause, new, reason)
    return clause, []


# ---------------------------------------------------------------------------
# 6) 精确金额/等待时长（task-41）
# ---------------------------------------------------------------------------


def _amounts_rule(clause, ctx):
    """聊天出站报账数字大概化：卡里还有4072 / 等了十五分钟 -> 四千多 / 好一会儿。

    唯一实现在 amo_memory_ledger.approximate_amounts（记忆注入早就在用），
    这里把同一条类别正则接到聊天事实守卫上——聊天出站以前没接，精确余额/等待
    分钟会原样发出去（holdout D3/D4）。
    """
    body = str(clause or "")
    if not body:
        return clause, []
    try:
        try:
            from .amo_memory_ledger import approximate_amounts
        except ImportError:
            from amo_memory_ledger import approximate_amounts
        fixed = approximate_amounts(body)
    except Exception:  # cleanup: 账本模块缺失时不做数字改写，不影响其他规则
        return clause, []
    if fixed and fixed != body:
        return fixed, _clause_hit(
            "amounts", clause, fixed, "精确金额/等待时长按口语大概化")
    return clause, []


#: 规则表（顺序即执行顺序；反向验证测试会临时删掉某一项）
RULES = (
    ("place", _place_rule),
    ("activity", _activity_rule),
    ("meal", _meal_rule),
    ("health", _health_rule),
    ("companion", _companion_rule),
    ("amounts", _amounts_rule),
)


def guard(text, ctx=None):
    """返回 (rewritten_text, violations)；违规只改冲突的那一小句。

    同一句里同一条规则只改一次：我已经好了，不烧了 不会把两个小句都替换成
    「还是有点不舒服」。
    """
    value = str(text or "")
    context = dict(ctx or {})
    if not value:
        return value, []
    sentences = _SENTENCE_RE.split(value)
    violations = []
    for s_index in range(0, len(sentences), 2):
        sentence = sentences[s_index]
        if not sentence.strip():
            continue
        # 整句是「路上/快到了」这种行程说法时，整句换成到达句，
        # 避免「还在家快到了」这种把地点词塞进半句的改写。
        whole = _whole_transit_sentence(sentence, context)
        if whole is not None:
            new, variants, whole_reason = whole
            violations.append({"rule": "place", "sentence": sentence,
                               "replacement": new, "reason": whole_reason,
                               "variants": variants})
            sentences[s_index] = new
            continue
        clauses = _CLAUSE_RE.split(sentence)
        applied = set()
        #: 本句开始前的违规条数：用来判断这句有没有发生地点改写（二次校验触发条件）。
        violation_start = len(violations)
        for c_index in range(0, len(clauses), 2):
            clause = clauses[c_index]
            if not clause.strip():
                continue
            for rule, handler in RULES:
                if rule in applied:
                    continue
                before = clause
                clause, hits = handler(clause, context)
                for hit in hits:
                    record = {
                        "rule": rule,
                        "sentence": before,
                        "replacement": hit["replacement"],
                        "reason": hit["reason"],
                    }
                    if hit.get("variants"):
                        record["variants"] = list(hit["variants"])
                    violations.append(record)
                if hits and rule == "health" and _is_sick(context.get("health")):
                    # 生病时同一句只提示一次；健康时要把所有「还病着」的小句都清掉
                    applied.add(rule)
            clauses[c_index] = clause
        joined = "".join(clauses)
        # 某条冲突小句被整句删掉时，别留下「，。」这种空标点
        joined = re.sub(r"([，,、；;])\1+", r"\1", joined)
        # 地点改写的整句二次校验（task-41）：小句里的地点词换掉了，但另外的小句
        # 还写着「拎着购物袋」「刚试完衣服」这类商场专属细节时，整句收成只讲当下
        # 地点的自然短句——既不出「站在外面扶梯上」，也不留下指向商场的语义。
        activity = context.get("activity") or {}
        zone = zone_of(activity) or ""
        place_touched = any(
            item.get("rule") == "place" for item in violations[violation_start:])
        if place_touched and zone not in _MALL_ZONES:
            residual = _residual_place_words(joined)
            if residual:
                target = _place_target(activity, zone)
                safe = _place_empty_tail_variants(
                    "我" if "我" in joined else "", target)[0]
                violations.append({
                    "rule": "place", "sentence": joined, "replacement": safe,
                    "reason": "地点改写后仍残留地点专属词：%s" % "、".join(residual),
                })
                joined = safe
        sentences[s_index] = re.sub(r"[，,、；;]+$", "", joined)
    return "".join(sentences), violations


def has_violation(text, ctx=None, rule=""):
    """便捷判断：有没有（某条规则的）冲突。"""
    _fixed, violations = guard(text, ctx)
    if not rule:
        return bool(violations)
    return any(item.get("rule") == rule for item in violations)
