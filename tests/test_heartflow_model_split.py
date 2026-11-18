# -*- coding: utf-8 -*-
"""heartflow_model 拆分验收：纯函数/数据类与改造前逐字节一致。"""
import hashlib, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import heartflow_engine  # noqa: E402
import heartflow_model  # noqa: E402

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

def test_old_names_are_reexported():
    for name in ("JudgeResult", "RawMessage", "ChatState", "clamp_score", "safe_extract_score",
                 "BASE_JUDGE_PROMPT", "BUILTIN_RULES", "SCORE_MIN", "SCORE_MAX"):
        assert getattr(heartflow_engine, name) is getattr(heartflow_model, name), name

def test_base_prompt_bytes_match():
    digest = hashlib.sha256(heartflow_model.BASE_JUDGE_PROMPT.encode("utf-8")).hexdigest()
    assert digest == GOLD["base_prompt_sha256"]

def test_clamp_score_matches_baseline():
    assert [heartflow_model.clamp_score(x) for x in (0, 6, 12, None, "7", "x")] == GOLD["clamp"]

def test_clamp_score_allow_zero_matches_baseline():
    got = [heartflow_model.clamp_score(0, 0.0, 1.0, 0.6, allow_zero=True),
           heartflow_model.clamp_score(0, 0.0, 1.0, 0.6)]
    assert got == GOLD["clamp_zero"]

def test_safe_extract_score_matches_baseline():
    got = [heartflow_model.safe_extract_score({"a": 0}, "a"),
           heartflow_model.safe_extract_score({"a": 9}, "a"),
           heartflow_model.safe_extract_score({}, "a")]
    assert got == GOLD["safe_extract"]

def test_normalizers_match_baseline():
    assert [heartflow_model._normalize_target(x) for x in ("机器人", "别人", "全群", "？", "", "assistant")] == GOLD["normalize"]["target"]
    assert [heartflow_model._normalize_style(x) for x in ("回答", "引用", "none", "？")] == GOLD["normalize"]["style"]
    assert [heartflow_model._normalize_context_type(x) for x in ("mention", "问题", "闲聊", "？")] == GOLD["normalize"]["context"]

def test_extract_json_matches_baseline():
    got = [heartflow_model._extract_json('{"a": 1}'),
           heartflow_model._extract_json(chr(96) * 3 + 'json\n{"b": 2}\n' + chr(96) * 3),
           heartflow_model._extract_json('前缀 {"c": 3} 后缀')]
    assert got == GOLD["extract_json"]

def test_rules_section_matches_baseline():
    assert heartflow_model._build_rules_section(RULES) == GOLD["rules_section"]

def test_weights_match_baseline():
    assert heartflow_model._extract_weights_from_rules(RULES) == GOLD["weights"]

def test_validate_config_matches_baseline():
    got = heartflow_model._validate_config({
        "reply_threshold": 0.7, "context_messages_count": 99, "judge_context_count": 0,
        "judge_max_retries": 99, "immersive_mode_duration": -1, "judgment_paused_timeout": 1,
        "dynamic_activity_window": 9999, "enable_dynamic_threshold": 1,
        "dynamic_threshold_max": 1.5, "dynamic_threshold_min": 0.9,
        "dynamic_activity_weights": "1,1,2", "judge_rules": RULES,
        "debounce_delay": 99, "max_debounce_wait": 0, "enable_debounce": 1,
    })
    assert got == GOLD["validate_config"]
