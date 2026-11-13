# -*- coding: utf-8 -*-
"""自拍不重复：全程离线，不调用生图、不发消息，只验去重闸门。"""
import asyncio
import time
import types

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from draw_engine import DrawEngine  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402


def _photo():
    engine = PhotoEngine.__new__(PhotoEngine)
    engine._suppress_until = 0
    engine._last_sent_at = 0
    return engine


def test_proactive_blocked_by_pending_llm_photo():
    photo = _photo()
    assert photo.proactive_blocked() is False
    photo.note_llm_handled()
    assert photo.suppressed() is True
    assert photo.proactive_blocked() is True


def test_proactive_blocked_after_a_photo_was_sent():
    photo = _photo()
    photo.note_sent()
    assert photo.sent_recently() is True
    assert photo.proactive_blocked() is True


def test_draw_engine_skips_auto_photo_when_llm_already_sent():
    engine = DrawEngine.__new__(DrawEngine)
    photo = _photo()
    photo.note_sent()
    engine.plugin = types.SimpleNamespace(photo=photo)
    # 不触发生成：这一轮已经发过图，直接返回 None
    assert engine.maybe_after_reply(None, "拍好了") is None


def test_draw_engine_claims_slot_before_generating():
    engine = DrawEngine.__new__(DrawEngine)
    photo = _photo()
    engine.plugin = types.SimpleNamespace(photo=photo)
    engine.decide = lambda event, text: {"prompt": "p", "reason": "topic", "cached": None,
                                        "cache_key": "", "selfie": True, "plan": None}
    engine.missing_omni_notice = lambda event: ""
    scheduled = []
    engine._schedule = lambda *a, **k: scheduled.append(a)
    assert engine.maybe_after_reply(None, "发张自拍") is not None
    assert scheduled and photo.sent_recently() is True
    # 生成期间再来一轮：不会再排第二张
    assert engine.maybe_after_reply(None, "再来一张") is None


class _ShareEngine(PhotoEngine):
    enabled = True


def test_share_marks_sent_only_after_success():
    engine = _ShareEngine.__new__(_ShareEngine)
    engine._cfg_int = lambda key, default=0: default
    engine.today_count = lambda: 0
    calls = []
    engine._target_umo = lambda profile: "umo"
    engine._notify_failure = lambda umo, reason="": calls.append(reason)

    async def fake_share(kind, scene, profile, **kw):
        return True

    engine._share = fake_share
    engine._last_sent_at = 0
    asyncio.run(engine._run_share("daily", "scene", {}))
    assert engine.sent_recently() is True
    assert calls == []
