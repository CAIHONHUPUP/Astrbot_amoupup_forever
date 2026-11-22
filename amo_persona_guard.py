# -*- coding: utf-8 -*-
"""人设单一真源 + 出站语义守卫（task-28 P0-3/P0-4/P0-5/P0-6，2026-11-22）。

审计结论（work/audit/memory_persona.md §3.5/§3.7/§3.9）：
- 两套人格真源打架：基础人格「不写动作旁白」，PromptRegistry 后置规则却「允许括号动作」，
  线上 56.5% 的 bot 消息带动作旁白、45.4% 的分段是「只有动作、没有台词」；
- 追问样例句写死「你美丽的老婆」，把人设往第三人称炫耀拉；
- 出站没有禁语/系统口吻/报账数字兜底，「你们男人」「收到你后来」「等了十五分钟」原样发出；
- 她刚说过的话 44 秒后就能否认（「刚刷到一个视频」→「什么视频…没别的视频」），
  而且他带着情绪时她先否认/先讲自己。

本模块是「人设唯一真源」的确定性执行层：prompt 里只留一套规则（见 amo_prompt_registry
llm.rule.core / persona pack），这里负责把漏网的旁白、禁语、系统口吻、精确数字、
没素材支撑的人名挡在发出去之前，并给回复链路提供「自己刚说过的话」硬清单和
「先接情绪」判定。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("amo.persona_guard")

#: 禁语（用户点名 + 审计里被明令禁止却仍出现的句式）。
FORBIDDEN_PHRASES = (
    "你美丽的老婆", "你们男人", "收到你后来", "收到你那句",
    "这有啥好笑的", "我哪生气了", "别给我扣帽子",
)

#: 整句丢弃的禁语（留着前半句会变成怪话）。
_DROP_SENTENCE = ("收到你后来", "收到你那句", "这有啥好笑的",
                  "我哪生气了", "别给我扣帽子")

#: 第三人称自称 → 第一人称（基础人格：自称「我」）。
_SELF_THIRD = (("你美丽的老婆", "我"), ("你老婆", "我"), ("你们男人", "你"))

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?~～\n])")


def _drop_bad_sentences(text):
    parts = _SENTENCE_SPLIT.split(str(text or ""))
    kept = [part for part in parts if not any(word in part for word in _DROP_SENTENCE)]
    return "".join(kept).strip()


def neutralize_forbidden(text):
    """删掉被明令禁止的句子/句式，第三人称自称改成「我」。"""
    body = _drop_bad_sentences(text)
    for old, new in _SELF_THIRD:
        body = body.replace(old, new)
    return body.strip()


# ---------------------------------------------------------------------------
# 动作旁白：≤5% 的确定性执行（整条动作不发，行内旁白剥掉）
# ---------------------------------------------------------------------------

#: 括号动作：中文全角/英文半角都算。只有含数字的括号（日期/价格）保留，
#: 含英文字母的照样是动作（「（套回大T恤，坐回沙发）」）。
_PAREN = re.compile(r"[（(]([^（()）\n]{1,60})[)）]")
_KEEP_PAREN = re.compile(r"\d")


def _strip_line(line):
    def _replace(match):
        return match.group(0) if _KEEP_PAREN.search(match.group(1)) else ""

    body = _PAREN.sub(_replace, str(line or ""))
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"^[\s，。、；：,.;:!?！？~～]+", "", body)
    body = re.sub(r"[\s，、；：,;:]+$", "", body)
    return body.strip()


def strip_action_narration(text):
    """剥掉括号动作旁白；整条只有动作时返回空串（不发）。"""
    lines = []
    for line in str(text or "").splitlines():
        cleaned = _strip_line(line)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def has_action_narration(text):
    return bool(_PAREN.search(str(text or "")))


def action_only(text):
    """整条回复只有括号动作、没有一句台词。"""
    body = str(text or "").strip()
    if not body or not _PAREN.search(body):
        return False
    return not strip_action_narration(body)


def action_ratio(texts):
    """动作旁白率（给验收/周报用）：带旁白或整条旁白的消息占比。"""
    rows = [str(item or "") for item in (texts or []) if str(item or "").strip()]
    if not rows:
        return 0.0
    hit = sum(1 for row in rows if has_action_narration(row) or action_only(row))
    return hit / float(len(rows))


# ---------------------------------------------------------------------------
# 短期自我发言清单：不否认自己刚说的话
# ---------------------------------------------------------------------------

_DENY = re.compile(
    r"(什么视频|什么照片|什么图|我没说|我没发|我没讲过|我没提|"
    r"不是你说的|哪有说过|我啥时候说|我什么时候说|你记错|你听错|"
    r"我这边就一个|没别的视频|啥食堂|什么食堂)")

#: 从「他追问」里取 2 字以上的实词，用来判断他问的是不是她刚说过的事。
_STOP_BIGRAMS = frozenset("什么 怎么 这个 那个 刚才 现在 然后 就是 不是 可以 我们 你们 他们".split())
_WORD = re.compile(r"[\u4e00-\u9fff]{2,4}")


def self_recent_texts(recent, limit=3):
    """从近聊行里取她最近说的几句话（去掉媒体占位符/重复前缀）。"""
    out = []
    for item in (recent or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "") not in ("bot", "assistant", "self"):
            continue
        content = str(item.get("content") or "").strip()
        if not content or content.startswith(("[我发的照片]", "[我发的语音]")):
            continue
        content = re.sub(r"\[(?:我发的照片|我发的语音)\][^\n]{0,20}", "", content).strip()
        if not content:
            continue
        out.append(content[:120])
    return out[-max(1, int(limit or 3)):]


def self_recent_block(self_texts, limit=3):
    """渲染进 system prompt 的「你刚说过」硬清单。"""
    rows = [str(item or "").strip() for item in (self_texts or []) if str(item or "").strip()]
    if not rows:
        return ""
    body = "\n".join("· " + row[:90] for row in rows[-max(1, int(limit or 3)):])
    return (
        "【你刚说过·不可否认】你刚才亲口说过：\n" + body + "\n"
        "这些是你自己刚说的话，不是他说的。他追问时沿用同一个对象和说法；"
        "绝对不许说「什么视频/我没说/不是你说的」来否认自己刚说过的事；"
        "真说错了就自然改口（「我记混了」），不要反问他。\n"
    )


def denial_of_self_issue(user_text, reply, self_texts):
    """他问的正是她刚说过的事，她却在否认 → issue。"""
    user = str(user_text or "")
    body = str(reply or "")
    rows = [str(item or "") for item in (self_texts or []) if str(item or "")]
    if not user or not body or not rows:
        return []
    if not _DENY.search(body):
        return []
    said = " ".join(rows)
    shared = []
    for index in range(len(user) - 1):
        bigram = user[index:index + 2]
        if bigram in _STOP_BIGRAMS or bigram in shared:
            continue
        if bigram in said and re.search(r"[\u4e00-\u9fff]{2}", bigram):
            shared.append(bigram)
    if not shared:
        return []
    return ["否认了自己刚说过的话（%s）" % "、".join(shared[:3])]


# ---------------------------------------------------------------------------
# 先接情绪
# ---------------------------------------------------------------------------

_EMOTION = re.compile(
    r"难过|伤心|委屈|生气|气死|烦死|好烦|烦|累|难受|不舒服|郁闷|崩溃|"
    r"想哭|哭了|不开心|焦虑|压力|痛|疼|被骂|挨骂|倒霉|糟心")
_DENY_OWN_EMOTION = re.compile(
    r"我哪(?:生|有|难|烦)|我没(?:生|难|烦|不高兴)|别给我扣|"
    r"谁(?:生|烦)气|我才没|我哪有")
_EMPATHY = re.compile(
    r"别|抱|心疼|怎么|乖|陪你|哄|难受|委屈|累|气|烦|在呢|我在|辛苦|"
    r"不容易|歇|休息|喝|吃|揉|摸摸|可怜|唉|哎|不急|慢慢|有我")


def emotion_first_issue(user_text, reply):
    """他带着情绪时，第一句必须先接情绪；他在哄她时不许否认自己的情绪。"""
    user = str(user_text or "")
    body = str(reply or "")
    if not user or not body or not _EMOTION.search(user):
        return []
    first = re.split(r"[。！？!?\n]", body.strip())[0][:60]
    if _DENY_OWN_EMOTION.search(first):
        return ["他在哄你/说情绪，你先否认了自己的情绪"]
    if not _EMPATHY.search(first):
        return ["他带着情绪，第一句没有先接住他的情绪"]
    return []


def issues(user_text, reply, self_texts=None):
    """回复侧的确定性复检（接话 critic 之外的人设/自我一致性）。"""
    result = []
    result.extend(emotion_first_issue(user_text, reply))
    result.extend(denial_of_self_issue(user_text, reply, self_texts))
    if action_only(reply):
        result.append("整条回复只有动作旁白，没有一句台词")
    hit = [word for word in FORBIDDEN_PHRASES if word in str(reply or "")]
    if hit:
        result.append("出现禁语：%s" % "、".join(hit))
    return result


def safe_reply(user_text, self_texts=None):
    """复检两次都失败时的安全句：宁可短，不可否认自己/不接情绪。"""
    user = str(user_text or "")
    if _EMOTION.search(user):
        return "我在呢，先别急，慢慢跟我说。"
    if self_texts:
        return "我刚说过的呀，就是刚才跟你提的那个，你别逗我。"
    return "我记混了，你再说一遍？"


# ---------------------------------------------------------------------------
# 姓名 / 物品 grounding（出站最后一道）
# ---------------------------------------------------------------------------


def _recent_context(plugin, umo):
    rows = []
    try:
        reader = getattr(plugin, "_recent", None)
        if callable(reader):
            rows = reader(umo, 8) or []
    except Exception:
        rows = []
    if not rows:
        try:
            rows = plugin.db.recent_messages(umo, 8) or []
        except Exception:
            rows = []
    parts = []
    for item in rows:
        if isinstance(item, dict):
            parts.append(str(item.get("content") or ""))
    return " ".join(parts)


#: 名单名字没素材支撑时的中性替换（task-40：妈妈/爸爸不能变成「同事」）。
_NAME_FALLBACK = {
    "妈妈": "我妈", "爸爸": "我爸",
    "柳如烟": "朋友", "白凝冰": "朋友",
    "季博达": "同事", "方圆": "同事",
    "沈幼微": "表姐",
}
_FAMILY_WORDS = ("妈妈", "爸爸", "我妈", "我爸")


def ground_names(plugin, umo, text):
    """名单里的名字在近聊素材里没出现 → 换成同圈层中性称呼（家人不再变同事）。"""
    body = str(text or "")
    if not body:
        return ""
    try:
        try:
            from .amo_names import ROSTER
        except ImportError:
            from amo_names import ROSTER
    except Exception:
        return body
    context = _recent_context(plugin, umo)
    if not context:
        return body
    for name in ROSTER:
        if name in _FAMILY_WORDS:
            continue  # 妈妈/爸爸是亲属称呼，不是编造的人名，不做中性替换
        if name not in body or name in context:
            continue
        body = body.replace(name, _NAME_FALLBACK.get(name, "有人"))
    return body


# ---------------------------------------------------------------------------
# 出站总闸
# ---------------------------------------------------------------------------


#: 看着像系统词、其实是正常中文的短语（task-40）：先换成同义写法再判系统词。
_BENIGN_SYSTEM = (
    ("犯了个错误", "犯了个错"), ("犯了错误", "犯了错"), ("犯错误", "犯错"),
    ("认错误", "认错"), ("出了个错误", "出了个岔子"), ("出了错误", "出了岔子"),
    # 「修 bug / bug 修完没」是日常口语，不是后台话术；系统判据里摘掉，原文保留
    ("bug", ""), ("Bug", ""), ("BUG", ""),
)
#: 拉丁后台词（bug/api/token…）在口语里出现时换成中文，不整句删掉（task-40）。
_LATIN_TECH_REPLACE = (
    ("api", "那事"), ("token", "那事"), ("sql", "那事"), ("http", "那事"),
    ("json", "那事"), ("webhook", "那事"),
)


def _mask_benign_system(text):
    """把「犯错误」这类正常说法换成不含系统词的写法。"""
    body = str(text or "")
    for old, new in _BENIGN_SYSTEM:
        if old in body:
            body = body.replace(old, new)
    return body


def _system_hit(text):
    """系统词命中（先把「犯错误」这类正常中文短语从判据里摘掉，不改原文）。"""
    probe = str(text or "")
    for old, _new in _BENIGN_SYSTEM:
        if old in probe:
            probe = probe.replace(old, "")
    try:
        try:
            from .amo_memory_ledger import system_word_hit
        except ImportError:
            from amo_memory_ledger import system_word_hit
    except Exception:  # cleanup: 判据不可用就不拦系统词
        return ""
    try:
        return str(system_word_hit(probe) or "")
    except Exception:  # cleanup: 判据异常按未命中
        return ""


def _humanize_system_sentences(text):
    """聊天里混进系统句：只删命中系统词的分句，尽量保留同一句里的人话。

    task-40：以前整句删除会把「犯错误了可得自己负责啊」「这次卡哪儿了？」这种
    正常内容一起吞掉；现在先替换正常短语/拉丁词，再按分句删，删空才给兜底句。
    """
    body = str(text or "")
    if not _system_hit(body):
        return body
    for old, new in _LATIN_TECH_REPLACE:
        body = body.replace(old, new)
    if not _system_hit(body):
        return body
    parts = _SENTENCE_SPLIT.split(str(body or ""))
    kept = []
    for part in parts:
        if not part.strip():
            continue
        if not _system_hit(part):
            kept.append(part)
            continue
        # 同一句里只删命中系统词的分句，人话部分留下（别再整句吞）
        clauses = [item for item in re.split(r"(?<=[，,、；;：:])", part) if item.strip()]
        human = [item for item in clauses if not _system_hit(item)]
        if human:
            kept.append("".join(human).strip())
    out = "".join(kept).strip()
    if out:
        return out
    return "刚才走神了一下，你再说一遍？"


def prepare_outbound(plugin, umo, text, source="chat"):
    """统一出口前的语义卫生：返回 (text, reason)；reason 非空表示丢弃。"""
    body = str(text or "").strip()
    # 2026-11-22 task-23：媒体占位符不是人话，统一出口的最后一道 strip
    # （chat/主动/push/分段都经过这里；图仍由图片组件发）。
    if "我发的照片" in body or "我发的语音" in body:
        try:
            try:
                from .amo_message import strip_media_markers
            except ImportError:
                from amo_message import strip_media_markers
            body = strip_media_markers(body)
        except Exception:  # cleanup: 兜底/降级，保持原行为
            pass
    if not body:
        return "", "empty"
    try:
        try:
            from .amo_memory_ledger import approximate_amounts, collapse_repeats, system_word_hit
        except ImportError:
            from amo_memory_ledger import approximate_amounts, collapse_repeats, system_word_hit
    except Exception:  # 账本模块缺失时只做本地卫生，不挡发送
        approximate_amounts = lambda value: value
        collapse_repeats = lambda value: value
        system_word_hit = lambda value: ""
    body = collapse_repeats(body)
    stripped = strip_action_narration(body)
    if stripped != body:
        if not stripped:
            return "", "action_only"
        body = stripped
    hit = _system_hit(body)
    if hit:
        if source not in ("chat",):
            return "", "system_phrase"
        body = _humanize_system_sentences(body)
        if _system_hit(body):
            return "", "system_phrase"
    body = neutralize_forbidden(body)
    if not body:
        return "", "forbidden_phrase"
    body = approximate_amounts(body)
    body = ground_names(plugin, umo, body)
    body = collapse_repeats(body).strip()
    if not body:
        return "", "empty_after_guard"
    # task-32：收件人/角色反转唯一守卫（最后一层兜底）。首句称呼只能老公/你/名字；
    # 「妈，我马上到」这类用户视角自述整条丢弃；只错称呼就改回老公/名字。
    # 判定只在 amo_addressee_guard 一处实现，这里只转发并带上 source。
    try:
        try:
            from .amo_addressee_guard import enforce as _addressee_enforce
        except ImportError:
            from amo_addressee_guard import enforce as _addressee_enforce
        body, addressee_reason = _addressee_enforce(
            plugin, umo, body, source=str(source or "chat"))
        if not body:
            return "", addressee_reason or "addressee"
        if addressee_reason:
            logger.info("[amo] 出站收件人守卫改写（%s）：%s", addressee_reason, body[:80])
    except Exception as _addressee_exc:
        logger.debug("[amo] 收件人守卫失败: %s", _addressee_exc)
    return body, ""


def sanitize_candidate(plugin, umo, text, user_text=""):
    """候选优先还原前的「该做的安全改写」（task-40）。

    回放里守卫把「完整候选」换成固定兜底/多池拼装句，出口还原候选时不能把
    旁白/禁语/技术话术/精确金额/没素材的人名/错误收件人一起带回来，所以候选
    也要过一遍与真实链路同口径的安全改写。返回 (text, reason)。
    """
    body = str(text or "").strip()
    if not body:
        return "", "empty"
    if "我发的照片" in body or "我发的语音" in body:
        try:
            try:
                from .amo_message import strip_media_markers
            except ImportError:
                from amo_message import strip_media_markers
            body = strip_media_markers(body)
        except Exception:  # cleanup: 剥不掉就保持原文
            pass
    stripped = strip_action_narration(body)
    if not stripped:
        return "", "action_only"
    body = stripped
    try:
        try:
            from .amo_shared_text import strip_tech_terms
        except ImportError:
            from amo_shared_text import strip_tech_terms
        body = str(strip_tech_terms(body, source="chat") or "").strip()
    except Exception:  # cleanup: 技术词清洗失败就保持候选
        pass
    if not body:
        return "", "tech_terms"
    body = _humanize_system_sentences(body)
    body = neutralize_forbidden(body)
    if not body:
        return "", "forbidden_phrase"
    try:
        try:
            from .amo_memory_ledger import approximate_amounts
        except ImportError:
            from amo_memory_ledger import approximate_amounts
        body = approximate_amounts(body)
    except Exception:  # cleanup: 数字大概化失败就不改
        pass
    body = ground_names(plugin, umo, body)
    try:  # 反催问：和真实链路同口径，候选里有催问句照样删
        try:
            from .amo_anti_nag import rewrite as _anti_nag
        except ImportError:
            from amo_anti_nag import rewrite as _anti_nag
        body = str(_anti_nag(body, str(user_text or "")) or body)
    except Exception:  # cleanup: 反催问失败就保持
        pass
    try:  # 消费事实：没记账就说「买了 N 元」的候选不许原样还原
        try:
            from .amo_purchase_guard import rewrite as _purchase_rewrite
        except ImportError:
            from amo_purchase_guard import rewrite as _purchase_rewrite
        body = str(_purchase_rewrite(body, plugin) or body)
    except Exception:  # cleanup: 消费守卫失败就保持
        pass
    try:  # 收件人/角色反转：还原候选也不能把「妈，我马上到」带回来
        try:
            from .amo_addressee_guard import enforce as _addressee_enforce
        except ImportError:
            from amo_addressee_guard import enforce as _addressee_enforce
        body, addressee_reason = _addressee_enforce(
            plugin, umo, body, source="chat")
        if not body:
            return "", addressee_reason or "addressee"
    except Exception:  # cleanup: 收件人守卫失败就保持
        pass
    return str(body or "").strip(), ""


def guard_candidate(plugin, umo, text, user_text=""):
    """候选的「安全探针」：跑事实守卫 + 场景/角色清洗，只看候选还能不能原样还原。

    候选优先不等于候选正确（task-41 交叉回归）：
    - B1「你发的那个视频…再发我一遍？」是角色反转，装饰链会收成「我在家呢。」；
    - C1 用户问「你在哪呢？」候选「我在商场逛街…」和日程冲突，会被收成场景短句。
    这两种候选不能靠 restore 又顶回去。这里返回守卫后的形态，由 amo_rewrite_safety
    判断「候选不安全」时改成守卫形态/保持现行文本。
    """
    body = str(text or "").strip()
    if not body:
        return ""
    try:
        try:
            from .amo_reply_pipeline import apply_fact_guard as _fact_guard
        except ImportError:
            from amo_reply_pipeline import apply_fact_guard as _fact_guard
        body = str(_fact_guard(body, plugin) or body).strip()
    except Exception:  # cleanup: 事实守卫失败就保持
        pass
    return str(body or "").strip()


__all__ = [
    "FORBIDDEN_PHRASES", "neutralize_forbidden", "strip_action_narration",
    "has_action_narration", "action_only", "action_ratio",
    "self_recent_texts", "self_recent_block", "denial_of_self_issue",
    "emotion_first_issue", "issues", "safe_reply", "ground_names",
    "prepare_outbound", "sanitize_candidate", "guard_candidate",
]
