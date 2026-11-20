# -*- coding: utf-8 -*-
"""heartflow_text 拆分验收：Mixin 接入、评分/沉浸提示词 A/B 逐字节。"""
import hashlib, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import heartflow_engine  # noqa: E402
import heartflow_text  # noqa: E402
from heartflow_engine import HeartflowEngine  # noqa: E402

GOLD = json.load(open(os.path.join(ROOT, "tests", "golden", "heartflow_split_golden.json"), encoding="utf-8"))
RULES = [
    {"dimension_name": "relevance", "dimension_display": "内容相关度", "weight": 0.3,
     "low_desc": "无关", "high_desc": "有关"},
    {"dimension_name": "willingness", "dimension_display": "回复意愿", "weight": 0.2,
     "low_desc": "不想", "high_desc": "想"},
    {"dimension_name": "social", "dimension_display": "社交适宜性", "weight": 0.2,
     "low_desc": "不宜", "high_desc": "适宜"},
    {"dimension_name": "timing", "dimension_display": "时机", "weight": 0.15,
     "low_desc": "早", "high_desc": "好"},
    {"dimension_name": "continuity", "dimension_display": "连贯", "weight": 0.15,
     "low_desc": "断", "high_desc": "连"},
    {"dimension_name": "mood", "dimension_display": "未知维", "weight": 0.99,
     "low_desc": "", "high_desc": ""},
]

class Event:
    unified_msg_origin = "group:100"
    message_str = "今天好热"
    def get_sender_name(self):
        return "小明"

class Engine:
    judge_rules = RULES
    judge_include_reasoning = True
    score_min = 1.0
    score_max = 10.0
    bot_name = "大肥鱼"
    bot_aliases = ["小鱼", "鱼鱼"]
    weights = {"relevance": 0.3, "willingness": 0.2, "social": 0.2, "timing": 0.15, "continuity": 0.15}
    def _get_minutes_since_last_reply(self, chat_id):
        return 7
    def _build_chat_context(self, event):
        return "状态：闲聊"
    def _analyze_conversation_flow(self, event):
        return "对话流：接话"
    def _get_last_bot_reply(self, event):
        return "上次：我也热"
    def _group_members_line(self, event):
        return "小明（最近发言）、小红"
    def _build_optimized_contexts(self, history):
        return ["ctx-1", "ctx-2"]
    def _get_raw_history_for_context(self, event):
        return ["raw-1"]

def test_mixin_is_base_of_engine():
    assert issubclass(HeartflowEngine, heartflow_text.HeartflowTextMixin)

def test_immersive_prompt_is_reexported():
    assert heartflow_engine._build_immersive_prompt is heartflow_text._build_immersive_prompt

def test_build_judge_prompt_matches_baseline():
    got = list(HeartflowEngine._build_judge_prompt(Engine(), Event(), 0.6, 0.42, "一般"))
    assert got == GOLD["build_judge_prompt"]

def test_build_immersive_prompt_matches_baseline():
    got = list(heartflow_text._build_immersive_prompt(Engine(), Event(), 120, 0.42, "一般"))
    assert got == GOLD["build_immersive_prompt"]

def test_judge_result_from_data_matches_baseline():
    data = {"relevance": 8, "willingness": 6, "social": 5, "timing": 7, "continuity": 9,
            "target": "机器人", "is_mentioning_bot": False, "context_type": "casual_chat",
            "topic": "天气", "reply_style": "comment", "thought": "接一句", "reasoning": "r"}
    res = HeartflowEngine._judge_result_from_data(Engine(), Event(), data, 0.6, 0.42, "一般")
    got = {k: getattr(res, k) for k in res.__dataclass_fields__}
    assert got == GOLD["judge_result"]

def test_base_prompt_hash_matches():
    digest = hashlib.sha256(heartflow_engine.BASE_JUDGE_PROMPT.encode("utf-8")).hexdigest()
    assert digest == GOLD["base_prompt_sha256"]

def test_text_helpers_are_reexported():
    assert heartflow_engine._build_rules_section is heartflow_text._build_rules_section
    assert heartflow_engine._normalize_target is heartflow_text._normalize_target

def test_status_helpers_live_on_mixin():
    for name in ("heartflow_status", "heartflow_debug", "heartflow_help", "terminate"):
        assert hasattr(heartflow_text.HeartflowTextMixin, name)
