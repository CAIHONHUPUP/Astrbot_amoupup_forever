# -*- coding: utf-8 -*-
"""共享底座·文本：清洗、切分、情绪词、JSON 抽取。从 amo_shared.py 按域拆出，旧名仍在门面。"""
from __future__ import annotations

import json
import random
import re
from typing import Any, Iterable, TYPE_CHECKING
try:  # 可选依赖兜底（从 amo_shared 拆出）
    from .amo_shared_deps import (
    _note_swallowed,
        to_int,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared_deps import (
        _note_swallowed,
            to_int,
        )

POS_WORDS = ("开心", "高兴", "哈哈", "笑死", "好耶", "喜欢", "爱你", "可爱", "棒", "不错",
             "太好了", "谢谢", "嘿嘿", "嘻嘻", "么么", "抱抱", "亲亲", "好吃", "好看")


NEG_WORDS = ("难过", "伤心", "哭", "烦", "累", "讨厌", "滚", "闭嘴", "无语", "失望",
             "委屈", "孤单", "想死", "崩溃", "气死", "不开心")


ANGRY_WORDS = ("滚", "闭嘴", "傻", "废物", "烦死", "气死", "去死", "恶心", "别烦", "有病")


SAD_WORDS = ("难过", "伤心", "想哭", "委屈", "孤单", "没人", "算了", "失望", "哭")


AFFECTION_WORDS = ("喜欢", "爱你", "想你", "抱抱", "亲亲", "贴贴", "宝贝", "老婆", "宝宝", "么么")


INSULT_WORDS = ("傻逼", "智障", "废物", "垃圾", "去死", "恶心", "滚")


def lark_quote_kwargs(is_private: bool, message_id: Any, sender_id: Any, chat_id: Any, quote: Any) -> dict:
    """飞书发送参数：决定走「引用回复」还是普通发送。

    AstrBot 的飞书适配器在 `LarkMessageEvent.send()` 里固定传
    `reply_message_id=self.message_obj.message_id`，所以不管私聊群聊、也不管
    `platform_settings.reply_with_quote`，每条消息都会变成飞书里的「回复」，
    私聊看起来就是一直在引用他上一条。

    - 私聊：普通发送（receive_id = 他的 open_id），不引用；
    - 群聊且按概率判定要引用：reply；
    - 群聊但不引用：普通发送（receive_id = 会话 chat_id）；
    - 拿不到 open_id / chat_id 时退回原来的 reply，宁可引用也别发不出去。
    """
    message_id = str(message_id or "")
    sender_id = str(sender_id or "")
    chat_id = str(chat_id or "")
    if is_private and sender_id:
        return {
            "receive_id": sender_id,
            "receive_id_type": "open_id",
            "fallback_chat_id": chat_id or None,
        }
    if quote and message_id:
        return {"reply_message_id": message_id}
    if chat_id:
        return {"receive_id": chat_id, "receive_id_type": "chat_id"}
    if message_id:
        return {"reply_message_id": message_id}
    return {}


def other_platform_bot_lines(rows: Any, umo: Any, limit: int = 3, platform: str = "") -> list:
    """从跨平台近聊里挑出「她自己说的、且不是当前会话」的几句话。

    主动消息以前只看当前会话的最近 12 句：QQ 上 08:48 说「等散会了跟你说」，
    之后一直没人说话，12:00 的主动消息就接着那场会往下说；与此同时飞书那边
    早就是「在便利店买饭」了。把她在别的平台刚说的话也放进去，两边才不会
    各讲各的故事（内容冲突时以当前状态为准，见 gen_proactive 的规则）。

    数据插件有的行没有 `umo`，所以再按 `platform` 兜一层，避免把本平台的
    话当成「另一边说过」。
    """
    umo = str(umo or "")
    platform = str(platform or "")
    out = []
    for row in rows or []:
        if str(row.get("role") or "") != "bot":
            continue
        if str(row.get("umo") or "") == umo:
            continue
        row_platform = str(row.get("platform") or "")
        if row_platform and platform and row_platform == platform:
            continue
        text = str(row.get("content") or "").strip().replace("\n", " ")
        if text:
            out.append(text)
    size = max(1, int(limit or 1))
    return out[-size:]


def truncate(text: Any, limit: int = 120) -> str:
    text = str(text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def uniq(items: Iterable[Any]) -> list:
    out = []
    seen = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def looks_like_plain_command(text: Any) -> bool:
    text = (text or "").strip()
    return text.startswith("/") or text.startswith("／")


def has_question(text: Any) -> bool:
    if "?" in text or "？" in text:
        return True
    return any(word in text for word in (
        "吗", "呢", "怎么", "为什么", "啥", "什么", "哪", "多少", "几",
        "是不是", "能不能", "可不可以", "如何", "几点", "谁",
    ))


def sentiment(text: Any) -> dict:
    text = text or ""
    pos = sum(1 for word in POS_WORDS if word in text)
    neg = sum(1 for word in NEG_WORDS if word in text)
    angry = sum(1 for word in ANGRY_WORDS if word in text)
    sad = sum(1 for word in SAD_WORDS if word in text)
    aff = sum(1 for word in AFFECTION_WORDS if word in text)
    total = pos + neg
    score = 0.0 if total == 0 else (pos - neg) / float(total)
    return {
        "pos": pos, "neg": neg, "angry": angry, "sad": sad,
        "aff": aff, "insult": any(w in text for w in INSULT_WORDS),
        "score": score,
    }


_WORD_RE = re.compile(r"[a-zA-Z0-9_]{2,}")


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokens(text: Any) -> set:
    text = (text or "").lower()
    out = set()
    for word in _WORD_RE.findall(text):
        out.add(word)
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
        for i in range(len(run) - 2):
            out.add(run[i:i + 3])
    return out


def overlap(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(max(1, min(len(a), len(b))))


def json_extract_list(text: Any) -> list:
    """给“要一个数组”的场景用：任何情况下都返回 list，绝不抛异常（解析失败返回 []）。"""
    try:
        if not text:
            return []
        raw = str(text).replace("\ufeff", "").replace("\u200b", "")
        raw = re.sub(r"```[a-zA-Z0-9_-]*", "", raw).replace("```", "").strip()
        if not raw:
            return []
        start = raw.find("[")
        if start >= 0:
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(raw)):
                char = raw[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "[":
                    depth += 1
                elif char == "]":
                    depth -= 1
                    if depth == 0:
                        chunk = re.sub(r",\s*([}\]])", r"\1", raw[start:index + 1])
                        try:
                            data = json.loads(chunk)
                            if isinstance(data, list):
                                return data
                        except Exception:
                            break
        data = json_extract(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.json_extract_list", _amo_exc)
        return []


def json_extract(text: Any) -> Any:
    if not text:
        return None
    raw = str(text).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        if start < 0:
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(raw)):
            char = raw[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    chunk = raw[start:index + 1]
                    chunk = re.sub(r",\s*([}\]])", r"\1", chunk)
                    try:
                        return json.loads(chunk)
                    except Exception as _amo_exc:
                        _note_swallowed("amo_shared.json_extract", _amo_exc)
                        break
    return None


CHAT_FORMAT_NOISE = re.compile(
    r"@\s*([^\s（(]+)\s*[（(]\s*QQ\s*[:：]?\s*\d+\s*[）)]"
    r"|[（(]\s*QQ\s*[:：]?\s*\d+\s*[）)]"
    r"|\bQQ\s*[:：]?\s*\d{4,}\b"
    r"|@\s*([^\s，。！？!?、；;：:]+)",
    re.I,
)


def strip_chat_format(text: Any) -> str:
    """去掉模型复述的 @昵称(QQ号)、QQ号等后台格式，保留自然称呼。"""
    value = str(text or "")
    value = CHAT_FORMAT_NOISE.sub(lambda m: (m.group(1) or m.group(2) or "").strip(), value)
    value = re.sub(r"[（(]\s*[）)]", "", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    return value.strip()


_HTML_TAG = re.compile(
    r"</?(?:div|p|span|br|b|i|u|s|strong|em|ul|ol|li|a|img|table|thead|tbody|"
    r"tr|td|th|h[1-6]|section|article|blockquote|code|pre|font|center)\b[^>]*>",
    re.I,
)
_HTML_ANY_CLOSE = re.compile(r"</[a-zA-Z][a-zA-Z0-9]{0,15}\s*>")
_HTML_ENTITIES = (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                  ("&quot;", '"'), ("&#39;", "'"))


def strip_html_noise(text: Any) -> str:
    """去掉模型漏出来的 HTML 标签/实体（2026-11-20 用户反馈句尾 `</div>`）。"""
    value = str(text or "")
    for old, new in _HTML_ENTITIES:
        value = value.replace(old, new)
    value = _HTML_TAG.sub("", value)
    value = _HTML_ANY_CLOSE.sub("", value)
    # 只剩一个孤立的 `<div` / `</div`（被截断）也清掉
    value = re.sub(r"</?[a-zA-Z][a-zA-Z0-9]{0,15}\s*$", "", value)
    return value


def clean_reply(text: Any, limit: int = 300) -> str:
    if not text:
        return ""
    text = strip_chat_format(str(text).strip())
    text = re.sub(r"<thought>.*?</thought>", "", text, flags=re.S | re.I)
    text = re.sub(r"<DSML>.*?</DSML>", "", text, flags=re.S | re.I)
    text = re.sub(r"</?(?:thought|DSML|analysis|tool_call)>", "", text, flags=re.I)
    text = strip_html_noise(text)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"^(?:Amo|amo|核心|我|助手|assistant|AI|机器人)\s*[:：]\s*", "", text)
    if len(text) >= 2 and text[0] in "\"'“‘" and text[-1] in "\"'”’":
        text = text[1:-1]
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


_PAIRS = {
    "《": "》", "〈": "〉", "“": "”", "‘": "’", "「": "」", "『": "』",
    "（": "）", "(": ")", "【": "】", "〔": "〕", "[": "]", "{": "}",
}


_STRONG = "。！？!?…\n"


_WEAK = "，,、；;：:~～"


_TRIM = " \t\r\n。！？!?…，,、；;：:~～"


def _protect_mask(text):
    length = len(text)
    mask = [False] * length

    def mark(start, end):
        for index in range(max(0, start), min(length, end)):
            mask[index] = True

    for match in re.finditer(r"```.*?```", text, re.S):
        mark(match.start(), match.end())
    for match in re.finditer(r"`[^`\n]+`", text):
        mark(match.start(), match.end())
    for match in re.finditer(r"https?://[^\s]+", text):
        mark(match.start(), match.end())

    index = 0
    while index < length:
        char = text[index]
        closer = _PAIRS.get(char)
        if closer:
            depth = 1
            scan = index + 1
            while scan < length:
                if text[scan] == char:
                    depth += 1
                elif text[scan] == closer:
                    depth -= 1
                    if depth == 0:
                        break
                scan += 1
            if scan < length:
                mark(index, scan + 1)
                index = scan + 1
                continue
            mark(index, length)
            break
        index += 1
    return mask


def _cut(text, mask, delimiters, min_keep=0):
    parts = []
    start = 0
    for index, char in enumerate(text):
        if mask[index]:
            continue
        if char in delimiters and (index + 1 - start) >= min_keep:
            parts.append(text[start:index + 1])
            start = index + 1
    parts.append(text[start:])
    return parts


def _protected_ranges(text):
    mask = _protect_mask(text)
    ranges = []
    start = None
    for index, flag in enumerate(mask):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(mask)))
    return ranges


def _mask_with_placeholders(text):
    ranges = _protected_ranges(text)
    if not ranges:
        return text, {}
    pieces = []
    placeholders = {}
    last = 0
    for index, (start, end) in enumerate(ranges):
        pieces.append(text[last:start])
        token = "\x00%d\x00" % index
        placeholders[token] = text[start:end]
        pieces.append(token)
        last = end
    pieces.append(text[last:])
    return "".join(pieces), placeholders


def _split_regex(text, pattern, protect_pairs=True):
    if not pattern:
        return None
    masked, placeholders = _mask_with_placeholders(text) if protect_pairs else (text, {})
    try:
        compiled = re.compile(pattern)
    except re.error:
        return None
    pieces = []
    last = 0
    for match in compiled.finditer(masked):
        end = match.end()
        if end <= last:
            continue
        pieces.append(masked[last:end])
        last = end
    pieces.append(masked[last:])
    restored = []
    for piece in pieces:
        value = piece
        for token, original in placeholders.items():
            value = value.replace(token, original)
        value = value.strip()
        if value:
            restored.append(value)
    return restored


def segment_text(text, keep_punc=True, min_len=6, max_len=42, max_segments=4,
                 mode="punct", split_chars="", regex_pattern="", protect_pairs=True):
    """按标点符号或正则分段；成对符号、代码块、URL 内部不会被切开。

    mode="punct" 时按 segment_split_chars + 内置标点分段；
    mode="regex" 时按 segment_regex 分段，失败自动回退到标点模式。
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    min_len = max(1, to_int(min_len, 6))
    max_len = max(min_len + 1, to_int(max_len, 42))
    max_segments = max(1, to_int(max_segments, 4))

    pieces = None
    if str(mode).lower() == "regex" and regex_pattern:
        pieces = _split_regex(text, regex_pattern, protect_pairs)
    if pieces is None:
        extra = "".join(char for char in str(split_chars or "") if char not in "\r\n")
        strong = "".join(dict.fromkeys(_STRONG + extra))
        mask = _protect_mask(text) if protect_pairs else [False] * len(text)
        pieces = []
        for part in _cut(text, mask, strong):
            part = part.strip()
            if not part:
                continue
            if len(part) > max_len:
                sub_mask = _protect_mask(part) if protect_pairs else [False] * len(part)
                pieces.extend(_cut(part, sub_mask, _WEAK, min_keep=max(4, max_len // 2)))
            else:
                pieces.append(part)

    normalized = []
    for part in pieces or []:
        part = str(part).strip()
        if not part:
            continue
        if len(part) > max_len:
            sub_mask = _protect_mask(part) if protect_pairs else [False] * len(part)
            normalized.extend(_cut(part, sub_mask, _WEAK, min_keep=max(4, max_len // 2)))
        else:
            normalized.append(part)
    # 没有可断标点的超长块（长英文/数字串）按长度硬切，保证 max_len 真的生效；
    # 链接、成对符号、代码块内部不切，宁可超长也不打断。
    capped = []
    for part in normalized:
        if len(part) <= max_len or (protect_pairs and _protected_ranges(part)):
            capped.append(part)
            continue
        capped.extend(part[start:start + max_len] for start in range(0, len(part), max_len))
    pieces = capped

    segments = []
    for part in pieces:
        seg = part.strip()
        if not keep_punc:
            seg = seg.strip(_TRIM)
        if seg:
            segments.append(seg)
    if not segments:
        return []

    index = 0
    while len(segments) > 1 and index < len(segments):
        if len(segments[index]) < min_len:
            if index + 1 < len(segments):
                segments[index + 1] = segments[index] + segments[index + 1]
                del segments[index]
                continue
            segments[index - 1] = segments[index - 1] + segments[index]
            del segments[index]
            break
        index += 1

    while len(segments) > max_segments:
        best = 0
        best_len = None
        for cursor in range(len(segments) - 1):
            total = len(segments[cursor]) + len(segments[cursor + 1])
            if best_len is None or total < best_len:
                best_len = total
                best = cursor
        segments[best] = segments[best] + segments[best + 1]
        del segments[best + 1]

    return [seg for seg in segments if seg][:max_segments]


AI_ADMISSION_RE = re.compile(
    r"(?:我是|作为|毕竟|本人|本机)\s*(?:AI|人工智能|机器人|人机|程序|模型)"
    r"|(?:只是|不过是|就是个|只是个)\s*(?:AI|人工智能|机器人|人机|程序|模型)"
    r"|人机(?:也|当然|本来|就是)"
    r"|不是真人|虚拟(?:人|角色|存在)"
    r"|(?:我|咱|我们)(?:其实|本质上|说到底|严格来说)?(?:就是|不过是|只是个)"
    r"\s*(?:AI|人工智能|机器人|人机|程序|语言模型|大模型)",
    re.I,
)


def looks_like_ai_admission(text: Any) -> bool:
    return bool(AI_ADMISSION_RE.search(str(text or "")))


# 她不能报后台：模型名、厂商、插件名、API、提示词这类都不该出现在聊天里
MODEL_LEAK_RE = re.compile(
    r"(?:deepseek|openai|anthropic|claude|gemini|chatgpt|gpt-?[0-9]|qwen|通义|千问|"
    r"glm|chatglm|智谱|moonshot|kimi|doubao|豆包|ernie|文心|hunyuan|混元|llama|mistral|"
    r"grok|sonnet|opus|haiku|dp-?[0-9]|llm|大模型|语言模型|模型名称|api\s*key|"
    r"token|提示词|prompt|system\s*prompt|插件|astrbot)",
    re.I,
)


DEFENSIVE_REPLY_RE = re.compile(
    r"(?:你才|你才是|谁家|哪有|我才不是)[^。！？]{0,12}(?:人机|机器人|AI|人工智能)"
    r"|(?:人机|机器人)(?:才|也|怎么|哪有)",
    re.I,
)


def looks_like_defensive_reply(text: Any) -> bool:
    """是不是在跟人争“我不是人机”——技术讨论里这种话很突兀。"""
    return bool(DEFENSIVE_REPLY_RE.search(str(text or "")))


def looks_like_model_leak(text: Any) -> bool:
    """回复里有没有把模型名/厂商/插件/API 这类后台信息说出来。"""
    raw = str(text or "")
    if not raw:
        return False
    # 只有短回答或带“模型/接/用/是”这类上下文才算泄漏，避免误伤正常聊天
    if not MODEL_LEAK_RE.search(raw):
        return False
    if len(re.sub(r"\s+", "", raw)) > 60:
        return False
    return True



# ---------------------------------------------------------------------------
# 出站技术词黑名单（2026-11-22 task-26）
#
# 现场（work/audit/cross_plugin.md §3.1）：聊天里出现过「可能是服务器那边卡住了」
# 「一直报错」「网络连接超时了」「说我没权限用这个」「详情在面板」。生成侧把工具
# 返回/异常当台词，发送侧旧守卫 looks_like_model_leak 只认模型名/插件名，且只对
# <=60 字生效。这里补一张统一的「不能说给人听」的技术词表 + 出口清洗：
# - chat：删掉命中技术词的分句；整条都不可说时换成一句角色化短句；
# - 系统通知 / push：整条只剩技术话时直接不发（返回 ""），不再让运维口吻进聊天；
# - 照片 caption：命中就丢说明、只发图（不把半句话硬切出去）。
# 词表是唯一事实源：聊天、主动、push、命令、照片说明都调这里，不许各写一份。
# ---------------------------------------------------------------------------

#: 一眼就是工程后台的词：命中即整句不可说。
TECH_HARD_TERMS = (
    "服务器", "后台", "接口", "报错", "超时", "权限", "面板", "异常", "重试",
    "网络连接", "数据库", "日志", "错误码", "节点", "降级", "软件", "系统",
    "traceback", "timeout", "exception", "api key", "error code",
)

#: 「失败 / 卡住」日常也可能说（减肥失败、门卡住了）：只有同句带技术上下文才算后台话。
TECH_SOFT_TERMS = ("失败", "卡住")
_TECH_CONTEXT_RE = re.compile(
    r"(?:图|照片|拍|发|加载|连接|请求|工具|生成|运行|出图|上传|下载|"
    r"消息|服务|页面|模型|接口|网络)"
)

#: 整条只剩技术话时的角色化兜底（按来源区分；push/system/command 直接不发）。
TECH_FALLBACKS = {
    "chat": "唔…这个先不说了，陪我聊点别的嘛。",
    "photo": "这张没拍好，等我重拍，好不好？",
    "system": "",
    "push": "",
    "command": "",
}

_SENTENCE_KEEP_RE = re.compile(r"[^。！？!?\n]*(?:[。！？!?]+|\n|$)")


def _tech_hits(text: Any, include_soft: bool = True) -> list:
    """一句/一段话里命中的技术词（硬词全算，软词要有技术上下文）。"""
    body = str(text or "")
    if not body:
        return []
    lowered = body.lower()
    hits = [word for word in TECH_HARD_TERMS if word in lowered]
    if not hits and include_soft:
        for word in TECH_SOFT_TERMS:
            if word in body and _TECH_CONTEXT_RE.search(body):
                hits.append(word)
    return hits


def looks_like_tech_talk(text: Any) -> bool:
    """这句话里有没有不该对用户说的后台/工程词。"""
    return bool(_tech_hits(text))


def strip_tech_terms(text: Any, source: str = "chat", kind: str = "") -> str:
    """删掉命中技术词的分句；剩下的都是人话才返回，全命中时按来源兜底/不发。

    - source=photo 或 kind 含 caption/photo：命中就整条 caption 丢掉（返回 ""）；
    - source=chat（默认）：全命中返回一句角色化短句；
    - source=system/push/command：全命中返回 ""（宁可不发，也不把后台话发出去）。
    """
    body = str(text or "")
    if not body.strip():
        return body
    src = str(source or "chat").strip().lower()
    name = str(kind or "").strip().lower()
    # 命令/管理员通道只用硬词（「统计：发 3｜失败 0」这种正常统计不该被删）。
    include_soft = src not in ("command", "admin")
    if src == "photo" or "caption" in name or name in ("photo", "draw"):
        return "" if _tech_hits(body, include_soft) else body
    kept = []
    for match in _SENTENCE_KEEP_RE.finditer(body):
        sentence = match.group(0)
        if not sentence:
            continue
        if sentence.strip() and _tech_hits(sentence, include_soft):
            continue
        kept.append(sentence)
    fixed = "".join(kept).strip()
    fixed = re.sub(r"\n{3,}", "\n\n", fixed)
    if fixed:
        return fixed
    if src in ("system", "push", "command", "admin"):
        return ""
    return TECH_FALLBACKS.get(src, TECH_FALLBACKS["chat"])


def sanitize_caption(text: Any) -> str:
    """照片说明：命中技术词就整条丢（图照发），不切半句。"""
    return strip_tech_terms(text, source="photo", kind="caption")


def typing_delay_ms(text: Any, base: int = 700, per_char: int = 400, cap: int = 20000) -> int:
    """按回复字数模拟打字时间，越长的回复停顿越久；标点再补一点。"""
    clean = re.sub(r"\s+", "", str(text or ""))
    chars = len(clean)
    pauses = (
        clean.count("。") + clean.count("！") + clean.count("？")
        + clean.count("!") + clean.count("?") + clean.count("…")
    )
    commas = clean.count("，") + clean.count(",") + clean.count("、")
    value: float = float(base + chars * per_char + pauses * 70 + commas * 35)
    value = min(value, float(cap))
    value = max(200, value * random.uniform(0.85, 1.2))
    return int(value)


def clean_ui_value(value, depth=0):
    """前端兜底：NaN/None/Tuple/Traceback 等绝不出现在 JSON 界面里。"""
    if depth > 8:
        return ""
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, str)):
        text = str(value)
        if text in ("None", "NaN", "nan", "inf", "-inf", "Tuple", "Error"):
            return ""
        if "Traceback" in text or "Exception" in text:
            return ""
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return 0
        return value
    if isinstance(value, dict):
        return {str(k): clean_ui_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean_ui_value(v, depth + 1) for v in value]
    return str(value)


def extract_persona_name(persona: Any) -> str:
    """从 AstrBot Personality 的 name / system prompt 中取角色名。"""
    if not persona:
        return ""
    name = ""
    prompt = ""
    try:
        name = str(persona.get("name") or "").strip()
        prompt = str(persona.get("prompt") or "")
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.extract_persona_name", _amo_exc)
        name = str(getattr(persona, "name", "") or "").strip()
        prompt = str(getattr(persona, "prompt", "") or "")
    generic = {"default", "assistant", "系统默认", "默认", "bot", "ai"}
    if name and name.lower() not in generic and not re.fullmatch(r"[0-9a-fA-F-]{8,}", name):
        return name[:30]
    patterns = (
        r"你的名字(?:是|叫|为)\s*[「“\"']?([\u4e00-\u9fa5A-Za-z0-9_·]{1,16})",
        r"你叫(?:做)?\s*[「“\"']?([\u4e00-\u9fa5A-Za-z0-9_·]{1,16})",
        r"你名叫\s*[「“\"']?([\u4e00-\u9fa5A-Za-z0-9_·]{1,16})",
        r"名为\s*[「“\"']?([\u4e00-\u9fa5A-Za-z0-9_·]{1,16})",
        r"你是\s*[「“\"']?([\u4e00-\u9fa5A-Za-z0-9_·]{1,16})",
    )
    blocked = {"一个", "一名", "一位", "个人", "人", "你", "我", "他", "她", "这个", "那个",
               "ai助手", "助手", "机器人", "人工智能", "ai"}
    for pattern in patterns:
        for match in re.finditer(pattern, prompt):
            candidate = str(match.group(1) or "").strip("「」“”\"' ")
            if (
                candidate and candidate.lower() not in blocked
                and not candidate.startswith(("一个", "一名", "一位", "个", "人"))
            ):
                return candidate[:30]
    return ""




#: 日摘里「谁在说」的称呼词：以这些开头的句子补主语时不能直接拼「他」
_DIGEST_ADDRESS = ("老公", "老婆", "亲爱的", "宝贝", "宝宝")


def digest_subject(piece):
    """给摘要的一句补主语；以称呼开头的句子换成「他……」。

    以前只要句子不是「他/我/她/用户」开头就机械补一个「他」，
    「老公说以后回来和我一起住」被写成「他老公说以后回来和我一起住」——
    读起来像「她老公」，主体就错了。
    """
    text = str(piece or "").strip()
    if not text:
        return text
    for address in _DIGEST_ADDRESS:
        if text.startswith(address):
            rest = text[len(address):].lstrip("，,、!！?？ \t")
            return "他" + (rest or "说")
    if text.startswith(("他", "我", "她", "用户")):
        return text
    return "他" + text
