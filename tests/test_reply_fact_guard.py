# -*- coding: utf-8 -*-
"""「回复事实不矛盾」单测（纯标准库，不连 AstrBot、不花钱）。

2026-11-17：用户拍板「不矛盾」档——她聊天里的硬事实（地点/活动/吃饭/健康/陪伴）
不能和当前日程状态打架，但日程没写的小细节（吃什么、说什么、心情）自由。
这里测四层：
- 正例放行：情绪/想念/撒娇/食物细节/含糊的「在忙」；
- 反例改写：上班说在家、在家说在公司、睡觉说在逛街、没病说发烧、没约会说抱我、非饭点说正在吃；
- 边界：吃饭前后 1 小时、rest vs sleep、trip/hotel、date together；
- 反向验证：拆掉 place / health / meal / companion 规则，对应的反例就不再被改写。
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import _plugin_source  # noqa: E402
import reply_fact_guard  # noqa: E402
from reply_fact_guard import guard, has_violation  # noqa: E402

NOW = _dt.datetime(2026, 11, 17, 10, 30)  # 工作日上午
OFFICE = {"zone": "office", "place": "莞城区的写字楼", "activity": "对着屏幕敲周报",
          "category": "work"}
HOME = {"zone": "home", "place": "家", "activity": "瘫在沙发上", "category": "rest"}
SLEEP = {"zone": "home", "place": "家", "activity": "睡觉", "category": "sleep"}
MEAL = {"zone": "restaurant", "place": "公司附近", "activity": "吃午饭", "category": "meal"}
HOSPITAL = {"zone": "hospital", "place": "市医院", "activity": "排队看诊", "category": "health"}
MALL = {"zone": "mall", "place": "商场", "activity": "逛街试衣服", "category": "life"}
SCENIC = {"zone": "scenic", "place": "贵阳·黔灵山公园", "spot": "黔灵山公园",
          "activity": "逛景区", "category": "play"}
HOTEL = {"zone": "hotel", "place": "民宿", "activity": "放下行李", "category": "rest"}
HEALTHY = {"status": "健康", "sick": False}
SICK = {"status": "感冒", "sick": True}


def ctx(activity=OFFICE, now=NOW, health=HEALTHY, date=None, trip=None,
        nearby=None, today_chat=True):
    return {
        "now": now, "activity": activity, "health": health, "date": date or {},
        "trip": trip or {}, "nearby": nearby or [], "today_chat": today_chat,
    }


class AllowTests(unittest.TestCase):
    """正例：这些说法不该被改。"""

    def test_emotion_and_missing_are_free(self):
        text = "好想你呀，今天有没有想我"
        fixed, violations = guard(text, ctx())
        self.assertEqual(fixed, text)
        self.assertEqual(violations, [])

    def test_coquetry_is_free(self):
        text = "要抱抱，再亲一下嘛"
        self.assertEqual(guard(text, ctx())[0], text)

    def test_vague_busy_is_allowed(self):
        text = "在忙呢，等我一下下"
        self.assertEqual(guard(text, ctx())[0], text)

    def test_matching_work_claim_is_allowed(self):
        text = "我在公司忙着呢，晚点回你"
        self.assertEqual(guard(text, ctx())[0], text)

    def test_food_detail_in_future_is_free(self):
        text = "晚上想吃火锅，加很多毛肚"
        self.assertEqual(guard(text, ctx())[0], text)

    def test_partial_discomfort_is_detail_not_illness(self):
        text = "有点困，靠一会儿就好"
        self.assertEqual(guard(text, ctx())[0], text)

    def test_negated_illness_is_allowed(self):
        text = "我没发烧，就是有点累"
        self.assertEqual(guard(text, ctx())[0], text)

    def test_talking_about_him_is_allowed(self):
        text = "他上班很辛苦，你别感冒了"
        self.assertEqual(guard(text, ctx())[0], text)

    def test_matching_home_claim_is_allowed(self):
        text = "我在家呢，刚洗完澡"
        self.assertEqual(guard(text, ctx(activity=HOME))[0], text)

    def test_matching_hospital_claim_is_allowed(self):
        text = "我在医院排队呢"
        self.assertEqual(guard(text, ctx(activity=HOSPITAL))[0], text)

    def test_future_plan_is_not_a_current_claim(self):
        text = "明天下班去逛街，你陪我吗"
        self.assertEqual(guard(text, ctx())[0], text)


class RewriteTests(unittest.TestCase):
    """反例：硬事实冲突必须按日程/状态改口。"""

    def test_home_while_at_work_is_rewritten_to_office(self):
        fixed, violations = guard("我在家躺着呢", ctx(activity=OFFICE))
        self.assertIn("公司", fixed)
        self.assertNotIn("家", fixed)
        self.assertEqual([item["rule"] for item in violations], ["place"])

    def test_office_while_at_home_is_rewritten_home(self):
        fixed, violations = guard("我在公司加班呢", ctx(activity=HOME))
        self.assertIn("家", fixed)
        self.assertNotIn("公司", fixed)
        self.assertIn("place", [item["rule"] for item in violations])

    def test_home_claim_at_hospital_is_rewritten(self):
        fixed, violations = guard("我在家呢，好累", ctx(activity=HOSPITAL))
        self.assertIn("医院", fixed)
        self.assertNotIn("家", fixed)
        self.assertEqual([item["rule"] for item in violations], ["place"])

    def test_shopping_claim_while_sleeping_is_rewritten(self):
        fixed, _violations = guard("我在逛街呢", ctx(activity=SLEEP))
        self.assertNotIn("逛街", fixed)
        self.assertIn("家", fixed)

    def test_downstairs_claim_while_at_work_is_rewritten(self):
        fixed, _violations = guard("我在楼下呢", ctx(activity=OFFICE))
        self.assertNotIn("楼下", fixed)
        self.assertIn("公司", fixed)

    def test_sleep_claim_while_at_work_is_rewritten(self):
        fixed, violations = guard("我在睡觉呢", ctx(activity=OFFICE))
        self.assertIn("上班", fixed)
        self.assertNotIn("睡觉", fixed)
        self.assertEqual([item["rule"] for item in violations], ["activity"])

    def test_work_claim_while_sleeping_is_rewritten(self):
        fixed, violations = guard("我在上班呢", ctx(activity=SLEEP))
        self.assertIn("睡觉", fixed)
        self.assertNotIn("上班", fixed)
        self.assertEqual([item["rule"] for item in violations], ["activity"])

    def test_work_claim_while_having_lunch_is_rewritten(self):
        fixed, violations = guard("我在加班呢", ctx(activity=MEAL))
        self.assertNotIn("加班", fixed)
        self.assertIn("吃饭", fixed)
        self.assertEqual([item["rule"] for item in violations], ["activity"])

    def test_travel_claim_without_trip_is_rewritten(self):
        fixed, violations = guard("我在旅行呢", ctx(activity=SCENIC))
        self.assertNotIn("旅行", fixed)
        self.assertIn("外面", fixed)
        self.assertEqual([item["rule"] for item in violations], ["activity"])

    def test_hotel_claim_without_trip_is_rewritten(self):
        fixed, violations = guard("我在酒店呢", ctx(activity=HOTEL))
        self.assertNotIn("酒店", fixed)
        self.assertEqual([item["rule"] for item in violations], ["activity"])

    def test_eating_off_meal_time_is_downgraded(self):
        fixed, violations = guard("我正在吃呢", ctx(activity=OFFICE))
        self.assertNotIn("正在吃", fixed)
        self.assertIn("待会儿", fixed)
        self.assertEqual([item["rule"] for item in violations], ["meal"])

    def test_finished_eating_off_meal_time_is_downgraded(self):
        fixed, violations = guard("我刚吃完了，好饱", ctx(activity=OFFICE))
        self.assertNotIn("刚吃完", fixed)
        self.assertIn("等会儿", fixed)
        self.assertEqual([item["rule"] for item in violations], ["meal"])

    def test_care_question_is_not_her_health_claim(self):
        # 线上 18:22：他发烧咳嗽，她问「咳嗽多久了…药吃了吗」被当成她生病，
        # 换成「就是有点累？」。关心/追问句不能按她的健康声明改写。
        for clause in ("咳嗽多久了", "药吃了吗？", "你咳成这样", "要不要去看医生"):
            with self.subTest(clause=clause):
                fixed, hits = reply_fact_guard._health_rule(clause, {"health": HEALTHY})
                self.assertEqual(clause, fixed)
                self.assertEqual([], hits)

    def test_fever_claim_while_healthy_is_softened(self):
        fixed, violations = guard("我还烧着呢", ctx(health=HEALTHY))
        self.assertNotIn("烧", fixed)
        self.assertIn("有点累", fixed)
        self.assertEqual([item["rule"] for item in violations], ["health"])

    def test_fever_claim_while_only_tired_is_softened(self):
        fixed, _violations = guard("我感冒了", ctx(health={"status": "疲惫", "sick": False}))
        self.assertNotIn("感冒", fixed)
        self.assertIn("有点累", fixed)

    def test_recovery_claim_while_sick_is_softened(self):
        fixed, violations = guard("我好了，不难受了", ctx(health=SICK))
        self.assertNotIn("我好了", fixed)
        self.assertIn("还是有点不舒服", fixed)
        self.assertEqual(fixed.count("还是有点不舒服"), 1)
        self.assertEqual([item["rule"] for item in violations], ["health"])

    def test_recovery_with_negated_fever_rewrites_only_once(self):
        fixed, violations = guard("我已经好了，不烧了", ctx(health=SICK))
        self.assertEqual(fixed.count("还是有点不舒服"), 1)
        self.assertNotIn("已经好了", fixed)
        self.assertIn("不烧了", fixed)
        self.assertEqual([item["rule"] for item in violations], ["health"])

    def test_two_recovery_claims_in_one_sentence_rewrite_once(self):
        fixed, violations = guard("我好了，已经好了，别担心", ctx(health=SICK))
        self.assertEqual(fixed.count("还是有点不舒服"), 1)
        self.assertEqual([item["rule"] for item in violations], ["health"])

    def test_not_recovered_claim_while_sick_still_rewritten(self):
        for text in ("我还没好", "我没好"):
            fixed, violations = guard(text, ctx(health=SICK))
            self.assertIn("还是有点不舒服", fixed)
            self.assertNotIn("没好", fixed)
            self.assertEqual([item["rule"] for item in violations], ["health"])

    def test_fever_recovery_claim_while_sick_is_softened(self):
        fixed, _violations = guard("我退烧了", ctx(health=SICK))
        self.assertNotIn("退烧", fixed)
        self.assertIn("还是有点不舒服", fixed)

    def test_hug_claim_without_date_is_rewritten(self):
        fixed, violations = guard("他刚抱我了", ctx())
        self.assertNotIn("抱", fixed)
        self.assertIn("手机上聊了几句", fixed)
        self.assertEqual([item["rule"] for item in violations], ["companion"])

    def test_beside_claim_without_date_is_rewritten(self):
        fixed, _violations = guard("他在我旁边坐着呢", ctx())
        self.assertNotIn("旁边", fixed)
        self.assertIn("手机", fixed)

    def test_violation_records_all_four_fields(self):
        _fixed, violations = guard("我在家躺着呢", ctx(activity=OFFICE))
        self.assertEqual(len(violations), 1)
        item = violations[0]
        for key in ("rule", "sentence", "replacement", "reason"):
            self.assertIn(key, item)
            self.assertTrue(str(item[key]).strip())
        self.assertEqual(item["sentence"], "我在家躺着呢")
        self.assertIn("公司", item["replacement"])
        self.assertIn("office", item["reason"])

    def test_multiple_conflicts_are_all_rewritten(self):
        fixed, violations = guard("我在家躺着，我还在发烧，他刚抱我", ctx(activity=OFFICE))
        self.assertEqual([item["rule"] for item in violations], ["place", "health", "companion"])
        self.assertNotIn("家", fixed)
        self.assertNotIn("发烧", fixed)
        self.assertNotIn("抱", fixed)


class BoundaryTests(unittest.TestCase):
    """边界：时间窗口 / rest 与 sleep / trip / date。"""

    def test_meal_within_next_hour_is_allowed(self):
        activity = {"zone": "office", "place": "公司", "activity": "敲代码",
                    "category": "work"}
        nearby = [{"category": "meal", "start": "12:00", "end": "13:00"}]
        text = "我正在吃呢"
        fixed, violations = guard(text, ctx(activity=activity, nearby=nearby,
                                            now=_dt.datetime(2026, 11, 17, 11, 50)))
        self.assertEqual(fixed, text)
        self.assertEqual(violations, [])

    def test_meal_ended_two_hours_ago_is_violation(self):
        activity = {"zone": "office", "place": "公司", "activity": "敲代码",
                    "category": "work"}
        nearby = [{"category": "meal", "start": "12:00", "end": "13:00"}]
        fixed, violations = guard("我刚吃完了", ctx(activity=activity, nearby=nearby,
                                                   now=_dt.datetime(2026, 11, 17, 15, 0)))
        self.assertNotEqual(fixed, "我刚吃完了")
        self.assertEqual([item["rule"] for item in violations], ["meal"])

    def test_meal_spanning_hour_is_allowed(self):
        activity = {"zone": "office", "place": "公司", "activity": "敲代码",
                    "category": "work"}
        nearby = [{"category": "meal", "start": "11:50", "end": "12:40"}]
        fixed, _violations = guard("我正在吃面", ctx(activity=activity, nearby=nearby,
                                                   now=_dt.datetime(2026, 11, 17, 12, 10)))
        self.assertEqual(fixed, "我正在吃面")

    def test_rest_allows_sleep_claim(self):
        text = "我在睡觉呢"
        self.assertEqual(guard(text, ctx(activity=HOME))[0], text)

    def test_rest_at_night_allows_sleep_claim(self):
        text = "我在睡觉呢"
        context = ctx(activity=HOME, now=_dt.datetime(2026, 11, 17, 23, 30))
        self.assertEqual(guard(text, context)[0], text)

    def test_transit_zone_home_claim_is_rewritten(self):
        transit = {"zone": "transit", "place": "地铁2号线", "activity": "挤地铁",
                   "category": "commute"}
        fixed, violations = guard("我在家躺着呢", ctx(activity=transit))
        self.assertNotIn("家", fixed)
        self.assertIn("路上", fixed)
        self.assertEqual([item["rule"] for item in violations], ["place"])

    def test_transit_zone_vehicle_claim_is_allowed(self):
        transit = {"zone": "transit", "place": "地铁2号线", "activity": "挤地铁",
                   "category": "commute"}
        text = "我在地铁上呢，快到了"
        self.assertEqual(guard(text, ctx(activity=transit))[0], text)

    def test_trip_active_allows_travel_and_hotel(self):
        trip = {"active": True, "phase": "traveling", "hotel": True}
        self.assertEqual(guard("我在旅行呢", ctx(activity=SCENIC, trip=trip))[0],
                         "我在旅行呢")
        self.assertEqual(guard("我在酒店呢", ctx(activity=HOTEL, trip=trip))[0],
                         "我在酒店呢")

    def test_date_together_allows_physical_claims(self):
        date = {"active": True, "together": True}
        text = "他刚抱我，在沙发上看剧"
        self.assertEqual(guard(text, ctx(activity=HOME, date=date))[0], text)

    def test_meal_category_itself_allows_eating_claim(self):
        text = "我正在吃呢"
        self.assertEqual(guard(text, ctx(activity=MEAL))[0], text)

    def test_empty_text_is_untouched(self):
        self.assertEqual(guard("", ctx()), ("", []))

    def test_unknown_activity_never_conflicts_on_place(self):
        text = "我在家躺着呢"
        self.assertEqual(guard(text, ctx(activity={}))[0], text)


class PlaceGapTests(unittest.TestCase):
    """3a-2b C1：place 规则的四个缺口（到达/目的地/裸地点词/残留地点词）。

    这四条都是真实模型原话上实测出来的：删掉老 place_guard.rewrite_reply 之后，
    回复事实守卫是唯一的地点守卫，它要么挡住，要么不许把句子改坏。
    每条断言在改前都是红的（见 work/audit/_scratch/plug_before_c1.py 对照）。
    """

    ROAD = {"zone": "road", "place": "地铁", "activity": "挤地铁", "category": "commute"}

    def test_arrival_with_particle_is_rewritten_home(self):
        # 「我到了商场啦」：到达句尾部只有语气助词，按到达改口（改前直接放行）
        fixed, violations = guard("我到了商场啦", ctx(activity=HOME))
        self.assertNotIn("商场", fixed)
        self.assertIn("家", fixed)
        self.assertTrue(fixed.startswith("我到"), fixed)
        self.assertTrue(fixed.endswith("啦"), fixed)
        self.assertEqual([item["rule"] for item in violations], ["place"])

    def test_return_with_particle_is_rewritten_home(self):
        # 「我回到公司了」：回到句同样按到达改口
        fixed, violations = guard("我回到公司了", ctx(activity=HOME))
        self.assertNotIn("公司", fixed)
        self.assertIn("家", fixed)
        self.assertEqual([item["rule"] for item in violations], ["place"])

    def test_arrival_at_school_is_rewritten_to_restaurant(self):
        # 改前输出是残句「我到了」：必须是一句完整话
        fixed, _violations = guard("我到学校了", ctx(activity=MEAL))
        self.assertNotIn("学校", fixed)
        self.assertIn("餐厅", fixed)
        self.assertNotEqual(fixed, "我到了")

    def test_arrival_destination_phrase_is_kept(self):
        # 「等我到家再拍」里的「到家」是目的地/以后的动作，不是此刻所在
        text = "等我到家再拍给你看"
        fixed, violations = guard(text, ctx(activity=OFFICE))
        self.assertEqual(fixed, text)
        self.assertEqual(violations, [])

    def test_going_to_place_is_kept(self):
        # 「去+地点」是目的地：不许把地点词删掉、留下「赶着去」这种残句
        text = "我赶着去公司呢"
        fixed, _violations = guard(text, ctx(activity=HOME))
        self.assertEqual(fixed, text)
        self.assertNotIn("赶着去呢", fixed)

    def test_going_to_place_on_transit_is_kept(self):
        # 同一条在通勤日程下（地铁里赶着去公司）：地铁里不是「快到了」整句改写
        text = "我现在在地铁里赶着去公司呢"
        fixed, _violations = guard(text, ctx(activity=self.ROAD))
        self.assertEqual(fixed, text)
        self.assertNotIn("赶着去呢", fixed)

    def test_towards_place_is_kept(self):
        # 「往+地点+走」：改前删成「往商场走」→「往走」，语义和语法都不成立
        text = "我这也刚出门往商场走呢"
        fixed, _violations = guard(text, ctx(activity=self.ROAD))
        self.assertEqual(fixed, text)
        self.assertNotIn("往走", fixed)

    def test_bare_place_word_is_judged_by_weather_not_by_omission(self):
        # 裸「外面」+ 天气是天气说法，改前删成「还热」
        text = "一动都不想动，外面还热"
        fixed, _violations = guard(text, ctx(activity=HOME))
        self.assertEqual(fixed, text)
        self.assertIn("外面还热", fixed)
        # 反过来：省略主语的裸地点词仍是地点声明，收窄判定不能把它一起放过
        fixed_claim, _violations = guard("工位上改周报", ctx(activity=HOME))
        self.assertNotIn("工位", fixed_claim)
        self.assertIn("家", fixed_claim)

    def test_residual_place_word_is_collapsed_to_place_only(self):
        # 最长地点词是活动词（爬山）时，改前只删后半句、把「景区」留了下来
        fixed, violations = guard("我在景区爬山呢", ctx(activity=HOME))
        self.assertNotIn("景区", fixed)
        self.assertIn("家", fixed)
        self.assertEqual([item["rule"] for item in violations], ["place"])
        # 商场词收句（task-41 已有语义）不能被这次改动带坏
        fixed_mall, _violations = guard("我在商场逛街呢", ctx(activity=OFFICE))
        self.assertNotIn("商场", fixed_mall)
        self.assertIn("公司", fixed_mall)


class ReverseVerificationTests(unittest.TestCase):
    """反向验证：拆掉一条规则，对应的反例就不再被改写；装回去又变绿。"""

    def _drop(self, rule):
        saved = reply_fact_guard.RULES
        reply_fact_guard.RULES = tuple(item for item in saved if item[0] != rule)
        return saved

    def test_place_rule_is_necessary(self):
        saved = self._drop("place")
        try:
            fixed, violations = guard("我在家躺着呢", ctx(activity=OFFICE))
        finally:
            reply_fact_guard.RULES = saved
        self.assertEqual(fixed, "我在家躺着呢")
        self.assertEqual(violations, [])
        self.assertTrue(has_violation("我在家躺着呢", ctx(activity=OFFICE), "place"))

    def test_health_rule_is_necessary(self):
        saved = self._drop("health")
        try:
            fixed, violations = guard("我还烧着呢", ctx(health=HEALTHY))
        finally:
            reply_fact_guard.RULES = saved
        self.assertEqual(fixed, "我还烧着呢")
        self.assertEqual(violations, [])
        self.assertTrue(has_violation("我还烧着呢", ctx(health=HEALTHY), "health"))

    def test_meal_rule_is_necessary(self):
        saved = self._drop("meal")
        try:
            fixed, violations = guard("我正在吃呢", ctx(activity=OFFICE))
        finally:
            reply_fact_guard.RULES = saved
        self.assertEqual(fixed, "我正在吃呢")
        self.assertEqual(violations, [])
        self.assertTrue(has_violation("我正在吃呢", ctx(activity=OFFICE), "meal"))

    def test_companion_rule_is_necessary(self):
        saved = self._drop("companion")
        try:
            fixed, violations = guard("他刚抱我了", ctx())
        finally:
            reply_fact_guard.RULES = saved
        self.assertEqual(fixed, "他刚抱我了")
        self.assertEqual(violations, [])
        self.assertTrue(has_violation("他刚抱我了", ctx(), "companion"))


class WiringTests(unittest.TestCase):
    @staticmethod
    def _read(name):
        with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
            return handle.read()

    def test_reply_pipeline_calls_fact_guard_after_place_guard(self):
        pipeline = self._read("amo_reply_pipeline.py")
        runtime = self._read("amo_runtime_reply.py")
        self.assertIn("def apply_fact_guard", pipeline)
        self.assertIn("[reply_guard]", pipeline)
        self.assertIn("reply_fact_guard", pipeline)
        # 装饰后、发送前真正调用一次（老 place_guard 调用点保持原样）
        self.assertIn("_reply_pipeline.apply_fact_guard", runtime)
        self.assertIn("_guard_self_location", runtime)
        self.assertLess(runtime.index("_guard_self_location(text"),
                        runtime.index("apply_fact_guard(text, self)"))

    def test_guard_targets_are_ordered_place_then_fact(self):
        names = [name for name, _handler in reply_fact_guard.RULES]
        self.assertEqual(names[:2], ["place", "activity"])
        self.assertIn("companion", names)

    def test_proactive_path_calls_fact_guard_after_consumption(self):
        proactive = self._read("amo_proactive.py")
        self.assertIn("_send_proactive_lines", proactive)
        self.assertIn("apply_fact_guard", proactive)
        self.assertLess(proactive.index("context_for_proactive"),
                        proactive.index("apply_fact_guard"))


class _FakeState:
    def __init__(self, activity):
        self.activity = dict(activity or {})

    def current_activity(self):
        return dict(self.activity)

    def health_info(self):
        return {"status": "健康", "sick": False}


class _FakePlugin:
    def __init__(self, activity=OFFICE):
        self.state = _FakeState(activity)

    def _guard_self_location(self, text, place):
        return text


class _KVDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)


class _LegacyPlugin(_FakePlugin):
    def __init__(self, kv=None, activity=HOME):
        super().__init__(activity)
        self.db = _KVDB(kv)


class LegacyBoundaryTests(unittest.TestCase):
    """老库边界：date_now / wander_trip / travel 只读回退，不迁移、不落库。"""

    def _ctx(self, kv, now=NOW, activity=HOME):
        import amo_reply_pipeline
        plugin = _LegacyPlugin(kv, activity)
        return amo_reply_pipeline.fact_guard_context(plugin, None, now)

    def test_legacy_date_now_counts_as_together(self):
        now = _dt.datetime(2026, 11, 17, 21, 10)
        context = self._ctx({"date_now": {"line": "和老公看电影",
                                          "until": now.timestamp() + 3600}}, now=now)
        self.assertTrue(context["date"]["active"])
        self.assertTrue(context["date"]["together"])
        text = "他抱着我呢，心里空空的"
        self.assertEqual(guard(text, context)[0], text)

    def test_expired_legacy_date_is_not_together(self):
        now = _dt.datetime(2026, 11, 17, 21, 10)
        context = self._ctx({"date_now": {"line": "旧约会",
                                          "until": now.timestamp() - 60}}, now=now)
        self.assertFalse(context["date"]["together"])
        fixed, violations = guard("他抱着我呢", context)
        self.assertNotIn("抱着我", fixed)
        self.assertEqual([item["rule"] for item in violations], ["companion"])

    def test_legacy_trip_phase_is_active(self):
        context = self._ctx({"wander_trip": {"phase": "traveling", "city": "贵阳",
                                             "days": 3}})
        self.assertTrue(context["trip"]["active"])
        self.assertTrue(context["trip"]["hotel"])

    def test_legacy_trip_window_is_active(self):
        now = _dt.datetime(2026, 11, 17, 15, 20)
        context = self._ctx({"travel": {"city": "贵阳", "start_day": "2026-11-16",
                                        "days": 3}}, now=now)
        self.assertTrue(context["trip"]["active"])

    def test_today_chat_reads_last_reply_stamp(self):
        import amo_reply_pipeline
        now = _dt.datetime(2026, 11, 17, 10, 30)
        plugin = _LegacyPlugin({}, HOME)
        plugin._last_reply_at = {"aiocqhttp:FriendMessage:1": now.timestamp() - 60}
        self.assertTrue(amo_reply_pipeline.fact_guard_context(plugin, None, now)["today_chat"])
        plugin._last_reply_at = {"aiocqhttp:FriendMessage:1": now.timestamp() - 86400}
        self.assertFalse(amo_reply_pipeline.fact_guard_context(plugin, None, now)["today_chat"])


class IntegrationTests(unittest.TestCase):
    """接入层：pipeline 的 apply_fact_guard 与 decorate 阶段确实会改口。"""

    def test_apply_fact_guard_reads_state_and_rewrites(self):
        import amo_reply_pipeline

        fixed = amo_reply_pipeline.apply_fact_guard("我在家躺着呢", _FakePlugin(OFFICE))
        self.assertIn("公司", fixed)
        self.assertNotIn("家", fixed)

    def test_apply_fact_guard_keeps_text_when_context_breaks(self):
        import amo_reply_pipeline

        class Broken:
            @property
            def state(self):
                raise RuntimeError("boom")

        self.assertEqual(amo_reply_pipeline.apply_fact_guard("我在家躺着呢", Broken()),
                         "我在家躺着呢")

    def test_decorate_self_location_stage_uses_fact_guard(self):
        import amo_reply_pipeline

        ctx = amo_reply_pipeline.ReplyContext(
            plugin=_FakePlugin(OFFICE), phase=amo_reply_pipeline.PHASE_DECORATE,
            place="莞城区的写字楼",
        )
        fixed = amo_reply_pipeline._dec_self_location("我在家躺着呢", ctx)
        self.assertIn("公司", fixed)


class ActivityCoverageTests(unittest.TestCase):
    """办公室/餐厅/路上说「在做饭/在开会/刚开完会/还在改方案」也要改口。"""

    ROAD = {"zone": "road", "place": "地铁", "activity": "挤地铁", "category": "commute"}
    HOME_MEAL = {"zone": "home", "place": "家", "activity": "做晚饭", "category": "meal"}

    def test_cooking_claim_at_office_is_rewritten(self):
        fixed, violations = guard("在做饭呢。", ctx())
        self.assertNotIn("做饭", fixed)
        self.assertIn("上班", fixed)
        self.assertEqual([item["rule"] for item in violations], ["activity"])

    def test_cooking_claim_at_restaurant_meal_is_rewritten(self):
        fixed, violations = guard("在做饭呢。", ctx(activity=MEAL))
        self.assertNotIn("做饭", fixed)
        self.assertIn("吃饭", fixed)
        self.assertEqual([item["rule"] for item in violations], ["activity"])

    def test_cooking_at_home_meal_is_allowed(self):
        text = "在做饭呢。"
        self.assertEqual(guard(text, ctx(activity=self.HOME_MEAL))[0], text)

    def test_meeting_or_overtime_claim_on_commute_is_rewritten(self):
        for text in ("在开会呢。", "在加班。", "刚开完会，脑子还是懵的。"):
            fixed, violations = guard(text, ctx(activity=self.ROAD))
            self.assertNotIn("开会", fixed, text)
            self.assertNotIn("加班", fixed, text)
            self.assertIn("路上", fixed, text)
            self.assertEqual([item["rule"] for item in violations], ["activity"], text)

    def test_work_edge_claim_while_sleeping_is_split(self):
        # 睡觉/休息时「刚开完会」可能是刚发生的事（近过去），保留原文；
        # 进行式的「还在改方案」说的是此刻，仍按当前状态改口（task-36：checker 把
        # 它算成动作矛盾，sleep 下也要改）。
        self.assertEqual(guard("刚开完会，脑子还是懵的。", ctx(activity=SLEEP))[0],
                         "刚开完会，脑子还是懵的。")
        fixed, violations = guard("还在改方案，烦死了。", ctx(activity=SLEEP))
        self.assertNotIn("改方案", fixed)
        self.assertEqual([item["rule"] for item in violations], ["activity"])

    def test_work_edge_claim_at_mall_uses_current_activity(self):
        fixed, _violations = guard("还在改方案，烦死了。", ctx(activity=MALL))
        self.assertNotIn("改方案", fixed)
        self.assertIn("逛街", fixed)

    def test_matching_work_edge_claims_at_office_are_allowed(self):
        for text in ("刚开完会，脑子还是懵的。", "还在改方案。"):
            self.assertEqual(guard(text, ctx())[0], text, text)


class CompanionShoulderTests(unittest.TestCase):
    """同伴的 edge 说法：「他靠在我肩上」也要和 together 状态对齐。"""

    def test_shoulder_claim_without_date_is_rewritten(self):
        fixed, violations = guard("他靠在我肩上呢。", ctx())
        self.assertNotIn("肩上", fixed)
        self.assertIn("手机上聊了几句", fixed)
        self.assertEqual([item["rule"] for item in violations], ["companion"])

    def test_shoulder_claim_with_date_is_allowed(self):
        text = "他靠在我肩上呢。"
        self.assertEqual(guard(text, ctx(date={"active": True, "together": True}))[0], text)


if __name__ == "__main__":
    unittest.main()
