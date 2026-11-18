# -*- coding: utf-8 -*-
"""同城不同住物理边界：线上聊天不能被改写成面对面，日记不能写他实体在场。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402
astrbot_stub.install()

import amo_date_context as dc  # noqa: E402
from amo_diary_guard import fact_rules, sanitize_diary  # noqa: E402
from amo_llm import ACTION_OWNERSHIP_RULES  # noqa: E402
from schedule_engine import ScheduleEngine, diary_presence_rules  # noqa: E402


def test_chat_prompt_has_physical_boundary():
    assert "物理边界" in ACTION_OWNERSHIP_RULES
    assert "他不在你身边" in ACTION_OWNERSHIP_RULES
    assert "手机" in ACTION_OWNERSHIP_RULES


def test_home_fixer_no_longer_turns_chat_into_face_to_face():
    raw = "在微信上跟老公聊了几句，心里踏实了点"
    assert ScheduleEngine._fix_home_diary(raw) == raw


def test_non_date_rules_forbid_physical_presence():
    rules = fact_rules(False)
    assert "他不在" in rules or "没有出现在你身边" in rules
    assert "他在旁边翻身" in rules


def test_sanitize_removes_physical_hallucination():
    text = "他在旁边翻了个身，嘟囔了句什么又睡回去，我懒得接话。"
    out = sanitize_diary(text, together=False)
    assert "翻了个身" not in out
    assert "一个人" in out or "微信" in out


#: B 方案：同城不同住、各回各家；这些写法只能出现在「约会时段内」
SHARED_HOME_WORDS = ("名义上同居", "住在一起", "共同的家", "同居")
PROMPT_FILES = ("amo_llm.py", "amo_llm_service.py", "amo_schedule.py", "schedule_engine.py",
                "amo_date_context.py", "amo_diary_guard.py", "amo_feature_api.py",
                "amo_fact_pool.py", "amo_proactive.py")


class _DB:
    def __init__(self, data):
        self.data = data

    def kv_get(self, key, default=None):
        return self.data.get(key, default)


class _Ctx:
    def __init__(self, data):
        self.db = _DB(data)


def test_prompt_sources_have_no_shared_home_wording():
    """内置提示词/事实文案不许再把两人写成同住一个家（B 方案：同城不同住）。"""
    for name in PROMPT_FILES:
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            source = fh.read()
        for phrase in SHARED_HOME_WORDS:
            assert phrase not in source, (name, phrase)


def test_after_date_prompt_says_apart():
    now = time.time()
    ending = dc.prompt_block(_Ctx({"date_ending": {
        "fact": "约会结束，他不在你身边，你一个人回家。", "until": now + 600}}))
    home = dc.prompt_block(_Ctx({"date_home": {"title": "火锅", "at": now,
                                               "until": now + 3600}}))
    for text in (ending, home):
        assert "一个人" in text, text
        for phrase in SHARED_HOME_WORDS + ("一起回家",):
            assert phrase not in text, (phrase, text)
    assert "各回各家" in home


def test_alone_rules_forbid_shared_home_after_date():
    """约会结束后的日记规则：各回各家，不许写同一个家/他守在她身边。"""
    rules = fact_rules(False)
    assert "各回各家" in rules
    assert "同一个家" in rules
    assert "面对面" not in rules


def test_schedule_change_facts_are_apart():
    """用户说「来接我」时排出来的日程事实也不能写「一起回家」。"""
    import schedule_change
    text = " ".join(str(rule.get("activity") or "") for rule in schedule_change._RULES)
    assert "一起回家" not in text
    assert "各回各家" in text


def test_date_hour_still_allows_face_to_face():
    """约会时段的块仍然允许面对面，不能因为收尾规则被误杀。"""
    rules = diary_presence_rules(True)
    assert "面对面" in rules
    assert "就在你对面" in rules
    for phrase in SHARED_HOME_WORDS + ("一起回家",):
        assert phrase not in rules, (phrase, rules)
