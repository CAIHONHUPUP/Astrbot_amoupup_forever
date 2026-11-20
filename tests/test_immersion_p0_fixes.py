# -*- coding: utf-8 -*-
"""IMM P0 修复回归测试（task-33）。

覆盖三个真实缺陷 + 反向验证：
1. 整句级复读：与最近 3-5 条 bot 消息相同/≥0.9 相似就删句；整条都重复换短句，
   不同内容、正常短情绪重复不误删；
2. 名单外「人名」误报：笑笑不 / 吃完 / 老样子 不再被当人名，真外名仍然拦；
3. 地点改口整句化：方位词/行程句不再被生拼，改不出通顺句就保留原句。

反向验证：没有近聊历史、地点本来一致、短情绪重复出现时，守卫一律不动。
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "work", "immersion_check")
for _p in (ROOT, TESTS, WORK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_names  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
import place_guard  # noqa: E402
import reply_fact_guard  # noqa: E402
import run_imm as IMM  # noqa: E402

HOME = {"zone": "home", "place": "家", "activity": "窝在沙发上", "category": "rest"}
OFFICE = {"zone": "office", "place": "莞城区的写字楼", "activity": "敲周报",
          "category": "work"}
ROAD = {"zone": "road", "place": "路上", "activity": "开车回去", "category": "commute"}


class _RecentPlugin:
    """只实现 _recent 的最小插件（读最近 bot 行）。"""

    def __init__(self, rows=None):
        self.rows = [dict(row) for row in (rows or [])]

    def _recent(self, umo, count=8):
        return list(self.rows)[-int(count):]

    def add_bot(self, text):
        self.rows.append({"role": "bot", "content": str(text)})


def _ctx(activity, now=None):
    import datetime as _dt
    return {"now": now or _dt.datetime(2026, 11, 17, 10, 30), "activity": activity,
            "health": {"status": "健康", "sick": False}, "nearby": [], "date": {},
            "trip": {}, "today_chat": True}


# ---------------------------------------------------------------------------
# 1) 整句级复读
# ---------------------------------------------------------------------------

def test_sentence_repeat_is_dropped_but_other_sentence_kept():
    plugin = _RecentPlugin([{"role": "bot", "content": "累死了，改了一天表格"}])
    pipeline.begin_status_turn("dup1")
    out = pipeline.apply_narrative_repetition(
        "还没顾上吃呢。累死了，改了一天表格", plugin, "晚上想吃啥", "dup1")
    assert "改了一天表格" not in out, out
    assert "还没顾上吃" in out, out
    assert "累" in out, out  # 情绪句不跟着一起删没
    assert out.strip()


def test_whole_sentence_repeat_becomes_short_and_varies():
    plugin = _RecentPlugin([{"role": "bot", "content": "嗯嗯，我在家呢，想你"}])
    first = pipeline.apply_narrative_repetition(
        "嗯嗯，我在家呢，想你", plugin, "想你", "dup2")
    assert first.strip() and first != "嗯嗯，我在家呢，想你"
    assert "想你" in first  # 情绪保留
    plugin.add_bot(first)
    second = pipeline.apply_narrative_repetition(
        "嗯嗯，我在家呢，想你", plugin, "我也想你", "dup2")
    assert second.strip() and second != first
    pipeline.begin_status_turn("dup2")
    third = pipeline.apply_narrative_repetition(
        "马上睡了，再刷五分钟", plugin, "五分钟到了", "dup2")
    assert third.strip()


def test_near_duplicate_sentence_is_dropped():
    plugin = _RecentPlugin([{"role": "bot", "content": "我刚从公园溜达回来"}])
    pipeline.begin_status_turn("dup3")
    out = pipeline.apply_narrative_repetition(
        "我刚从公园溜达回来呀。外面风挺大", plugin, "好", "dup3")
    assert "溜达回来" not in out
    assert "风挺大" in out


def test_different_content_and_short_emotion_are_not_dropped():
    plugin = _RecentPlugin([{"role": "bot", "content": "我今天有点累。抱抱"}])
    pipeline.begin_status_turn("dup4")
    out = pipeline.apply_narrative_repetition(
        "那我给你捶捶背。抱抱", plugin, "好", "dup4")
    assert out == "那我给你捶捶背。抱抱"


def test_reverse_no_recent_history_keeps_sentence():
    plugin = _RecentPlugin([])
    pipeline.begin_status_turn("dup5")
    raw = "嗯嗯，我在家呢，想你"
    assert pipeline.apply_narrative_repetition(raw, plugin, "想你", "dup5") == raw


def test_segment_guard_replaces_repeat_and_keeps_distinct_segment():
    plugin = _RecentPlugin([{"role": "bot", "content": "好呀，我在呢。"}])
    pipeline.begin_status_turn("seg1")
    repeat = pipeline.apply_segment_repeat_guard("好呀，我在呢。", plugin, "seg1")
    assert repeat.strip() and repeat != "好呀，我在呢。"
    distinct = pipeline.apply_segment_repeat_guard("刚泡了杯茶。", plugin, "seg1")
    assert distinct == "刚泡了杯茶。"


def test_segment_guard_reverse_without_history_keeps_text():
    plugin = _RecentPlugin([])
    pipeline.begin_status_turn("seg2")
    raw = "好呀，我在呢。"
    assert pipeline.apply_segment_repeat_guard(raw, plugin, "seg2") == raw


def test_model_leak_deflection_rotates():
    plugin = _RecentPlugin([])
    first = pipeline._model_leak_deflection(plugin, "leak1")
    plugin.add_bot(first)
    second = pipeline._model_leak_deflection(plugin, "leak1")
    assert first.strip() and second.strip()
    assert second != first


# ---------------------------------------------------------------------------
# 2) foreign_names 误报
# ---------------------------------------------------------------------------

def test_common_phrases_are_not_taken_as_foreign_names():
    for text in ("问了，我就笑笑不说话", "吃完跟我说说吃了啥", "还是老样子。",
                 "我明白你说的意思"):
        assert amo_names.foreign_names(text) == [], text
        assert amo_names.sanitize(text) == text, text


def test_real_foreign_name_is_still_caught():
    assert amo_names.foreign_names("李婷约我吃饭") == ["李婷"]
    assert amo_names.foreign_names("被王强骂了一顿") == ["王强"]
    cleaned = amo_names.sanitize("李婷约我吃饭，说她最近很忙")
    assert "李婷" not in cleaned and "有人" in cleaned


def test_roster_and_aliases_still_pass():
    for name in amo_names.ROSTER:
        assert amo_names.foreign_names(name + "约我明天逛街") == [], name
    assert amo_names.foreign_names("老公说今晚一起吃饭") == []


def test_reverse_sentence_final_negation_is_not_a_name():
    # 「笑笑不」的「不」是句尾否定，不是名字的一部分
    assert amo_names._suspect("笑笑不") is False
    assert amo_names._suspect("吃完") is False
    assert amo_names._suspect("老样子") is False
    assert amo_names._suspect("李婷") is True


# ---------------------------------------------------------------------------
# 3) 地点改口语法
# ---------------------------------------------------------------------------

def test_workplace_place_is_rewritten_as_whole_clause():
    fixed, violations = reply_fact_guard.guard("在工位上改周报呢，头都大了", _ctx(HOME))
    assert [item["rule"] for item in violations] == ["place"]
    assert "在家改周报呢" in fixed
    assert "家上" not in fixed
    assert "头都大了" in fixed


def test_transit_sentence_is_rewritten_as_arrival():
    fixed, violations = reply_fact_guard.guard("还在路上，快到了", _ctx(HOME))
    assert [item["rule"] for item in violations] == ["place"]
    assert fixed == "已经到家了"
    assert "还在家" not in fixed and "家快到了" not in fixed


def test_noun_tail_keeps_natural_sentence():
    fixed, _violations = reply_fact_guard.guard(
        "老公今天中午吃啥子嘛，我这边路上饭点还没到，先馋一下你", _ctx(HOME))
    assert "路上" not in fixed
    assert "我这边饭点还没到" in fixed


def test_motion_origin_sentence_is_left_alone():
    raw = "我刚从公园溜达回来，坐草地上发了会儿呆"
    fixed, violations = reply_fact_guard.guard(raw, _ctx(HOME))
    assert fixed == raw
    assert violations == []


def test_place_reverse_matching_place_is_untouched():
    raw = "我在工位上改周报呢"
    assert reply_fact_guard.guard(raw, _ctx(OFFICE))[0] == raw
    raw2 = "还在路上，快到了"
    assert reply_fact_guard.guard(raw2, _ctx(ROAD))[0] == raw2


def test_place_tone_variants_never_glue_localizers():
    plugin_rows = []
    plugin = _RecentPlugin(plugin_rows)
    fixed, violations = reply_fact_guard.guard("在工位上改周报呢，头都大了", _ctx(HOME))
    for _ in range(8):
        out, _actions = pipeline.naturalize_fact_rewrites(
            fixed, [dict(item) for item in violations], plugin, HOME,
            _ctx(HOME)["now"])
        assert "家上" not in out and "在家上" not in out, out
        assert "改周报" in out and "头都大了" in out, out


def test_place_guard_drops_localizer():
    assert place_guard.rewrite_reply("我在工位上改周报呢", HOME) == "我在家改周报呢"
    assert "公司" in place_guard.rewrite_reply("我在家躺着呢", OFFICE)


def test_broken_rewrite_detector_flags_bad_and_allows_natural():
    bad = ("在家上改周报", "这会儿在家上改周报", "还在家快到了", "家待着快到了",
           "家这边饭点还没到", "家窝着饭点还没到", "还在家溜")
    natural = ("在家窝着呢，怎么啦", "我还在家呢", "我在家改周报呢",
               "已经到家了", "在家待着呢")
    for text in bad:
        assert IMM.BROKEN_REWRITE_RE.search(text), text
    for text in natural:
        assert not IMM.BROKEN_REWRITE_RE.search(text), text
