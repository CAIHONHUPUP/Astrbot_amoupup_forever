# -*- coding: utf-8 -*-
"""task-25 场景写者迁移验收（2026-11-22）。

覆盖：
1. JIT 到点决策按 scene_ledger 硬约束钳制（病假不写公司/通勤）；
2. 过渡引擎 start/_ensure_blocks/finish 在请假/出差日不启动、不落块、不写记忆；
3. 日程唯一落库点 _commit_schedule_change_locked 前的 _constrain_scene_items 钳制；
4. build_snapshot 改读 scene_at（病假在家时被旧写入污染成公司 → 快照仍是家）；
5. 摘要/里程碑/记忆写者场景守卫（只改「现在/此刻」，过去事实不动）；
6. shopping/date 事实带 scene_id；
7. photo 发送前 verify_photo_scene 的判定口径。
"""
from __future__ import annotations

import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_date_facts as DF  # noqa: E402
import amo_scene_ledger as L  # noqa: E402
import amo_state_jit as JIT  # noqa: E402
import amo_state_schedule as SS  # noqa: E402
import amo_state_snapshot as Snap  # noqa: E402
import amo_transition as T  # noqa: E402

NOW = datetime.datetime(2026, 11, 18, 10, 0, 0)
DAY = "2026-11-18"
LEAVE_KV = {"mood_applied:leave:2026-11-18:事假一天:mood": True}


class FakeDb:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def kv_keys(self, prefix="", limit=200, offset=0):
        keys = [key for key in sorted(self.kv) if str(key).startswith(str(prefix or ""))]
        return keys[offset:offset + limit]

    def schedule_version(self, day):
        return 0


class FakeState:
    def __init__(self, activity=None, reason="schedule", sick=False, snapshot=None):
        self.activity = dict(activity or {})
        self.reason = reason
        self.sick = sick
        self._snapshot = snapshot

    def current_activity_info(self):
        return dict(self.activity), self.reason

    def current_activity(self):
        return dict(self.activity)

    def snapshot(self):
        if self._snapshot is not None:
            return dict(self._snapshot)
        return {"activity": dict(self.activity)}

    def on_sick_leave(self, day=None):
        return bool(self.sick)

    def sick_decision(self, day=None):
        return {"action": "take_sick_leave"} if self.sick else {}

    def _trip_day_kind(self, day=None):
        return ""


class FakePlugin:
    def __init__(self, state, kv=None):
        self.state = state
        self.db = FakeDb(kv)


def home_state(**kwargs):
    return FakeState({"place": "家", "zone": "home", "activity": "在家休息"}, **kwargs)


def office_state(**kwargs):
    return FakeState({"place": "公司", "zone": "office", "activity": "在工位上忙工作",
                      "event_id": "ev-office"}, **kwargs)


class JitSceneConstraintTests(unittest.TestCase):
    class Host(JIT.JitStateMixin):
        def __init__(self, state):
            self.state = state
            self.db = state.db
            self.p = None
            self.schedule = {}

    def _host(self, state):
        state.db = FakeDb()
        return self.Host(state)

    def test_sick_leave_clamps_office_plan(self):
        host = self._host(office_state(sick=True))
        plan = {"action": "在工位上忙工作", "place": "公司", "zone": "office",
                "category": "work", "cost_cents": 0}
        plan["cost_cents"] = 5800
        plan["cost"] = {"kind": "shopping"}
        fixed = JIT._scene_constrain_plan(host, plan, DAY, NOW)
        self.assertEqual(fixed["place"], "家")
        self.assertEqual(fixed["zone"], "home")
        self.assertEqual(fixed["cost_cents"], 0)
        self.assertNotIn("cost", fixed)
        self.assertIn("请假", str(fixed["action"]))
        self.assertTrue(fixed.get("_scene_clamped"))
        audit = L.audit_entries(host)
        self.assertTrue(any(row.get("source") == "jit" for row in audit), audit)

    def test_leave_day_blocks_office_plan(self):
        state = home_state()
        state.db = FakeDb(LEAVE_KV)
        host = self.Host(state)
        fixed = JIT._scene_constrain_plan(
            host, {"action": "去公司上班", "place": "公司", "zone": "office",
                  "category": "work"}, DAY, NOW)
        self.assertEqual(fixed["place"], "家")

    def test_normal_day_keeps_office_plan(self):
        state = office_state()
        state.db = FakeDb()
        host = self.Host(state)
        fixed = JIT._scene_constrain_plan(
            host, {"action": "在工位上忙工作", "place": "公司", "zone": "office",
                  "category": "work"}, DAY, NOW)
        self.assertEqual(fixed["place"], "公司")
        self.assertNotIn("_scene_clamped", fixed)

    def test_persist_writes_zone_into_current_action(self):
        state = home_state()
        state.db = FakeDb()
        host = self.Host(state)
        host._jit_persist(DAY, 10, {"action": "在家休息", "place": "家", "zone": "home",
                                    "category": "rest"}, "jit:%s:10" % DAY, True)
        action = host.db.kv_get("current_action", {})
        self.assertEqual(action.get("zone"), "home")
        self.assertEqual(action.get("source"), "jit")


class TransitionConstraintTests(unittest.TestCase):
    def test_leave_blocks_mall_destination(self):
        p = FakePlugin(home_state(), LEAVE_KV)
        self.assertEqual(T._hard_constraint_block(p, "商场", NOW), "leave")
        self.assertEqual(T._hard_constraint_block(p, "家", NOW), "")

    def test_sick_leave_blocks_company_destination(self):
        p = FakePlugin(office_state(sick=True))
        self.assertEqual(T._hard_constraint_block(p, "公司", NOW), "sick_leave")
        self.assertEqual(T._hard_constraint_block(p, "医院", NOW), "")

    def test_start_refuses_offsite_on_leave(self):
        p = FakePlugin(home_state(), LEAVE_KV)
        self.assertIsNone(T.start(p, "commute", "商场", now=NOW))

    def test_ensure_blocks_cancels_offsite_on_leave(self):
        p = FakePlugin(home_state(), LEAVE_KV)
        data = {"kind": "commute", "from": "家", "to": "商场", "steps": [
            {"name": "在路上", "minutes": 30, "place": "地铁", "zone": "road",
             "action": "在地铁上"}],
            "started_at": NOW.timestamp() - 60, "blocks_written": False}
        p.db.kv_set(T.STATE_KEY, dict(data))
        T._ensure_blocks(p, data, NOW)
        self.assertTrue(data.get("cancelled"))
        self.assertEqual(p.db.kv_get(T.STATE_KEY, {}), {})

    def test_finish_refuses_offsite_on_leave(self):
        p = FakePlugin(home_state(), LEAVE_KV)
        data = {"kind": "commute", "from": "家", "to": "商场", "steps": [
            {"name": "在路上", "minutes": 30, "place": "地铁", "zone": "road",
             "action": "在地铁上"}],
            "started_at": NOW.timestamp() - 3600}
        self.assertIsNone(T.finish(p, data, now=NOW))
        self.assertEqual(p.db.kv_get(T.STATE_KEY, {}), {})


class ScheduleConstrainTests(unittest.TestCase):
    class Host(SS.ScheduleStateMixin):
        def __init__(self, state):
            self.state = state
            self.db = state.db
            self.p = None
            self.schedule = {}
            self.schedule_ctx = {}

    def _host(self, state):
        state.db = FakeDb()
        return self.Host(state)

    def test_sick_day_clamps_schedule_rows(self):
        host = self._host(office_state(sick=True))
        rows = [
            {"start": "10:00", "end": "11:00", "activity": "在工位上忙工作",
             "place": "公司", "zone": "office", "category": "work", "event_id": "ev1"},
            {"start": "11:00", "end": "12:00", "activity": "在家休息",
             "place": "家", "zone": "home", "category": "rest"},
        ]
        kept, clamped = host._constrain_scene_items(DAY, rows)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(clamped), 1)
        self.assertEqual(kept[0]["place"], "家")
        self.assertEqual(kept[0]["scene_constraint"], "sick_leave")
        self.assertEqual(kept[1]["place"], "家")
        self.assertNotIn("scene_constraint", kept[1])

    def test_normal_day_unchanged(self):
        host = self._host(office_state())
        rows = [{"start": "10:00", "end": "11:00", "activity": "在工位上忙工作",
                 "place": "公司", "zone": "office", "category": "work"}]
        kept, clamped = host._constrain_scene_items(DAY, rows)
        self.assertEqual(clamped, [])
        self.assertEqual(kept[0]["place"], "公司")


class SnapshotLedgerTests(unittest.TestCase):
    def test_snapshot_uses_clamped_ledger_scene(self):
        polluted = {"activity": {"place": "公司", "zone": "office",
                                 "activity": "在工位上忙工作"},
                    "health": {"status": "感冒", "sick": True},
                    "dominant": "委屈"}
        p = FakePlugin(office_state(sick=True, snapshot=polluted))
        snap = Snap.build_snapshot(p, NOW)
        self.assertEqual(snap["zone"], "home")
        self.assertEqual(snap["place"], "家")
        self.assertIn("请假", snap["activity"])
        self.assertTrue(snap["health"]["sick"])

    def test_leave_marker_clamps_snapshot(self):
        p = FakePlugin(office_state(), LEAVE_KV)
        snap = Snap.build_snapshot(p, NOW)
        self.assertEqual(snap["zone"], "home")


class MemoryGuardTests(unittest.TestCase):
    def test_present_claim_rewritten(self):
        p = FakePlugin(home_state())
        out = L.guard_memory_text(p, "我现在在公司加班，晚点回。")
        self.assertNotIn("公司", out)
        self.assertIn("家", out)

    def test_past_fact_kept(self):
        p = FakePlugin(home_state())
        text = "昨天我去了公司，还逛了商场。"
        self.assertEqual(L.guard_memory_text(p, text), text)

    def test_other_people_claim_kept(self):
        p = FakePlugin(home_state())
        text = "他现在在公司加班，让我先睡。"
        self.assertEqual(L.guard_memory_text(p, text), text)


class SceneIdFactTests(unittest.TestCase):
    class Db(FakeDb):
        def __init__(self, kv=None, schedule=None):
            super().__init__(kv)
            self._schedule = schedule or {}

        def get_schedule(self, day):
            return list(self._schedule.get(str(day)) or [])

    def test_date_window_facts_carry_scene_id(self):
        item = {"start": "19:00", "end": "21:00", "activity": "和老公吃火锅",
                "place": "楼下小馆", "category": "date", "event_id": "ev-date-1"}
        p = FakePlugin(home_state())
        p.db = self.Db(schedule={DAY: [item]})
        p.state.schedule = {DAY: [item]}
        windows = DF.date_windows(p, DAY)
        self.assertTrue(windows)
        self.assertEqual(windows[0]["scene_id"], "ev-date-1")
        facts = DF.date_presence_facts(p, DAY, slot=(19 * 60, 21 * 60))
        self.assertTrue(facts)
        self.assertEqual(facts[0]["scene_id"], "ev-date-1")

    def test_shopping_pending_carries_scene_id(self):
        import amo_feature_api as API

        class Host(API.FeatureApiMixin):
            pass

        p = Host()
        state = FakeState({"place": "商场", "zone": "mall", "activity": "在商场试衣服",
                           "event_id": "ev-shop-1"})
        p.state = state
        p.db = FakeDb()
        p.note_shopping_pending("正在商场试衣服", 0)
        record = p.db.kv_get("shopping_prompt", {})
        self.assertEqual(record.get("scene_id"), "ev-shop-1")
        self.assertEqual(record.get("scene_zone"), "mall")


class PhotoSceneVerifyTests(unittest.TestCase):
    def test_zone_changed_detected(self):
        p = FakePlugin(home_state())
        check = L.verify_photo_scene(p, expected_zone="mall", now=NOW)
        self.assertFalse(check["ok"])
        self.assertTrue(any("zone_changed" in reason for reason in check["reasons"]))

    def test_same_zone_ok(self):
        p = FakePlugin(home_state())
        check = L.verify_photo_scene(p, expected_zone="home", now=NOW)
        self.assertTrue(check["ok"], check)


class PhotoShareWiringTests(unittest.TestCase):
    def test_share_verifies_scene_before_send(self):
        import inspect

        import photo_share

        source = inspect.getsource(photo_share.PhotoShareMixin._share)
        self.assertIn("verify_photo_scene", source)
        self.assertIn("生图期间场景已变化", source)
        self.assertLess(source.index("verify_photo_scene"),
                        source.index("await self._send("))


class MemoryWriteGuardTests(unittest.TestCase):
    def test_write_memory_checked_guards_current_claim(self):
        import asyncio

        import amo_memory_bridge as MB

        saved = {}

        class Hub:
            def writable(self):
                return True

            async def remember(self, uid, content, kind, importance, scope="", group_id="",
                               subject=""):
                saved["content"] = content
                return {"ok": True}

        class Host(MB.BridgeMixin):
            def __init__(self):
                self.state = home_state()
                self.db = FakeDb()

            def _memory_hub(self):
                return Hub()

            def _data_mem(self):
                return None

            def _is_registered_lover(self, uid):
                return True

        host = Host()
        ok = asyncio.run(host._write_memory_checked(
            "u1", "我现在在公司加班。", "milestone", 2, scope="private"))
        self.assertTrue(ok)
        self.assertNotIn("公司", saved["content"])


if __name__ == "__main__":
    unittest.main()
