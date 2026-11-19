# -*- coding: utf-8 -*-
"""B 方案：大日记改成「第二天任意时间写昨天」，重写只看事实矛盾。

钉住的回归：
- 第二天 10:00 / 15:00 / 21:00 开机都补写昨天，不限制 08:00-08:30；
- 连续缺 3 天按日期顺序补，最多 7 天；
- 事实门：只改「和事实矛盾」的；只是遗漏（没提宵夜/某顿饭）不重写；
- 日记「上班」+ 生病/医院日程 → 矛盾；「早早睡了」/「没吃东西」+ 23:30 宵夜 → 矛盾；
- 「在家」+ 日程在公司/医院 → 矛盾；「没生病/好了」+ health sick → 矛盾；
- diary_locked 不再阻挡写入；一致就清 stale 保留原文，矛盾才重写（有重写上限）；
- 旧配置 diary_time=22:00 仍能按 22:00-22:30 写当天；面板文案说明第二天自动写昨天。
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import clock  # noqa: E402

import amo_schedule  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_diary_guard import diary_day_facts, diary_fact_mismatch  # noqa: E402
from amo_schedule import DIARY_REWRITE_LIMIT, ScheduleMixin, diary_end_day, diary_manual_target  # noqa: E402


class FakeState:
    """只给事实门用的最小 state：健康状态 + 日程回落（build_day_view 的读法）。"""

    def __init__(self, sick=False, db=None):
        self.schedule = {}
        self.db = db
        self.health = {"status": "感冒" if sick else "健康", "sick": bool(sick)}

    def health_info(self):
        return dict(self.health)


class FakeWallet:
    """只给事实门用的最小钱包：返回指定流水。"""

    def __init__(self, rows):
        self.rows = list(rows or [])

    def rows_between(self, start, end):
        return list(self.rows)

    def ledger(self, limit=200):
        # build_day_view 的读法（唯一事实视图）；老读法 rows_between 保留给旧调用方
        return list(self.rows)


class FakePlugin(ScheduleMixin):
    def __init__(self, tmp, config=None):
        self.config = dict(config or {})
        self.config.setdefault("diary_enabled", True)
        self.config.setdefault("block_diary_enabled", True)
        self.db = AmoDB(tmp)
        self.db.init()
        # 事实视图（F1）从 state.schedule → state.db.get_schedule 读日程
        self.state = FakeState(db=self.db)
        self.llm = None
        self.wardrobe = None
        self._last_umo = ""
        self._diary_dir = lambda: os.path.join(tmp, "diary")

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))


WORK_BLOCK = {"start": "09:00", "end": "18:00", "activity": "上班", "place": "公司",
              "category": "work", "zone": "work"}
HOME_BLOCK = {"start": "09:00", "end": "18:00", "activity": "休息", "place": "家",
              "category": "rest", "zone": "home"}
HOSPITAL_BLOCK = {"start": "09:00", "end": "18:00", "activity": "去医院看病", "place": "医院",
                  "category": "health", "zone": "hospital"}
SNACK_BLOCK = {"start": "23:30", "end": "24:00", "activity": "吃泡面（宵夜）", "place": "家",
               "category": "meal", "zone": "home"}


class DiaryMorningBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-diary-b-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.plugin = FakePlugin(self.tmp)
        self.generated = []
        self.written = {}

        async def fake_generate(day):
            self.generated.append(day)
            return "日记 %s" % day.isoformat()

        def fake_write(root, day, text):
            self.written[day.isoformat()] = text
            return "/tmp/%s.md" % day.isoformat()

        self.plugin._generate_daily_diary = fake_generate
        self.fake_write = fake_write

    def tearDown(self):
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def run_at(self, when):
        with mock.patch.object(amo_schedule, "_now", return_value=when), \
                mock.patch.object(amo_schedule, "_diary_write", self.fake_write):
            asyncio.run(self.plugin._maybe_daily_diary())

    def save_schedule(self, day, items):
        self.plugin.db.save_schedule(day.isoformat(), items)

    def write_existing(self, day, text, locked=True, stale=False):
        path = os.path.join(self.tmp, day.isoformat() + ".txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.plugin.db.kv_set("diary_written:" + day.isoformat(), path)
        if locked:
            self.plugin.db.kv_set("diary_locked:" + day.isoformat(), True)
        if stale:
            self.plugin.db.kv_set("diary_stale:" + day.isoformat(), True)
        return path

    # ---------- 第二天任意时间补昨天 ----------

    def test_end_day_morning_writes_yesterday_any_time(self):
        for hour in (0, 7, 10, 15, 21, 23):
            now = datetime.datetime(2026, 11, 18, hour, 30)
            self.assertEqual(diary_end_day(now, 8, 0), date(2026, 11, 17), hour)

    def test_catchup_writes_yesterday_at_any_hour(self):
        for idx, hour in enumerate((10, 15, 21)):
            target = date(2026, 11, 17 + idx)
            tomorrow = target + timedelta(days=1)
            self.plugin.db.kv_set("diary_last_day", (target - timedelta(days=1)).isoformat())
            self.generated.clear()
            self.run_at(datetime.datetime.combine(tomorrow, datetime.time(hour, 0)))
            self.assertEqual(self.generated, [target], hour)
            self.assertIn(target.isoformat(), self.written)

    def test_0800_writes_yesterday_with_late_snack(self):
        self.plugin.db.kv_set("diary_last_day", "2026-11-16")
        self.plugin.db.save_block_diary(
            "2026-11-17", "block-2330", "她煮了泡面当宵夜",
            start="23:30", end="24:00", place="家", version=0, status="valid")
        self.run_at(datetime.datetime(2026, 11, 18, 8, 5))
        self.assertEqual(self.generated, [date(2026, 11, 17)])
        self.assertIn("2026-11-17", self.written)
        self.assertTrue(self.plugin.db.kv_get("diary_locked:2026-11-17", False))
        self.assertEqual(self.plugin.db.kv_get("diary_last_day"), "2026-11-17")
        hourly = self.plugin._diary_hourly_lines(date(2026, 11, 17))
        self.assertTrue(any("23:30" in line and "宵夜" in line for line in hourly), hourly)
        with mock.patch.object(amo_schedule, "_now",
                               return_value=datetime.datetime(2026, 11, 18, 8, 5)):
            rules = amo_schedule._diary_time_rules(self.plugin, date(2026, 11, 17))
        self.assertIn("00:00-24:00", rules)
        self.assertIn("2026-11-17", rules)
        self.assertNotIn("事实只到", rules)

    def test_2230_does_not_write_today(self):
        self.plugin.db.kv_set("diary_last_day", "2026-11-16")
        self.run_at(datetime.datetime(2026, 11, 17, 22, 30))
        self.assertEqual(self.generated, [])
        self.assertFalse(self.plugin.db.kv_get("diary_written:2026-11-17", False))

    # ---------- 连续缺多天按序补 ----------

    def test_downtime_backfill_caps_at_seven_days(self):
        self.plugin.db.kv_set("diary_last_day", "2026-11-09")
        self.run_at(datetime.datetime(2026, 11, 18, 10, 0))
        self.assertEqual(
            [day.isoformat() for day in self.generated],
            ["2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14",
             "2026-11-15", "2026-11-16", "2026-11-17"],
        )

    def test_stopped_three_days_backfills_in_order(self):
        self.plugin.db.kv_set("diary_last_day", "2026-11-14")
        self.run_at(datetime.datetime(2026, 11, 18, 9, 0))
        self.assertEqual([day.isoformat() for day in self.generated],
                         ["2026-11-15", "2026-11-16", "2026-11-17"])

    # ---------- 已写且一致 / 矛盾 ----------

    def test_existing_diary_not_duplicated(self):
        target = date(2026, 11, 17)
        self.write_existing(target, "今天吃了饭，晚上看了会儿剧。")
        self.plugin.db.kv_set("diary_last_day", target.isoformat())
        self.run_at(datetime.datetime(2026, 11, 18, 10, 0))
        self.assertEqual(self.generated, [])

    def test_stale_but_consistent_clears_without_rewrite(self):
        target = date(2026, 11, 17)
        self.save_schedule(target, [WORK_BLOCK, SNACK_BLOCK])
        self.write_existing(target, "今天上班很累，晚上吃了宵夜才睡。", stale=True)
        self.plugin.db.kv_set("diary_last_day", target.isoformat())
        self.run_at(datetime.datetime(2026, 11, 18, 10, 0))
        # 没有「没吃宵夜 / 一直在家」这种否定句 → 遗漏不算矛盾，不重写
        self.assertEqual(self.generated, [])
        self.assertFalse(self.plugin.db.kv_get("diary_stale:" + target.isoformat(), False))

    def test_stale_and_contradiction_rewrites_once(self):
        target = date(2026, 11, 17)
        self.save_schedule(target, [WORK_BLOCK])
        self.write_existing(target, "今天没出门，一直在家。", stale=True)
        self.plugin.db.kv_set("diary_last_day", target.isoformat())
        self.run_at(datetime.datetime(2026, 11, 18, 10, 0))
        self.assertEqual(self.generated, [target])
        self.assertTrue(self.plugin.db.kv_get("diary_locked:" + target.isoformat(), False))
        self.assertFalse(self.plugin.db.kv_get("diary_stale:" + target.isoformat(), False))
        self.assertEqual(self.plugin._daily_diary_rewrites(target.isoformat()), 1)
        # 改写后的正文（fake_generate 返回无否定句）再次被标 stale 时只清标记，不再循环重写
        self.plugin.db.kv_set("diary_stale:" + target.isoformat(), True)
        self.run_at(datetime.datetime(2026, 11, 18, 11, 0))
        self.assertEqual(self.generated, [target])
        self.assertFalse(self.plugin.db.kv_get("diary_stale:" + target.isoformat(), False))

    def test_locked_key_does_not_block_rewrite(self):
        target = date(2026, 11, 17)
        self.save_schedule(target, [HOSPITAL_BLOCK])
        self.write_existing(target, "今天在公司上班，忙了一整天。", locked=True, stale=True)
        self.run_at(datetime.datetime(2026, 11, 18, 15, 0))
        # 日记「上班」和医院日程矛盾：locked 键不该挡住修正
        self.assertEqual(self.generated, [target])
        self.assertTrue(self.plugin.db.kv_get("diary_locked:" + target.isoformat(), False))

    def test_rewrite_budget_caps_auto_rewrites(self):
        target = date(2026, 11, 17)
        self.save_schedule(target, [WORK_BLOCK])
        self.write_existing(target, "今天没出门，一直在家。", stale=True)
        self.plugin.db.kv_set("diary_rewrites", {target.isoformat() + "|daily": DIARY_REWRITE_LIMIT})
        self.run_at(datetime.datetime(2026, 11, 18, 10, 0))
        self.assertEqual(self.generated, [])
        self.assertFalse(self.plugin.db.kv_get("diary_stale:" + target.isoformat(), False))

    # ---------- 事实门：只查矛盾 ----------

    def test_mismatch_work_claim_vs_sick_schedule(self):
        target = date(2026, 11, 17)
        self.save_schedule(target, [HOSPITAL_BLOCK])
        self.assertTrue(diary_fact_mismatch(self.plugin, target, "今天在公司上班，忙到很晚。"))

    def test_mismatch_work_claim_vs_sick_health(self):
        target = date(2026, 11, 17)
        self.plugin.state = FakeState(sick=True, db=self.plugin.db)
        self.save_schedule(target, [HOME_BLOCK])
        self.assertTrue(diary_fact_mismatch(self.plugin, target, "今天去公司上班了。"))
        self.assertTrue(diary_fact_mismatch(self.plugin, target, "病已经好了，精神不错。"))

    def test_mismatch_early_sleep_vs_late_snack(self):
        target = date(2026, 11, 17)
        self.save_schedule(target, [SNACK_BLOCK])
        self.assertTrue(diary_fact_mismatch(self.plugin, target, "今天早早睡了，什么都没吃。"))

    def test_mismatch_at_home_vs_out_schedule(self):
        target = date(2026, 11, 17)
        self.save_schedule(target, [WORK_BLOCK])
        self.assertTrue(diary_fact_mismatch(self.plugin, target, "今天一直在家。"))
        self.save_schedule(target, [HOME_BLOCK])
        self.assertFalse(diary_fact_mismatch(self.plugin, target, "今天一直在家。"))

    def test_omission_is_not_mismatch(self):
        target = date(2026, 11, 17)
        self.save_schedule(target, [WORK_BLOCK, SNACK_BLOCK])
        facts = diary_day_facts(self.plugin, target)
        self.assertTrue(facts["out"])
        self.assertTrue(facts["snack"])
        # 只是没提宵夜/没提上班 → 遗漏，不是矛盾
        self.assertFalse(diary_fact_mismatch(self.plugin, target, "今天有点累，吃了饭就睡了。"))
        self.assertFalse(diary_fact_mismatch(self.plugin, target, "今天很平淡，没提宵夜，也没提上班。"))

    def test_manual_correction_keeps_lock_and_marks_stale(self):
        target = clock.today() - timedelta(days=1)
        self.write_existing(target, "今天在家休息。", locked=True)
        self.assertTrue(self.plugin._mark_daily_diary_stale(target.isoformat(), 0))
        self.assertTrue(self.plugin.db.kv_get("diary_locked:" + target.isoformat(), False))
        self.assertTrue(self.plugin.db.kv_get("diary_stale:" + target.isoformat(), False))

    def test_manual_correction_rewrites_locked_history(self):
        todays = clock.today()
        target = todays - timedelta(days=1)
        self.save_schedule(target, [WORK_BLOCK])
        self.write_existing(target, "今天没出门，一直在家。", locked=True, stale=True)
        self.plugin.db.kv_set("diary_last_day", (target - timedelta(days=1)).isoformat())
        self.run_at(datetime.datetime.combine(todays, datetime.time(10, 0)))
        self.assertEqual(self.generated, [target])
        self.assertTrue(self.plugin.db.kv_get("diary_locked:" + target.isoformat(), False))
        self.assertFalse(self.plugin.db.kv_get("diary_stale:" + target.isoformat(), False))

    def test_stale_early_sleep_contradiction_rewrites(self):
        target = date(2026, 11, 17)
        self.save_schedule(target, [SNACK_BLOCK])
        self.write_existing(target, "今天早早睡了，什么都没吃。", locked=True, stale=True)
        self.run_at(datetime.datetime(2026, 11, 18, 15, 0))
        self.assertEqual(self.generated, [target])
        self.assertFalse(self.plugin.db.kv_get("diary_stale:" + target.isoformat(), False))

    def test_mismatch_from_ledger_night_takeout(self):
        target = date(2026, 11, 17)
        self.plugin.wallet = FakeWallet([{
            "delta_cents": -1800, "item_name": "烧烤外卖", "reason": "宵夜",
            "ts": datetime.datetime.combine(target, datetime.time(23, 30)).timestamp(),
        }])
        facts = diary_day_facts(self.plugin, target)
        self.assertTrue(facts["snack"])
        self.assertTrue(diary_fact_mismatch(self.plugin, target, "今晚没点外卖。"))

    # ---------- 旧配置兼容 ----------

    def test_legacy_2200_still_writes_today(self):
        self.plugin.config["diary_time"] = "22:00"
        self.plugin.db.kv_set("diary_last_day", "2026-11-16")
        self.run_at(datetime.datetime(2026, 11, 17, 22, 10))
        self.assertEqual(self.generated, [date(2026, 11, 17)])
        self.assertIn("2026-11-17", self.written)
        # 旧配置照旧：22:00 前不写
        self.plugin.config["diary_time"] = "22:00"
        self.plugin.db.kv_set("diary_last_day", "2026-11-16")
        self.generated.clear()
        self.run_at(datetime.datetime(2026, 11, 17, 21, 50))
        self.assertEqual(self.generated, [])

    def test_manual_target_message_says_second_day(self):
        day, error = diary_manual_target(
            datetime.datetime(2026, 11, 17, 15, 0), date(2026, 11, 17))
        self.assertIsNone(day)
        self.assertIn("第二天", error)

    def test_panel_copy_says_any_time_next_day(self):
        path = os.path.join(ROOT, "views", "dashboard", "index.html")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("第二天开机后自动补写", text)
        self.assertIn("几点都行", text)
        self.assertNotIn("08:00–08:30 自动写", text)


if __name__ == "__main__":
    unittest.main()
