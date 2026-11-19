# -*- coding: utf-8 -*-
"""heartflow_state 拆分验收：Mixin 接入、暂停/沉浸/计数行为与缺失方法回归。"""
import os, sys, threading, time

import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import clock  # noqa: E402
import heartflow_engine  # noqa: E402
import heartflow_state  # noqa: E402
from heartflow_engine import HeartflowEngine  # noqa: E402

def _engine():
    e = HeartflowEngine.__new__(HeartflowEngine)
    e.chat_states = {}
    e._lock = threading.RLock()
    e._pause_restore_tasks = {}
    e.judgment_paused_timeout = 60
    e.enable_detailed_log = False
    e._whitelist_set = {"123"}
    e.group_threshold_configs = {}
    e.reply_threshold = 0.6
    e.dynamic_threshold_max = 0.85
    e.dynamic_threshold_min = 0.35
    e.enable_dynamic_threshold = False
    e.score_min = 1.0
    e.score_max = 10.0
    e.immersive_mode_duration = 30
    e.config = {}
    return e

def test_mixin_is_base_of_engine():
    assert issubclass(HeartflowEngine, heartflow_state.HeartflowStateMixin)

def test_log_detail_method_present():
    # 拆分时漏搬过 _log_detail，这条防回归
    assert hasattr(HeartflowEngine, "_log_detail")
    assert HeartflowEngine._log_detail is heartflow_state.HeartflowStateMixin._log_detail

def test_get_chat_state_is_stable():
    e = _engine()
    first = e._get_chat_state("g:1")
    assert e._get_chat_state("g:1") is first

def test_minutes_since_last_reply():
    e = _engine()
    state = e._get_chat_state("g:1")
    state.last_reply_time = clock.now_ts() - 120
    assert e._get_minutes_since_last_reply("g:1") == 2

def test_judgment_paused_roundtrip():
    e = _engine()
    e._set_judgment_paused("g:1", True)
    assert e._is_judgment_paused("g:1") is True
    e._set_judgment_paused("g:1", False)
    assert e._is_judgment_paused("g:1") is False

def test_judgment_pause_times_out():
    e = _engine()
    state = e._get_chat_state("g:1")
    state.judgment_paused = True
    state.judgment_paused_at = clock.now_ts() - 999
    assert e._is_judgment_paused("g:1") is False

def test_whitelist_matches_group_id():
    e = _engine()
    assert e._is_whitelisted("group:100:123") is True
    assert e._is_whitelisted("group:100:456") is False

def test_dynamic_threshold_disabled():
    e = _engine()
    assert e._get_dynamic_threshold("g:1") == pytest.approx((0.6, 0.0, 6.4))

def test_immersive_mode_activate_and_remaining():
    e = _engine()
    assert e._is_immersive_mode_active("g:1") is False
    e._activate_immersive_mode("g:1")
    assert e._is_immersive_mode_active("g:1") is True
    assert 0 < e._get_immersive_mode_remaining("g:1") <= 30

def test_activity_description_is_text():
    e = _engine()
    assert isinstance(e._get_activity_description(0.5), str)
    assert e._get_activity_description(0.5) != ""
