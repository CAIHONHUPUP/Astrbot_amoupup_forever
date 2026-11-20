# -*- coding: utf-8 -*-
"""块级小日记（block_diary）：块身份、按块生成、调度、迁移、回退开关。

线上根因：旧逻辑按「整点」存、按「块起点/结束小时」两套钥匙读写，16:07-16:56 与
16:56-18:26 会落到同一条，详情按 18:00 读、后台按 16:56 写，于是永远 generating。
这组用例钉住新模型：一块一 id、一块一条日记、未结束只 pending。
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_fact_pool  # noqa: E402
import amo_human_state  # noqa: E402
import amo_state_common  # noqa: E402
from amo_api_schedule import ScheduleApiMixin  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_schedule import ScheduleMixin  # noqa: E402
from amo_shared import today_str  # noqa: E402
from amo_state_common import block_diary_enabled, block_id  # noqa: E402
from amo_wallet import Wallet  # noqa: E402


def _block(start, end, activity, place, zone="home", category="rest"):
    return {"start": start, "end": end, "activity": activity, "place": place,
            "zone": zone, "category": category}


class FakeEngine:
    """记录调用并按块内容回不同正文：两个块串味时断言会红。"""

    def __init__(self):
        self.calls = 0
        self.seen = []
        self.contexts = []
        self.on_generate = None

    async def generate_diary(self, umo, context, item, prev_item, next_item, llm, prev_diary=""):
        self.calls += 1
        self.seen.append(dict(item))
        self.contexts.append(dict(context))
        if callable(self.on_generate):
            self.on_generate()
        # 把事实池的内容写进正文：块窗口里的事实必须进得了这一块的日记
        facts = [str(f.get("detail") or "") for f in (context.get("fact_pool") or [])
                 if isinstance(f, dict) and str(f.get("detail") or "").strip()]
        detail = ("；" + "、".join(facts)) if facts else ""
        return {"diary": "块 %s-%s %s 的日记%s" % (
            item.get("start"), item.get("end"), item.get("activity"), detail), "mood": "平静"}

    def fallback_diary(self, context, item, prev_item=None):
        return "兜底日记 %s" % (item.get("start") or "")

    def yesterday_summary(self, day):
        return {}


class FakePlugin(ScheduleMixin):
    def __init__(self, tmp, config=None):
        self.config = dict(config or {})
        self.db = AmoDB(tmp)
        self.db.init()
        self.state = None
        self.llm = None
        self.wardrobe = None
        self.photo = None
        self._last_umo = ""
        self._diary_locks = {}
        self._diary_tasks = {}
        self._spawned = []

    def _spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self._spawned.append(task)
        return task

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def _diary_lock(self, day, start):
        key = (str(day), str(start))
        lock = self._diary_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._diary_locks[key] = lock
        return lock


class DetailPlugin(FakePlugin, ScheduleApiMixin):
    """详情接口需要 ScheduleApiMixin 的方法，其余保持轻量假件。"""

    def __init__(self, tmp, config=None):
        super().__init__(tmp, config)
        self.body = {}
        self.spawns = 0
        self.wallet = Wallet(self.db)
        self.wallet.ensure_schema()

    async def _ensure_ready(self):
        return None

    async def _body(self):
        return dict(self.body)

    def _q(self, key, default=None):
        return default

    def _spawn_diary_task(self, *args, **kwargs):
        self.spawns += 1
        return super()._spawn_diary_task(*args, **kwargs)


class BaseCase(unittest.TestCase):
    HOUR = 20
    MINUTE = 0
    PLUGIN = FakePlugin

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-block-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.now = datetime.datetime.combine(
            datetime.date.fromisoformat(today_str()), datetime.time(self.HOUR, self.MINUTE))
        self._clock = mock.patch.object(amo_human_state, "_now", return_value=self.now)
        self._clock.start()
        self.day = today_str()
        self.plugin = self.PLUGIN(self.tmp)
        self.state = HumanState(self.plugin)
        self.plugin.state = self.state
        self.engine = FakeEngine()
        self.state.engine = self.engine

    def tearDown(self):
        self._clock.stop()
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def set_now(self, hour, minute=0):
        self.now = datetime.datetime.combine(
            datetime.date.fromisoformat(today_str()), datetime.time(hour, minute))
        self._clock.stop()
        self._clock = mock.patch.object(amo_human_state, "_now", return_value=self.now)
        self._clock.start()

    def install(self, items):
        self.state.schedule[self.day] = [dict(x) for x in items]
        self.state.schedule_ctx[self.day] = {"version": "backbone-v5", "day": self.day}
        self.plugin.db.save_schedule(self.day, items)

    def run_block(self, item, force=False):
        return asyncio.run(self.state.expand_block(self.day, item, self.state.schedule[self.day], force))

    def drain(self):
        async def _wait():
            pending = [t for t in self.plugin._spawned if not t.done()]
            if pending:
                await asyncio.gather(*pending)
        asyncio.run(_wait())


class BlockIdentityTests(unittest.TestCase):
    def test_same_block_id_is_stable_and_short(self):
        item = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        first = block_id("2026-11-16", item)
        second = block_id("2026-11-16", dict(item))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in first))

    def test_touching_blocks_have_different_ids(self):
        # 反向验证 R1：把 block_id 换成 start 的整点键，这两条会相等 → 用例红
        first = block_id("2026-11-16", _block("16:07", "16:56", "在医院排队", "医院"))
        second = block_id("2026-11-16", _block("16:56", "18:26", "在商场逛街", "商场"))
        self.assertNotEqual(first, second)

    def test_content_or_time_change_changes_id(self):
        base = _block("16:07", "16:56", "在医院排队", "医院")
        self.assertNotEqual(block_id("2026-11-16", base),
                            block_id("2026-11-16", dict(base, activity="在商场逛街")))
        self.assertNotEqual(block_id("2026-11-16", base),
                            block_id("2026-11-16", dict(base, place="家")))
        self.assertNotEqual(block_id("2026-11-16", base),
                            block_id("2026-11-16", dict(base, end="17:30")))

    def test_same_block_on_another_day_is_keyed_by_day_column(self):
        item = _block("16:07", "16:56", "在医院排队", "医院")
        self.assertEqual(block_id("2026-11-16", item), block_id("2026-11-17", item))


class BlockGenerationTests(BaseCase):
    def test_two_touching_blocks_get_distinct_diaries(self):
        first = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        second = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([first, second])
        r1 = self.run_block(first)
        r2 = self.run_block(second)
        self.assertTrue(str(r1.get("diary") or "").strip())
        self.assertTrue(str(r2.get("diary") or "").strip())
        self.assertNotEqual(r1["diary"], r2["diary"])
        self.assertIn("16:07", r1["diary"])
        self.assertIn("16:56", r2["diary"])
        bid1 = block_id(self.day, first)
        bid2 = block_id(self.day, second)
        self.assertNotEqual(bid1, bid2)
        self.assertEqual(self.plugin.db.get_block_diary(self.day, bid1), r1["diary"])
        self.assertEqual(self.plugin.db.get_block_diary(self.day, bid2), r2["diary"])
        # 不再写旧的 hour 键（16:07 / 16:00 / 18:00 都不该出现）
        for key in ("16:07", "16:00", "18:00"):
            self.assertEqual(self.plugin.db.get_diary(self.day, key), "")
        self.assertEqual(self.engine.calls, 2)

    def test_unfinished_block_is_pending_and_saved_only_after_end(self):
        # 反向验证 R2：去掉 slot_has_ended 判断（未结束也生成）→ 这条用例红
        self.set_now(16, 30)
        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        self.install([block])
        pending = self.run_block(block)
        self.assertTrue(pending.get("pending"))
        self.assertEqual(str(pending.get("diary") or ""), "")
        self.assertEqual(self.engine.calls, 0)
        self.assertEqual(self.plugin.db.list_block_diaries(self.day), [])
        self.set_now(17, 30)
        done = self.run_block(block)
        self.assertFalse(done.get("pending"))
        self.assertTrue(str(done.get("diary") or "").strip())
        self.assertEqual(self.engine.calls, 1)
        self.assertEqual(len(self.plugin.db.list_block_diaries(self.day)), 1)

    def test_cross_hour_block_is_one_id_one_diary(self):
        block = _block("13:41", "15:41", "在医院排队看诊", "医院", "hospital", "health")
        self.install([block])
        first = self.run_block(block)
        self.assertTrue(str(first.get("diary") or "").strip())
        rows = self.plugin.db.list_block_diaries(self.day)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["block_id"], block_id(self.day, block))
        self.assertEqual(rows[0]["start"], "13:41")
        self.assertEqual(rows[0]["end"], "15:41")
        second = self.run_block(block)
        self.assertTrue(second.get("cached"))
        self.assertEqual(self.engine.calls, 1)

    def test_cache_hit_does_not_call_engine_again(self):
        block = _block("10:00", "11:00", "上班", "公司", "office", "work")
        self.install([block])
        self.run_block(block)
        again = self.run_block(block)
        self.assertTrue(again.get("cached"))
        self.assertEqual(self.engine.calls, 1)

    def test_fact_pool_window_equals_block_window(self):
        seen = {}

        async def fake_pool(plugin, day, now=None, hour_slot=None, slot_start=None, slot_end=None):
            seen.update({"hour_slot": hour_slot, "slot_start": slot_start, "slot_end": slot_end})
            return {"facts": []}

        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        self.install([block])
        with mock.patch.object(amo_fact_pool, "build_fact_pool", fake_pool):
            self.run_block(block)
        self.assertEqual(seen.get("slot_start"), "16:07")
        self.assertEqual(seen.get("slot_end"), "16:56")
        self.assertIsNone(seen.get("hour_slot"))

    def test_reload_does_not_regenerate(self):
        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        self.install([block])
        first = self.run_block(block)
        # 模拟插件重载：新 DB 连接 + 新状态机，读同一份数据
        plugin = FakePlugin(self.tmp)
        state = HumanState(plugin)
        plugin.state = state
        engine = FakeEngine()
        state.engine = engine
        state.schedule[self.day] = [dict(block)]
        state.schedule_ctx[self.day] = {"version": "backbone-v5", "day": self.day}
        second = asyncio.run(state.expand_block(self.day, block, [dict(block)]))
        self.assertTrue(second.get("cached"))
        self.assertEqual(second["diary"], first["diary"])
        self.assertEqual(engine.calls, 0)

    def test_place_gate_allows_cross_place_block(self):
        block = _block("13:41", "16:00", "在医院排队后回家", "医院", "hospital", "health")
        self.install([block])
        bid = block_id(self.day, block)
        text = "从医院出来回到家，窝在被窝里不想动。"
        crossed = self.state._guard_block_location(
            text, block, self.day, bid, ["医院", "家"], save=False)
        self.assertEqual(crossed, text, "跨地点块不能把块里真实去过的地点删掉")
        single = self.state._guard_block_location(
            text, block, self.day, bid, ["医院"], save=False)
        self.assertNotIn("被窝", single)

    def test_block_change_generates_new_diary(self):
        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        self.install([block])
        self.run_block(block)
        changed = dict(block, activity="在商场逛街", place="商场", zone="mall")
        self.state.schedule[self.day] = [changed]
        self.plugin.db.save_schedule(self.day, [changed])
        result = self.run_block(changed)
        self.assertFalse(result.get("cached"))
        self.assertEqual(self.engine.calls, 2)
        self.assertEqual(len(self.plugin.db.list_block_diaries(self.day)), 2)

    def test_rewrite_budget_is_per_block(self):
        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        other = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([block, other])
        bid = block_id(self.day, block)
        self.state._bump_diary_rewrite(self.day, bid)
        self.assertEqual(self.state._diary_rewrites(self.day, bid), 1)
        self.assertEqual(
            self.state._diary_rewrites(self.day, block_id(self.day, other)), 0)


class DiaryStateCompatTests(BaseCase):
    def test_switch_off_falls_back_to_legacy_hour_table(self):
        self.plugin.config["block_diary_enabled"] = False
        self.assertFalse(block_diary_enabled(self.plugin))
        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        self.install([block])
        result = self.run_block(block)
        self.assertTrue(str(result.get("diary") or "").strip())
        self.assertTrue(self.plugin.db.get_diary(self.day, "16:07"))
        self.assertEqual(self.plugin.db.list_block_diaries(self.day), [])
        self.assertEqual(self.engine.calls, 1)

    def test_switch_default_is_on(self):
        self.assertTrue(block_diary_enabled(self.plugin))


class MigrationTests(BaseCase):
    def test_offset_start_block_with_hourly_row_migrates_stale(self):
        # 起点偏移块 16:07-16:56 与旧 16:00 行的小时窗口并不相同 → 不可信
        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        self.install([block])
        self.plugin.db.save_diary(self.day, "16:00", "旧的整点日记", version=0, status="valid")
        migrated = self.plugin.db.migrate_block_diary()
        self.assertEqual(migrated, 1)
        record = self.plugin.db.get_block_diary_record(self.day, block_id(self.day, block))
        self.assertIsNotNone(record)
        self.assertEqual(record["text"], "旧的整点日记")
        self.assertEqual(record["status"], "stale")

    def test_exact_hour_block_with_hourly_row_stays_valid(self):
        # 恰好整点块 16:00-17:00 = 旧 16:00 行覆盖的那一小时 → 仍然可信，不误杀
        block = _block("16:00", "17:00", "在工位上写周报", "公司", "office", "work")
        self.install([block])
        self.plugin.db.save_diary(self.day, "16:00", "整点这一小时的日记", version=0, status="valid")
        self.plugin.db.migrate_block_diary()
        record = self.plugin.db.get_block_diary_record(self.day, block_id(self.day, block))
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "valid")
        self.assertEqual(record["text"], "整点这一小时的日记")

    def test_cross_hour_block_with_hourly_row_migrates_stale(self):
        # 线上：块 16:56-18:26 却套用旧 17:00 那条“热了 15 块汤面”（只覆盖 17-18 点）
        block = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([block])
        self.plugin.db.save_diary(
            self.day, "17:00", "热了 15 块汤面，缩在沙发上", version=0, status="valid")
        self.plugin.db.migrate_block_diary()
        record = self.plugin.db.get_block_diary_record(self.day, block_id(self.day, block))
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "stale", "跨多小时块不能直接复用旧整点行")

    def test_rejudge_stales_old_migration_rows_only_once(self):
        cross = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        exact = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        late = _block("21:00", "23:00", "在公司加班", "公司", "office", "work")
        self.install([cross, exact, late])
        old_text = "热了 15 块汤面，缩在沙发上"
        self.plugin.db.save_diary(self.day, "17:00", old_text, version=0, status="valid")
        self.plugin.db.save_diary(self.day, "22:00", "加班到很晚", version=0, status="valid")
        # 模拟旧版迁移留下的行：一条是旧文副本（source 列还没有），一条后来真生成过
        self.plugin.db.save_block_diary(
            self.day, block_id(self.day, cross), old_text,
            start="16:56", end="18:26", place="商场", version=0, status="valid")
        self.plugin.db.save_block_diary(
            self.day, block_id(self.day, exact), "真正生成过的新正文",
            start="16:07", end="16:56", place="医院", version=0, status="valid")
        # 另一条：来源标记为迁移，但正文已被改过 → 仍按来源重判
        self.plugin.db.save_block_diary(
            self.day, block_id(self.day, late), "迁移来源的旧文",
            start="21:00", end="23:00", place="公司", version=0, status="valid",
            source="migration")
        self.assertEqual(self.plugin.db.migrate_block_diary(), 0)
        cross_row = self.plugin.db.get_block_diary_record(self.day, block_id(self.day, cross))
        self.assertEqual(cross_row["status"], "stale")
        self.assertEqual(cross_row["text"], old_text)
        late_row = self.plugin.db.get_block_diary_record(self.day, block_id(self.day, late))
        self.assertEqual(late_row["status"], "stale")
        exact_row = self.plugin.db.get_block_diary_record(self.day, block_id(self.day, exact))
        self.assertEqual(exact_row["status"], "valid")
        self.assertEqual(exact_row["text"], "真正生成过的新正文")
        self.assertEqual(self.plugin.db.kv_get("block_diary_migrate_version"), 2)
        # 再启动不重复改；强制重跑也不会误杀新正文
        self.plugin.db.migrate_block_diary()
        self.plugin.db.kv_set("block_diary_migrate_version", 0)
        self.plugin.db.migrate_block_diary()
        self.assertEqual(
            self.plugin.db.get_block_diary_record(self.day, block_id(self.day, exact))["status"],
            "valid")

    def test_ambiguous_hour_writes_readonly_legacy_row(self):
        first = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        second = _block("16:30", "17:30", "在商场逛街", "商场", "mall", "play")
        self.install([first, second])
        self.plugin.db.save_diary(self.day, "16:00", "候选太多的旧日记", version=0, status="valid")
        self.plugin.db.migrate_block_diary()
        legacy = self.plugin.db.get_block_diary_record(self.day, "legacy:16:00")
        self.assertIsNotNone(legacy)
        self.assertEqual(legacy["text"], "候选太多的旧日记")
        self.assertIsNone(
            self.plugin.db.get_block_diary_record(self.day, block_id(self.day, first)))

    def test_migration_is_idempotent_and_never_overwrites(self):
        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        self.install([block])
        self.plugin.db.save_diary(self.day, "16:00", "旧的整点日记", version=0, status="valid")
        self.plugin.db.migrate_block_diary()
        bid = block_id(self.day, block)
        self.plugin.db.save_block_diary(
            self.day, bid, "新生成的内容", start="16:07", end="16:56", place="医院",
            version=1, status="valid")
        before = self.plugin.db.list_block_diaries(self.day)
        self.plugin.db.migrate_block_diary()  # 再次启动
        self.plugin.db.migrate_block_diary()
        after = self.plugin.db.list_block_diaries(self.day)
        self.assertEqual(len(before), len(after))
        self.assertEqual(self.plugin.db.get_block_diary(self.day, bid), "新生成的内容")

    def test_non_hour_row_maps_to_containing_block_and_is_stale(self):
        # 线上那条 hour="16:56" 的旧文本（五点半、窝在床上）时间口径是歪的
        block = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([block])
        self.plugin.db.save_diary(
            self.day, "16:56", "五点半天有点阴，窝在床上", version=0, status="valid")
        self.plugin.db.migrate_block_diary()
        record = self.plugin.db.get_block_diary_record(self.day, block_id(self.day, block))
        self.assertIsNotNone(record)
        self.assertEqual(record["text"], "五点半天有点阴，窝在床上")
        self.assertEqual(record["status"], "stale", "非整点旧行时间口径不可信，必须标 stale")


class DetailTests(BaseCase):
    PLUGIN = DetailPlugin

    def _detail(self, start, end):
        self.plugin.body = {"date": self.day, "start": start, "end": end}

        async def _run():
            first = await self.plugin.api_schedule_detail()
            pending = [t for t in self.plugin._spawned if not t.done()]
            if pending:
                await asyncio.gather(*pending)
            second = await self.plugin.api_schedule_detail()
            return first, second

        return asyncio.run(_run())

    def test_detail_ignores_old_hour_table_and_regenerates_block(self):
        # 第 1 阶段重构：详情不再 fallback 旧 schedule_diary 的 item.start 行；
        # 拿不到块日记就交给块重新生成，旧正文绝不当缓存回放。
        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        self.install([block])
        self.plugin.db.save_diary(self.day, "16:07", "旧块日记", version=0, status="valid")
        first, second = self._detail("16:07", "16:56")
        self.assertEqual(first["data"].get("status"), "generating")
        self.assertEqual(self.plugin.spawns, 1)
        text = str(second["data"].get("diary") or "")
        self.assertTrue(text.strip())
        self.assertNotEqual(text, "旧块日记")
        self.assertIn("16:07-16:56", text)

    def test_detail_ignores_legacy_hour_key_rows(self):
        # legacy:<hour> 的旧迁移副本也不再是详情缓存：块身份对不上就重新生成。
        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        self.install([block])
        self.plugin.db.save_block_diary(
            self.day, "legacy:16:00", "旧迁移副本", start="16:00", end="17:00",
            place="医院", version=0, status="valid")
        first, second = self._detail("16:07", "16:56")
        self.assertEqual(first["data"].get("status"), "generating")
        self.assertEqual(self.plugin.spawns, 1)
        text = str(second["data"].get("diary") or "")
        self.assertTrue(text.strip())
        self.assertNotIn("旧迁移副本", text)

    def test_cross_hour_old_hourly_row_regenerated_on_block_window(self):
        # 反向验证 R5：让迁移无条件信任整点旧行 → 详情直接返回“15 块汤面”，用例红
        block = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([block])
        self.plugin.db.save_diary(
            self.day, "17:00", "热了 15 块汤面，缩在沙发上", version=0, status="valid")
        self.plugin.db.migrate_block_diary()
        row = self.plugin.db.get_block_diary_record(self.day, block_id(self.day, block))
        self.assertEqual(row["status"], "stale", "跨多小时块的旧整点行不能当可信缓存")
        seen = {}

        async def fake_pool(plugin, day, now=None, hour_slot=None, slot_start=None, slot_end=None):
            seen.update({"start": slot_start, "end": slot_end})
            return {"facts": [{"type": "支出", "detail": "18:10 点了 25 块外卖", "time": "18:10"}]}

        with mock.patch.object(amo_fact_pool, "build_fact_pool", fake_pool):
            first, second = self._detail("16:56", "18:26")
        self.assertEqual(first["data"].get("status"), "generating")
        self.assertEqual(self.plugin.spawns, 1, "只入队一次，不得反复 generating")
        text = str(second["data"].get("diary") or "")
        self.assertIn("外卖", text, "新正文必须覆盖块窗口内 18:10 的外卖事实")
        self.assertNotIn("汤面", text, "不能把旧 17:00 行（15 块汤面）当缓存返回")
        self.assertEqual(seen.get("start"), "16:56")
        self.assertEqual(seen.get("end"), "18:26")
        self.assertEqual(self.engine.calls, 1)
        after = self.plugin.db.get_block_diary_record(self.day, block_id(self.day, block))
        self.assertEqual(after["status"], "valid")
        self.assertEqual(after["source"], "", "重新生成后不再是迁移副本")

    def test_stale_legacy_row_regenerates_from_block_not_reused(self):
        # 反向验证 R3：把 stale 当可信返回 → 用例红（返回“五点半”那段）
        block = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([block])
        self.plugin.db.save_diary(
            self.day, "16:56", "五点半天有点阴，窝在床上", version=0, status="valid")
        self.plugin.db.migrate_block_diary()
        first, second = self._detail("16:56", "18:26")
        self.assertEqual(first["data"].get("status"), "generating")
        self.assertEqual(self.plugin.spawns, 1, "只允许入队一次，不能反复 generating")
        text = str(second["data"].get("diary") or "")
        self.assertTrue(text.strip())
        self.assertNotEqual(second["data"].get("status"), "generating")
        self.assertNotIn("五点半", text)
        self.assertNotIn("被窝", text)
        self.assertIn("16:56-18:26", text)
        self.assertEqual(self.engine.calls, 1)

    def test_detail_matches_background_block_key(self):
        # 线上：后台按 item.start=16:56 写，详情按结束小时 18:00 读 → 永远 generating。
        # 块化后两边都用 block_id，第二次必定读到文本。
        block = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([block])
        first, second = self._detail("16:56", "18:26")
        self.assertEqual(first["data"].get("status"), "generating")
        self.assertEqual(self.plugin.spawns, 1)
        self.assertTrue(str(second["data"].get("diary") or "").strip())
        self.assertEqual(self.plugin.db.get_diary(self.day, "18:00"), "")
        self.assertEqual(self.plugin.db.get_diary(self.day, "16:56"), "")

    def test_detail_finds_scheduler_row_when_request_clamps_midnight(self):
        # 面板把 end=24:00 夹成 23:00；调度按原始块（23:00-24:00）写，详情也要能认出来。
        self.day = (datetime.date.fromisoformat(today_str()) - datetime.timedelta(days=1)).isoformat()
        block = _block("23:00", "24:00", "准备睡觉", "家", "home", "sleep")
        self.install([block])
        self.plugin.db.save_block_diary(
            self.day, block_id(self.day, block), "调度写好的日记",
            start="23:00", end="24:00", place="家", version=0, status="valid")
        self.plugin.body = {"date": self.day, "start": "23:00", "end": "24:00"}
        result = asyncio.run(self.plugin.api_schedule_detail())
        self.assertEqual(result["data"].get("diary"), "调度写好的日记")
        self.assertEqual(self.plugin.spawns, 0)
        self.assertEqual(self.engine.calls, 0)

    def test_unfinished_block_stays_pending_in_detail(self):
        self.set_now(18, 40)
        block = _block("18:26", "19:06", "在商场逛街", "商场", "mall", "play")
        self.install([block])
        self.plugin.body = {"date": self.day, "start": "18:26", "end": "19:06"}
        result = asyncio.run(self.plugin.api_schedule_detail())
        self.assertTrue(result["data"].get("pending"))
        self.assertEqual(str(result["data"].get("diary") or ""), "")
        self.assertEqual(self.plugin.spawns, 0)
        self.assertEqual(self.engine.calls, 0)


class TimelineIdentityTests(BaseCase):
    """同一块无论从详情 / 调度 / 日记 读到，block_id 必须一致。"""

    PLUGIN = DetailPlugin

    def test_same_block_id_from_detail_scheduler_and_diary(self):
        from amo_schedule import _backfill_blocks
        from amo_timeline import Timeline
        from amo_api_schedule import _detail_blocks

        first_block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        second_block = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([first_block, second_block])
        timeline = Timeline(self.state)
        rows = timeline.blocks(self.day)
        self.assertEqual([row["start"] for row in rows], ["16:07", "16:56"])
        bid = timeline.block_id(self.day, rows[0])
        self.assertEqual(bid, block_id(self.day, rows[0]), "Timeline 与 state_common 身份必须同源")
        self.assertNotEqual(bid, block_id(self.day, rows[1]), "相接的块不能共用一个身份")

        # 详情读到的原始块身份
        _items, _item, raw_item, blocked = _detail_blocks(
            self.plugin, self.day, "16:07", "16:56", "")
        self.assertIsNone(blocked)
        self.assertEqual(block_id(self.day, raw_item), bid)

        # 调度补写时传给 expand 的块身份
        spawned = []
        self.plugin._spawn_diary_task = lambda day, item, items, version, reason="slot_ended":             spawned.append(block_id(day, item))
        try:
            _backfill_blocks(self.plugin, self.day, [first_block], {}, 0, 2,
                             lambda *args, **kwargs: True)
        finally:
            del self.plugin._spawn_diary_task
        self.assertEqual(spawned, [bid])

        # 日记生成结果回传的块身份
        result = asyncio.run(self.state.expand_block(
            self.day, first_block, self.state.schedule[self.day]))
        self.assertEqual(result.get("block_id"), bid)
        self.assertEqual(self.plugin.db.get_block_diary(self.day, bid), result.get("diary"))


class SchedulerState:
    """只实现调度需要的成员：expand_block 不在 → 调度会退回 expand_hour 假实现。"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.schedule = {today_str(): []}
        self.calls = []

    async def ensure_schedule(self, umo, force=False):
        return list(self.schedule.get(today_str()) or [])

    async def expand_block(self, day, item, items=None, force=False):
        self.calls.append(item.get("start"))
        key = block_id(day, item)
        self.plugin.db.save_block_diary(
            day, key, "调度生成 %s" % item.get("start"),
            start=str(item.get("start") or ""), end=str(item.get("end") or ""),
            place=str(item.get("place") or ""), version=0, status="valid")
        return {"diary": "调度生成 %s" % item.get("start")}

    def _hourly_schedule(self, items):
        raise AssertionError("块模式调度不能再用 _hourly_schedule")


class SchedulerPlugin(FakePlugin):
    def __init__(self, tmp, config=None):
        super().__init__(tmp, config)
        self.state = SchedulerState(self)


class SchedulerTests(BaseCase):
    PLUGIN = SchedulerPlugin

    def setUp(self):
        super().setUp()
        self.plugin.state = self.plugin.state if isinstance(
            self.plugin.state, SchedulerState) else SchedulerState(self.plugin)
        self.state = self.plugin.state

    def tick(self):
        async def _run():
            await self.plugin._diary_scheduler_tick()
            pending = [t for t in self.plugin._spawned if not t.done()]
            if pending:
                await asyncio.gather(*pending)

        self.plugin.state.schedule[self.day] = [
            dict(x) for x in self.plugin.state.schedule.get(self.day) or []]
        asyncio.run(_run())

    def reset_budget(self):
        self.plugin.db.kv_set("diary_backfill_minute", {})

    def test_scheduler_only_generates_ended_blocks(self):
        ended_a = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        ended_b = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        future = _block("21:00", "22:00", "休息", "家")
        self.plugin.state.schedule[self.day] = [ended_a, ended_b, future]
        self.tick()
        self.assertEqual(sorted(self.plugin.state.calls), ["16:07", "16:56"])
        self.assertNotIn("21:00", self.plugin.state.calls)

    def test_scheduler_generates_at_most_two_per_tick(self):
        rows = [_block("%02d:00" % hour, "%02d:00" % (hour + 1), "上班", "公司",
                       "office", "work") for hour in range(8, 14)]
        self.plugin.state.schedule[self.day] = rows
        self.tick()
        self.assertEqual(len(self.plugin.state.calls), 2)
        self.assertEqual(len([t for t in self.plugin._spawned if t.done()]), 2)

    def test_scheduler_does_not_repeat_valid_blocks(self):
        rows = [_block("16:07", "16:56", "在医院排队", "医院", "hospital", "health"),
                _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")]
        self.plugin.state.schedule[self.day] = rows
        self.tick()
        self.assertEqual(sorted(self.plugin.state.calls), ["16:07", "16:56"])
        self.plugin._diary_tasks.clear()
        self.reset_budget()
        self.tick()
        self.assertEqual(sorted(self.plugin.state.calls), ["16:07", "16:56"])

    def test_scheduler_never_writes_old_hour_keys(self):
        block = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.plugin.state.schedule[self.day] = [block]
        self.tick()
        self.assertEqual(self.plugin.db.get_diary(self.day, "18:00"), "")
        self.assertEqual(self.plugin.db.get_diary(self.day, "16:56"), "")
        rows = self.plugin.db.list_block_diaries(self.day)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["block_id"], block_id(self.day, block))



class P1RegressionTests(BaseCase):
    PLUGIN = DetailPlugin
    """本轮 P0/P1 自检钉住的三处：24:00 pending、错小时旧行、过去日期无限 generating。"""

    def test_midnight_end_request_stays_pending_until_24(self):
        # 面板请求 end=24:00 被夹成 23:00 后，23:30 不能把 23:00-24:00 当成已结束。
        self.set_now(23, 30)
        block = _block("23:00", "24:00", "准备睡觉", "家", "home", "sleep")
        self.install([block])
        self.plugin.body = {"date": self.day, "start": "23:00", "end": "24:00"}
        result = asyncio.run(self.plugin.api_schedule_detail())
        self.assertTrue(result["data"].get("pending"), result)
        self.assertEqual(str(result["data"].get("diary") or ""), "")
        self.assertEqual(self.plugin.spawns, 0)
        self.assertEqual(self.engine.calls, 0)

    def test_wrong_hour_old_row_is_never_returned(self):
        # 16:56-18:26 块不能拿旧 18:00 行（只描述 18-19 点）当缓存。
        self.set_now(19, 0)
        block = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([block])
        self.plugin.db.save_diary(
            self.day, "18:00", "18点整点日记，只属于18-19点", version=0, status="valid")
        self.plugin.body = {"date": self.day, "start": "16:56", "end": "18:26"}
        result = asyncio.run(self.plugin.api_schedule_detail())
        self.assertNotEqual(result["data"].get("diary"), "18点整点日记，只属于18-19点", result)
        self.assertEqual(result["data"].get("status"), "generating")
        self.assertEqual(self.plugin.spawns, 1)

    def test_past_day_without_diary_does_not_loop_generating(self):
        # 过去日期后台只会返回 archived；再入队就是每次刷新都 generating。
        self.day = (datetime.date.fromisoformat(today_str()) - datetime.timedelta(days=1)).isoformat()
        block = _block("23:00", "24:00", "准备睡觉", "家", "home", "sleep")
        self.install([block])
        self.plugin.body = {"date": self.day, "start": "23:00", "end": "24:00"}
        result = asyncio.run(self.plugin.api_schedule_detail())
        self.assertEqual(self.plugin.spawns, 0, result)
        self.assertNotEqual(result["data"].get("status"), "generating", result)


class SleepPlaceholderTests(BaseCase):
    def test_sleep_placeholder_is_persisted_and_reused(self):
        # 睡眠块只返回占位、不落库 → 调度每 tick 都重试、详情永远 generating。
        self.set_now(7, 0)
        block = _block("00:00", "06:00", "睡觉", "家", "home", "sleep")
        self.install([block])
        first = self.run_block(block)
        self.assertTrue(first.get("placeholder"), first)
        self.assertEqual(self.engine.calls, 0)
        self.assertTrue(
            self.plugin.db.block_diary_is_valid(self.day, block_id(self.day, block)),
            self.plugin.db.list_block_diaries(self.day))
        second = self.run_block(block)
        self.assertEqual(second.get("diary"), first.get("diary"))
        self.assertEqual(len(self.plugin.db.list_block_diaries(self.day)), 1)
        self.assertEqual(self.engine.calls, 0)



class SpendingDetailTests(BaseCase):
    """详情「本时段消费」：窗口=块 start-end，金额分→元，只读。"""

    PLUGIN = DetailPlugin

    def _spend(self, at, cents, reason, idem):
        # 钱包流水的时间戳取 clock.now_ts()：要落进块窗口，就得把时钟临时拨到消费那一刻
        # （不能只 patch time.time，账本根本不再读它）。用完恢复原冻结点。
        import clock as _clock

        day = datetime.date.fromisoformat(self.day)
        hour, minute = [int(x) for x in at.split(":")]
        base = __import__("time").mktime(day.timetuple())
        prev = _clock.frozen()
        _clock.freeze(datetime.datetime.combine(day, datetime.time(hour, minute)))
        try:
            self.plugin.wallet.credit(100000, "init", "init-" + idem)
            ok = self.plugin.wallet.debit(cents, reason, idem, category="food", item_name=reason)
        finally:
            if prev is None:
                _clock.unfreeze()
            else:
                _clock.freeze(prev)
        self.assertTrue(ok, reason)
        return base

    def _detail(self, start, end):
        self.plugin.body = {"date": self.day, "start": start, "end": end}
        return asyncio.run(self.plugin.api_schedule_detail())["data"]

    def test_spending_uses_block_window_and_converts_cents(self):
        self.set_now(19, 0)
        block = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([block])
        self.plugin.db.save_block_diary(self.day, block_id(self.day, block), "块日记",
                                        start="16:56", end="18:26", place="商场",
                                        version=0, status="valid")
        self._spend("18:10", 2500, "外卖", "spend-1")
        self._spend("17:40", 1500, "面条", "spend-2")
        self._spend("15:00", 9900, "午饭", "spend-3")  # 窗口外，不能混进来
        data = self._detail("16:56", "18:26")
        self.assertEqual([item["reason"] for item in data["spending"]], ["面条", "外卖"])
        self.assertEqual([item["amount_yuan"] for item in data["spending"]], [15.0, 25.0])
        self.assertEqual(data["spending_total_yuan"], 40.0, "25.00 + 15.00，不是 2500")
        self.assertNotIn(2500.0, [item["amount_yuan"] for item in data["spending"]])

    def test_spending_only_inside_subhour_block(self):
        self.set_now(19, 0)
        block = _block("16:07", "16:56", "在医院排队", "医院", "hospital", "health")
        self.install([block])
        self.plugin.db.save_block_diary(self.day, block_id(self.day, block), "块日记",
                                        start="16:07", end="16:56", place="医院",
                                        version=0, status="valid")
        self._spend("16:20", 500, "挂号", "spend-4")
        self._spend("17:00", 700, "水果", "spend-5")
        data = self._detail("16:07", "16:56")
        self.assertEqual([item["reason"] for item in data["spending"]], ["挂号"])
        self.assertEqual(data["spending_total_yuan"], 5.0)

    def test_spending_empty_returns_empty_list(self):
        self.set_now(19, 0)
        block = _block("16:56", "18:26", "在商场逛街", "商场", "mall", "play")
        self.install([block])
        self.plugin.db.save_block_diary(self.day, block_id(self.day, block), "块日记",
                                        start="16:56", end="18:26", place="商场",
                                        version=0, status="valid")
        data = self._detail("16:56", "18:26")
        self.assertEqual(data["spending"], [])
        self.assertEqual(data["spending_total_yuan"], 0.0)


if __name__ == "__main__":
    unittest.main()
