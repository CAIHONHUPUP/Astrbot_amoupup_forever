# -*- coding: utf-8 -*-
"""口头禅去逐字复读守卫测试（tone_guard，2026-11-17）。

覆盖三层：
1. 单元：连续同开头去重、最近五轮同片段 ≥2 次去重、五轮 1 次允许、
   情绪句/事实句不动、独立短语删除、重启清空、可选 kv；
2. 接线：_deco_soft_guards 在 fact_guard 之后调用、主动消息同样接入、
   提示词规则注入 amo_llm 的系统提示词拼装处；
3. 兜底：空文本/短开头/异常时守卫绝不改写、绝不拦发送。

反向验证（去掉接线就变红）：
test_deco_soft_guards_calls_tone_guard_after_fact_guard /
test_proactive_send_lines_calls_tone_guard_after_fact_guard /
test_llm_base_parts_injects_tone_guard_rule。
"""
from __future__ import annotations

import ast
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_reply_pipeline as pipeline  # noqa: E402
import tone_guard as tg  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_memory():
    tg.reset()
    yield
    tg.reset()


class _FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class _FakePlugin:
    def __init__(self):
        self.db = _FakeDB()


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


def _func_source(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


# ---------------------------------------------------------------------------
# 提示词侧
# ---------------------------------------------------------------------------

def test_prompt_rule_mentions_catchphrase_five_rounds_and_consecutive_openings():
    rule = tg.prompt_rule()
    assert "口头禅" in rule
    assert "5 轮" in rule
    assert "连续两条" in rule
    assert rule == tg.PROMPT_RULE


def test_prompt_rule_has_no_config_key_and_no_concrete_catchphrase():
    rule = tg.prompt_rule()
    assert "cfg" not in rule.lower()
    assert "【口头禅】" in rule
    assert len(rule) <= 200
    for concrete in ("我跟你说", "绝了", "你懂吧", "好家伙", "我的天"):
        assert concrete not in rule


def test_prompt_rule_is_stable_across_calls():
    assert tg.prompt_rule() == tg.prompt_rule() == tg.PROMPT_RULE


def test_pipeline_forwards_the_same_prompt_rule():
    assert pipeline.tone_guard_prompt_rule() == tg.PROMPT_RULE


def test_llm_base_parts_injects_tone_guard_rule():
    """反向验证：amo_llm 拼系统提示词的地方没注入口头禅规则就会红。"""
    source = _read("amo_llm.py")
    body = _func_source(source, "_reply_base_parts")
    assert "tone_guard_prompt_rule" in body
    assert "system_parts.append(_tone_rule)" in body


def test_no_new_config_key_for_catchphrase():
    with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as handle:
        schema = json.load(handle)
    bad = [key for key in schema if "tone_guard" in key.lower() or "catchphrase" in key.lower()]
    assert not bad, "不许新增口头禅配置键: %s" % bad


# ---------------------------------------------------------------------------
# 连续同开头
# ---------------------------------------------------------------------------

def test_guard_strips_consecutive_same_opening():
    first, actions = tg.guard("就是说，你这人真逗。", umo="a")
    assert first == "就是说，你这人真逗。"
    assert actions == []
    second, actions = tg.guard("就是说，我也有点饿了。", umo="a")
    assert second == "我也有点饿了。"
    assert actions and actions[0]["phrase"] == "就是说"
    assert actions[0]["reason"] == "consecutive"


def test_guard_strips_all_three_consecutive_openings():
    outs = [tg.guard("就是说，第%d句。" % index, umo="a3")[0] for index in (1, 2, 3)]
    assert outs[0] == "就是说，第1句。"
    assert outs[1] == "第2句。"
    assert outs[2] == "第3句。"


def test_guard_does_not_touch_middle_text_when_stripping():
    tg.guard("就是说，你这人真逗。", umo="a4")
    out, _actions = tg.guard("就是说，我也有点饿了，等下再说。", umo="a4")
    assert out == "我也有点饿了，等下再说。"


def test_guard_returns_shorter_or_equal_text_only():
    texts = ["就是说，真逗。", "嗯，好。", "3 点在公司开会。", "我好难过啊。", ""]
    for text in texts:
        out, _actions = tg.guard(text, umo="len")
        assert len(out) <= len(text)


# ---------------------------------------------------------------------------
# 最近五轮：1 次允许 / ≥2 次去重 / 出窗忘记
# ---------------------------------------------------------------------------

def test_same_opening_once_within_five_rounds_is_allowed():
    tg.guard("好家伙，今儿真热。", umo="b")
    for text in ("突然想吃火锅。", "刚看完一部剧。", "外面下雨了呢。", "明早要早起。"):
        tg.guard(text, umo="b")
    out, actions = tg.guard("好家伙，这风真大。", umo="b")
    assert out == "好家伙，这风真大。"
    assert actions == []


def test_repeat_fragment_twice_in_last_five_is_stripped():
    tg.guard("今天风挺大。", umo="c")
    tg.guard("我心里就是说不上来。", umo="c")
    tg.guard("刚看完一部剧。", umo="c")
    tg.guard("你这句话就是说给我听的吧。", umo="c")
    tg.guard("明早要早起。", umo="c")
    out, actions = tg.guard("就是说，真有意思。", umo="c")
    assert out == "真有意思。"
    assert actions[0]["phrase"] == "就是说"
    assert actions[0]["reason"] == "repeat"


def test_old_repeat_outside_five_rounds_is_allowed():
    tg.guard("俗话说，天气不错。", umo="d")
    for text in ("刚看完一部剧。", "外面下雨了呢。", "明早要早起。", "突然想吃火锅。", "风好大呀。"):
        tg.guard(text, umo="d")
    out, _actions = tg.guard("俗话说，真有意思。", umo="d")
    assert out == "俗话说，真有意思。"


def test_history_keeps_only_last_five_openings():
    for index in range(8):
        tg.guard("第%d回，随便说说。" % index, umo="e")
    openings = tg.recent_openings("e")
    assert len(openings) == tg.HISTORY_SIZE == 5


# ---------------------------------------------------------------------------
# 情绪句 / 事实句不许动
# ---------------------------------------------------------------------------

def test_emotion_sentence_is_preserved_after_stripping_catchphrase():
    tg.guard("就是说，真逗。", umo="f")
    out, _actions = tg.guard("就是说，我好难过啊。", umo="f")
    assert out == "我好难过啊。"
    assert "难过" in out


def test_emotion_opening_is_never_treated_as_catchphrase():
    tg.guard("我好难过啊。", umo="g")
    out, actions = tg.guard("我好难过啊，真的。", umo="g")
    assert out == "我好难过啊，真的。"
    assert actions == []


def test_fact_opening_is_never_treated_as_catchphrase():
    tg.guard("3 点在公司开会。", umo="h")
    out, actions = tg.guard("3 点在公司开会呢。", umo="h")
    assert out == "3 点在公司开会呢。"
    assert actions == []


def test_fact_body_is_not_rewritten_when_opening_stripped():
    tg.guard("就是说，真逗。", umo="i")
    out, _actions = tg.guard("就是说，3 点在公司开会，晚点聊。", umo="i")
    assert out == "3 点在公司开会，晚点聊。"


# ---------------------------------------------------------------------------
# 独立短语 / 边界
# ---------------------------------------------------------------------------

def test_standalone_phrase_sentence_is_dropped():
    tg.guard("就是说，真逗。", umo="j")
    tg.guard("就是说，我也饿了。", umo="j")
    out, actions = tg.guard("就是说。今天真热。", umo="j")
    assert out == "今天真热。"
    assert actions and actions[0]["action"] == "drop"


def test_short_opening_below_three_chars_is_untouched():
    tg.guard("嗯，好。", umo="k")
    out, actions = tg.guard("嗯，好。", umo="k")
    assert out == "嗯，好。"
    assert actions == []


def test_empty_and_whitespace_text_is_safe():
    for text in ("", "   ", "\n"):
        out, actions = tg.guard(text, umo="l")
        assert out == text
        assert actions == []


# ---------------------------------------------------------------------------
# 重启清空 / 会话隔离 / 可选 kv
# ---------------------------------------------------------------------------

def test_restart_clears_in_process_memory():
    tg.guard("就是说，真逗。", umo="m")
    tg.reset()  # 模拟进程重启
    out, actions = tg.guard("就是说，我也饿了。", umo="m")
    assert out == "就是说，我也饿了。"
    assert actions == []


def test_reset_only_clears_one_session():
    tg.guard("就是说，真逗。", umo="n1")
    tg.guard("就是说，真逗。", umo="n2")
    tg.reset("n1")
    out, _actions = tg.guard("就是说，我也饿了。", umo="n1")
    assert out == "就是说，我也饿了。"
    out2, _actions2 = tg.guard("就是说，我也饿了。", umo="n2")
    assert out2 == "我也饿了。"


def test_kv_persistence_is_opt_in_only():
    plugin = _FakePlugin()
    tg.guard("就是说，真逗。", plugin=plugin, umo="kv", use_kv=False)
    assert plugin.db.kv == {}
    tg.reset()
    tg.guard("就是说，真逗。", plugin=plugin, umo="kv", use_kv=True)
    assert "tone_guard:kv" in plugin.db.kv
    tg.reset("kv")  # 只清进程内，kv 保留
    out, _actions = tg.guard("就是说，我也饿了。", plugin=plugin, umo="kv", use_kv=True)
    assert out == "我也饿了。"


def test_guard_is_deterministic_for_same_sequence():
    def run():
        tg.reset()
        outs = []
        for text in ("就是说，真逗。", "就是说，我也饿了。", "就是说，今天真热。"):
            outs.append(tg.guard(text, umo="stable")[0])
        return outs

    assert run() == run()


# ---------------------------------------------------------------------------
# 接线：装饰链路 / 主动消息 / 异常兜底
# ---------------------------------------------------------------------------

def test_deco_soft_guards_calls_tone_guard_after_fact_guard():
    """反向验证：_deco_soft_guards 里去掉口头禅守卫就会红。"""
    body = _func_source(_read("amo_runtime_reply.py"), "_deco_soft_guards")
    assert "apply_fact_guard" in body
    assert "apply_tone_guard" in body
    assert body.index("apply_fact_guard") < body.index("apply_tone_guard")
    assert "event.unified_msg_origin" in body.split("apply_tone_guard", 1)[1][:120]


def test_proactive_send_lines_calls_tone_guard_after_fact_guard():
    """反向验证：主动消息里去掉口头禅守卫就会红。"""
    body = _func_source(_read("amo_proactive.py"), "_send_proactive_lines")
    assert "apply_fact_guard" in body
    assert "apply_tone_guard" in body
    assert body.index("apply_fact_guard") < body.index("apply_tone_guard")


def test_pipeline_wrapper_returns_text_and_handles_empty():
    assert pipeline.apply_tone_guard("", None, "x") == ""
    out = pipeline.apply_tone_guard("就是说，真逗。", None, "wrap")
    assert out == "就是说，真逗。"
    out2 = pipeline.apply_tone_guard("就是说，我也饿了。", None, "wrap")
    assert out2 == "我也饿了。"


def test_pipeline_wrapper_swallows_guard_failure(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tg, "guard", boom)
    original = "就是说，真逗。"
    assert pipeline.apply_tone_guard(original, None, "boom") == original
