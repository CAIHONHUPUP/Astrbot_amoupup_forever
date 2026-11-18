# -*- coding: utf-8 -*-
"""photo_share 拆分验收：Mixin 接入、冷却闸门、源码契约与旧名转发。"""
import inspect, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import photo_engine  # noqa: E402
import photo_share  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402

def _engine():
    engine = object.__new__(PhotoEngine)
    engine._suppress_until = 0
    engine._last_sent_at = 0
    return engine

def test_mixin_is_base_of_engine():
    assert issubclass(PhotoEngine, photo_share.PhotoShareMixin)

def test_share_entrypoints_exist():
    for name in ("tick", "_run_share", "_share", "share_now", "_notify_failure", "on_schedule_changed"):
        assert hasattr(photo_share.PhotoShareMixin, name)

def test_old_module_still_exposes_share():
    assert photo_engine.PhotoEngine._share is photo_share.PhotoShareMixin._share

def test_share_never_falls_back_to_empty_shot():
    source = inspect.getsource(PhotoEngine._share)
    assert "空镜兜底已禁用" in source
    assert '_prompt_for("scenery"' not in source

def test_sent_recently_after_note_sent():
    engine = _engine()
    engine.note_sent()
    assert engine.sent_recently() is True
    assert engine.proactive_blocked() is True

def test_suppressed_after_llm_handled():
    engine = _engine()
    assert engine.suppressed() is False
    engine.note_llm_handled()
    assert engine.suppressed() is True
    assert engine.proactive_blocked() is True

def test_notify_failure_without_umo_is_noop():
    engine = object.__new__(PhotoEngine)
    import asyncio
    assert asyncio.run(engine._notify_failure("", "share_failed")) is None

def test_scene_for_kind_mapping():
    assert "风景" in PhotoEngine._scene_for_kind("travel", "家")
    assert "街" in PhotoEngine._scene_for_kind("shopping", "家")
    assert "公园" in PhotoEngine._scene_for_kind("weekend", "家")

def test_share_group_is_not_in_engine_facade_source():
    source = inspect.getsource(photo_engine)
    assert "class PhotoEngine(" in source
    assert "def _share(" not in source
