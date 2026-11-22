# -*- coding: utf-8 -*-
"""回复语气（tone）纯内核：从状态/关系/场景挑候选语气，不读库、不碰插件。

域内只允许标准库 + amo_observability（架构棘轮 leaf 规则）；日志走 note()。
根目录 amo_reply_pipeline.py 以 re-export 适配器保留旧名字，identity 不变。
搬自 amo_reply_pipeline.py（2026-11-18，逻辑逐字未改；仅 logger.debug 换成 note）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict

try:
    from ...amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    from amo_observability import note as _note_swallowed

TONE_POOLS = {
    "health": {
        "tired": ("就是有点累", "今天有点乏", "有点提不起精神", "身子有点发沉",
                  "就是有点困", "眼皮有点沉", "想早点歇着", "没什么力气"),
        "sweet": ("就是有点累", "有点想赖着你", "今天有点没力气", "就是有点乏",
                  "想靠着你歇会儿", "身上有点软", "有点提不起劲", "想早点窝着"),
        "default": ("就是有点累", "今天有点乏", "有点没精神", "身子有点沉",
                    "就是有点困", "歇一会儿就好", "眼皮有点沉", "没什么力气"),
    },
    "companion": {
        "close": ("刚才在手机上聊了几句", "这会儿没在一块儿", "刚刚只在微信上说了两句",
                  "这会儿各自忙着呢", "隔着屏幕聊了两句", "刚刚手机上说呢",
                  "这会儿见不着面", "人没在跟前，手机上说了两句"),
        "busy": ("刚才在手机上聊了几句", "这会儿没在一块儿", "刚刚只在微信上说了两句",
                 "这会儿各自忙呢", "隔着屏幕聊了两句", "手机上说呢",
                 "这会儿抽不开身", "人没在跟前，手机上说呢"),
        "default": ("刚才在手机上聊了几句", "这会儿没在一块儿", "刚刚只在微信上说了两句",
                    "这会儿各自忙呢", "隔着屏幕聊了两句", "刚刚手机上说呢",
                    "这会儿见不着面", "人没在跟前，手机上说了两句"),
    },
}
RECOVERY_TONE = ("还是有点不舒服", "身上还是不得劲", "人还没缓过来",
                 "还是有点难受", "还没好利索", "身子还发虚",
                 "还是没什么精神", "头还有点沉")
MEAL_TONE = {
    "等会儿弄点吃的": ("等会儿弄点吃的", "晚点再弄点吃的", "一会儿去弄点吃的",
                       "过会儿再吃", "等下找点吃的", "等会儿煮点东西",
                       "一会儿再弄吃的", "晚点随便弄点"),
    "待会儿吃": ("待会儿吃", "过会儿再吃", "等会儿就吃", "晚点吃",
                 "一会儿再吃", "稍后吃", "晚一点儿吃", "等会儿再吃"),
    "待会儿吃饭": ("待会儿吃饭", "过会儿再吃饭", "等会儿就吃饭", "晚点吃饭",
                   "一会儿再吃饭", "稍后吃饭", "晚一点儿吃饭", "等会儿再吃饭"),
    "待会儿": ("待会儿", "过会儿", "等会儿", "晚点", "一会儿", "稍后",
               "晚一点儿", "等一下"),
    "等会儿": ("等会儿", "待会儿", "过会儿", "晚点", "一会儿", "稍后",
               "晚一点儿", "等一下"),
}
ACTIVITY_TONE = {
    "上班": ("上班", "忙工作", "工位上", "单位", "开会", "做事",
             "处理点事", "忙一会儿"),
    "睡觉": ("睡觉", "补觉", "眯一会儿", "床上躺着", "被窝里", "犯困",
             "睡回笼觉", "躺着歇着"),
    "吃饭": ("吃饭", "吃着", "动筷子", "填肚子", "饭桌上", "扒拉两口",
             "吃两口", "垫垫肚子"),
    "休息": ("休息", "歇着", "缓一缓", "放空", "躺平", "歇一会儿",
             "偷个懒", "眯一下"),
    "家歇着": ("家歇着", "家待着", "家缓一缓", "家里躺着", "家放空", "窝家里",
               "家窝着", "家躺平"),
    "医院": ("医院", "医院排队", "医院候诊", "诊室这边", "医院等着", "医院看诊",
             "医院坐着", "医院走廊"),
    "路上": ("路上", "车上", "路上赶", "赶路", "路上堵着", "路上晃",
             "路上挤着", "路上慢慢走"),
    "在外面逛逛": ("在外面逛逛", "在外面转转", "在外面溜达", "街上逛",
                   "在外面透透气", "在外面走走", "外面晃悠", "街上转转"),
    "家待着呢": ("家待着", "家窝着", "家歇着", "宅家里", "家没出门", "家里待着",
                 "家躺着", "家放空"),
}
PLACE_TONE = {
    "work": ("{p}{t}{s}", "{p}{t}这边{s}", "这会儿在{t}{s}", "我就在{t}{s}",
             "人就在{t}{s}", "还在{t}{s}", "{p}{t}忙着{s}", "{p}{t}待着{s}"),
    "home": ("{p}{t}{s}", "{p}{t}这边{s}", "这会儿在{t}{s}", "我就在{t}{s}",
             "在{t}待着{s}", "还在{t}{s}", "{p}{t}窝着{s}", "{p}{t}歇着{s}"),
    "transit": ("{p}{t}{s}", "{p}{t}这边{s}", "这会儿在{t}{s}", "我就在{t}{s}",
                "已经在{t}{s}", "还在{t}{s}", "人还在{t}{s}", "{p}{t}慢慢走{s}"),
    "default": ("{p}{t}{s}", "{p}{t}这边{s}", "这会儿在{t}{s}", "我就在{t}{s}",
                "人就在{t}{s}", "还在{t}{s}", "在{t}待着{s}", "{p}{t}待着{s}"),
}
_TONE_FALLBACK: Dict[str, str] = {}
def _tone_relation(plugin):
    """关系只读：plugin.relation 或 state.relation；读不到算普通关系。"""
    try:
        relation = str(getattr(plugin, "relation", "") or "")
        if not relation and getattr(plugin, "state", None) is not None:
            relation = str(getattr(plugin.state, "relation", "") or "")
        return relation
    except Exception as exc:  # cleanup: 关系读不到按普通关系
        _note_swallowed("reply.tone._tone_relation", exc)
        return ""
def _tone_mood(plugin):
    """心情只读：state.mood 是 dict（开心/疲惫/烦…），拼成文本做匹配。"""
    try:
        mood = getattr(getattr(plugin, "state", None), "mood", None)
        if isinstance(mood, dict):
            return " ".join("%s%s" % (key, value) for key, value in mood.items())
        return str(mood or "")
    except Exception as exc:  # cleanup: 心情读不到就用默认语气
        _note_swallowed("reply.tone._tone_mood", exc)
        return ""
def _tone_scene(activity):
    """场景：work / home / transit / default（只看只读日程块）。"""
    data = activity if isinstance(activity, dict) else {}
    zone = str(data.get("zone") or "")
    category = str(data.get("category") or "")
    if zone in ("office", "school") or category == "work":
        return "work"
    if zone in ("home", "hotel") or category in ("sleep", "rest", "meal"):
        return "home"
    if zone in ("transit", "road", "station", "street") or category == "commute":
        return "transit"
    return "default"
def _tone_group(rule, plugin, activity, middle):
    """规则 + 关系/心情/场景 -> 语气组名；组不存在时退回 default。"""
    pools = TONE_POOLS.get(rule) or {}
    if rule == "health":
        relation = _tone_relation(plugin)
        mood = _tone_mood(plugin)
        if any(word in relation for word in ("恋人", "老公", "对象", "亲密")):
            return "sweet"
        if any(word in mood for word in ("疲惫", "累", "困", "乏")):
            return "tired"
        return "default"
    if rule == "companion":
        relation = _tone_relation(plugin)
        mood = _tone_mood(plugin)
        if any(word in relation for word in ("恋人", "老公", "对象", "亲密")):
            return "close"
        if any(word in mood for word in ("烦", "累", "忙", "疲惫")):
            return "busy"
        return "default"
    scene = _tone_scene(activity)
    if scene in pools:
        return scene
    if middle and any(token in str(middle) for token in ("家", "睡", "歇", "窝")):
        return "home" if "home" in pools else "default"
    return "default"
def _tone_state(plugin):
    """轮换状态挂在 plugin 上（每个会话/实例独立）；没有可写属性用进程内兜底。"""
    if plugin is None:
        return _TONE_FALLBACK
    state = getattr(plugin, "_fact_tone_state", None)
    if not isinstance(state, dict):
        state = {}
        try:
            setattr(plugin, "_fact_tone_state", state)
        except Exception as exc:  # cleanup: 极简 fake 不能挂属性
            _note_swallowed("reply.tone._tone_state", exc)
            return _TONE_FALLBACK
    return state
def _pick_tone_variant(pool, key, state, seed, rotate_first=False):
    """种子轮换：默认第一次用旧固定句；health 等池可 rotate_first 首次就按种子散开。"""
    variants = [str(item) for item in (pool or ()) if str(item or "").strip()]
    if not variants:
        return ""
    name = str(key)
    record = state.get(name)
    if not isinstance(record, int) or not (0 <= record < len(variants)):
        if rotate_first and len(variants) > 1:
            digest = hashlib.sha1(str(seed or "").encode("utf-8")).hexdigest()[:8]
            index = int(digest, 16) % len(variants)
        else:
            index = 0
    elif record == 0 and len(variants) > 1:
        digest = hashlib.sha1(str(seed or "").encode("utf-8")).hexdigest()[:8]
        index = 1 + int(digest, 16) % (len(variants) - 1)
    else:
        index = (record + 1) % len(variants)
        if index == 0 and len(variants) > 1:
            index = 1
    state[name] = index
    return variants[index]
def _changed_segment(sentence, replacement):
    """原句 -> 改口句的 (公共前缀, 发生改动的中间, 公共后缀)。"""
    left = str(sentence or "")
    right = str(replacement or "")
    if not left or not right or left == right:
        return "", "", ""
    size = 0
    for one, two in zip(left, right):
        if one != two:
            break
        size += 1
    prefix = right[:size]
    tail = 0
    limit = min(len(left) - size, len(right) - size)
    while tail < limit and left[-1 - tail] == right[-1 - tail]:
        tail += 1
    middle = right[size:len(right) - tail] if tail else right[size:]
    suffix = right[len(right) - tail:] if tail else ""
    return prefix, middle, suffix
def _activity_tone(middle):
    """活动改口的候选：键是去掉「在」之后的动作。"""
    text = str(middle or "")
    if text.startswith("在"):
        text = text[1:]
    return list(ACTIVITY_TONE.get(text) or ()), text
def _tone_candidates(rule, prefix, middle, suffix, plugin, activity, variants=None):
    """当前冲突可选的整句候选（5-8 条）；事实目标由模板保证。"""
    group = _tone_group(rule, plugin, activity, middle)
    if rule == "health" and "不舒服" in str(middle):
        return [prefix + item + suffix for item in RECOVERY_TONE]
    if rule == "place":
        scene = _tone_scene(activity)
        pool: Any = PLACE_TONE.get(scene) or PLACE_TONE["default"]
        if not pool:
            return []
        # 守卫已经按原句结构给了整句候选（reply_fact_guard._place_rule）时优先用：
        # 这些候选已经处理过方位词，不会出现「在家上改周报」「家这边快到了」。
        full = []
        for item in variants or ():
            text = str(item or "").strip()
            if text and text not in full:
                full.append(text)
        if len(full) >= 2:
            return full
        out = []
        for template in pool:
            try:
                clause = template.format(p=prefix, t=middle, s=suffix)
            except (KeyError, IndexError, ValueError):
                continue
            if clause not in out:
                out.append(clause)
        return out
    if rule == "activity":
        variants, _core = _activity_tone(middle)
        if not variants:
            return []
        fixed = []
        for item in variants:
            if item.startswith("在") and "在" in prefix:
                item = item[1:]
            if not item.startswith(("在", "正", "刚", "先")):
                item = "在" + item if "在" not in prefix else item
            fixed.append(item)
        return [prefix + item + suffix for item in fixed]
    if rule == "meal":
        key = str(middle)
        variants = MEAL_TONE.get(key) or ()
        if not variants:
            for marker in ("等会儿", "待会儿"):
                if marker in key:
                    variants = MEAL_TONE.get(marker) or ()
                    break
        return [prefix + item + suffix for item in (variants or ())]
    pool = (TONE_POOLS.get(rule) or {}).get(group)
    if not pool:
        pool = (TONE_POOLS.get(rule) or {}).get("default")
    return [prefix + item + suffix for item in (pool or ())]
def _tone_key(rule, middle, plugin, activity):
    """轮换键：同一键的候选共用一个轮换状态。"""
    if rule == "health" and "不舒服" in str(middle):
        return ("health", "recovery")
    if rule == "place":
        return ("place", _tone_scene(activity))
    if rule == "activity":
        return ("activity", _activity_tone(middle)[1])
    if rule == "meal":
        return ("meal", str(middle))
    return (rule, _tone_group(rule, plugin, activity, middle))
def _natural_clause(rule, prefix, middle, suffix, sentence, replacement,
                    plugin, activity, seed, variants=None):
    """一条冲突小句 -> 确定性语气池说法；事实目标（地点/活动）一字不改。"""
    state = _tone_state(plugin)
    candidates = _tone_candidates(rule, prefix, middle, suffix, plugin, activity, variants)
    chosen = _pick_tone_variant(candidates, _tone_key(rule, middle, plugin, activity),
                                state, seed,
                                rotate_first=(rule == "health"
                                              and "不舒服" not in str(middle)))
    return chosen or replacement
def _today_chat(plugin, moment):
    """今天是否已经聊过：读进程内最近回复时间；重启后首轮保守返回 False。"""
    stamps = getattr(plugin, "_last_reply_at", None)
    if not isinstance(stamps, dict):
        return False
    try:
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        end = moment.timestamp()
    except (AttributeError, OSError, ValueError):
        return False
    for value in stamps.values():
        try:
            stamp = float(value)
        except (TypeError, ValueError):
            continue
        if start <= stamp <= end:
            return True
    return False
