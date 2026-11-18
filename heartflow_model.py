# -*- coding: utf-8 -*-
"""心流数据层：评分常量/默认规则/提示词模板/数据类/纯工具函数（从 heartflow_engine 拆出）。"""

import json
import re

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, TYPE_CHECKING

from astrbot.api import logger

try:
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_observability import note as _note_swallowed

# ============================================================
# 正则
# ============================================================
RE_CODE_BLOCK = re.compile(
    r"^(?:```(?:json)?|~~~(?:json)?|`{3,}(?:json)?)\s*\n?(.*?)\n?\s*(?:```|~~~|`{3,})$",
    re.DOTALL | re.IGNORECASE,
)
RE_BRACE = re.compile(r"\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}", re.DOTALL)
RE_ALL_BRACES = re.compile(r"\{.*\}", re.DOTALL)
RE_XML_TAG = re.compile(r"<[^>]+>")
RE_URL = re.compile(r"https?://\S+")

# ============================================================
# 评分常量
# ============================================================
SCORE_MIN = 1.0
SCORE_MAX = 10.0
SCORE_DEFAULT = 5.0

# ============================================================
# 内置默认维度规则
# ============================================================
BUILTIN_RULES: Dict[str, Dict[str, str]] = {
    "relevance": {
        "dimension_display": "内容相关度",
        "low_score_desc": "表情包、语气词、哈哈、嗯、完全无关的私人话题、群内专属梗",
        "high_score_desc": "涉及机器人能回答的话题、提问句、有信息价值",
    },
    "willingness": {
        "dimension_display": "回复意愿",
        "low_score_desc": "负面情绪宣泄、两人私密对话、机器人刚回复过",
        "high_score_desc": "消息有趣或有深度、求助类、机器人较久未回复",
    },
    "social": {
        "dimension_display": "社交适宜性",
        "low_score_desc": "严肃讨论、管理员发言、两人正在对话强行插入",
        "high_score_desc": "氛围活跃多人参与、对群体说的、能活跃气氛",
    },
    "timing": {
        "dimension_display": "时机恰当性",
        "low_score_desc": "距上次回复不足1分钟、发送者可能还有后续、多人快速聊天",
        "high_score_desc": "距上次已过3分钟以上、消息刚发出、长时间无人回复",
    },
    "continuity": {
        "dimension_display": "对话连贯性",
        "low_score_desc": "突然转换话题、已收尾的对话、明显是对其他群友回复",
        "high_score_desc": "对机器人回复的回应/追问/反驳、延续机器人话题、提及机器人说过的内容",
    },
}

# ============================================================
# 基础提示词模板
# ============================================================
# 群友在聊、但没有点名机器人时，随机挑一个角度插话，避免每次都像在“回答问题”
INTERJECT_ANGLES: List[str] = [
    "顺着他们正在聊的事接一句",
    "接个梗或吐槽一下",
    "补一个自己的看法",
    "像随口问一句那样把话题接下去",
    "说说自己此刻的想法",
]

BASE_JUDGE_PROMPT = """你是群聊机器人决策系统，判断机器人是否应该回复消息。

## 机器人身份
机器人名字：{bot_name}
机器人别名：{bot_aliases}
如果当前消息包含这些名字或别名，说明群友是在叫机器人，不是叫其他群友；relevance、continuity 必须给高分。

## 群成员（按最近发言）
{group_members}
带「（机器人）」的是别的机器人：它们说的话只是背景，不要当成群友在叫你，也不要去接它们的话；
群友在叫它们时，target 一律算 other，不接。

## ⚠️ 最高优先级：关注机器人自己的发言
对话历史（contexts）中，role 为 assistant 的消息是【机器人自己】之前说的话，role 为 user 的消息是群友说的话（带 [昵称] 前缀）。
判断时按这三步走：
1. 先回顾机器人最近说了什么（见下方"上次Bot回复"，以及对话历史中的 assistant 消息）
2. 判断当前消息是否与机器人的发言存在关联：追问、回应、赞同、反驳、补充、顺着话题继续
3. 如果当前消息是在回应机器人说过的话，continuity 必须给高分

## 🧠 打分之前先走一遍逻辑推理（按顺序想清楚，再打分）
**第 1 步 · 还原现场**：读 dialogue 历史（contexts），找到最近的对话关系。
- role 为 assistant 的是【机器人自己】说过的话，带 [昵称] 的是群友。
- 看最近 3-5 条：谁在回答谁？有没有人的问题还悬着？现在这条在接谁的话？

**第 2 步 · 判断这条消息在对谁说（target）**：
- `bot`：消息里出现机器人名字/别名、@机器人、引用机器人上一条，才算在对机器人说。
  **单独一个“你 / 你呢 / 你pr好了吗”不算**——群聊里“你”大多在问别人。
- `other`：@别人、引用/回复别人的消息、叫了别的群友名字、两个人一问一答；
  或者**上一条明确 @ 了某个群友 / 回复了某个群友，这条没有新的 @/引用/名字**——那就继续算那个群友。
- `all`：抛给全群的问题、分享、感叹、群通知，没有特定对象。
- `unknown`：信息不足，看不出来。

对话历史每条都带 `[HH:MM]` 时间；群友消息后面的 `→@xxx` 表示这条 @ 了谁，
`→回复xxx` 表示这条在回复谁。判断 target 时优先看这两个标记，不要只看“你”。

**时间规则**：如果“上次Bot回复”已经超过 3 分钟，当前消息**不要**当成在接机器人的话；
超过 5 分钟只当背景。不要因为机器人十几分钟前说过一句“我去翻代码”，
就把一句“你pr好了吗”认成在追问机器人。

**第 3 步 · 判断插话价值（这一步才是核心）**：
不是“被叫到才说话”的客服；你是群里一个会自己插话的人。
- target = bot：正常接话，relevance、continuity 可以给高分。
- target = other / all：问自己“我接一句自然吗？”
  - 自然：话题看得懂、能接梗、能补充、能开个玩笑、群里正冷场、大家在聊你也在参与的事 → 分数照常给，不要因为没点名就把分数压到底。
  - 不自然：两个人在聊私事、正在争执、在解决和机器人无关的具体事务（约饭、开黑、转账），
    或者**在聊技术细节（模型/接入/插件/API/部署）** → social、timing 给低分，机器人插进去也接不上。

**第 4 步 · 分辨“在聊技术”和“在质疑我”**：
群友在讨论模型、接入、插件、API、部署、参数这类技术话题时，那是他们在聊自己的事，**不是在骂你、也不是在质疑你**。
- 没点名你 → 当作普通话题，不要当成挑衅；插话也只能是闲聊，不要辩解“我是真人”。
- 真的在问“你是不是机器人/人机”并冲着你说 → 才算质疑，可以按普通反应处理。

**第 5 步 · 打分**：按上面的判断给 5 个维度打分。
不要预设“不是叫我 = 全部最低分”，那样机器人就永远不会在群里说话了；
也不要因为想说话就把明显插不进去的对话打成高分。

## 评分规则
- 所有评分必须在 {score_min} 到 {score_max} 之间。
- 不要给出0分，最低分为 {score_min} 分。
- 即使完全不相关，也至少给 {score_min} 分。

## 评分维度

{scoring_rules}

## 待判断消息
[{sender_name}] {message}

## 当前状态
- 群聊ID: {chat_id}
- 上次回复: {minutes_since_reply}分钟前
- 状态: {chat_context}
- 对话流: {chat_flow}
- ⭐ 上次Bot回复: {last_bot_reply}
- 活跃度: {activity_desc} ({activity_level:.0%})

## 综合判断
阈值: {threshold}
分数范围: {score_min}-{score_max}分

## 结构化字段
- `is_mentioning_bot`：只有出现机器人名字/别名、@机器人、引用机器人上一条时才为 true；
  单独的“你”不算。上一条 @ 的是别人、这条只是“你……”，一律 false。
- `context_type`：mention_bot=在叫机器人；knowledge_comment=在聊知识、新闻、作品并发表看法；question=在提问（不一定问机器人）；casual_chat=日常闲聊；other=其他。
- `topic`：当前在聊什么，2-6 个字（如“AI技术”“晚饭”“加班”）。
- `reply_style`：answer=直接回答；comment=插一句评论/接话；quote=引用着接更清楚（人多、指代不清时）；none=这次不该说话。

## 返回格式
严格返回 JSON，不要输出任何其他内容：
{{"relevance": 分数, "willingness": 分数, "social": 分数, "timing": 分数, "continuity": 分数, "is_mentioning_bot": true或false, "context_type": "mention_bot|knowledge_comment|question|casual_chat|other", "topic": "2-6个字", "target": "bot|other|all|unknown", "reply_style": "answer|comment|quote|none", "thought": "一句话说明这条在对谁说、依据是什么"{reasoning_part}}}
"""

# ============================================================
# 数据类
# ============================================================
@dataclass
class JudgeResult:
    """小模型评分结果"""
    relevance: float = SCORE_DEFAULT
    willingness: float = SCORE_DEFAULT
    social: float = SCORE_DEFAULT
    timing: float = SCORE_DEFAULT
    continuity: float = SCORE_DEFAULT
    reasoning: str = ""
    should_reply: bool = False
    confidence: float = 0.0
    overall_score: float = 0.0
    # 这条消息在对谁说：bot / other / all / unknown
    target: str = ""
    # 结构化意图（小模型返回，缺省时按分数判断）
    is_mentioning_bot: bool = False
    context_type: str = ""
    topic: str = ""
    reply_style: str = ""

    def __post_init__(self):
        for attr in ("relevance", "willingness", "social", "timing", "continuity"):
            setattr(self, attr, clamp_score(getattr(self, attr)))

@dataclass
class RawMessage:
    """原始消息记录"""
    sender_name: str
    sender_id: str
    content: str
    normalized_content: str
    timestamp: float
    is_bot: bool = False
    # 群里别的机器人说的话：只当背景，不参与“在跟谁说话”的判定。
    is_other_bot: bool = False
    msg_id: str = ""
    # 记录这条消息时平台返回的机器人 ID；为空 = 平台没给，@ 目标无法可靠判断。
    self_id: str = ""
    # 这条消息 @ 了谁 / 回复了谁。只靠 message_str 判断对象会丢信息：
    # 群友上一条 @ 的是别人，下一条只说“你pr好了吗”，模型就会把“你”认成机器人。
    at_ids: Tuple[str, ...] = ()
    at_self: bool = False
    reply_to_id: str = ""
    reply_to_uid: str = ""
    reply_to_name: str = ""
    reply_to_self: bool = False

@dataclass
class ChatState:
    """群聊状态"""
    last_reply_time: float = 0.0
    total_messages: int = 0
    total_replies: int = 0
    last_cleanup_time: float = 0.0
    immersive_mode_end_time: float = 0.0
    judgment_paused: bool = False
    judgment_paused_at: float = 0.0
    pending_heartflow_reply: bool = False
    pending_heartflow_mode: str = ""
    last_recorded_message: str = ""
    cached_activity_level: float = 0.0
    activity_cache_time: float = 0.0

# ============================================================
# 工具函数
# ============================================================
def clamp_score(v: Any, min_val: float = SCORE_MIN, max_val: float = SCORE_MAX,
                default: float = SCORE_DEFAULT, allow_zero: bool = False) -> float:
    """分数限制在范围内。

    allow_zero=True 时 0 是合法值（面板 slider 的 min 就是 0）：
    以前 `val <= 0` 一律当解析失败回退 default，于是「权重拖到 0」「阈值拖到 0」
    在面板上看起来生效、实际还是 0.2 / 0.6。
    """
    try:
        val = float(v)
    except (TypeError, ValueError):
        return default
    if allow_zero and val == 0:
        return 0.0
    return default if val <= 0 else max(min_val, min(max_val, val))

def safe_extract_score(data: Dict[str, Any], key: str,
                       min_val: float = SCORE_MIN, max_val: float = SCORE_MAX) -> float:
    """安全提取分数"""
    raw = data.get(key)
    if raw is None or raw == "": return SCORE_DEFAULT
    try: val = float(raw)
    except (TypeError, ValueError): return SCORE_DEFAULT
    return min_val if val <= 0 else max(min_val, min(max_val, val))

def _normalize_target(raw: Any) -> str:
    """把模型返回的 target 归一化成 bot / other / all / unknown。"""
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    table = {
        "bot": "bot", "assistant": "bot", "self": "bot", "me": "bot",
        "机器人": "bot", "自己": "bot", "叫我": "bot", "被叫": "bot", "at": "bot",
        "other": "other", "others": "other", "member": "other", "someone": "other",
        "别人": "other", "群友": "other", "某个人": "other", "其他人": "other",
        "all": "all", "everyone": "all", "全体": "all", "大家": "all", "全群": "all",
        "unknown": "unknown", "不确定": "unknown", "说不清": "unknown", "无法判断": "unknown",
    }
    if text in table:
        return table[text]
    if "机器人" in text or "bot" in text:
        return "bot"
    if "别人" in text or "群友" in text or "other" in text:
        return "other"
    if "全" in text or "all" in text or "大家" in text:
        return "all"
    if "unknown" in text or "不确定" in text:
        return "unknown"
    return ""

def _normalize_style(raw: Any) -> str:
    """把 reply_style 归一化成 answer / comment / quote / none。"""
    text = str(raw or "").strip().lower()
    table = {
        "answer": "answer", "reply": "answer", "回答": "answer", "直接回答": "answer",
        "comment": "comment", "chat": "comment", "评论": "comment", "接话": "comment", "插话": "comment",
        "quote": "quote", "引用": "quote", "引用回复": "quote",
        "none": "none", "silent": "none", "不说话": "none", "不回复": "none",
    }
    if text in table:
        return table[text]
    if "quote" in text or "引用" in text:
        return "quote"
    if "answer" in text or "回答" in text:
        return "answer"
    if "none" in text or "沉默" in text or "不说" in text:
        return "none"
    if "comment" in text or "评论" in text or "插" in text:
        return "comment"
    return ""


def _normalize_context_type(raw: Any) -> str:
    """把 context_type 归一化成 mention_bot / knowledge_comment / question / casual_chat / other。"""
    text = str(raw or "").strip().lower()
    table = {
        "mention_bot": "mention_bot", "mention": "mention_bot", "at_bot": "mention_bot", "叫我": "mention_bot",
        "knowledge_comment": "knowledge_comment", "knowledge": "knowledge_comment", "评论": "knowledge_comment", "知识": "knowledge_comment",
        "question": "question", "ask": "question", "提问": "question", "问题": "question",
        "casual_chat": "casual_chat", "casual": "casual_chat", "chat": "casual_chat", "闲聊": "casual_chat",
        "other": "other", "其他": "other",
    }
    if text in table:
        return table[text]
    if "mention" in text or "at" == text:
        return "mention_bot"
    if "question" in text or "问" in text:
        return "question"
    if "knowledge" in text or "评论" in text:
        return "knowledge_comment"
    if "casual" in text or "聊" in text:
        return "casual_chat"
    if "other" in text:
        return "other"
    return ""


def _extract_json(text: str) -> Dict[str, Any]:
    """从文本中提取JSON"""
    if not text or not (text := text.strip()): raise ValueError("输入文本为空")
    try:
        result = json.loads(text)
        if isinstance(result, dict): return result
    except json.JSONDecodeError: pass  # cleanup
    if m := RE_CODE_BLOCK.search(text):
        try:
            result = json.loads(m.group(1).strip())
            if isinstance(result, dict): return result
        except json.JSONDecodeError: pass  # cleanup
    if m := RE_BRACE.search(text):
        try:
            result = json.loads(m.group())
            if isinstance(result, dict): return result
        except json.JSONDecodeError: pass  # cleanup
    for candidate in reversed(RE_ALL_BRACES.findall(text)):
        try:
            result = json.loads(candidate)
            if isinstance(result, dict): return result
        except json.JSONDecodeError: continue
    raise ValueError(f"无法从文本中提取有效 JSON: {text[:100]}...")

def _build_rules_section(judge_rules: List[Dict[str, Any]]) -> str:
    """将评分规则转换为提示词中的Markdown格式"""
    dimension_order = ["relevance", "willingness", "social", "timing", "continuity"]
    rules_dict: Dict[str, Dict[str, Any]] = {}
    for item in judge_rules:
        name = str(item.get("dimension_name", "")).strip()
        if name: rules_dict[name] = item

    lines = []
    idx = 1
    for dim_name in dimension_order:
        user_rule = rules_dict.get(dim_name, {})
        builtin = BUILTIN_RULES.get(dim_name, {})
        display = user_rule.get("dimension_display") or builtin.get("dimension_display", dim_name)
        low_desc = user_rule.get("low_desc") or builtin.get("low_score_desc", "无")
        high_desc = user_rule.get("high_desc") or builtin.get("high_score_desc", "无")
        lines.append(f"### {idx}. {dim_name} {display}\n- 低分情况：{low_desc}\n- 高分情况：{high_desc}\n")
        idx += 1
    return "\n".join(lines)

def _extract_weights_from_rules(judge_rules: List[Dict[str, Any]]) -> Dict[str, float]:
    """从配置中提取各维度权重并归一化。

    只对**已知维度**归一化：以前先除以 total 再 `if k in weights` 过滤，
    只要 judge_rules 里混进一个自定义维度名（如把 dimension_name 改成 mood），
    已知维度的权重和就小于 1，overall 被系统性压低（甚至 extracted 全是未知维时
    返回空字典、sum=0），表现为「心流永远不触发」。
    """
    weights = {"relevance": 0.25, "willingness": 0.2, "social": 0.2, "timing": 0.15, "continuity": 0.2}
    extracted: Dict[str, float] = {}
    unknown: List[str] = []
    for item in judge_rules:
        if not isinstance(item, dict):
            continue
        name = str(item.get("dimension_name", "")).strip()
        w = item.get("weight")
        if not name or w is None:
            continue
        try:
            value = float(w)
        except (TypeError, ValueError):
            continue
        if name not in weights:
            unknown.append(name)
            continue
        if value >= 0:
            extracted[name] = value
    if unknown:
        logger.warning("[心流] judge_rules 里有未知维度（已忽略）：%s" % "、".join(sorted(set(unknown))))
    total = sum(extracted.values())
    if extracted and total > 0:
        return {k: v / total for k, v in extracted.items()}
    if extracted:
        logger.warning("[心流] judge_rules 权重全为 0，回退默认权重")
    return weights

def _validate_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """验证并规范化配置"""
    v = dict(config or {})
    v["reply_threshold"] = clamp_score(v.get("reply_threshold", 0.6), 0.0, 1.0, 0.6, allow_zero=True)
    limits = {
        "context_messages_count": (1, 50, 5), "judge_context_count": (1, 50, 5),
        "judge_max_retries": (0, 10, 3), "immersive_mode_duration": (0, 300, 30),
        "judgment_paused_timeout": (5, 300, 60), "dynamic_activity_window": (60, 600, 300),
    }
    for k, (mn, mx, df) in limits.items(): v[k] = max(mn, min(mx, int(v.get(k, df))))

    v["enable_dynamic_threshold"] = bool(v.get("enable_dynamic_threshold", False))
    v["dynamic_threshold_max"] = max(0.0, min(1.0, float(v.get("dynamic_threshold_max", 0.85))))
    v["dynamic_threshold_min"] = max(0.0, min(1.0, float(v.get("dynamic_threshold_min", 0.35))))
    if v["dynamic_threshold_min"] > v["dynamic_threshold_max"]:
        v["dynamic_threshold_min"] = max(0.0, v["dynamic_threshold_max"] - 0.1)

    try:
        raw_w = v.get("dynamic_activity_weights", "0.4,0.3,0.3")
        weights = [float(w.strip()) for w in raw_w.split(",")] if isinstance(raw_w, str) else [float(w) for w in raw_w] if isinstance(raw_w, list) else [0.4, 0.3, 0.3]
        if len(weights) == 3 and all(0 <= w <= 1 for w in weights):
            total = sum(weights)
            v["dynamic_activity_weights"] = [w / total for w in weights] if total > 0 else [0.4, 0.3, 0.3]
        else: v["dynamic_activity_weights"] = [0.4, 0.3, 0.3]
    except Exception as _amo_exc: 
        _note_swallowed("heartflow_engine._validate_config", _amo_exc)
        v["dynamic_activity_weights"] = [0.4, 0.3, 0.3]

    if "chat_whitelist" in v and not isinstance(v["chat_whitelist"], list): v["chat_whitelist"] = []
    if "judge_rules" not in v or not isinstance(v["judge_rules"], list): v["judge_rules"] = []
    cleaned_rules = []
    for item in v["judge_rules"]:
        if isinstance(item, dict) and item.get("dimension_name"):
            cleaned_rules.append({
                "dimension_name": str(item.get("dimension_name", "")).strip(),
                "dimension_display": str(item.get("dimension_display", "")).strip(),
                "weight": clamp_score(item.get("weight", 0.2), 0.0, 1.0, 0.2, allow_zero=True),
                "low_desc": str(item.get("low_desc", "")), "high_desc": str(item.get("high_desc", "")),
            })
    v["judge_rules"] = cleaned_rules
    v["enable_debounce"] = bool(v.get("enable_debounce", True))
    v["debounce_delay"] = max(0.5, min(10.0, float(v.get("debounce_delay", 2.0))))
    v["max_debounce_wait"] = max(1.0, min(15.0, float(v.get("max_debounce_wait", 5.0))))
    v["enable_detailed_log"] = bool(v.get("enable_detailed_log", False))
    return v
