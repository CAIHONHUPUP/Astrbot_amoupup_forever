# -*- coding: utf-8 -*-
"""回复与当前地点/状态一致性（2026-11-21）。

线上截图：她人在路上，却说「刚到家、脱外套、自己煮面」，还问「笑什么、也在刷视频」。
这里做确定性收口：
- 不在家：删掉居家动作/到家句，只保留「还在路上」；
- 在家：删掉「还在路上/快到了」；
- 他分享视频/图片：不许角色反转让他看自己发的，不许换动物；
- 他刚说笑死：不许再问「笑什么」；
- 她刚分享过：回复必须接住他最后一句，用分享的最后一个信息点补，
  删空时也不回落成「我在家呢」这类通用句。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("amo.state_consistency")

_HOME_ACTION = re.compile(
    r"(?:我|自己|等会儿|等会|回去).{0,8}"
    r"(?:煮.{0,3}面|做(?:个|点|顿)?饭|洗碗|洗衣服|换.{0,3}(?:衣服|鞋)|脱.{0,4}外套|上.{0,2}床)")
_ARRIVAL = re.compile(r"(?:刚到家|到家了|到家|刚进门|进家门|进屋|鞋都换了|换好鞋|穿上拖鞋)")
_ROAD_CLAIM = re.compile(r"(?:还在路上|在路上|快到了|马上到|地铁上|公交上|在车上)")
_HOME_PROP = re.compile(
    r"(?:沙发|床上|躺床|趴着|窝在|厨房|洗澡|睡衣|到家|进门"
    r"|脱.{0,4}外套|外套.{0,4}(?:脱|换|挂)|换鞋)")
_OUT_PROP = re.compile(r"(?:商场|试衣|逛街|腿站酸|地铁|在路上|在公司|去公司|到公司)")
# 未来计划不是“此刻矛盾”：带时间/打算的句子不按当前场景改写
_FUTURE = re.compile(r"\d{1,2}[:：]\d{2}|明天|后天|下周|下次")
# 角色反转：只有把内容归到「他发的」并要求他再发时才算，不能拿「你快看/给你看」当同义词。
# 命中后只删含触发词的分句，保留同一句里对内容的描述（issue #1）。
_SHARE_ROLE = re.compile(
    r"你(?:发(?:的|我)|再发|那个(?:视频|图片|照片|图)|翻上去|自己看|再看一遍)"
    r"|发我(?:看看|一遍|一次)?"
    r"|再发我")
# 重复开场：只匹配开头短语本身，替换时保留后面的描述（issue #1/#3）。
# 覆盖视频/图片/事情三类，且不再要求上下文含「视频/图片/刷到/看到」才执行（issue #3）。
_SHARE_OPENER = re.compile(
    r"(?:老公[，,]?)?(?:我)?"
    r"(?:"
    r"刚刷到(?:一个|一件|个)?[^，。！？\n]{0,16}?(?:视频|图片|照片|图|事)"
    r"|你快看这个(?:视频|图片|图)"
    r"|快看这个(?:视频|图片|图)"
    r"|给你看(?:这个|一个|个)?[^，。！？\n]{0,10}?(?:视频|图片|照片|图|事)"
    r")")
# 上下文里出现过「她已经分享过」的痕迹，才把开场当重复；否则视为她的 legit 新分享（issue #2）。
_PRIOR_SHARE = re.compile(
    r"刚才那个|还有后续|刚刷到|我刷到|刚分享|我分享过|分享过|给你看|快看这个"
    r"|刚看了个|刚听说了个")
_LAUGH_ASK = re.compile(r"[^。！？\n]*(?:笑什么|笑啥)[^。！？\n]*[。！？]?")
_FAKE_SHARE = re.compile(r"[^。！？\n]*(?:你快看这个视频|快看这个视频|给你看这个视频|我刚刷到(?:一个)?[^。！？\n]{0,10}?视频)[^。！？\n]*[。！？]?")
_BAD_COOK = re.compile(r"[^。！？\n]*(?:弄点吃的[^。！？\n]*煮.{0,2}面|煮.{0,2}面[^。！？\n]*弄点吃的)[^。！？\n]*[。！？]?")
_ANIMALS = (("猫", ("猫", "猫猫", "小猫", "布偶", "橘猫")), ("狗", ("狗", "狗狗", "狗子", "小狗", "柯基", "柴犬")),
            ("兔", ("兔子", "小兔", "兔")), ("鸟", ("小鸟", "鸟")), ("仓鼠", ("仓鼠",)))


def _scene(plugin):
    try:
        act = plugin.state.current_activity() or {}
    except Exception:
        act = {}
    place = str(act.get("place") or "")
    zone = str(act.get("zone") or "")
    transit = zone == "road" or any(
        word in place for word in ("路上", "地铁", "公交", "车上", "街", "外面"))
    outside = zone in ("outside", "work", "mall") or any(
        word in place for word in ("商场", "公司", "店", "医院", "餐厅"))
    home = zone.startswith("home") or ("家" in place and "路上" not in place)
    return {"home": home, "road": transit and not home,
            "outside": outside and not home and not transit}


def _sentences(text):
    return [part for part in re.split(r"(?<=[。！？!?\n])", str(text or "")) if part.strip()]


def _strip_clauses(sentence, *patterns):
    """只删命中的冲突分句，保留同一句里对分享内容的描述（最后一个信息点）。"""
    parts = [part for part in re.split(r"(?<=[，,；;])", str(sentence or "")) if part.strip()]
    kept = [part for part in parts if not any(pattern.search(part) for pattern in patterns)]
    text = "".join(kept).strip()
    text = re.sub(r"^[，,；;、\s]+", "", text)
    text = re.sub(r"[，,；;]\s*$", "。", text)
    return text


def _animals(text):
    body = str(text or "")
    hit = set()
    for name, words in _ANIMALS:
        if any(word in body for word in words):
            hit.add(name)
    return hit


def _last_animal(text):
    """上下文里最后一个出现的动物：当前分享通常排在历史末尾，比按码点排序可靠。"""
    body = str(text or "")
    best, pos = None, -1
    for name, words in _ANIMALS:
        for word in words:
            index = body.rfind(word)
            if index > pos:
                best, pos = name, index
    return best


# 动作/地点冲突只出现在括号里时，先摘掉括号，别把整句分享描述一起删掉。
_HOME_PAREN = re.compile(
    r"（[^）]*(?:刚到家|到家|进门|脱.{0,4}外套|换鞋|煮.{0,3}面|做饭|洗碗"
    r"|沙发|床上|躺床|趴着|窝在|厨房|洗澡|睡衣)[^）]*）")
_ROAD_PAREN = re.compile(
    r"（[^）]*(?:还在路上|在路上|快到了|马上到|地铁上|公交上|在车上)[^）]*）")
_SHARE_MARK = re.compile(
    r"刷到|给你看|快看|分享|刚才那个|还有后续|刚看了个|刚听说了个|视频|图片|照片|图")
_SHARE_TOPIC_WORDS = ("表情包", "奶茶店", "奶茶", "食堂")
# 回复自己已经点明「就是猫的视频」时，不要拿历史里上一轮的对象覆盖它。
_EXPLICIT_SUBJECT = re.compile(
    r"(?:就是|这是|那是)[^，。！？\n]{0,8}(?:视频|图片|照片|图)")


def _soften_opener(match):
    """摘掉场景冲突的括号后，顺手把「刚刷到」说顺，避免同一句又重复新分享开场。"""
    return "刚听说了个" if "件" in match.group(0) else "刚看了个"


def _share_context_animal(context):
    """从最后一个「分享痕迹」之后取对象，避免把上一轮回复里的动物当成当前话题。"""
    body = str(context or "")
    marks = list(_SHARE_MARK.finditer(body))
    if not marks:
        return None
    tail = body[marks[-1].start():]
    return _last_animal(tail) or _last_animal(body)


def _share_label(context, body):
    """给重复开场挑一个对象词：优先「刚才那个 X 还有后续」里的 X。"""
    match = re.search(r"刚才那个([^，。！？\n]{1,12}?)(?:还有后续|的事|的视频|的图)",
                      str(context or ""))
    if match:
        label = match.group(1).strip()
        if label:
            return label
    animal = _share_context_animal(context)
    if animal:
        return animal
    animal = _last_animal(body)
    if animal:
        return animal
    for word in ("表情包", "奶茶店", "食堂", "视频", "图片", "照片", "图"):
        if word in str(body or ""):
            return "图" if word in ("图片", "照片") else word
    return "视频"


def _is_repeat_share(body, context):
    """只有上下文确有「分享过」的痕迹、且对象对得上时，才把开场当重复。

    这样「你快看这个视频，猫在跳」在空上下文里会保留为合法分享；而在
    「刚才那个猫还有后续」之后再次说「我刚刷到一个猫的视频」才会被改写。
    """
    ctx = str(context or "")
    if not ctx.strip() or not _PRIOR_SHARE.search(ctx):
        return False
    body_animals = _animals(body)
    ctx_animals = _animals(ctx)
    if body_animals:
        # 同一对象：确认是接着刚才那个聊；对象被换但仍在重复开场：也按重复处理，
        # 交给后面的对象纠正（历史里已有分享时，「你快看这个视频」不再算新分享）。
        return bool(body_animals & ctx_animals) or bool(_SHARE_OPENER.search(body))
    if re.search(r"(?:一件|件)事", body):
        return True
    label = _share_label(ctx, body)
    if label and label in body:
        return True
    for token in _SHARE_TOPIC_WORDS:
        if token in body and token in ctx:
            return True
    # 只有「视频/图」这种泛称时，必须上下文同类且没有具体对象，避免拿旧对象套新分享。
    body_media = [word for word in ("视频", "图片", "照片", "图") if word in body]
    if body_media and not body_animals and not ctx_animals:
        return any(word in ctx for word in body_media)
    return False


def _strip_role_reversal(sentence):
    """删掉角色反转分句，保留同句里的内容描述；整句都是反转才返回空。"""
    if not _SHARE_ROLE.search(sentence):
        return sentence
    parts = re.split(r"(?<=[，,；;])", sentence)
    kept = [part for part in parts if part.strip() and not _SHARE_ROLE.search(part)]
    if not kept:
        return ""
    text = "".join(kept).strip()
    text = re.sub(r"^[，,；;、\s]+", "", text)
    text = re.sub(r"^(?:不过|但是|但|而且|然后|可|所以)\s*[，,]?", "", text)
    text = re.sub(r"[，,；;]\s*$", "。", text)
    return text


# ---------------------------------------------------------------------------
# 接住最后一句：分享的最后一个信息点必须留在回复里（2026-11-22）
#
# 分享语境里他只会说这几类话：笑、夸、问是什么、问在哪看到的、要后续。
# 回复如果没接住（模型原文敷衍，或场景守卫把冲突句删空后回落成「我在家呢」），
# 就用「她刚分享的最后一个信息点」补一句，而不是发通用地点句。
# 规则统一：①信息点只从历史/台账里的分享取，定义来自 amo_share_ledger.last_info；
# ②已经接住就原样不动；③删空或只剩敷衍/通用句时用信息点重写。
# ---------------------------------------------------------------------------
_DEMAND_NEXT = ("然后", "后来", "接着")
_DEMAND_WHERE = ("在哪", "哪里", "哪儿")
_DEMAND_WHAT = ("什么", "啥", "咋了")
_DEMAND_PRETTY = ("好看", "可爱", "漂亮", "真的假的", "不是吧", "离谱",
                  "喜欢", "是吧")
_ANSWER_PRETTY = ("好看", "可爱", "漂亮", "喜欢", "真的", "是吧")
_ANSWER_WHERE = ("抖音", "B站", "b站", "微博", "小红书", "商场", "路上", "店里",
                 "网上", "拍的", "刷到", "工位", "面馆", "地铁", "公司", "午休")
_ANSWER_TAIL = ("然后", "后来", "接着")
#: 场景守卫自己的通用回落句：不算接话，有信息点时必须被替换（issue #2）。
#: 句子只在 domains.reply.repetition_guard.FALLBACKS["scene_generic"] 一处维护，
#: 下面这个别名在 _repetition_guard 就绪后赋值（_attach_last_line 用）。


def _last_user_line(context):
    for line in str(context or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _line_chars(line):
    return len(re.sub(r"[^\u4e00-\u9fff]", "", str(line or "")))


def _reply_demand(line):
    """他最后一句要她怎么接：笑/夸/是什么/在哪/后续；不是这五类就不抢话。

    分享语境里这类话都很短（「哈哈哈」「好看」「然后呢」「什么视频」「在哪」），
    长问句（「你中午吃的什么」）不按分享接话处理，避免拿旧分享乱答。
    """
    body = str(line or "")
    chars = _line_chars(body)
    if chars <= 6 and any(word in body for word in _DEMAND_NEXT):
        return "next"
    if chars <= 8 and any(word in body for word in _DEMAND_WHERE):
        return "where"
    if chars <= 5 and any(word in body for word in _DEMAND_WHAT):
        return "what"
    if chars <= 6 and ("哈" in body or "笑" in body):
        return "laugh"
    if chars <= 6 and any(word in body for word in _DEMAND_PRETTY):
        return "pretty"
    return ""


def _anchor_share(context, last_user):
    """历史里最后一条分享句：当前这轮分享排在末尾，取最后一条分享痕迹即可。"""
    segments = [part.strip() for part in re.split(r"(?<=[。！？!?\n])", str(context or ""))]
    for text in reversed(segments):
        if not text:
            continue
        if last_user and (text == last_user or text in last_user):
            continue
        # 历史末尾会再回显一遍他这句（可能带着上一句的场景括号），不能当分享句。
        if last_user and last_user in text and len(text) <= len(last_user) + 8:
            continue
        if "【你刚分享过】" in text:
            continue
        if _SHARE_MARK.search(text):
            return text
    # 「老公，楼下奶茶店排队…」这种事情分享不带视频/图片/刷到等标记：
    # 退一步取「他最后一句」之前紧挨着的那句，就是她刚分享的分享句。
    if not last_user:
        return ""
    for index in range(len(segments) - 1, -1, -1):
        text = segments[index]
        if not text or not text.endswith(last_user):
            continue
        for previous in range(index - 1, -1, -1):
            candidate = segments[previous]
            if not candidate or candidate.endswith(last_user):
                continue
            return candidate
        break
    # 历史里没有回显他这句时，最后一句不是他的就是她刚分享的。
    for text in reversed(segments):
        if not text or text == last_user or text in last_user:
            continue
        return text
    return ""


def _share_info(text, plugin):
    """分享的最后一个信息点：历史句优先，没有历史才用台账（同一套定义）。"""
    body = str(text or "").strip()
    try:
        try:
            from .amo_share_ledger import last_info as _last_info
            from .amo_share_ledger import recent_info as _recent_info
        except ImportError:
            from amo_share_ledger import last_info as _last_info
            from amo_share_ledger import recent_info as _recent_info
        info = _last_info(body) if body else str(_recent_info(plugin) or "")
    except Exception:  # cleanup: 台账读不到就退回原句，不抢话
        info = body
    return str(info or body).strip()


def _info_label(info):
    animal = _last_animal(info)
    if animal:
        return animal
    for word in ("表情包", "奶茶店", "食堂"):
        if word in info:
            return word
    if "图片" in info or "照片" in info:
        return "图片"
    if "图" in info:
        return "图"
    if "视频" in info:
        return "视频"
    return "事"


def _info_tail(info):
    parts = [part for part in re.split(r"(?<=[，,；;])", str(info or "")) if part.strip()]
    return parts[-1].strip() if parts else str(info or "").strip()


def _covers(demand, body, info):
    """回复是否已经接住最后一句（与判据同口径，但不共享具体样例）。"""
    text = str(body or "")
    label = _info_label(info)
    if demand == "laugh":
        return "笑" in text or "哈" in text
    if demand == "pretty":
        return any(word in text for word in _ANSWER_PRETTY)
    if demand == "what":
        return label in text or any(word in text for word in ("视频", "图", "事"))
    if demand == "where":
        return any(word in text for word in _ANSWER_WHERE)
    if demand == "next":
        return label in text or any(word in text for word in _ANSWER_TAIL)
    return True


def _place_source(plugin):
    try:
        act = plugin.state.current_activity() or {}
    except Exception:  # cleanup: 读不到状态时不编地点
        act = {}
    place = str(act.get("place") or "").strip()
    return ("在%s刷到的" % place) if place else "在手机上刷到的"


def _clean_info(info):
    return str(info or "").strip().rstrip("。！？，,；;、 \t")


# ---------------------------------------------------------------------------
# 地点答案兜底（task-29 收口，2026-11-22 真实模型层 guard-introduced）
#
# 现场 G36/t10：他问「你在家吗？」，候选「在啊，窝沙发上刷手机呢，……」，分段前的
# 自我复读去重把「窝沙发上刷手机呢」整句删掉，剩下「在啊，今天下班回来就不想动了」，
# 地点答案被删 → probe_unanswered。场景剥离同样可能删掉地点句。
# 这里在出口做一次「他问地点、正文没地点」的确定性补齐：地点只取 scene_at 的当前
# place（唯一场景源），不编、不猜；不是问地点就不补。
# ---------------------------------------------------------------------------
_ASK_WHERE = re.compile(
    r"在哪|在哪里|在哪儿|什么地方|哪个地方|在家吗|在家么|在不在家|在家不|到了吗|到了没")
#: 「在哪」答案里的地点词（与 amo_question_guard 的 where 口径一致）
_WHERE_WORDS = ("家", "公司", "商场", "路上", "地铁", "公交", "餐厅", "咖啡店", "医院",
                "学校", "店里", "外面", "这边", "那边", "工位", "健身房", "景区", "酒店",
                "街上", "公园")
#: 吃啥/吃了没是事实链的答案，不是「分享接话」里的「什么」，不许被分享补句劫持。
_FOOD_ASK = re.compile(
    r"吃啥|吃什么|吃点什么|吃的什么|晚上吃|中午吃|午饭|晚饭|吃了没|吃了吗|吃饭了没")
#: 分享补句里与当前场景冲突的地点断言：在家说「在外面/商场/路上」，或在外说「沙发/厨房」。
_CATCH_HOME_CONFLICT = re.compile(r"外面|商场|逛街|街上|地铁|在公司|到公司|在路上|路上")
_CATCH_OUT_CONFLICT = re.compile(r"沙发|床上|躺|窝在|厨房|洗澡|睡衣|煮饭|做饭|在家")


def _scene_place(plugin):
    """此刻地点：唯一场景源 amo_scene_ledger.scene_at；读不到才退回活动快照。"""
    try:
        try:
            from .amo_scene_ledger import scene_at as _scene_at
        except ImportError:
            from amo_scene_ledger import scene_at as _scene_at
        place = str((_scene_at(plugin) or {}).get("place") or "").strip()
        if place:
            return place
    except Exception:  # cleanup: 场景账本不可用退回活动快照
        pass
    try:
        return str((plugin.state.current_activity() or {}).get("place") or "").strip()
    except Exception:
        return ""


def _where_answer_sentence(plugin):
    """「我在X呢」：地点取当前 scene_at.place；没有地点才按场景回落。"""
    place = _scene_place(plugin)
    if place:
        return "我在%s呢。" % place
    scene = _scene(plugin)
    if scene["road"]:
        return "我在路上呢。"
    if scene["outside"]:
        return "我在外面呢。"
    if scene["home"]:
        return "我在家呢。"
    return ""


def _answers_where(text):
    body = str(text or "")
    return any(word in body for word in _WHERE_WORDS)


def _turn_ask(umo, fallback_text):
    """取「本轮他真正问的那句」：优先 reply_pipeline 记的本轮问题（turn 对齐），
    拿不到才用传入文本的最后一句（send 阶段的 user_text 可能是上一轮，不能信）。"""
    key = str(umo or "").strip()
    if key:
        try:
            try:
                from .amo_reply_pipeline import _ACTIVE_TURN as _active
                from .amo_reply_pipeline import _TURN_USER_TEXT as _store
            except ImportError:
                from amo_reply_pipeline import _ACTIVE_TURN as _active
                from amo_reply_pipeline import _TURN_USER_TEXT as _store
            entry = _store.get(key)
            if entry and entry[0] == _active.get(key):
                # 本轮已登记：他这一轮没问句就是没问句，绝不回退到上一轮的旧文本。
                return str(entry[1] or "").strip()
        except Exception:  # cleanup: 取不到就退回传入文本
            pass
    return _last_user_line(fallback_text)


def _ensure_where_answer(text, plugin, user_text, umo=""):
    """他问「在哪/在家吗」而正文没地点（被删句/场景剥离删掉）→ 用当前地点补一句。

    只补「此刻在哪」这类问句；普通聊天不硬塞地点，正文已有地点词就不动。
    问句优先取本轮问题（turn 对齐），不用 send 阶段可能过期的 user_text。
    """
    ask = _turn_ask(umo, user_text)
    if not _ASK_WHERE.search(ask):
        return text
    body = str(text or "").strip()
    if not body or _answers_where(body):
        return body
    sentence = _where_answer_sentence(plugin)
    if not sentence:
        return body
    if body.endswith(("。", "！", "？", "!", "?", "~", "～", "…")):
        return body + sentence
    return body + "。" + sentence


def _append_clashes_scene(catch, plugin):
    """追加的分享信息点若带与当前场景冲突的地点（在家说在外面逛），不追加。"""
    scene = _scene(plugin)
    text = str(catch or "")
    if not text:
        return False
    if scene["home"]:
        return bool(_CATCH_HOME_CONFLICT.search(text))
    if scene["road"] or scene["outside"]:
        return bool(_CATCH_OUT_CONFLICT.search(text))
    return False


def _asks_share_followup(line):
    """他这句是不是在追问分享内容（问句/要后续），而不是普通陈述或吃啥。"""
    body = str(line or "")
    if _FOOD_ASK.search(body):
        return False
    return bool(re.search(r"[?？]|什么|啥|哪|怎么|然后|后来|接着|好看|真的假的", body))


def _share_kind(anchor, label, info):
    body = str(anchor or "") + str(info or "")
    if "图" in body or label == "表情包":
        return "图"
    if label in ("奶茶店", "食堂", "事") or "事" in body:
        return "事"
    return "视频"


def _catch_sentence(demand, info, label, anchor, plugin, body):
    """用分享的最后一个信息点接住他最后一句；信息点已在回复里就只补接话词。"""
    info = _clean_info(info)
    tail = _info_tail(info)
    carried = bool(info) and (info in body or (tail and tail in body))
    if demand == "laugh":
        if not info:
            return "哈哈哈，笑死我了。"
        if carried:
            return "哈哈哈，笑死我了。"
        if "笑" in info or "哈" in info:
            return "哈哈哈，%s。" % info
        return "哈哈哈，%s，笑死我了。" % info
    if demand == "pretty":
        if label in ("奶茶店", "食堂", "事"):
            return "真的。" if (carried or not info) else "真的，%s。" % info
        return "也太可爱了吧。" if (carried or not info) else "%s，也太可爱了吧。" % info
    if demand == "what":
        head = "就是%s的%s" % (label, _share_kind(anchor, label, info))
        return head + "。" if (carried or not info) else head + "，%s。" % info
    if demand == "where":
        source = _place_source(plugin)
        return source + "。" if (carried or not info) else "%s，%s。" % (source, info)
    if demand == "next":
        if not info:
            return ""
        if info.startswith(("然后", "后来", "接着")):
            return info + "。"
        return "然后%s。" % info
    return ""


def _attach_last_line(body, context, plugin):
    """分享的最后一个信息点必须出现在回复里；删空也不回落通用句（issue #2）。

    task-29（2026-11-22 真实模型层 guard-introduced 收口）三条边界：
    1) 吃啥/吃了没是事实链的答案，不是分享接话的「什么」→ 不追加信息点；
    2) 追加的信息点若和当前场景打架（在家说「我刚在外面逛着」）→ 不追加；
    3) 非问句轮只救「删空/只剩通用句/短答」，不往完整正文后面硬补最后信息点。
    """
    last_user = _last_user_line(context)
    demand = _reply_demand(last_user)
    if not demand:
        return body
    if _FOOD_ASK.search(last_user):
        return body
    anchor = _anchor_share(context, last_user)
    info = _share_info(anchor, plugin)
    if not info:
        return body
    label = _info_label(anchor) if anchor else _info_label(info)
    text = str(body or "").strip()
    if text and text not in _GENERIC_REPLIES and _covers(demand, text, info):
        return text
    catch = _catch_sentence(demand, info, label, anchor, plugin, text)
    if not catch:
        return body
    if _append_clashes_scene(catch, plugin):
        return body
    if not text or text in _GENERIC_REPLIES or len(text) <= 12:
        return catch
    if not _asks_share_followup(last_user):
        return body
    return text + catch


def rewrite(text, plugin, user_text="", umo=""):
    body = str(text or "")
    if not body.strip():
        return body
    scene = _scene(plugin)
    user = str(user_text or "")
    # 括号里的居家动作/到家，先整段拿掉（不在家时）；在家时同理拿掉括号里的路上动作。
    removed_scene_paren = False
    if scene["road"] or scene["outside"]:
        stripped = _HOME_PAREN.sub("", body)
        removed_scene_paren = stripped != body
        body = stripped
    elif scene["home"]:
        stripped = _ROAD_PAREN.sub("", body)
        removed_scene_paren = stripped != body
        body = stripped
    out = []
    for sentence in _sentences(body):
        if _FUTURE.search(sentence):
            out.append(sentence)
            continue
        if scene["road"]:
            if (_ARRIVAL.search(sentence) or _HOME_ACTION.search(sentence)
                    or _HOME_PROP.search(sentence)):
                # 只删冲突分句，同一句里对分享内容的描述要留下（issue #1）。
                rest = _strip_clauses(sentence, _ARRIVAL, _HOME_ACTION, _HOME_PROP)
                if rest:
                    out.append(rest)
                elif _ARRIVAL.search(sentence):
                    out.append("我还在路上呢，快到了")
                continue
        elif scene["outside"]:
            if (_ARRIVAL.search(sentence) or _ROAD_CLAIM.search(sentence)
                    or _HOME_ACTION.search(sentence) or _HOME_PROP.search(sentence)):
                rest = _strip_clauses(sentence, _ARRIVAL, _ROAD_CLAIM,
                                      _HOME_ACTION, _HOME_PROP)
                if rest:
                    out.append(rest)
                elif _ARRIVAL.search(sentence) or _ROAD_CLAIM.search(sentence):
                    out.append("我到了")
                continue
        elif scene["home"]:
            if _ROAD_CLAIM.search(sentence) and not _ARRIVAL.search(sentence):
                rest = _strip_clauses(sentence, _ROAD_CLAIM)
                out.append(rest or "我在家呢")
                continue
            if _OUT_PROP.search(sentence) and not _HOME_PROP.search(sentence):
                rest = _strip_clauses(sentence, _OUT_PROP)
                if rest:
                    out.append(rest)
                continue
        out.append(sentence)
    body = "".join(out).strip()
    # 角色反转只删反转分句：不能把「你快看这个视频，猫在跳…」整句吞掉（issue #1）
    # 也不再把「你快看/给你看」本身当角色反转（issue #2）
    body = "".join(_strip_role_reversal(sentence) for sentence in _sentences(body)).strip()
    # 记录开场替换前的对象：只有当前对象原本不在句子里时才纠对象，
    # 避免把「猫…后面还有一只狗」里的狗也改成猫（保留描述）。
    object_before = _animals(body)
    # 刚摘掉场景冲突括号的分享句，把「刚刷到」软化成「刚看了个」，
    # 既保留对象描述，也不再触发重复开场判据。
    if removed_scene_paren:
        body = re.sub(r"刚刷到(?:一个|一件|个)?", _soften_opener, body, count=1)
    # 同一件事不要重复“新分享”：上下文确实有她分享过的痕迹且对象对得上才改写；
    # 后续句「刚才那个兔还有后续」也算分享痕迹（issue #3）。只换开头，保留描述。
    if _is_repeat_share(body, user):
        label = _share_label(user, body)
        if _SHARE_OPENER.search(body):
            body = _SHARE_OPENER.sub("就是刚才那个%s" % label, body).strip()
    # 把「弄点吃的…煮的面」这种硬拼句改顺
    if _BAD_COOK.search(body):
        body = _BAD_COOK.sub("我等会儿煮点面吃。", body).strip()
    # 对象只在「分享类上下文」里找到当前对象时才纠正；当前对象已在句子里就不动，
    # 避免把「猫…后面还有一只狗」里的狗也改成猫，也避免拿上一轮对象覆盖这一轮。
    topic_animal = _share_context_animal(user)
    if (topic_animal and topic_animal not in object_before
            and not _EXPLICIT_SUBJECT.search(body)):
        for name, words in _ANIMALS:
            if name == topic_animal:
                continue
            for word in words:
                body = body.replace(word, topic_animal)
    # 他刚说笑死/好笑：不许反问笑什么
    if any(word in user for word in ("笑死", "好笑", "笑喷", "哈哈")) and _LAUGH_ASK.search(body):
        body = _LAUGH_ASK.sub("", body).strip()
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    # 统一收口：分享的最后一个信息点必须留在回复里；删空也不回落通用句。
    body = _attach_last_line(body, user, plugin)
    if not body:
        if scene["road"]:
            body = "我还在路上呢，快到了。"
        elif scene["outside"]:
            body = "我到了，等会儿说。"
        else:
            body = "我在家呢。"
    # 唯一后置语义复检（task-29）：本函数每一步都在整句删除/替换（场景分句、
    # 分享补句、回落句），出口必须再判一次「还答不答得上他最后一句」；
    # 答不上就保留本轮候选（候选优先于通用场景句）。
    body = _post_recheck(body, plugin, umo, _last_user_line(user))
    # 他问「在哪」而地点答案被场景剥离/删句删掉 → 用 scene_at 的当前地点补一句，
    # 不能留一句没有地点的「在啊，……」（task-29 真实模型层 G36/t10）。
    # 放在复检之后：还原的候选也可能没有地点词，补地点不覆盖候选内容。
    return _ensure_where_answer(body, plugin, user, umo)


# ---------------------------------------------------------------------------
# 回复拼装前的统一校验（2026-11-22 task-15，10:32 真实 P0）
#
# 现场：她此刻在家，模型把上一条自己发过的「老公，我在商场试了件米白色针织开衫，
# 你猜好不好看？」和更早的「[我发的照片] 自拍」一起复读进正文，分段引擎再拆成两段
# 发出去，同一条回复同时出现"家"和"商场"两个地点。
#
# 这里只提供纯函数，按「先去标识、再去复读、最后按当前场景收口」的顺序：
# - 媒体标记的唯一定义在 amo_message（is_media_record / strip_media_markers）；
# - demote_past_claims：喂给模型的旧上下文里，把与当前场景冲突的断言降级成过去时，
#   旧事可以留、但不能被当成"此刻"；
# - strip_current_scene_conflicts：出站前把"此刻不可能在那"的断言删掉；
# - drop_repeated_clauses / dedupe_reply_segments：子句级自我复读 + 分段去重。
# 场景词表全部复用现有 _OUT_PROP/_ROAD_CLAIM/_HOME_ACTION/_HOME_PROP/_ARRIVAL，
# 复读归一化复用 domains.reply.repetition_guard，不按线上样例新增正则。
# ---------------------------------------------------------------------------

#: 已有过去标记的分句不再当"此刻"处理（与 amo_state_snapshot.PAST_MARKERS 同口径）。
_PAST_CTX_MARKERS = ("昨天", "昨晚", "前天", "以前", "之前", "上周", "上次", "那天",
                     "当时", "那会儿", "刚才", "刚刚", "小时候", "去年",
                     "前阵子", "前两天", "今天早上", "早上", "上午", "那次")

#: 子句切分：和 repetition_guard 的整句切分同口径，但补上逗号/顿号。
_CLAUSE_RE = re.compile(r"[^，,。！？!?；;…~～\n]+[，,。！？!?；;…~～]?")


def current_scene(plugin):
    """当前场景（home/road/outside）；给读侧和测试一个稳定入口。"""
    return _scene(plugin)


def _repetition_guard():
    try:
        from .domains.reply import repetition_guard as guard
    except ImportError:
        from domains.reply import repetition_guard as guard
    return guard


_GENERIC_REPLIES = _repetition_guard().FALLBACKS["scene_generic"]


def history_bot_texts(plugin, umo="", count=6):
    """最近几条她自己说过的话（已按 _recent 的读侧过滤：媒体占位符/旧场景已处理）。"""
    if plugin is None:
        return []
    try:
        return list(_repetition_guard().recent_bot_texts(plugin, umo, count) or [])
    except Exception:
        return []


def _conflict_patterns(scene):
    """当前场景下"此刻不可能"的断言。过去时/未来时由调用方放行。"""
    if not isinstance(scene, dict):
        return ()
    if scene.get("home"):
        return (_OUT_PROP,)
    if scene.get("road") or scene.get("outside"):
        return (_ROAD_CLAIM, _HOME_ACTION, _HOME_PROP, _ARRIVAL)
    return ()


def _has_past_marker(text, scene):
    body = str(text or "")
    if any(marker in body for marker in _PAST_CTX_MARKERS):
        return True
    # 未来计划不是"此刻矛盾"：带时间/打算的分句不按当前场景改写（沿用 rewrite 口径）。
    return bool(_FUTURE.search(body))


def demote_past_claims(text, plugin=None, scene=None):
    """把旧上下文里与当前场景冲突、又没说成过去的地点断言降级成过去说法。

    只用于喂给模型的"最近对话/记忆"：内容不丢，只把"我在商场"改成"我之前在商场"，
    模型就不会把旧商场当成现在。出站侧用 strip_current_scene_conflicts 直接剥离。
    """
    body = str(text or "")
    if not body.strip():
        return body
    scene = scene if isinstance(scene, dict) else _scene(plugin)
    patterns = _conflict_patterns(scene)
    if not patterns:
        return body
    parts = re.split(r"([，,。！？!?；;…~～\n])", body)
    for index in range(0, len(parts), 2):
        clause = parts[index]
        if not clause.strip():
            continue
        if not any(pattern.search(clause) for pattern in patterns):
            continue
        if _has_past_marker(clause, scene):
            continue
        if "我" in clause:
            parts[index] = re.sub(r"我", "我之前", clause, count=1)
        else:
            parts[index] = "之前" + clause.lstrip()
    return "".join(parts)


def strip_current_scene_conflicts(text, plugin=None, scene=None):
    """出站前：删掉当前场景下"此刻不可能"的分句（过去时/未来时放行）。

    和 rewrite 的区别：这里不补分享信息点、不回落通用句，只做"地点断言现在是假的"的
    确定性剥离；空串由 validate_reply_assembly 统一兜底。
    """
    body = str(text or "")
    if not body.strip():
        return body
    scene = scene if isinstance(scene, dict) else _scene(plugin)
    patterns = _conflict_patterns(scene)
    if not patterns:
        return body
    out = []
    for sentence in _sentences(body):
        if not sentence.strip():
            continue
        if _has_past_marker(sentence, scene):
            out.append(sentence)
            continue
        if not any(pattern.search(sentence) for pattern in patterns):
            out.append(sentence)
            continue
        rest = _strip_clauses(sentence, *patterns)
        if rest:
            out.append(rest)
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def _normalize_clause(text):
    try:
        return _repetition_guard().compare_body(text)
    except Exception:
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(text or ""))


def _clause_units(text):
    return [part for part in _CLAUSE_RE.findall(str(text or "")) if part.strip()]


def drop_repeated_clauses(text, refs, min_chars=5):
    """删掉与 refs（她最近说过的话 / 本轮前面的段）重复的分句；同段内重复也只留一次。

    返回 (新文本, 被删分句)。模型把上一条自己的整句拆进一个新句子里时，
    整句相似度去重会漏掉（中间夹着新内容），分句级包含/高相似才能拦住。
    """
    body = str(text or "")
    if not body.strip():
        return body, []
    ref_units = []
    for ref in refs or ():
        for unit in _clause_units(ref):
            norm = _normalize_clause(unit)
            if len(norm) >= min_chars:
                ref_units.append(norm)
    try:
        from difflib import SequenceMatcher as _matcher
    except Exception:  # pragma: no cover - 标准库不会失败
        _matcher = None
    kept, dropped, seen = [], [], set()
    for unit in _clause_units(body):
        norm = _normalize_clause(unit)
        duplicate = False
        if len(norm) >= min_chars:
            if norm in seen:
                duplicate = True
            else:
                for ref_norm in ref_units:
                    if norm == ref_norm or norm in ref_norm:
                        duplicate = True
                        break
                    if _matcher is not None and len(norm) >= 6:
                        if _matcher(None, norm, ref_norm).ratio() >= 0.9:
                            duplicate = True
                            break
        if duplicate:
            dropped.append(unit.strip())
            continue
        if norm:
            seen.add(norm)
        kept.append(unit)
    return "".join(kept).strip(), dropped


def dedupe_reply_segments(segments, refs=None, plugin=None, umo=""):
    """分段去重：后段和最近说过的话/本轮前面的段重复，就整段不发。"""
    refs = list(refs or [])
    if not refs:
        refs = history_bot_texts(plugin, umo)
    out = []
    for raw in segments or ():
        segment = str(raw or "")
        if not segment.strip():
            continue
        fixed, _dropped = drop_repeated_clauses(segment, refs + out)
        if not fixed.strip():
            # 整段都是复读：丢掉；若它是唯一一段，调用方会回退到校验后的整条文本。
            continue
        out.append(fixed)
    return out


def validate_reply_assembly(text, plugin=None, umo="", user_text="", recent=None):
    """回复拼装前的统一校验（聊天/主动共用）：媒体标记、自我复读、当前场景。

    顺序不能换：
    1) 先去掉媒体占位符（模型会把它当一句话复读，且它本就不该出现在人话里）；
    2) 再删与最近她自己说过的话重复的分句（整句相似度拦不住"旧句拆进新句子"）；
    3) 最后按「此刻场景」剥离冲突地点（当前在家就不许把旧商场当成现在）。
    全被删空时给一句不与历史复读的短句，绝不回退原文（原文就是复读）。
    """
    body = str(text or "")
    if not body.strip():
        return body
    try:
        try:
            from .amo_message import strip_media_markers as _strip_media
        except ImportError:
            from amo_message import strip_media_markers as _strip_media
        body = _strip_media(body)
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass
    if not body.strip():
        body = ""
    if not body.strip():
        return _post_recheck(_safe_short_reply(umo, history_bot_texts(plugin, umo)),
                             plugin, umo, user_text)
    refs = list(recent or [])
    if not refs:
        refs = history_bot_texts(plugin, umo)
    body, _dropped = drop_repeated_clauses(body, refs)
    if body.strip():
        body = strip_current_scene_conflicts(body, plugin)
    if not body.strip():
        body = _safe_short_reply(umo, refs)
    # task-32：收件人/角色反转守卫（聊天拼装阶段先拦一次，避免错误称呼被分段发出去）。
    # 判定只在 amo_addressee_guard 一处实现；这里是 chat 拼装入口，丢弃时给安全短句，
    # 绝不把原文交回 AstrBot 核心（核心回退会把原文原样发出去）。
    try:
        try:
            from .amo_addressee_guard import enforce as _addressee_enforce
        except ImportError:
            from amo_addressee_guard import enforce as _addressee_enforce
        guarded, addressee_reason = _addressee_enforce(
            plugin, umo, body, source="assembly")
        if not guarded:
            logger.info("[state] 收件人守卫丢弃（%s）：%s",
                        addressee_reason, str(body or "")[:80])
            body = _safe_short_reply(umo, refs)
        elif addressee_reason:
            logger.info("[state] 收件人守卫改写（%s）：%s", addressee_reason, guarded[:80])
            body = guarded
    except Exception as exc:  # cleanup: 守卫失败不挡正常聊天
        logger.debug("[state] 收件人守卫失败: %s", exc)
    # 唯一后置语义复检（task-29）：媒体标记/自我复读/场景剥离三步都可能把答案
    # 一起删掉，出口再判一次；原候选答得上就保留候选（别发通用短句）。
    body = _post_recheck(body, plugin, umo, user_text)
    # 他问「在哪/在家吗」而地点答案被整句去重删掉 → 用 scene_at 的当前地点补一句，
    # 不能把 where 问句发成一句没有地点的「在啊，……」（G36/t10）。
    return _ensure_where_answer(body, plugin, user_text, umo)


def _safe_short_reply(umo, refs=None):
    """删空时的兜底：轮换一句短话，避开最近说过的话（复用 repetition_guard）。"""
    try:
        fixed = _repetition_guard().short_fallback(umo or "", list(refs or []))
    except Exception:
        fixed = ""
    return str(fixed or "").strip() or "嗯，我在呢。"


def _post_recheck(text, plugin, umo="", user_text=""):
    """唯一后置语义复检入口（task-29）：本模块整句删除/替换跑完后统一过一道。

    判定/兜底句识别只在 amo_reply_pipeline.final_semantic_recheck ->
    amo_grounding_critic.post_rewrite_recheck 一处实现，这里只做安全转发：
    - 没有 umo / 没有本轮候选时不做事（主动消息/独立调用不受影响）；
    - 复检异常时保持改写结果，绝不把回复弄丢。
    """
    if not str(umo or "").strip():
        return text
    try:
        try:
            from .amo_reply_pipeline import final_semantic_recheck as _recheck
        except ImportError:
            from amo_reply_pipeline import final_semantic_recheck as _recheck
        return _recheck(text, plugin, umo=umo, user_text=user_text)
    except Exception as exc:  # cleanup: 复检出错不影响出站
        logger.debug("[state] 后置语义复检失败: %s", exc)
        return text
