# -*- coding: utf-8 -*-
"""B 方案：LLM 只在语气池候选里选序号（可开关，默认关闭）。

覆盖：默认关闭零调用 / 开关打开选中候选 / 超时/非法/越界/无 LLM 回退 /
无冲突不调用 / 环境变量与配置开关 / 同步入口绝不调用 LLM / 事实仍正确 /
调用点接线（runtime + proactive）。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import inspect
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
import reply_fact_guard  # noqa: E402

DAY = "2026-11-17"
NOW = _dt.datetime(2026, 11, 17, 10, 30)
HEALTHY = {"status": "健康", "sick": False}
OFFICE = {"start": "09:00", "end": "18:00", "zone": "office", "place": "公司",
          "activity": "敲周报", "category": "work"}
HOME = {"start": "09:00", "end": "18:00", "zone": "home", "place": "家",
        "activity": "窝在沙发上", "category": "rest"}


class _DB:
    def kv_get(self, key, default=None):
        return default


class _State:
    def __init__(self, activity):
        self.schedule = {DAY: [dict(activity)]}
        self.health = dict(HEALTHY)
        self.mood = {"开心": 60}
        self.relation = "恋人"
        self.db = _DB()

    def health_info(self):
        return dict(self.health)

    def current_activity(self):
        return dict(self.schedule[DAY][0]) if self.schedule[DAY] else {}


class _Wallet:
    def ledger(self, limit=200):
        return []


class _FakeLLM:
    def __init__(self, answer=None, delay=0.0, error=None):
        self.answer = answer
        self.delay = delay
        self.error = error
        self.calls = []

    async def chat(self, umo, system, prompt, limit=300):
        self.calls.append({"umo": umo, "system": system, "prompt": prompt,
                           "limit": limit})
        if self.error is not None:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.answer


class _Plugin:
    def __init__(self, activity=OFFICE, llm=None, tone_llm_pick=None,
                 cfg_pick=False):
        self.state = _State(activity)
        self.db = _DB()
        self.wallet = _Wallet()
        self.relation = "恋人"
        self.llm = llm
        if tone_llm_pick is not None:
            self.tone_llm_pick = tone_llm_pick
        self._cfg_pick = bool(cfg_pick)

    def cfg_bool(self, key, default=False):
        if key == "tone_llm_pick":
            return self._cfg_pick
        return default


def _plugin(**kwargs):
    plugin = _Plugin(**kwargs)
    pipeline.reset_tone_naturalness(plugin)
    pipeline.reset_tone_llm_pick_stats()
    return plugin


def _run(plugin, text="我在家躺着呢，好累呀"):
    return asyncio.run(pipeline.apply_fact_guard_async(text, plugin, now=NOW))


def _deterministic(plugin, text="我在家躺着呢，好累呀"):
    return pipeline.apply_fact_guard(text, plugin, now=NOW)


def _place_choices():
    return pipeline._tone_candidates(
        "place", "我在", "公司", "呢", _plugin(), OFFICE)[1:]


# ---------------------------------------------------------------------------
# 默认关闭 / 开关
# ---------------------------------------------------------------------------

def test_b_is_disabled_by_default_and_never_calls_llm(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", False)
    llm = _FakeLLM('{"index": 3}')
    plugin = _plugin(llm=llm)
    out = _run(plugin)
    assert out == _deterministic(_plugin())
    assert llm.calls == []
    assert pipeline.tone_llm_pick_stats()["calls"] == 0


def test_b_off_matches_a_byte_for_byte(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", False)
    texts = [
        "我在家躺着呢，好累呀",
        "我正在吃呢，有点饿",
        "他刚抱我了，我有点难过",
        "我还烧着呢，别担心",
        "我在上班呢，好困",
    ]
    for text in texts:
        async_plugin = _plugin(llm=_FakeLLM('{"index": 3}'))
        sync_plugin = _plugin()
        assert _run(async_plugin, text) == _deterministic(sync_plugin, text), text
        assert (getattr(async_plugin, "_fact_tone_state", {})
                == getattr(sync_plugin, "_fact_tone_state", {})), text


def test_b_default_timeout_and_schema_switch_are_visible():
    assert pipeline.TONE_LLM_PICK_TIMEOUT == 1.5
    with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as handle:
        schema = json.load(handle)
    meta = schema["tone_llm_pick"]
    assert meta["type"] == "bool"
    assert meta["default"] is False
    assert "B 方案" in meta["description"]
    assert "序号" in meta["hint"]


def test_b_enabled_picks_candidate_index(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    llm = _FakeLLM('{"index": 3}')
    plugin = _plugin(llm=llm)
    out = _run(plugin)
    assert "人就在公司呢" in out
    assert len(llm.calls) == 1
    assert "候选" in llm.calls[0]["prompt"]
    assert "只选序号" in llm.calls[0]["prompt"]
    assert pipeline.tone_llm_pick_stats()["picked"] == 1


def test_b_pick_does_not_use_llm_free_text(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    llm = _FakeLLM('3 随便加的，不该出现')
    plugin = _plugin(llm=llm)
    out = _run(plugin)
    assert "随便加的" not in out
    assert "人就在公司呢" in out


def test_b_plugin_attribute_switch():
    llm = _FakeLLM('{"index": 2}')
    plugin = _plugin(llm=llm, tone_llm_pick=True)
    assert pipeline.tone_llm_pick_enabled(plugin) is True
    assert "我就在公司呢" in _run(plugin)


def test_b_config_switch():
    llm = _FakeLLM('{"index": 2}')
    plugin = _plugin(llm=llm, cfg_pick=True)
    assert pipeline.tone_llm_pick_enabled(plugin) is True
    assert _run(plugin)


def test_b_env_switch(monkeypatch):
    monkeypatch.setenv("AMO_TONE_LLM_PICK", "1")
    assert pipeline.tone_llm_pick_enabled(None) is True
    monkeypatch.setenv("AMO_TONE_LLM_PICK", "off")
    assert pipeline.tone_llm_pick_enabled(None) is False


# ---------------------------------------------------------------------------
# 回退
# ---------------------------------------------------------------------------

def test_b_timeout_falls_back(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_TIMEOUT", 0.02)
    llm = _FakeLLM('{"index": 3}', delay=0.2)
    plugin = _plugin(llm=llm)
    out = _run(plugin)
    assert "公司" in out and "家" not in out
    assert llm.calls
    assert pipeline.tone_llm_pick_stats()["timeout"] == 1


def test_b_invalid_json_falls_back(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    llm = _FakeLLM("我不知道")
    plugin = _plugin(llm=llm)
    out = _run(plugin)
    assert "公司" in out and "家" not in out
    assert pipeline.tone_llm_pick_stats()["invalid"] == 1


def test_b_out_of_range_falls_back(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    llm = _FakeLLM('{"index": 99}')
    plugin = _plugin(llm=llm)
    out = _run(plugin)
    assert "公司" in out and "家" not in out
    assert pipeline.tone_llm_pick_stats()["invalid"] == 1


def test_b_no_llm_falls_back(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    plugin = _plugin(llm=None)
    out = _run(plugin)
    assert "公司" in out and "家" not in out
    assert pipeline.tone_llm_pick_stats()["no_llm"] == 1


def test_b_llm_exception_falls_back(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    llm = _FakeLLM(error=RuntimeError("boom"))
    plugin = _plugin(llm=llm)
    out = _run(plugin)
    assert "公司" in out and "家" not in out
    assert pipeline.tone_llm_pick_stats()["error"] == 1


# ---------------------------------------------------------------------------
# 边界 / 事实 / 接线
# ---------------------------------------------------------------------------

def test_b_non_conflict_never_calls_llm(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    llm = _FakeLLM('{"index": 3}')
    plugin = _plugin(llm=llm)
    out = _run(plugin, "我在公司忙着呢，晚点聊")
    assert out == "我在公司忙着呢，晚点聊"
    assert llm.calls == []


def test_b_sync_entry_never_calls_llm(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    llm = _FakeLLM(error=RuntimeError("must not be called"))
    plugin = _plugin(llm=llm)
    out = _deterministic(plugin)
    assert "公司" in out
    assert llm.calls == []


def test_b_every_candidate_passes_fact_guard(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    choices = _place_choices()
    assert len(choices) >= 5
    for index in range(len(choices)):
        plugin = _plugin(llm=_FakeLLM('{"index": %d}' % index))
        out = _run(plugin)
        assert "公司" in out and "家" not in out
        ctx = pipeline.fact_guard_context(plugin, None, NOW)
        _fixed, violations = reply_fact_guard.guard(out, ctx)
        assert violations == [], (index, out, violations)


def test_b_candidates_are_pool_sentences():
    choices = _place_choices()
    assert len(choices) == 7
    assert len(set(choices)) == len(choices)
    assert all("公司" in item for item in choices)
    health = pipeline._tone_candidates(
        "health", "我还", "就是有点累", "呢", _plugin(activity=HOME), HOME)
    assert len(health) >= 5


def test_b_stats_reset():
    pipeline.TONE_LLM_STATS["calls"] = 5
    pipeline.reset_tone_llm_pick_stats()
    assert pipeline.tone_llm_pick_stats() == {
        "calls": 0, "picked": 0, "fallback": 0, "timeout": 0,
        "invalid": 0, "error": 0, "no_llm": 0, "fact_fallback": 0,
    }


def test_b_call_sites_are_wired():
    with open(os.path.join(ROOT, "amo_proactive.py"), encoding="utf-8") as handle:
        proactive = handle.read()
    assert "await _reply_pipeline.apply_fact_guard_async" in proactive
    with open(os.path.join(ROOT, "amo_runtime_reply.py"), encoding="utf-8") as handle:
        runtime = handle.read()
    # runtime 的同步守卫方法体被结构测试哈希锁住：B 走 pending 桥，不在里面 await。
    assert "_reply_pipeline.apply_fact_guard(text, self)" in runtime
    assert "apply_pending_tone_pick_async" in runtime
    import amo_runtime_reply
    polish_source = inspect.getsource(
        amo_runtime_reply.RuntimeReplyMixin._deco_polish_native)
    assert "apply_pending_tone_pick_async" in polish_source
    import amo_tone_pick
    assert "asyncio.wait_for" in inspect.getsource(pipeline._llm_pick_index)
    assert "tone_llm_pick_enabled" in inspect.getsource(
        amo_tone_pick.apply_fact_guard_async)


def test_b_runtime_pending_bridge_selects_candidate(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", True)
    llm = _FakeLLM('{"index": 3}')
    plugin = _plugin(llm=llm)
    fixed = _deterministic(plugin)
    pending = getattr(plugin, "_tone_pick_pending", None)
    assert isinstance(pending, dict)
    assert pending["text"] == fixed
    picked = asyncio.run(
        pipeline.apply_pending_tone_pick_async(fixed, plugin))
    assert "人就在公司呢" in picked
    assert len(llm.calls) == 1
    assert getattr(plugin, "_tone_pick_pending", None) is None


def test_b_runtime_pending_bridge_off_keeps_a(monkeypatch):
    monkeypatch.setattr(pipeline, "TONE_LLM_PICK_ENABLED", False)
    llm = _FakeLLM('{"index": 3}')
    plugin = _plugin(llm=llm)
    fixed = _deterministic(plugin)
    assert getattr(plugin, "_tone_pick_pending", None) is None
    picked = asyncio.run(
        pipeline.apply_pending_tone_pick_async(fixed, plugin))
    assert picked == fixed
    assert llm.calls == []
