# -*- coding: utf-8 -*-
"""heartflow_events 拆分验收：Mixin 接入、静态判定与旧名转发。"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import heartflow_engine  # noqa: E402
import heartflow_events  # noqa: E402
from heartflow_engine import HeartflowEngine, RawMessage  # noqa: E402

def _engine():
    e = HeartflowEngine.__new__(HeartflowEngine)
    e.bot_name = "大肥鱼"
    e.bot_aliases = ["小鱼"]
    return e

def test_mixin_is_base_of_engine():
    assert issubclass(HeartflowEngine, heartflow_events.HeartflowEventsMixin)

def test_event_entrypoints_exist():
    for name in ("on_group_message", "on_llm_response", "on_after_message_sent",
                 "_should_process_message", "_clean_message_content", "_normalize_for_dedup"):
        assert hasattr(heartflow_events.HeartflowEventsMixin, name)

def test_clean_message_content_strips_tags_and_links():
    text = HeartflowEngine._clean_message_content(None, "你好 <b>世界</b> https://x.com 完")
    assert "<b>" not in text and "</b>" not in text
    assert "https://x.com" not in text
    assert "[链接]" in text

def test_clean_message_content_truncates():
    text = HeartflowEngine._clean_message_content(None, "字" * 20, max_len=5)
    assert text == "字" * 5 + "..."

def test_normalize_for_dedup_is_lower_and_stripped():
    assert HeartflowEngine._normalize_for_dedup(None, "  你好 A  ") == "你好 a"

def test_side_question_detection():
    assert HeartflowEngine._is_side_question("你pr好了吗") is True
    assert HeartflowEngine._is_side_question("你吃了吗") is True
    assert HeartflowEngine._is_side_question("今天好热") is False

def test_side_question_veto_requires_other_and_hard():
    assert HeartflowEngine._should_veto_side_question("other", True, "你pr好了吗") is True
    assert HeartflowEngine._should_veto_side_question("other", True, "今天好热") is False
    assert HeartflowEngine._should_veto_side_question("bot", True, "你pr好了吗") is False

def test_explicit_target_from_at_and_reply():
    assert HeartflowEngine._explicit_target(RawMessage("n", "1", "c", "c", 0.0, at_ids=("2",), at_self=True)) == "bot"
    assert HeartflowEngine._explicit_target(RawMessage("n", "1", "c", "c", 0.0, at_ids=("2",), self_id="9")) == "other"
    assert HeartflowEngine._explicit_target(RawMessage("n", "1", "c", "c", 0.0, at_ids=("2",), self_id="")) == ""
    assert HeartflowEngine._explicit_target(RawMessage("n", "1", "c", "c", 0.0, reply_to_id="x", reply_to_self=True)) == "bot"
    assert HeartflowEngine._explicit_target(RawMessage("n", "1", "c", "c", 0.0, reply_to_id="x", self_id="9", reply_to_name="a")) == "other"

def test_addresses_bot_by_name_and_alias():
    e = _engine()
    assert e._addresses_bot("叫大肥鱼出来") == "大肥鱼"
    assert e._addresses_bot("小鱼在吗") == "小鱼"
    assert e._addresses_bot("今天好热") == ""
