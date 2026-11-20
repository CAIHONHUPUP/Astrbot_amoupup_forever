# -*- coding: utf-8 -*-
"""居住关系/在场场景（2026-11-20）。"""
from __future__ import annotations

import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_household as hh  # noqa: E402


class _DB:
    def __init__(self):
        self.data = {}

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value

    def kv_delete(self, key):
        self.data.pop(key, None)


class _Plugin:
    def __init__(self, stage=hh.STAGE_APART):
        self.db = _DB()
        self.stage = stage
        self._last_uid = "10001"
        self.written = []
        self.db.kv_set("household", {"v": 2, "stage": stage})

    def cfg_bool(self, key, default=False):
        return default

    def cfg_str(self, key, default=""):
        return default

    def cfg_int(self, key, default=0):
        return default

    async def _write_memory_checked(self, uid, content, kind="fact", tier=1):
        self.written.append((uid, content, kind))


def test_blank_defaults_to_apart():
    p = _Plugin(hh.STAGE_APART)
    assert hh.stage_of(p) == hh.STAGE_APART


def test_apart_home_is_alone():
    p = _Plugin(hh.STAGE_APART)
    scene = hh.scene_at(p, "2026-11-20", "10:00", "11:00", "家")
    assert scene.presence == hh.PRESENCE_ALONE
    assert scene.partner is False


def test_part_time_weekend_window():
    p = _Plugin(hh.STAGE_PART_TIME)
    # 2026-11-20 周五
    assert hh.scene_at(p, "2026-11-20", "19:00", "20:00", "家").partner is False
    assert hh.scene_at(p, "2026-11-20", "21:00", "22:00", "家").partner is True
    # 周六全天
    assert hh.scene_at(p, "2026-11-21", "15:00", "16:00", "家").partner is True
    # 周日 22:00 前
    assert hh.scene_at(p, "2026-11-22", "21:00", "22:00", "家").partner is True
    assert hh.scene_at(p, "2026-11-22", "22:30", "23:30", "家").partner is False
    # 周四晚上不在
    assert hh.scene_at(p, "2026-11-19", "21:00", "22:00", "家").partner is False


def test_together_partner_at_work_is_alone():
    p = _Plugin(hh.STAGE_TOGETHER)
    assert hh.scene_at(p, "2026-11-20", "10:00", "11:00", "家").partner is False
    assert hh.scene_at(p, "2026-11-20", "20:00", "21:00", "家").partner is True
    assert hh.scene_at(p, "2026-11-21", "10:00", "11:00", "家").partner is True


def test_together_outside_is_alone_unless_date():
    p = _Plugin(hh.STAGE_TOGETHER)
    assert hh.scene_at(p, "2026-11-20", "10:00", "11:00", "公司").partner is False
    assert hh.scene_at(p, "2026-11-20", "10:00", "11:00", "公司",
                       together_hint=True).partner is True


def test_date_hint_overrides_every_stage():
    for stage in hh.STAGES:
        p = _Plugin(stage)
        scene = hh.scene_at(p, "2026-11-20", "10:00", "11:00", "商场",
                            together_hint=True)
        assert scene.presence == hh.PRESENCE_DATE
        assert scene.partner is True


def test_count_night_is_daily_idempotent():
    p = _Plugin(hh.STAGE_PART_TIME)
    scene = hh.scene_at(p, "2026-11-21", "23:00", "23:30", "家")
    hh.count_night(p, "2026-11-21", scene)
    hh.count_night(p, "2026-11-21", scene)
    assert hh.load(p)["night_count"] == 1


def test_ready_after_threshold(monkeypatch):
    p = _Plugin(hh.STAGE_PART_TIME)
    monkeypatch.setattr(hh, "to_together_nights", lambda plugin: 2)
    scene = hh.scene_at(p, "2026-11-21", "23:00", "23:30", "家")
    hh.count_night(p, "2026-11-21", scene)
    assert hh.load(p)["ready"] is False
    hh.count_night(p, "2026-11-22", scene)
    assert hh.load(p)["ready"] is True


def test_force_transition_any_stage():
    p = _Plugin(hh.STAGE_APART)
    ok, msg = hh.transition(p, hh.STAGE_TOGETHER, force=True, reason="test")
    assert ok, msg
    assert hh.stage_of(p) == hh.STAGE_TOGETHER
    assert hh.load(p)["history"][-1]["to"] == hh.STAGE_TOGETHER


def test_non_force_transition_blocks_skip():
    p = _Plugin(hh.STAGE_APART)
    ok, msg = hh.transition(p, hh.STAGE_TOGETHER, force=False)
    assert ok is False
    assert "请先" in msg


def test_wording_apart_is_empty_for_legacy_fallback():
    p = _Plugin(hh.STAGE_APART)
    assert hh.wording("boundary", plugin=p) == ""
    assert hh.wording("worldview", plugin=p) == ""


def test_wording_together_has_boundary():
    p = _Plugin(hh.STAGE_TOGETHER)
    text = hh.wording("boundary", plugin=p)
    assert "【动作归属·最高优先级】" in text
    assert "住在一起" in text


def test_presence_line_always_present():
    for stage in hh.STAGES:
        p = _Plugin(stage)
        line = hh.presence_line(p)
        assert line.startswith("【居住】")


def test_fact_rules_home_branch():
    from amo_diary_guard import fact_rules
    assert "住在一起" in fact_rules(False, home=True)
    assert "住在一起" not in fact_rules(False)


def test_presence_claim_allowed_at_home():
    from amo_diary_guard import _diary_contradictions
    hits = _diary_contradictions("他抱着我，靠在沙发上看电视。", {"partner_home": True})
    assert not [h for h in hits if h["rule"] == "companion"]
    hits = _diary_contradictions("他抱着我，靠在沙发上看电视。", {"partner_home": False})
    assert [h for h in hits if h["rule"] == "companion"]


def test_claims_today_interaction_allows_home_daily():
    from amo_diary_guard import claims_today_interaction
    text = "今天他跟我说晚上一起吃饭。"
    assert claims_today_interaction(text, "", None) is True
    assert claims_today_interaction(text, "", None, partner_home=True) is False


def test_schedule_guard_uses_block_time():
    from amo_state_schedule import ScheduleStateMixin

    class _State:
        p = _Plugin(hh.STAGE_TOGETHER)
        ACTIVITY_GUARD_SOURCES = ("jit",)

        def _date_approved(self):
            return False

    state = _State()
    method = ScheduleStateMixin._guard_activity_subjects.__get__(state, _State)
    items = [{"activity": "和老公在家看电影", "start": "10:00", "end": "11:00",
              "place": "家", "category": "rest"}]
    out = method(items, "jit", "2026-11-20")
    assert "闺蜜" in out[0]["activity"]  # 工作日 10:00 他在上班
    items[0]["start"] = "21:00"
    items[0]["end"] = "22:00"
    out = method(items, "jit", "2026-11-20")
    assert "老公" in out[0]["activity"]  # 同居晚上在家


def test_diary_presence_rules_home_branch():
    from schedule_plan import diary_presence_rules
    scene = hh.scene_at(_Plugin(hh.STAGE_TOGETHER), "2026-11-20", "21:00", "22:00", "家")
    text = diary_presence_rules(False, scene=scene)
    assert "住在一起" in text
    assert "他不在我身边" in text  # 作为禁止项出现
