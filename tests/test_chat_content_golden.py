# -*- coding: utf-8 -*-
"""聊天内容 golden（20 个跨场景）：把「生成内容」当数据来测。

覆盖：在家早晨/午睡/休息、上班/加班、通勤地铁/开车、午饭/晚饭/非饭点、
生病发烧/病中/痊愈、医院排队、约会在一起/约会结束、旅游/酒店/无旅游、
商场/餐厅、深夜睡觉、主动消息（无上下文）。

每条场景断言四件事：
1. violations 规则列表与 expect.violations 完全一致（顺序也一致，顺序＝守卫规则序）；
2. 改写后不含 must_not_contain 里的冲突词；
3. must_contain_any 里至少命中一个「改口后的说法」；
4. preserve 里的情绪句/细节句一字不改地保留。

另有生产链路用例：从 fake plugin state 读 ctx，走 amo_reply_pipeline.apply_fact_guard，
确认最终发给用户的文本被改写。

为什么允许更新 tests/golden/chat_scenarios.json：见该文件的 _update_policy——只有
守卫规则被有意改动时才允许更新 expect，且必须写清原因、同步更新规则单测。禁止
为了让新文案通过而放宽断言。时钟固定为 2026-11-17 + 每条场景自己的 now(HH:MM)。
"""
from __future__ import annotations

import datetime as _dt
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

DATE = _dt.date(2026, 11, 17)
GOLDEN_PATH = os.path.join(TESTS, "golden", "chat_scenarios.json")
CTX_KEYS = {"zone", "place", "category", "activity", "sick", "together",
            "trip", "hotel", "nearby_meal", "has_chat"}
RULES = {"place", "activity", "meal", "health", "companion"}


def _load_golden():
    with open(GOLDEN_PATH, encoding="utf-8") as handle:
        return json.load(handle)


GOLDEN = _load_golden()
SCENARIOS = GOLDEN["scenarios"]


def _moment(scenario):
    text = str(scenario.get("now") or GOLDEN.get("default_now") or "10:30")
    hour, _, minute = text.partition(":")
    return _dt.datetime(DATE.year, DATE.month, DATE.day, int(hour), int(minute or 0))


def _guard_ctx(scenario):
    """场景世界状态 -> reply_fact_guard.guard() 的原始 ctx（缺什么就传空）。"""
    raw = dict(scenario.get("ctx") or {})
    activity = {key: raw.get(key) for key in ("zone", "place", "category", "activity")
                if raw.get(key)}
    moment = _moment(scenario)
    nearby = []
    if raw.get("nearby_meal"):
        nearby = [{"category": "meal",
                   "start": "%02d:00" % max(0, moment.hour - 1),
                   "end": "%02d:59" % min(23, moment.hour + 1)}]
    sick = bool(raw.get("sick"))
    together = bool(raw.get("together"))
    trip = bool(raw.get("trip"))
    return {
        "now": moment,
        "activity": activity,
        "health": {"status": "感冒" if sick else "健康", "sick": sick},
        "date": {"together": together, "active": together},
        "trip": {"phase": "traveling" if trip else "", "active": trip,
                 "hotel": bool(raw.get("hotel"))},
        "nearby": nearby,
        "today_chat": bool(raw.get("has_chat")),
    }


def _explain(scenario, fixed, violations, expect):
    return ("场景 %s（%s）\n输入: %s\n输出: %s\n实际 rules: %s\n期望: %s\n"
            "如果这次改动是有意的，请更新 tests/golden/chat_scenarios.json 的 "
            "expect 并写清原因；禁止为了让测试变绿而放宽断言。"
            % (scenario.get("id"), scenario.get("desc"), scenario.get("input"),
               fixed, [item.get("rule") for item in violations],
               json.dumps(expect, ensure_ascii=False)))


@pytest.mark.parametrize("scenario", SCENARIOS,
                         ids=[str(item.get("id")) for item in SCENARIOS])
def test_chat_scenario(scenario):
    fixed, violations = reply_fact_guard.guard(scenario["input"], _guard_ctx(scenario))
    expect = dict(scenario.get("expect") or {})
    info = _explain(scenario, fixed, violations, expect)
    assert [item.get("rule") for item in violations] == list(expect.get("violations") or []), info
    for word in expect.get("must_not_contain") or []:
        assert word not in fixed, "冲突词 %r 仍在输出里。%s" % (word, info)
    wanted = list(expect.get("must_contain_any") or [])
    if wanted:
        assert any(word in fixed for word in wanted), "改口后应命中 %r 之一。%s" % (wanted, info)
    for word in expect.get("preserve") or []:
        assert word in fixed, "情绪句/细节句 %r 被改掉了。%s" % (word, info)
    if expect.get("violations"):
        assert fixed != scenario["input"], "有冲突却没改写。%s" % info
    else:
        assert fixed == scenario["input"], "没冲突却被改写。%s" % info


def test_golden_has_twenty_scenarios_with_unique_ids():
    assert len(SCENARIOS) == 20
    ids = [item.get("id") for item in SCENARIOS]
    assert len(set(ids)) == len(ids)
    assert all(item.get("desc") for item in SCENARIOS)
    assert all(item.get("input") for item in SCENARIOS)


def test_scenarios_carry_the_documented_ctx_keys():
    for scenario in SCENARIOS:
        ctx = scenario.get("ctx") or {}
        assert set(ctx) == CTX_KEYS, "场景 %s 的 ctx 键不齐: %s" % (
            scenario.get("id"), sorted(set(ctx) ^ CTX_KEYS))


def test_scenarios_cover_all_five_rules_and_only_known_rules():
    used = set()
    for scenario in SCENARIOS:
        rules = list((scenario.get("expect") or {}).get("violations") or [])
        assert set(rules) <= RULES, "场景 %s 出现未知规则 %s" % (scenario.get("id"), rules)
        used.update(rules)
    assert used == RULES, "五类规则没覆盖全: %s" % sorted(RULES - used)


class _FakeState:
    def __init__(self, activity, health):
        self._activity = dict(activity or {})
        self._health = dict(health or {})

    def current_activity(self):
        return dict(self._activity)

    def health_info(self):
        return dict(self._health)


class _FakeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)


class _FakePlugin:
    def __init__(self, activity, health, kv=None):
        self.state = _FakeState(activity, health)
        self.db = _FakeDB(kv)


OFFICE = {"zone": "office", "place": "莞城区的写字楼", "activity": "敲周报",
          "category": "work"}
HOME = {"zone": "home", "place": "家", "activity": "窝在沙发上", "category": "rest"}
HEALTHY = {"status": "健康", "sick": False}
SICK = {"status": "感冒", "sick": True}


def test_production_chain_apply_fact_guard_reads_plugin_state():
    """生产链路：ctx 从 plugin.state/db 读，apply_fact_guard 返回改写后的文本。"""
    when = _dt.datetime(2026, 11, 17, 10, 30)
    plugin = _FakePlugin(OFFICE, HEALTHY)
    ctx = pipeline.fact_guard_context(plugin, None, when)
    assert ctx["activity"] == OFFICE
    assert ctx["health"] == HEALTHY
    fixed = pipeline.apply_fact_guard("我在家躺着呢，晚点再聊", plugin, now=when)
    assert fixed == "我在公司呢，晚点再聊"

    sick_plugin = _FakePlugin(HOME, SICK)
    assert pipeline.apply_fact_guard("我已经好了，别担心我", sick_plugin,
                                     now=when) == "我还是有点不舒服，别担心我"
    # 没冲突的回复保持原样，守卫绝不乱改
    assert pipeline.apply_fact_guard("我在公司忙着呢，晚点聊", plugin,
                                     now=when) == "我在公司忙着呢，晚点聊"
    assert pipeline.apply_fact_guard("", plugin, now=when) == ""


def test_sick_recovery_negated_clause_is_not_rewritten_twice():
    ctx = _guard_ctx({"now": "10:20", "ctx": {
        "zone": "home", "place": "家", "category": "rest", "activity": "躺床上",
        "sick": True, "together": False, "trip": False, "hotel": False,
        "nearby_meal": False, "has_chat": True}})
    fixed, _violations = reply_fact_guard.guard("我已经好了，不烧了", ctx)
    assert fixed.count("还是有点不舒服") == 1
