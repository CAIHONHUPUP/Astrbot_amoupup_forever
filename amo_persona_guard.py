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
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("amo.persona_guard")

#: 禁语（用户点名 + 审计里被明令禁止却仍出现的句式）。
FORBIDDEN_PHRASES = (
    "你美丽的老婆", "你们男人", "收到你后来", "收到你那句",
    "这有啥好笑的", "我哪生气了", "别给我扣帽子",
)

#: 整句丢弃的禁语（留着前半句会变成怪话）。
_DROP_SENTENCE = ("收到你后来", "收到你那句", "这有啥好笑的",
                  "我哪生气了", "别给我扣帽子")

#: 第三人称自称 → 「我」（保住谓语）；群体称呼 → 「你」（换「我」= 自我贬低，语义反了）。
_SELF_THIRD = (("你美丽的老婆", "我"), ("你老婆", "我"), ("你们男人", "你"))
_THIRD_REPLACE = dict(_SELF_THIRD)

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?~～\n])")
#: 分句符：命中词前面最后一个分句符之后是「分句首」= 主语位。
_CLAUSE_CHARS = "，,、；;：:"
#: 命中词前面只剩这些称呼时仍按主语位处理（「老公你美丽的老婆…」→「老公我…」）。
_VOCATIVE_ONLY = re.compile(
    r"^(?:老公啊|老公|你啊|喂|哎呀|哎|诶|哼|那个)[，,、\s]*$")
#: 残片判据：只剩虚词开头 / 整句只剩代词 / 只剩定语结尾。
_FRAGMENT_HEAD = re.compile(r"^(?:儿|了|的|们|着|而|嘛|吧|啊|呢|个)")
_FRAGMENT_TAIL = re.compile(r"(?:的|地|得)$")
_FRAGMENT_BODIES = frozenset(("我", "你", "我的", "你的", "我我", "我想我"))

#: 禁语处理计数（进程内观测，不驱动逻辑；与 naturalness_stats 同款）。
_FORBIDDEN_COUNTER: dict[str, int] = {}


def forbidden_hits(text):
    """文本里命中的禁语词（顺序同 FORBIDDEN_PHRASES，同一个词只报一次）。"""
    body = str(text or "")
    return [word for word in FORBIDDEN_PHRASES if word in body]


def _body_of(text):
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(text or ""))


def _looks_fragment(text):
    """残片：整句只剩虚词/代词/定语，或还留着「我我」「我想我」。"""
    body = str(text or "").strip()
    if not body:
        return True
    core = _body_of(body)
    if not core or core in _FRAGMENT_BODIES:
        return True
    if "我我" in body or "我想我" in body:
        return True
    if _FRAGMENT_HEAD.match(body):
        return True
    return bool(_FRAGMENT_TAIL.search(core))


def _tail_clause(text, index):
    """命中词前面、最后一个分句符之后的那段（空 = 分句首 = 主语位）。"""
    head = str(text or "")[:index]
    cut = max(head.rfind(char) for char in _CLAUSE_CHARS)
    return head[cut + 1:]


def _rewrite_word(sentence, word, replacement):
    """按位置把一处第三人称自称/群体称呼改掉；改不干净返回 None（该句丢）。

    T1 主语位（句首/分句首/称呼后）→ 直接换主语，保留谓语；
    T2 非主语位（宾语/兼语/定语）→ 前面已有「我」就不能换（会拼出「我想我」），
       且换完全句只能剩一个「我」才放行（「你别惹我生气」）；
    其余 → 丢句。
    """
    for _step in range(8):
        index = sentence.find(word)
        if index < 0:
            return sentence
        prefix = sentence[:index]
        tail = _tail_clause(sentence, index)
        subject_position = (not prefix.strip() or not tail.strip()
                            or bool(_VOCATIVE_ONLY.match(prefix)))
        if not subject_position and "我" in prefix:
            return None
        fresh = prefix + replacement + sentence[index + len(word):]
        if not subject_position and fresh.count("我") > 1:
            return None
        sentence = fresh
    return None  # 同一个词命中太多：不再猜，整句丢


def _rewrite_sentence(sentence):
    """单句：命中词逐个位置判定重写，改不出干净句子就返回 ("", False)。"""
    hits = [word for word, _new in _SELF_THIRD if word in sentence]
    if not hits:
        return sentence, False
    body = sentence
    for word in hits:
        fresh = _rewrite_word(body, word, _THIRD_REPLACE[word])
        if fresh is None:
            return "", False
        body = fresh
    # 同位语自称「你老婆我饿了」→「我我饿了」→「我饿了」
    body = re.sub(r"我(?=[，,、]?我)", "", body)
    body = re.sub(r"^[，,、\s]+", "", body)
    if any(word in body for word in _THIRD_REPLACE):
        return "", False
    if _looks_fragment(body):
        return "", False
    return body, True


def _join_kept(parts):
    """丢句后重拼：被删句两侧的空隙一起去掉，不留下多余空行。"""
    out: list[str] = []
    gap = False
    for part in parts:
        if not part.strip():
            gap = bool(out)
            continue
        if out and gap:
            out.append("\n")
        gap = False
        out.append(part)
    return "".join(out).strip()


def rewrite_forbidden(text):
    """低阶禁语重写：整句重写 + 残片丢句，**不做中性兜底**（全丢返回空串）。

    返回 (text, stats)。stats 键与 _FORBIDDEN_COUNTER 同口径（rewrite/drop_sentence）。
    候选链专用：返回空串表示「候选不安全」，由 amo_rewrite_safety.recover 保持现行
    文本 —— 这里绝不能返回中性句，否则会用更空的回复顶掉正常回复。
    """
    original = str(text or "")
    stats = {"rewrite": 0, "drop_sentence": 0}
    if not original.strip():
        return "", stats
    kept, changed = [], False
    for part in _SENTENCE_SPLIT.split(original):
        if not part.strip():
            kept.append(part)  # 换行等纯空白：由 _join_kept 按位置还原
            continue
        if any(word in part for word in _DROP_SENTENCE):
            stats["drop_sentence"] += 1
            changed = True
            continue
        fresh, rewritten = _rewrite_sentence(part)
        if not fresh.strip():
            stats["drop_sentence"] += 1
            changed = True
            continue
        if rewritten:
            stats["rewrite"] += 1
            changed = True
        kept.append(fresh)
    if not changed:
        return original.strip(), stats
    return _join_kept(kept), stats


def contextual_fallback(user_text):
    """按他最后一句给上下文兜底；唯一实现在 domains/reply/repetition_guard。"""
    try:
        try:
            from .domains.reply import repetition_guard as _registry
        except ImportError:
            from domains.reply import repetition_guard as _registry
        return str(_registry.contextual_fallback(user_text) or "")
    except Exception:  # cleanup: 注册表不可用就返回空，交给调用方决定
        return ""


def _neutral_sentence(key="", user_text=""):
    """全句被禁语吃掉时的中性句：先按他最后一句，再回统一兜底注册表。"""
    contextual = contextual_fallback(user_text)
    if contextual:
        return contextual
    line = ""
    try:
        try:
            from .domains.reply import repetition_guard as _registry
        except ImportError:
            from domains.reply import repetition_guard as _registry
        line = _registry.pick("short", "forbidden:" + str(key or ""), ())
    except Exception:  # cleanup: 注册表不可用就退回字面量（兜底必须自带兜底）
        line = ""
    line = str(line or "").strip()
    if not line or "嗯，我在呢" in line:
        # 这条旧固定句只保留在识别池里；生产兜底不再返回它（上下文兜底已优先）。
        line = "在听你说呢。"
    return line


def _note_forbidden(stats, original, result):
    """计数 + 命中日志（只在命中时写 INFO；线上极低频）。"""
    for name in ("rewrite", "drop_sentence", "fallback"):
        count = int(stats.get(name, 0) or 0)
        if count:
            _FORBIDDEN_COUNTER[name] = _FORBIDDEN_COUNTER.get(name, 0) + count
    hits = forbidden_hits(original)
    if hits:
        if stats.get("fallback"):
            action = "fallback"
        elif not str(result or "").strip():
            action = "drop"  # 候选链：全丢返回空，由 recover 保持现行文本
        else:
            action = "rewrite"
        logger.info("[amo] 禁语处理 action=%s hits=%s text=%s",
                    action, "、".join(hits), str(result or "")[:80])


def forbidden_stats():
    """禁语处理计数快照（进程内；观测用，不驱动逻辑）。"""
    return dict(_FORBIDDEN_COUNTER)


def reset_forbidden_stats():
    """清空禁语计数（测试与回放用）。"""
    _FORBIDDEN_COUNTER.clear()


def neutralize_forbidden(text, key="", user_text=""):
    """出站禁语处理：整句重写 → 残片丢句 → 全丢给中性兜底。

    硬不变量：**非空输入永不返回空串**（三条出站路径的空返回都会丢消息）；
    空/空白输入仍返回空串（prepare_outbound 的 "empty" 分支依赖这一点）。
    """
    body = str(text or "")
    if not body.strip():
        return ""
    fresh, stats = rewrite_forbidden(body)
    if not fresh:
        stats["fallback"] = 1
        fresh = _neutral_sentence(key, user_text)
    _note_forbidden(stats, body, fresh)
    return fresh


def prepare_generated_line(text):
    """模型新生成的一句在采用前的确定性收口：只做禁语（neutralize_forbidden）。

    3C 生成式修复专用（amo_reply_pipeline.repair_reply）。返回 "" = 这一句不能用。
    不进现有出站链/候选链；收件人守卫已随社交/群聊精简移除。
    """
    body = str(text or "").strip()
    if not body:
        return ""
    return str(neutralize_forbidden(body) or "").strip()


# ---------------------------------------------------------------------------
# 动作旁白：≤5% 的确定性执行（整条动作不发，行内旁白剥掉）
# ---------------------------------------------------------------------------

#: 括号动作：中文全角/英文半角都算。只有含数字的括号（日期/价格）保留，
#: 含英文字母的照样是动作（「（套回大T恤，坐回沙发）」）。
_PAREN = re.compile(r"[（(]([^（()）\n]{1,60})[)）]")
_KEEP_PAREN = re.compile(r"\d")

#: 开关 persona_action_narration 打开时：允许「一条很短的括号动作」跟着台词一起发。
#: 超过这个长度、跨行、或者整条只有动作，仍然按原来的规矩剥掉/丢弃——
#: 2026-11-22 的老问题是「56.5% 的消息带旁白、45.4% 的分段只有动作」，开关不能把它放回来。
MAX_INLINE_ACTION_CHARS = 16


def action_narration_enabled(plugin=None):
    """配置开关 persona_action_narration（默认关=全部剥掉）。口径统一在 amo_narration_policy。"""
    try:
        try:
            from .amo_narration_policy import enabled as _enabled
        except ImportError:  # 直接按文件加载时
            from amo_narration_policy import enabled as _enabled
        return bool(_enabled(plugin))
    except Exception as exc:  # cleanup: 配置读不到按关处理（零行为变化）
        logger.debug("[persona] 动作开关读取失败: %s", exc)
        return False


def _is_inline_action(body):
    """一句够短、单行、不带数字的括号动作（数字括号留给日期/价格，另有规则）。"""
    text = str(body or "").strip()
    if not text or len(text) > MAX_INLINE_ACTION_CHARS:
        return False
    if _KEEP_PAREN.search(text):
        return False
    return "（" not in text and "）" not in text and "(" not in text and ")" not in text

#: 未闭合「（」后面：先按句末标点切掉第一句动作，再看剩下的是不是像台词。
_SENTENCE_END = re.compile(r"[。！？!?…]+")
_DIALOGUE_PARTICLES = re.compile(r"[吧呢啊呀嘛哦啦哈么吗]")
_DIALOGUE_HINTS = re.compile(
    r"(晚安|早安|午安|睡吧|好梦|抱抱|想你|爱你|再见|拜拜|在吗|干嘛|干什么|"
    r"谢谢|对不起|没事|好的|嗯嗯|辛苦了)")


def _looks_like_spoken_line(text):
    """未闭合括号后的残余像不像一句台词（而不是继续的动作旁白）。

    只用于「括号没闭合」这一种残片：有称呼/告别/语气词/第二人称才算台词；
    动作续写（"然后站起来"/"转身去倒水"）没有这些标记，仍然丢掉。
    """
    body = str(text or "").strip()
    if len(body) < 2 or not re.search(r"[\u4e00-\u9fff]", body):
        return False
    if _DIALOGUE_HINTS.search(body):
        return True
    if re.search(r"[你他她我咱]", body) and _DIALOGUE_PARTICLES.search(body):
        return True
    if _DIALOGUE_PARTICLES.search(body) and re.search(r"[。！？!?…]$", body):
        return True
    return False


def _keep_speech_after_unclosed(body, open_index):
    """未闭合「（」：只丢第一句动作，保留后面像台词的部分。

    例：「（刚把盒子推远一点，好饱。晚安，明天还得早起呢。」
    → 「晚安，明天还得早起呢。」（2026-11-23 23:00 事故）
    """
    prefix = body[:open_index]
    tail = body[open_index + 1:]
    match = _SENTENCE_END.search(tail)
    if not match:
        return prefix
    rest = tail[match.end():].strip().lstrip("，,、；;：: ")
    if not _looks_like_spoken_line(rest):
        return prefix
    if prefix and not re.search(r"[。！？!?…，,；;：:]\s*$", prefix):
        prefix = prefix.rstrip() + "，"
    return (prefix + rest).strip()


def _strip_line(line, keep_action=False):
    def _replace(match):
        if _KEEP_PAREN.search(match.group(1)):
            return match.group(0)
        if keep_action and _is_inline_action(match.group(1)):
            return match.group(0)
        return ""

    body = _PAREN.sub(_replace, str(line or ""))
    # 分段器/模型可能把「（动作）」劈成两半：只剩全角闭括号的片段要丢到
    # 括回为止（「嘴角一下翘起来）我也想你了」→「我也想你了」）；只有开
    # 括号、没有闭括号的尾巴整段丢（「（听到这句话」→ 空）。
    _close = body.find("）")
    if _close >= 0 and "（" not in body[:_close] and "(" not in body[:_close]:
        body = body[_close + 1:]
    _open = body.find("（")
    if _open < 0:
        _open = body.find("(")
    if _open >= 0 and body.find("）", _open) < 0 and body.find(")", _open) < 0:
        body = _keep_speech_after_unclosed(body, _open)
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"^[\s，。、；：,.;:!?！？~～]+", "", body)
    body = re.sub(r"[\s，、；：,;:]+$", "", body)
    return body.strip()


def strip_action_narration(text, keep_action=False):
    """剥掉括号动作旁白；整条只有动作时返回空串（不发）。

    keep_action=True（配置 persona_action_narration 打开）时保留**一句很短的
    行内动作**；整条只有动作仍然返回空串，未闭合的残括号仍然剥掉。
    """
    lines = []
    for line in str(text or "").splitlines():
        cleaned = _strip_line(line, keep_action=keep_action)
        if cleaned:
            lines.append(cleaned)
    body = "\n".join(lines).strip()
    if body and not _PAREN.sub("", body).strip():
        # 只剩括号：动作整条不发；但「（11月24日）」这种数字括号改前是会发的，保持原样
        numeric_only = all(
            _KEEP_PAREN.search(match.group(1)) for match in _PAREN.finditer(body))
        if not numeric_only:
            return ""
    return body


_JUDGMENT_FINAL = re.compile(r"(够了|行了|就这样|随你|可以了|别说了)[。！!？?…]*$")
#: 上位者评词：不要求出现在句尾，出现即算判词（线上「这就对了，乖」）。
_JUDGMENT_PHRASES = ("这就对了", "听话")
_PARTICLES = ("呀", "呢", "嘛", "啦", "吧", "哦", "嗯", "啊", "哈")
_NATURALNESS_COUNTER: dict[str, int] = {}


def naturalness_issues(text, intent=""):
    """自然度检测（只判不改）：句尾判词 / 零语气词 / 固定模板句。

    第一版只做埋点，不触发重写；N2 再决定是否条件重生成。
    """
    body = str(text or "").strip()
    if not body:
        return ["empty"]
    issues = []
    if _JUDGMENT_FINAL.search(body) or any(word in body for word in _JUDGMENT_PHRASES):
        issues.append("judgment_final")
    if not any(word in body for word in _PARTICLES):
        issues.append("no_particle")
    try:
        try:
            from .domains.reply import repetition_guard as _reg
        except ImportError:
            from domains.reply import repetition_guard as _reg
        if _reg.is_fixed_rewrite(body):
            issues.append("template")
    except Exception as exc:  # cleanup: 注册表不可用就只留前两项
        logger.debug("[persona] naturalness 注册表不可用: %s", exc)
    return issues


def note_naturalness(text, intent=""):
    codes = naturalness_issues(text, intent)
    for code in codes:
        _NATURALNESS_COUNTER[code] = _NATURALNESS_COUNTER.get(code, 0) + 1
    return codes


def naturalness_stats():
    return dict(_NATURALNESS_COUNTER)


def has_orphan_paren(text):
    """出站不变量：剥掉成对括号后还残留全角括号，就是被切开的残片。"""
    body = _PAREN.sub("", str(text or ""))
    return "（" in body or "）" in body


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


# ---------------------------------------------------------------------------
# 姓名 / 物品 grounding（出站最后一道）
# ---------------------------------------------------------------------------


def _recent_context(plugin, umo):
    rows: list[Any] = []
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


_FAMILY_WORDS = ("妈妈", "爸爸", "我妈", "我爸")


def ground_names(plugin, umo, text):
    """名单里的名字在近聊素材里没出现 → 换成同圈层中性称呼（家人不再变同事）。"""
    body = str(text or "")
    if not body:
        return ""
    try:
        try:
            from .amo_names import NAME_FALLBACK as _NAME_FALLBACK
            from .amo_names import ROSTER
        except ImportError:
            from amo_names import NAME_FALLBACK as _NAME_FALLBACK
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


# ---------------------------------------------------------------------------
# 出站链：一个 runner + 三张 profile 表 + 三个薄壳（task-3a-3）
# ---------------------------------------------------------------------------
# prepare_outbound / sanitize_candidate 是同序清洗链，guard_candidate 只是
# 「候选还能不能原样还原」的事实探针。合并只共享 runner，不共享阶段：
# 探针拿到的是已经 sanitize 过的文本，再跑一遍媒体/旁白/禁语会二次改写探针
# 结果，让 amo_rewrite_safety._candidate_unsafe 的比较基准漂移。
#
# S0（trim）在 runner 内；S1..S13 是 13 个 stage 函数，契约统一：
#     _stage_xxx(ctx) -> str | None
# 原地写 ctx.text；返回非空 reason = 立即丢弃（后续 stage 一个都不跑）。
# 空串即丢弃：任何可能把文本变空的 stage 必须返回对应 reason。
# 异常策略写在 stage 内（outbound 金额上抛、candidate 金额吞），不得用公共 try 包住。
# import 仍在函数内：架构棘轮只数目标模块，模块级 import 会改 fanout 快照。


@dataclass
class _ChainCtx:
    """一次出站/候选/探针调用的上下文。"""

    plugin: Any
    umo: str
    text: str
    source: Any = "chat"
    user_text: str = ""
    profile: str = "outbound"
    #: outbound 专用：账本函数在 collapse 阶段取一次，amounts/collapse_tail 复用。
    ledger: Any = None


def _stage_media(ctx):
    """S1 媒体占位符：不是人话，统一出口的最后一道 strip。"""
    body = ctx.text
    if "我发的照片" in body or "我发的语音" in body:
        try:
            try:
                from .amo_message import strip_media_markers
            except ImportError:
                from amo_message import strip_media_markers
            body = strip_media_markers(body)
        except Exception as exc:  # cleanup: 兜底/降级，保持原行为，但必须留痕
            logger.debug("[persona] media 占位符清洗失败: %s", exc)
    ctx.text = body
    # 出站：剥空就是「没有内容」；候选：留给旁白阶段按 action_only 归因（不统一）。
    if not body and ctx.profile == "outbound":
        return "empty"
    return None


def _stage_collapse(ctx):
    """S2 重复前缀折叠（outbound 第一遍）+ 缓存账本函数。"""
    try:
        try:
            from .amo_memory_ledger import approximate_amounts, collapse_repeats
        except ImportError:
            from amo_memory_ledger import approximate_amounts, collapse_repeats
    except Exception:  # 账本模块缺失时只做本地卫生，不挡发送
        approximate_amounts = lambda value: value
        collapse_repeats = lambda value: value
    ctx.ledger = (approximate_amounts, collapse_repeats)
    ctx.text = collapse_repeats(ctx.text)
    return None


def _stage_narration(ctx):
    """S3 动作旁白：整条只有旁白就丢，行内旁白剥掉（开关打开时留一句短动作）。"""
    allow = action_narration_enabled(ctx.plugin)
    stripped = strip_action_narration(ctx.text, keep_action=allow)
    if not stripped:
        return "action_only"
    if stripped != ctx.text:
        ctx.text = stripped
    return None


#: 道具 grounding 的实现在 amo_fact_binder（唯一事实源），这里只转发；
#: 经 amo_message 暴露，避免 persona_guard 新增 current_action/food_delivery 依赖。
def ground_props(plugin, text):
    """没有 meal_facts/current_action 支撑时，删掉「盒子/饭盒/推远/在车上」分句。"""
    try:
        from .amo_message import ground_props as _ground
    except ImportError:
        from amo_message import ground_props as _ground
    return _ground(plugin, text)


def _stage_tech(ctx):
    """S4 技术词（candidate 专属：候选还原绕过了 amo_message 的 strip_tech_terms）。"""
    try:
        try:
            from .amo_shared_text import strip_tech_terms
        except ImportError:
            from amo_shared_text import strip_tech_terms
        ctx.text = str(strip_tech_terms(ctx.text, source="chat") or "").strip()
    except Exception as exc:  # cleanup: 技术词清洗失败就保持候选
        logger.debug("[persona] 技术词清洗失败: %s", exc)
    if not ctx.text:
        return "tech_terms"
    return None


def _stage_system(ctx):
    """S5 系统词：outbound 非 chat 整条丢、chat 先 humanize 再复判；candidate 只 humanize。"""
    if not _system_hit(ctx.text):
        return None
    if ctx.profile == "outbound" and ctx.source not in ("chat",):
        return "system_phrase"
    ctx.text = _humanize_system_sentences(ctx.text)
    if ctx.profile == "outbound" and _system_hit(ctx.text):
        return "system_phrase"
    return None


def _stage_forbidden(ctx):
    """S6 禁语：整句重写 → 残片丢句 →（仅出站）中性兜底。

    出站走 neutralize_forbidden：非空输入恒非空，所以下面的 forbidden_phrase
    正常不可达（测试钉住这条不变量，改坏时立刻变红）；
    候选走低阶 rewrite_forbidden：全丢仍返回空串，交给 recover 保持现行文本
    —— 候选链拿中性句会顶掉正常回复。
    """
    original = ctx.text
    if ctx.profile == "candidate":
        ctx.text, stats = rewrite_forbidden(original)
        _note_forbidden(stats, original, ctx.text)
    else:
        ctx.text = neutralize_forbidden(original, user_text=ctx.user_text)
    if not ctx.text:
        return "forbidden_phrase"
    return None


def _stage_amounts(ctx):
    """S7 精确数字大概化。outbound 异常上抛，candidate 吞掉保候选。"""
    if ctx.profile == "outbound":
        # 账本函数已在 S2 取到；运行时异常不吞（由调用方 fail-open 发原文）
        ctx.text = ctx.ledger[0](ctx.text)
        return None
    try:
        try:
            from .amo_memory_ledger import approximate_amounts
        except ImportError:
            from amo_memory_ledger import approximate_amounts
        ctx.text = approximate_amounts(ctx.text)
    except Exception as exc:  # cleanup: 数字大概化失败就不改
        logger.debug("[persona] 数字大概化失败: %s", exc)
    return None


def _stage_names(ctx):
    """S8 人名 grounding：没素材支撑的名字换中性称呼。"""
    ctx.text = ground_names(ctx.plugin, ctx.umo, ctx.text)
    return None


def _stage_anti_nag(ctx):
    """S9 反催问（candidate 专属：出站上游已跑，且需要 user_text）。"""
    try:  # 反催问：和真实链路同口径，候选里有催问句照样删
        try:
            from .amo_anti_nag import rewrite as _anti_nag
        except ImportError:
            from amo_anti_nag import rewrite as _anti_nag
        body = str(_anti_nag(ctx.text, str(ctx.user_text or "")) or "")
        if not body:
            return "anti_nag"  # 催问句删空：候选整条不安全，不许原样还原
        ctx.text = body
    except Exception as exc:  # cleanup: 反催问失败就保持
        logger.debug("[persona] 反催问失败: %s", exc)
    return None


def _stage_purchase(ctx):
    """S10 消费事实：钱包/物价退役后不再改写，保留阶段位以维持链序。"""
    return None


def _stage_collapse_tail(ctx):
    """S11 重复前缀折叠（outbound 第二遍）+ 空判。"""
    ctx.text = ctx.ledger[1](ctx.text).strip()
    if not ctx.text:
        return "empty_after_guard"
    return None


def _stage_fact_probe(ctx):
    """S13 事实探针（probe 专属）：只看候选还能不能原样还原，绝不进发送链。"""
    try:
        try:
            from .amo_reply_pipeline import apply_fact_guard as _fact_guard
        except ImportError:
            from amo_reply_pipeline import apply_fact_guard as _fact_guard
        ctx.text = str(_fact_guard(ctx.text, ctx.plugin) or ctx.text).strip()
    except Exception as exc:  # cleanup: 事实守卫失败就保持
        logger.debug("[persona] 事实探针失败: %s", exc)
    return None


#: 阶段实现表（名字 -> 函数）。判据/异常策略全在函数内，表只负责顺序。
def _stage_props(ctx):
    """道具 grounding：没有 meal_facts/current_action 支撑就删掉对应分句。"""
    body = ground_props(ctx.plugin, ctx.text)
    if not body:
        return "prop_ungrounded"
    ctx.text = body
    return None


_STAGES = {
    "media": _stage_media,
    "props": _stage_props,
    "collapse": _stage_collapse,
    "narration": _stage_narration,
    "tech": _stage_tech,
    "system": _stage_system,
    "forbidden": _stage_forbidden,
    "amounts": _stage_amounts,
    "names": _stage_names,
    "anti_nag": _stage_anti_nag,
    "purchase": _stage_purchase,
    "collapse_tail": _stage_collapse_tail,
    "fact_probe": _stage_fact_probe,
}

#: 三张阶段表（唯一事实源）。差异是「作用对象/失败语义不同」，不是历史遗留：
#: - outbound 没有 tech/anti_nag/purchase（出站上游已跑；候选还原绕过了那些闸）；
#: - candidate 没有 collapse（出站才有的两遍折叠）与 empty_after_guard；
#: - probe 只有 fact_probe：探针绝不共享任何清洗阶段。
#: 不变量：outbound 的 "collapse" 必须排在 "amounts"/"collapse_tail" 之前（账本函数在此缓存）。
_CHAIN = {
    "outbound": (
        "media", "collapse", "narration", "props", "system", "forbidden",
        "amounts", "names", "collapse_tail",
    ),
    "candidate": (
        "media", "narration", "props", "tech", "system", "forbidden",
        "amounts", "names", "anti_nag", "purchase",
    ),
    "probe": ("fact_probe",),
}


def _run_chain(plugin, umo, text, *, profile, source="chat", user_text=""):
    """唯一 runner：S0 trim -> 查表逐步 -> 首个非空 reason 立即短路。"""
    ctx = _ChainCtx(plugin=plugin, umo=umo, text=str(text or "").strip(),
                    source=source, user_text=user_text, profile=profile)
    if not ctx.text:
        return "", "empty"
    for name in _CHAIN[profile]:
        reason = _STAGES[name](ctx)
        if reason:
            return "", reason
    return ctx.text, ""


def prepare_outbound(plugin, umo, text, source="chat"):
    """统一出口前的语义卫生：返回 (text, reason)；reason 非空表示丢弃。"""
    return _run_chain(plugin, umo, text, profile="outbound", source=source)


def sanitize_candidate(plugin, umo, text, user_text=""):
    """候选优先还原前的「该做的安全改写」（task-40）。

    回放里守卫把「完整候选」换成固定兜底/多池拼装句，出口还原候选时不能把
    旁白/禁语/技术话术/精确金额/没素材的人名/错误收件人一起带回来，所以候选
    也要过一遍与真实链路同口径的安全改写。返回 (text, reason)。
    """
    body, reason = _run_chain(plugin, umo, text, profile="candidate", user_text=user_text)
    if reason:
        return "", reason
    return str(body or "").strip(), ""


def guard_candidate(plugin, umo, text, user_text=""):
    """候选的「安全探针」：跑事实守卫 + 场景/角色清洗，只看候选还能不能原样还原。

    候选优先不等于候选正确（task-41 交叉回归）：
    - B1「你发的那个视频…再发我一遍？」是角色反转，装饰链会收成「我在家呢。」；
    - C1 用户问「你在哪呢？」候选「我在商场逛街…」和日程冲突，会被收成场景短句。
    这两种候选不能靠 restore 又顶回去。这里返回守卫后的形态，由 amo_rewrite_safety
    判断「候选不安全」时改成守卫形态/保持现行文本。

    注意：probe 表只有 fact_probe —— 这里不跑媒体/旁白/禁语等清洗阶段，
    否则探针基准会漂移。返回裸 str（recover 的 guard= 回调依赖这一点）。
    """
    body, _reason = _run_chain(plugin, umo, text, profile="probe")
    return str(body or "").strip()


__all__ = [
    "FORBIDDEN_PHRASES", "forbidden_hits", "rewrite_forbidden", "neutralize_forbidden",
    "forbidden_stats", "reset_forbidden_stats", "prepare_generated_line", "strip_action_narration",
    "action_narration_enabled", "MAX_INLINE_ACTION_CHARS",
    "has_action_narration", "has_orphan_paren", "action_only", "action_ratio",
    "naturalness_issues", "note_naturalness", "naturalness_stats",
    "self_recent_texts", "self_recent_block", "denial_of_self_issue",
    "emotion_first_issue", "issues", "ground_names",
    "prepare_outbound", "sanitize_candidate", "guard_candidate",
]
