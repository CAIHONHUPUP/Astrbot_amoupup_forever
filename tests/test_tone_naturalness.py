# -*- coding: utf-8 -*-
"""N2 守卫改写自然度：固定句 -> 语气池（种子轮换、不连续重复）。

覆盖：
1. 同一冲突（地点/活动/吃饭/健康/陪伴）多种自然说法；
2. 第一次命中保留旧固定句（旧 golden 兼容），之后轮换且不连续重复；
3. 事实仍正确、情绪句保留、只改冲突小句；
4. 心情/关系/场景选不同语气组；
5. AB 机械率：连续 20 次命中，固定句占比 <= 2；
6. 反向验证 >=3：去掉轮换/让轮换失败/清空语气池，固定句就回来。
"""
from __future__ import annotations

import datetime as _dt
import inspect
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
SLEEP = {"start": "09:00", "end": "12:00", "zone": "home", "place": "家",
         "activity": "补觉", "category": "sleep"}


class _DB:
    def kv_get(self, key, default=None):
        return default


class _State:
    def __init__(self, activity, health, mood, relation):
        self.schedule = {DAY: [dict(activity)]}
        self.health = dict(health)
        self.mood = dict(mood or {})
        self.relation = relation
        self.db = _DB()

    def health_info(self):
        return dict(self.health)

    def current_activity(self):
        return dict(self.schedule[DAY][0]) if self.schedule[DAY] else {}


class _Wallet:
    def ledger(self, limit=200):
        return []


class _Plugin:
    def __init__(self, activity=OFFICE, health=HEALTHY, mood=None, relation=""):
        self.state = _State(activity, health, mood, relation)
        self.db = _DB()
        self.wallet = _Wallet()
        self.relation = relation


def _plugin(**kwargs):
    plugin = _Plugin(**kwargs)
    pipeline.reset_tone_naturalness(plugin)
    return plugin


def _guard_parts(text, plugin):
    ctx = pipeline.fact_guard_context(plugin, None, NOW)
    return reply_fact_guard.guard(text, ctx)


def _rotate_guard(fixed, violations, plugin, times=8):
    outs = []
    for _ in range(times):
        out, _actions = pipeline.naturalize_fact_rewrites(
            fixed, violations, plugin, None, NOW)
        outs.append(out)
    return outs


# ---------------------------------------------------------------------------
# 同一冲突多种自然说法
# ---------------------------------------------------------------------------

def test_place_conflict_has_many_natural_phrasings():
    plugin = _plugin()
    fixed, violations = _guard_parts("我在家躺着呢，好累呀", plugin)
    assert [item["rule"] for item in violations] == ["place"]
    outs = _rotate_guard(fixed, violations, plugin)
    assert outs[0] == fixed
    assert len(set(outs)) >= 5
    for out in outs:
        assert "公司" in out
        assert "家" not in out
        assert "好累呀" in out


def test_companion_conflict_has_many_natural_phrasings():
    plugin = _plugin(activity=HOME)
    fixed, violations = _guard_parts("他刚抱我了，我有点难过", plugin)
    assert [item["rule"] for item in violations] == ["companion"]
    outs = _rotate_guard(fixed, violations, plugin)
    assert len(set(outs)) >= 5
    for out in outs:
        assert "抱" not in out and "旁边" not in out
        assert "我有点难过" in out


def test_health_conflict_has_many_natural_phrasings():
    plugin = _plugin(activity=HOME)
    fixed, violations = _guard_parts("我还烧着呢，别担心", plugin)
    assert [item["rule"] for item in violations] == ["health"]
    outs = _rotate_guard(fixed, violations, plugin)
    assert len(set(outs)) >= 5
    for out in outs:
        assert "烧" not in out
        assert "别担心" in out


def test_meal_conflict_has_many_natural_phrasings():
    plugin = _plugin()
    fixed, violations = _guard_parts("我正在吃呢，有点饿", plugin)
    assert [item["rule"] for item in violations] == ["meal"]
    outs = _rotate_guard(fixed, violations, plugin)
    assert len(set(outs)) >= 5
    for out in outs:
        assert "正在吃" not in out and "刚吃完" not in out
        assert "有点饿" in out


def test_activity_conflict_has_many_natural_phrasings():
    plugin = _plugin(activity=SLEEP)
    fixed, violations = _guard_parts("我在上班呢，好困", plugin)
    assert [item["rule"] for item in violations] == ["activity"]
    outs = _rotate_guard(fixed, violations, plugin)
    assert len(set(outs)) >= 5
    for out in outs:
        assert "上班" not in out
        assert "好困" in out


# ---------------------------------------------------------------------------
# 轮换 / 不连续重复 / 只改冲突小句
# ---------------------------------------------------------------------------

def test_no_consecutive_repeat_for_every_rule():
    cases = [
        ("place", "我在家躺着呢，好累呀", OFFICE),
        ("companion", "他刚抱我了，我有点难过", HOME),
        ("health", "我还烧着呢，别担心", HOME),
        ("meal", "我正在吃呢，有点饿", OFFICE),
        ("activity", "我在上班呢，好困", SLEEP),
    ]
    for rule, text, activity in cases:
        plugin = _plugin(activity=activity)
        fixed, violations = _guard_parts(text, plugin)
        outs = _rotate_guard(fixed, violations, plugin, times=10)
        for before, after in zip(outs, outs[1:]):
            assert before != after, "%s 连续重复: %r" % (rule, before)


def test_only_conflicting_clause_changes_and_emotion_is_kept():
    plugin = _plugin()
    text = "今天好累呀，我在家躺着呢，你早点睡"
    fixed, violations = _guard_parts(text, plugin)
    out, _actions = pipeline.naturalize_fact_rewrites(
        fixed, violations, plugin, None, NOW)
    assert out.startswith("今天好累呀，")
    assert out.endswith("，你早点睡")
    assert "公司" in out and "家" not in out


def test_rotation_is_deterministic_for_same_seed():
    def run():
        plugin = _plugin()
        fixed, violations = _guard_parts("我在家躺着呢，好累呀", plugin)
        return _rotate_guard(fixed, violations, plugin, times=6)

    assert run() == run()


def test_health_first_variant_rotates_by_seed():
    """3c：health 池第一次就按种子散开，不再永远先出「就是有点累」。"""
    pool = pipeline.TONE_POOLS["health"]["default"]
    outs = {pipeline._pick_tone_variant(pool, "health|%d" % seed, {}, "seed-%d" % seed,
                                        rotate_first=True)
            for seed in range(12)}
    assert len(outs) > 1, outs
    assert pipeline._pick_tone_variant(pool, "health|fixed", {}, "seed") == pool[0]


def test_mood_and_relation_select_different_groups():
    sweet = _plugin(relation="恋人")
    plain = _plugin()
    assert pipeline._tone_group("health", sweet, None, "就是有点累") == "sweet"
    assert pipeline._tone_group("health", plain, None, "就是有点累") == "default"
    assert pipeline._tone_group("companion", sweet, None, "刚才在手机上聊了几句") == "close"
    assert pipeline._tone_group("companion", plain, None, "刚才在手机上聊了几句") == "default"
    tired = _plugin(activity=HOME, mood={"疲惫": 70})
    assert pipeline._tone_group("health", tired, None, "就是有点累") == "tired"


def test_all_tone_pools_have_five_to_eight_variants():
    for rule, groups in pipeline.TONE_POOLS.items():
        for name, pool in groups.items():
            assert 5 <= len(pool) <= 8, "%s/%s 变体数 %d" % (rule, name, len(pool))
    assert 5 <= len(pipeline.RECOVERY_TONE) <= 8
    for name, pool in pipeline.MEAL_TONE.items():
        assert 5 <= len(pool) <= 8, "meal/%s" % name
    for name, pool in pipeline.ACTIVITY_TONE.items():
        assert 5 <= len(pool) <= 8, "activity/%s" % name
    for scene, pool in pipeline.PLACE_TONE.items():
        assert 5 <= len(pool) <= 8, "place/%s" % scene


def test_empty_violations_and_empty_text_are_safe():
    assert pipeline.naturalize_fact_rewrites("", []) == ("", [])
    assert pipeline.naturalize_fact_rewrites("原文", []) == ("原文", [])
    assert pipeline.naturalize_fact_rewrites("原文", None) == ("原文", [])


def test_ab_mechanical_rate_drops_after_first_hit():
    plugin = _plugin()
    fixed, violations = _guard_parts("我在家躺着呢，好累呀", plugin)
    outs = _rotate_guard(fixed, violations, plugin, times=20)
    mechanical = sum(1 for out in outs if out == fixed)
    assert mechanical <= 2, "20 条里固定句 %d 条" % mechanical


# ---------------------------------------------------------------------------
# 反向验证：去掉轮换 / 轮换失败 / 清空语气池，固定句必须回来
# ---------------------------------------------------------------------------

def test_reverse_without_rotation_picker_keeps_guard_sentence(monkeypatch):
    plugin = _plugin()
    fixed, violations = _guard_parts("我在家躺着呢，好累呀", plugin)
    monkeypatch.setattr(pipeline, "_pick_tone_variant", lambda *a, **k: "")
    out = pipeline.apply_fact_guard("我在家躺着呢，好累呀", plugin, now=NOW)
    assert out == fixed


def test_reverse_when_naturalizer_raises_guard_result_survives(monkeypatch):
    plugin = _plugin()
    fixed, _violations = _guard_parts("我在家躺着呢，好累呀", plugin)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "naturalize_fact_rewrites", boom)
    out = pipeline.apply_fact_guard("我在家躺着呢，好累呀", plugin, now=NOW)
    assert out == fixed


def test_reverse_empty_place_pool_keeps_guard_sentence(monkeypatch):
    plugin = _plugin()
    fixed, _violations = _guard_parts("我在家躺着呢，好累呀", plugin)
    monkeypatch.setitem(pipeline.PLACE_TONE, "work", ())
    monkeypatch.setitem(pipeline.PLACE_TONE, "default", ())
    out = pipeline.apply_fact_guard("我在家躺着呢，好累呀", plugin, now=NOW)
    assert out == fixed


def test_reverse_source_wiring_is_visible():
    """反向验证：apply_fact_guard 不接语气池、或语气池被清空，测试就红。"""
    source = inspect.getsource(pipeline.apply_fact_guard)
    assert "naturalize_fact_rewrites" in source
    assert "PLACE_TONE" in inspect.getsource(pipeline._tone_candidates)
    assert "MEAL_TONE" in inspect.getsource(pipeline._tone_candidates)
