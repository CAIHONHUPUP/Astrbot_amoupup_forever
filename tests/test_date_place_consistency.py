# -*- coding: utf-8 -*-
"""P1 约会地点的跨模块不变量。

date_session（active/together）是约会状态的唯一真相：同一份约会地点必须让
place_guard / reply_fact_guard / amo_diary_guard / amo_location_rules 给出同一套
「她在哪」的判断；把 together 去掉后，面对面的说法必须被改回一个人/手机。

反向验证：下面每条正向断言都配一条「去掉 together/日期上下文后结果不同」的断言，
生产代码一旦不再读 date_session/together，测试会红。
"""
from __future__ import annotations

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_date_session  # noqa: E402
import amo_diary_guard  # noqa: E402
import amo_location_rules  # noqa: E402
import place_guard  # noqa: E402
import reply_fact_guard  # noqa: E402


class FakeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self, kv=None):
        self.db = FakeDB(kv)


def date_activity(place="商场", zone="mall", activity="和老公逛街", category="play"):
    return {"activity": activity, "place": place, "zone": zone, "category": category}


def office_activity():
    return {"activity": "上班", "place": "公司", "zone": "office", "category": "work"}


def date_session_kv(place="商场", together=True, now=None):
    now = now if now is not None else time.time()
    return {"date_session": {
        "schema_version": 1, "phase": "active", "together": together,
        "title": "逛街", "place": place, "line": "和老公在" + place,
        "day": time.strftime("%Y-%m-%d", time.localtime(now)),
        "start_ts": now - 600, "end_ts": now + 3600,
        "updated_at": now - 600,
    }}


class DatePlaceConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()

    def test_date_session_together_place_drives_place_guard(self):
        plugin = FakePlugin(date_session_kv(place="商场", now=self.now))
        session = amo_date_session.load(plugin, now=self.now)
        self.assertEqual(session["phase"], "active")
        self.assertTrue(session["together"])
        self.assertEqual(session["place"], "商场")
        activity = date_activity(session["place"])
        self.assertEqual(place_guard.allowed_groups(activity), frozenset({"mall"}))
        self.assertEqual(place_guard.spoken_place(activity), "商场")
        # 反向：同一条街上的 office 活动只允许公司，说明 allowed_groups 确实读地点
        self.assertEqual(place_guard.allowed_groups(office_activity()), frozenset({"office"}))

    def test_fact_guard_rewrites_conflict_to_date_place(self):
        # 3a-2b：place_guard.rewrite_reply 已删，同一句的改口由 reply_fact_guard 承担；
        # 正反两个方向都断言（冲突必须改，合法不许动），比原来只测 A 更强。
        activity = date_activity()
        fixed, violations = reply_fact_guard.guard(
            "我在公司加班呢", {"activity": activity,
                               "date": {"active": True, "together": True}})
        self.assertIn("商场", fixed)
        self.assertNotIn("公司", fixed)
        self.assertIn("place", {item["rule"] for item in violations})
        # 反向：日程本来就在公司时，这句话合法、不许改
        fixed_ok, violations_ok = reply_fact_guard.guard(
            "我在公司加班呢", {"activity": office_activity()})
        self.assertEqual(fixed_ok, "我在公司加班呢")
        self.assertEqual(violations_ok, [])

    def test_reply_fact_guard_place_uses_date_place(self):
        ctx = {"activity": date_activity(), "date": {"active": True, "together": True}}
        fixed, violations = reply_fact_guard.guard("我在公司加班呢", ctx)
        self.assertIn("商场", fixed)
        self.assertNotIn("公司", fixed)
        self.assertIn("place", {item["rule"] for item in violations})
        # 反向：没有进行中的约会、日程就在公司 → 不判冲突
        office_ctx = {"activity": office_activity(), "date": {"active": False, "together": False}}
        fixed2, violations2 = reply_fact_guard.guard("我在公司加班呢", office_ctx)
        self.assertEqual(fixed2, "我在公司加班呢")
        self.assertFalse(violations2)

    def test_reply_fact_guard_companion_depends_on_together(self):
        text = "他抱着我，说今天真开心。"
        _, ok = reply_fact_guard.guard(
            text, {"activity": date_activity(), "date": {"active": True, "together": True}})
        self.assertFalse([item for item in ok if item["rule"] == "companion"], ok)
        fixed, bad = reply_fact_guard.guard(
            text, {"activity": date_activity(), "date": {"active": False, "together": False}})
        self.assertTrue([item for item in bad if item["rule"] == "companion"], bad)
        self.assertNotIn("抱着我", fixed)
        # 反向验证：去掉 together/date 传参（默认就是没在一起）→ 同样的句子被改
        fixed_default, bad_default = reply_fact_guard.guard(text, {"activity": date_activity()})
        self.assertTrue([item for item in bad_default if item["rule"] == "companion"], bad_default)
        self.assertNotIn("抱着我", fixed_default)

    def test_diary_guard_face_to_face_only_when_together(self):
        face = "他抱着我，说今天真开心。"
        self.assertIn("抱着我", amo_diary_guard.sanitize_diary(face, together=True))
        alone = amo_diary_guard.sanitize_diary(face, together=False)
        self.assertNotIn("抱着我", alone)
        # 反向验证：不传 together 与 together=False 等价（默认不在一起）
        self.assertEqual(amo_diary_guard.sanitize_diary(face), alone)
        self.assertIn("约会面对面", amo_diary_guard.fact_rules(True))
        self.assertNotIn("约会面对面", amo_diary_guard.fact_rules(False))
        self.assertIn("各回各家", amo_diary_guard.fact_rules(False))

    def test_location_rules_words_match_date_place(self):
        plugin = FakePlugin(date_session_kv(place="商场", now=self.now))
        place = amo_date_session.load(plugin, now=self.now)["place"]
        self.assertEqual(amo_location_rules.resolve(place), "mall")
        prompt = amo_location_rules.prompt_clause(place=place)
        self.assertIn("专柜", prompt)
        self.assertIn("工位", prompt)  # 商场规则把工位列为 forbidden
        self.assertEqual(amo_location_rules.check("我在工位加班", place=place), ["工位"])
        self.assertEqual(amo_location_rules.check("我在专柜挑东西", place=place), [])
        self.assertIn("被窝", amo_location_rules.prompt_clause(place="家"))
        self.assertIn("工位", amo_location_rules.prompt_clause(place="公司"))
        # 整天日记：约会地点=商场 → 工位专属词要被拦，商场词放行
        self.assertEqual(amo_location_rules.day_check("我在工位加班", places=[place]), ["工位"])
        self.assertEqual(amo_location_rules.day_check("我在专柜挑东西", places=[place]), [])


if __name__ == "__main__":
    unittest.main()
