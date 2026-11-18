# -*- coding: utf-8 -*-
"""地点物理法则（2026-11-16）：日记/消息只能描述“当前地点里真的有”的东西。

线上穿帮：日程 13:41-15:41「在医院排队看诊」，小日记却写“倒在床上、茶几上外卖、
翻个身没人拉被角”。根因是跨时段素材（上一小时在家的小日记、全天聊天/账单）被
原样喂进医院时段的提示词，模型就把家里的场景搬了过来。

这里把“地点 → 允许/禁止词”固化成纯数据 + 纯函数，日记生成前注入提示词，
生成后再按同一张表校验；命中禁止词先重写，仍命中就降级成地点兜底句，
绝不把幻觉放行。本模块不 import 插件引擎，方便被 fact_pool / schedule_engine /
diary_engine / tests 共用。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("amo")

#: 配置开关（_conf_schema.json 只加这一个键）
CONFIG_KEY = "location_rules_enabled"

#: 对外暴露的地点类别（顺序即 resolve 的匹配顺序：越具体越前）
CATEGORIES = (
    "hospital",
    "school",
    "station",
    "restaurant",
    "hotel",
    "mall",
    "office",
    "scenic",
    "home",
    "road",
    "unknown",
)

#: 每个类别：allowed/forbidden/prompt_clause/fallback_line + exceptions（可选）
#: 说明：forbidden 里写「床」是为了抓“躺床/倒在床上”；医院里合法的“病床”
#: 由 exceptions 放行，避免把“在病床上输液”误判成幻觉。
LOCATION_RULES = {
    "home": {
        "label": "家",
        "allowed": ("床", "被窝", "沙发", "茶几", "厨房", "电视", "衣柜", "洗澡",
                    "做饭", "卧室", "客厅", "阳台", "外卖", "枕头", "被子", "拖鞋",
                    "洗衣机", "热水"),
        "forbidden": ("叫号", "工位", "试衣间", "消毒水", "病房", "候诊", "挂号",
                      "输液", "护士站", "办公室", "会议室"),
        "exceptions": (),
        "prompt_clause": (
            "你当前所在的地点是【{place}】。只能描述【{place}】里有的东西"
            "（允许：{allowed}）。绝对不能提及：{forbidden}。"
        ),
        "fallback_line": "（在家里窝了一天，什么也没干成，就听着窗外的动静发呆。）",
    },
    "hospital": {
        "label": "医院",
        "allowed": ("排队", "挂号", "塑料椅", "叫号", "消毒水", "走廊", "药",
                    "候诊", "诊室", "病历", "缴费", "输液", "护士", "医生",
                    "体温计", "口罩", "病床", "化验单", "发烧", "咳嗽"),
        "forbidden": ("被窝", "床", "躺床", "茶几", "沙发", "厨房", "做饭", "洗澡",
                      "衣柜", "电视", "床单", "枕头", "卧室", "被角"),
        "exceptions": ("病床", "诊床", "输液床", "检查床"),
        "prompt_clause": (
            "你当前所在的地点是【{place}】。只能描述【{place}】里有的东西"
            "（允许：{allowed}）。绝对不能提及：{forbidden}。"
        ),
        "fallback_line": "（在医院排了一下午队，烧得晕乎乎的，连坐都坐不踏实。）",
    },
    "office": {
        "label": "公司",
        "allowed": ("工位", "电脑", "键盘", "会议", "文件", "打印机", "咖啡",
                    "办公桌", "加班", "邮件", "屏幕", "周报", "会议室", "同事"),
        "forbidden": ("被窝", "床", "洗澡", "做饭", "厨房", "被角"),
        "exceptions": (),
        "prompt_clause": (
            "你当前所在的地点是【{place}】。只能描述【{place}】里有的东西"
            "（允许：{allowed}）。绝对不能提及：{forbidden}。"
        ),
        "fallback_line": "（在公司工位坐了一天，眼睛都花了，只想赶紧下班。）",
    },
    "mall": {
        "label": "商场",
        "allowed": ("橱窗", "专柜", "电梯", "试衣间", "购物袋", "货架", "收银台",
                    "奶茶", "逛街", "扶梯", "店铺", "折扣", "导购"),
        "forbidden": ("被窝", "床", "工位", "厨房", "做饭", "洗澡", "被角"),
        "exceptions": (),
        "prompt_clause": (
            "你当前所在的地点是【{place}】。只能描述【{place}】里有的东西"
            "（允许：{allowed}）。绝对不能提及：{forbidden}。"
        ),
        "fallback_line": "（在商场里逛了一圈，腿都酸了，最后什么也没舍得买。）",
    },
    "scenic": {
        "label": "景区",
        "allowed": ("风景", "拍照", "山", "水", "游客", "门票", "缆车", "步道",
                    "太阳", "风", "石阶", "湖", "树", "观景台"),
        "forbidden": ("工位", "被窝", "床", "厨房", "办公室", "消毒水", "叫号",
                      "试衣间", "会议室", "被角"),
        "exceptions": (),
        "prompt_clause": (
            "你当前所在的地点是【{place}】。只能描述【{place}】里有的东西"
            "（允许：{allowed}）。绝对不能提及：{forbidden}。"
        ),
        "fallback_line": "（在景区走了一天，累得腿都抬不起来，风景倒是真的好看。）",
    },
    "hotel": {
        "label": "酒店",
        "allowed": ("房卡", "大床", "浴室", "窗景", "前台", "行李", "枕头",
                    "被子", "房间", "热水", "拖鞋"),
        "forbidden": ("工位", "家里沙发", "办公室", "公司", "厨房", "做饭", "会议室"),
        "exceptions": (),
        "prompt_clause": (
            "你当前所在的地点是【{place}】。只能描述【{place}】里有的东西"
            "（允许：{allowed}）。绝对不能提及：{forbidden}。"
        ),
        "fallback_line": "（在酒店房间里歇着，哪儿也没去，就听着空调声发呆。）",
    },
    "road": {
        "label": "路上",
        "allowed": ("地铁", "公交", "车厢", "车窗", "堵车", "扶手", "座位",
                    "司机", "红绿灯", "导航", "打车", "站台", "行李"),
        "forbidden": ("被窝", "床", "工位", "厨房", "办公室", "沙发", "洗澡",
                      "做饭", "被角"),
        "exceptions": (),
        "prompt_clause": (
            "你当前所在的地点是【{place}】。只能描述【{place}】里有的东西"
            "（允许：{allowed}）。绝对不能提及：{forbidden}。"
        ),
        "fallback_line": "（在路上折腾了半天，人挤人，站得脚都麻了。）",
    },
    "restaurant": {
        "label": "餐厅",
        "allowed": ("菜单", "点菜", "服务员", "餐具", "餐桌", "菜", "饭", "饮料",
                    "排队", "买单", "筷子", "碗", "热气"),
        "forbidden": ("被窝", "床", "工位", "厨房", "办公室", "洗澡", "被角"),
        "exceptions": (),
        "prompt_clause": (
            "你当前所在的地点是【{place}】。只能描述【{place}】里有的东西"
            "（允许：{allowed}）。绝对不能提及：{forbidden}。"
        ),
        "fallback_line": "（在餐厅坐了一会儿，吃了点热的，胃里总算舒服些。）",
    },
    "station": {
        "label": "车站",
        "allowed": ("候车", "站台", "列车", "检票", "行李", "人群", "广播",
                    "座位", "车次", "安检", "地铁", "公交"),
        "forbidden": ("被窝", "床", "工位", "厨房", "办公室", "洗澡", "做饭",
                      "被角"),
        "exceptions": (),
        "prompt_clause": (
            "你当前所在的地点是【{place}】。只能描述【{place}】里有的东西"
            "（允许：{allowed}）。绝对不能提及：{forbidden}。"
        ),
        "fallback_line": "（在车站等车，人挤人，广播一遍一遍地响。）",
    },
    "school": {
        "label": "学校",
        "allowed": ("教室", "课桌", "黑板", "老师", "同学", "课本", "图书馆",
                    "操场", "考试", "笔记", "走廊", "课间"),
        "forbidden": ("被窝", "床", "工位", "厨房", "办公室", "消毒水", "叫号",
                      "被角"),
        "exceptions": (),
        "prompt_clause": (
            "你当前所在的地点是【{place}】。只能描述【{place}】里有的东西"
            "（允许：{allowed}）。绝对不能提及：{forbidden}。"
        ),
        "fallback_line": "（在学校待了一整天，课表排得满满的，脑子都木了。）",
    },
    "unknown": {
        "label": "未知地点",
        "allowed": ("走路", "坐着", "发呆", "看手机", "喝水", "等", "忙", "门口"),
        "forbidden": ("叫号", "消毒水", "工位", "试衣间", "被窝", "病床"),
        "exceptions": (),
        "prompt_clause": (
            "你当前所在的地点不明确。只能写中性的日常动作，"
            "绝对不能出现其他地点专属词：{forbidden}。"
        ),
        "fallback_line": "（今天有点记不清了，只记得忙忙叨叨的，也没顾上别的。）",
    },
}

#: 整天日记的地点校验：这些词各自只属于一个地点，当天没去过就拦。
#: 只收“强专属”名词，避免把“坐地铁去医院”这种真实通勤误删。
DAY_EXCLUSIVE_WORDS = {
    "工位": "office", "办公室": "office", "会议室": "office", "写字楼": "office",
    "打卡": "office",
    "叫号": "hospital", "消毒水": "hospital", "病床": "hospital", "挂号": "hospital",
    "输液": "hospital", "诊室": "hospital", "候诊": "hospital", "病历": "hospital",
    "被窝": "home", "沙发": "home", "茶几": "home", "厨房": "home", "卧室": "home",
    "衣柜": "home", "阳台": "home", "床单": "home", "枕头": "home",
    "试衣间": "mall", "专柜": "mall", "橱窗": "mall", "货架": "mall", "收银台": "mall",
    "房卡": "hotel", "大床": "hotel",
    "菜单": "restaurant", "服务员": "restaurant", "餐桌": "restaurant", "点菜": "restaurant",
    "站台": "station", "候车厅": "station", "检票口": "station",
    "教室": "school", "黑板": "school", "课桌": "school", "讲台": "school",
    "门票": "scenic", "缆车": "scenic", "观景台": "scenic",
}

#: 地点词 → 类别（顺序从最具体到最泛；「地铁站」必须先于「地铁」）
PLACE_RULES = (
    (("医院", "诊所", "卫生院", "急诊", "病房", "门诊", "输液室"), "hospital"),
    (("学校", "教室", "图书馆", "校园", "大学", "教学楼", "操场", "宿舍楼"), "school"),
    (("地铁站", "公交站", "车站", "火车站", "机场", "候车厅", "站台", "高铁站"),
     "station"),
    (("餐厅", "饭馆", "饭店", "食堂", "咖啡店", "火锅店", "小吃店", "面馆"),
     "restaurant"),
    (("酒店", "宾馆", "民宿", "旅馆", "客栈"), "hotel"),
    (("商场", "购物中心", "步行街", "专柜", "商店", "超市", "卖场"), "mall"),
    (("写字楼", "公司", "办公室", "工位", "单位", "会议室", "办公楼"), "office"),
    (("景区", "景点", "古镇", "海边", "山上", "游乐园", "乐园", "公园", "动物园",
      "植物园", "博物馆"), "scenic"),
    (("家", "家里", "出租屋", "卧室", "客厅", "沙发", "床", "厨房", "阳台",
      "被窝", "宿舍", "住处", "公寓"), "home"),
    (("路上", "地铁", "公交", "大巴", "车厢", "车上", "出租车", "网约车", "高速",
      "街", "路边", "外面", "马路"), "road"),
)

#: photo_rules 的 zone 别名（相册/日程可能直接给英文 zone）
ZONE_ALIASES = {
    "home": "home",
    "hospital": "hospital",
    "health": "hospital",
    "office": "office",
    "work": "office",
    "mall": "mall",
    "shopping": "mall",
    "scenic": "scenic",
    "playground": "scenic",
    "hotel": "hotel",
    "road": "road",
    "street": "road",
    "transit": "road",
    "restaurant": "restaurant",
    "station": "station",
    "school": "school",
    "unknown": "unknown",
    "default": "unknown",
    "gym": "unknown",
}

def _enabled_from_plugin(plugin):
    """读配置开关；没有插件的纯函数调用默认开。"""
    cfg = getattr(plugin, "cfg_bool", None)
    if not callable(cfg):
        return True
    try:
        return bool(cfg(CONFIG_KEY, True))
    except Exception as exc:  # 配置读不到不能把日记打挂
        logger.debug("[DIARY_GUARD] 读 %s 失败，按开启处理: %r", CONFIG_KEY, exc)
        return True


def enabled(target=True):
    """接受插件对象或 bool；插件对象走 cfg_bool，bool 原样返回。"""
    if isinstance(target, bool):
        return target
    return _enabled_from_plugin(target)


def _category_of(text):
    value = str(text or "").strip()
    if not value:
        return ""
    for words, category in PLACE_RULES:
        if any(word in value for word in words):
            return category
    return ""


def resolve(place=None, zone=None):
    """地点/zone → 类别；地点词优先，未知地点返回 unknown。"""
    category = _category_of(place)
    if category:
        return category
    zone_text = str(zone or "").strip().lower()
    if zone_text:
        if zone_text in LOCATION_RULES:
            return zone_text
        if zone_text in ZONE_ALIASES:
            return ZONE_ALIASES[zone_text]
    return "unknown"


def rule_for(place=None, zone=None):
    """取地点规则 dict（永远有 unknown 兜底）。"""
    return LOCATION_RULES.get(resolve(place, zone)) or LOCATION_RULES["unknown"]


def _label_of(rule, place=None):
    text = str(place or "").strip()
    return text or str(rule.get("label") or "未知地点")


def prompt_clause(place=None, zone=None, enabled_flag=True):
    """给模型的地点绝对约束句；开关关闭时返回空串。"""
    if not enabled_flag:
        return ""
    rule = rule_for(place, zone)
    label = _label_of(rule, place)
    allowed = "、".join(rule.get("allowed") or ())
    forbidden = "、".join(rule.get("forbidden") or ())
    template = str(rule.get("prompt_clause") or "")
    try:
        return template.format(place=label, allowed=allowed, forbidden=forbidden)
    except (KeyError, IndexError, ValueError) as exc:
        logger.debug("[DIARY_GUARD] prompt_clause 模板异常: %r", exc)
        return "你当前所在的地点是【%s】。只能描述这里有的东西。" % label


def fallback_line(place=None, zone=None):
    """两次仍冲突时的地点兜底句（不抛错、不放行幻觉）。"""
    return str(rule_for(place, zone).get("fallback_line") or "（今天有点累，没顾上记太多。）")


def _clean_places(places):
    cleaned = []
    for place in places or ():
        text = str(place or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def day_prompt_clause(places=None, enabled_flag=True):
    """长日记（整天）的地点清单句：不把最后地点当成“当前地点”。"""
    if not enabled_flag:
        return ""
    cleaned = _clean_places(places)
    if not cleaned:
        return ("今天的地点记不清了。只写中性的日常动作，不要编造没去过的地点。")
    return (
        "今天去过：%s。日记里写到地点时只能写这些真实去过的地方；"
        "没去过的地点专属细节（工位、试衣间、酒店房卡等）不能写。"
        % "、".join(cleaned)
    )


def day_check(text, places=None, enabled_flag=True):
    """整天日记只拦“当天任何地点都不符”的强专属词（如没去公司却写工位）。"""
    if not enabled_flag:
        return []
    value = str(text or "")
    cleaned = _clean_places(places)
    if not value.strip() or not cleaned:
        return []
    categories = {resolve(place) for place in cleaned}
    if not categories or categories <= {"unknown"}:
        return []
    hits = []
    for word, category in DAY_EXCLUSIVE_WORDS.items():
        if category in categories:
            continue
        if word in value and word not in hits:
            hits.append(word)
    return hits


def strip_day_violations(text, places=None, enabled_flag=True):
    """整天日记的确定性兜底：删掉含“没去过地点专属词”的句子。"""
    if not enabled_flag:
        return str(text or "")
    if not day_check(text, places, enabled_flag):
        return str(text or "")
    kept = []
    for sentence in _sentences(text):
        if not sentence.strip():
            continue
        if day_check(sentence, places, enabled_flag):
            continue
        kept.append(sentence)
    return "".join(kept).strip()


def day_fallback_line():
    """整天日记被删空时的兜底。"""
    return "（今天想记的太多，反而不知道从哪儿写起，先这样吧。）"


def _match_forbidden(text, rule):
    """返回命中的 forbidden 词；合法组合（如医院病床）由 exceptions 放行。"""
    value = str(text or "")
    if not value:
        return []
    exceptions = tuple(rule.get("exceptions") or ())
    hits = []
    for word in rule.get("forbidden") or ():
        token = str(word or "")
        if not token:
            continue
        start = 0
        while True:
            index = value.find(token, start)
            if index < 0:
                break
            window = value[max(0, index - 2):index + len(token) + 2]
            if any(exc in window for exc in exceptions):
                start = index + 1
                continue
            if token not in hits:
                hits.append(token)
            break
    return hits


def check(text, place=None, zone=None, enabled_flag=True):
    """事后校验：返回命中的 forbidden 词（空列表=通过）。"""
    if not enabled_flag:
        return []
    value = str(text or "")
    if not value.strip():
        return []
    if not str(place or "").strip() and not str(zone or "").strip():
        return []
    return _match_forbidden(value, rule_for(place, zone))


def _sentences(text):
    return re.split(r"(?<=[。！？!?\n])", str(text or ""))


def strip_violations(text, place=None, zone=None, enabled_flag=True):
    """确定性兜底：删掉含禁止词的句子；全删光返回空串。"""
    if not enabled_flag:
        return str(text or "")
    if not check(text, place, zone, enabled_flag):
        return str(text or "")
    kept = []
    for sentence in _sentences(text):
        if not sentence.strip():
            continue
        if check(sentence, place, zone, enabled_flag):
            continue
        kept.append(sentence)
    return "".join(kept).strip()


def check_detail(text, place=None, zone=None, enabled_flag=True):
    """给日志/测试用的结构化结果。"""
    category = resolve(place, zone)
    violations = check(text, place, zone, enabled_flag)
    return {
        "category": category,
        "place": str(place or ""),
        "zone": str(zone or ""),
        "violations": violations,
        "ok": not violations,
    }


async def guard_generation(text, place=None, zone=None, regenerate=None, retries=2,
                           enabled_flag=True, tag="DIARY"):
    """生成后校验 + 最多 retries 次重写 + 地点兜底（绝不抛错）。

    regenerate(hint) 必须是 async 可调用：拿到“你刚才写了不属于 X 的东西”的
    强化提示后重新生成一段。若没有 regenerate，直接按确定性兜底处理。
    """
    result = {
        "text": str(text or ""),
        "violations": [],
        "retry": 0,
        "fallback": False,
        "category": resolve(place, zone),
    }
    if not enabled_flag:
        return result
    label = str(place or "").strip() or str(rule_for(place, zone).get("label") or "未知地点")
    violations = check(result["text"], place, zone, enabled_flag)
    result["violations"] = violations
    if not violations:
        return result
    logger.info("[DIARY_GUARD] place=%s violations=%s retry=0", label, "、".join(violations))
    attempts = max(0, int(retries or 0))
    for attempt in range(1, attempts + 1):
        if regenerate is None:
            break
        hint = (
            "你刚才写了不属于【%s】的东西：%s。只重写本段，"
            "只保留【%s】里真的会出现的细节，不能把别处的场景搬过来。"
            % (label, "、".join(violations), label)
        )
        try:
            fresh = await regenerate(hint)
        except Exception as exc:  # 重写失败不能把已经生成的内容清空
            logger.warning("[DIARY_GUARD] place=%s regenerate 失败: %r", label, exc)
            break
        fresh = str(fresh or "").strip()
        if not fresh:
            logger.info("[DIARY_GUARD] place=%s violations=%s retry=%d empty",
                        label, "、".join(violations), attempt)
            continue
        result["text"] = fresh
        violations = check(fresh, place, zone, enabled_flag)
        result["violations"] = violations
        result["retry"] = attempt
        logger.info("[DIARY_GUARD] place=%s violations=%s retry=%d",
                    label, "、".join(violations) or "none", attempt)
        if not violations:
            return result
    result["text"] = fallback_line(place, zone)
    result["fallback"] = True
    logger.info("[DIARY_GUARD] place=%s violations=%s retry=%d fallback=1",
                label, "、".join(result["violations"]) or "none", result["retry"])
    return result


__all__ = [
    "CATEGORIES",
    "CONFIG_KEY",
    "LOCATION_RULES",
    "DAY_EXCLUSIVE_WORDS",
    "check",
    "check_detail",
    "day_check",
    "day_fallback_line",
    "day_prompt_clause",
    "enabled",
    "fallback_line",
    "guard_generation",
    "prompt_clause",
    "resolve",
    "rule_for",
    "strip_day_violations",
    "strip_violations",
]
