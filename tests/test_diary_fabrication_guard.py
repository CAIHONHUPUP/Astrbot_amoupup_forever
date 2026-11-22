# -*- coding: utf-8 -*-
"""日记虚构互动守卫（2026-11-17）。

现场：当天 messages 表 0 条聊天，块日记却写「想起他早上在微信上提醒我多喝水」。
根因之一是指示词拿示例句讲「主客体反转」，模型把示例当成素材；validate_text
只做了有限拦截。这里钉住两件事：
1) 提示词里不再有可直接抄的示例句，且空聊天时有一条硬规则；
2) 素材没有聊天时，代码按句删/改「他今天提醒我/跟我说/发消息」这类具体互动，
   情绪句保留；素材里真有这条聊天时允许保留。
每个用例都对应一条真实回退：去掉硬规则/守卫，对应断言就红。
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_diary_guard as guard  # noqa: E402
import amo_fact_pool as fp  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402

SICK_POOL = {
    "facts": [
        {"type": "日程", "detail": "03:00 发烧，裹着被子躺着（家）"},
        {"type": "状态", "detail": "现在在家"},
    ],
    "meta": {"has_chat": False},
}
#: 真实库 2026-11-17 06:12-06:22 那条孤儿日记的同款句子。
FABRICATED = ("烧还没退，浑身发酸，赖在被窝里懒得动。"
              "想起他早上在微信上提醒我多喝水，就顺手倒了杯温水放在床头。")
NO_CHAT_FACTS = [{"type": "日程", "detail": "06:00 在家躺着（家）"},
                 {"type": "状态", "detail": "现在在家"}]
CHAT_FACTS = [{"type": "线上聊天", "detail": "[他对我说]：他提醒我多喝水"}]


class PromptTests(unittest.TestCase):
    def test_prompt_has_no_copyable_example(self):
        text = fp.render_prompt(SICK_POOL)
        # 原句是「更禁止主客体反转（许写“他提醒我多喝水”，不许写“我提醒他多喝水”）」
        self.assertNotIn("更禁止主客体反转", text)
        self.assertNotIn("他提醒我多喝水", text)
        self.assertNotIn("他问我要不要去看医生", text)

    def test_empty_chat_prompt_has_hard_rule(self):
        text = fp.render_prompt(SICK_POOL)
        self.assertIn("没聊天就禁止具体互动·硬规则", text)
        self.assertIn("今天没发生的事", text)

    def test_chat_prompt_keeps_abstract_rule_only(self):
        pool = {"facts": CHAT_FACTS, "meta": {"has_chat": True}}
        text = fp.render_prompt(pool)
        self.assertIn("今天没发生的事", text)
        self.assertNotIn("没聊天就禁止具体互动·硬规则", text)


class DeterministicGuardTests(unittest.TestCase):
    def test_empty_chat_strips_today_interaction(self):
        out = fp.validate_text(FABRICATED, SICK_POOL)
        for token in ("提醒我", "在微信上", "跟我说", "发消息", "打电话"):
            self.assertNotIn(token, out)
        # 只删具体互动句，体感/情绪不能整段丢
        self.assertIn("烧还没退", out)
        self.assertIn("想起他以前叮嘱过", out)

    def test_real_chat_material_is_kept(self):
        pool = {"facts": CHAT_FACTS, "meta": {"has_chat": True}}
        out = fp.validate_text("他早上在微信上提醒我多喝水，我听话地倒了杯温水。", pool)
        self.assertIn("提醒我多喝水", out)

    def test_past_reminder_is_allowed(self):
        out = guard.strip_fabricated_interaction("想起他以前叮嘱过我要多喝水。", "", False)
        self.assertIn("以前叮嘱", out)

    def test_reverse_without_hard_guard_keeps_fabrication(self):
        # 反向验证：把确定性兜底换成原样返回，虚构句就会留在日记里 → 上面那条用例会红。
        with mock.patch.object(fp, "_strip_interaction",
                               lambda text, facts_text, has_chat, partner_home=False: text):
            out = fp.validate_text(FABRICATED, SICK_POOL)
        self.assertIn("提醒我多喝水", out)

    def test_hour_diary_sanitizer_strips_fabrication(self):
        out = ScheduleEngine._sanitize_hour_diary(FABRICATED, NO_CHAT_FACTS)
        self.assertNotIn("提醒我", out)
        self.assertIn("烧还没退", out)

    def test_hour_diary_sanitizer_keeps_real_chat(self):
        out = ScheduleEngine._sanitize_hour_diary(
            "他早上在微信上提醒我多喝水，我听话地倒了杯温水。", CHAT_FACTS)
        self.assertIn("提醒我多喝水", out)

    def test_reverse_hour_diary_without_guard_keeps_fabrication(self):
        with mock.patch.object(guard, "claims_today_interaction", lambda *a, **k: False):
            out = ScheduleEngine._sanitize_hour_diary(FABRICATED, NO_CHAT_FACTS)
        self.assertIn("提醒我多喝水", out)

    def test_safe_line_rotates_across_days(self):
        # P2（2026-11-22）：兜底句按日期轮换，连续两天不再复读同一句。
        text = "他今天在微信上提醒我多喝水，我就顺手倒了杯温水。"
        outs = set()
        for day in ("2026-11-06", "2026-11-07", "2026-11-08"):
            out = guard.strip_fabricated_interaction(text, "", False, day=day)
            self.assertNotIn("提醒我", out)
            outs.add(out)
        self.assertEqual(3, len(outs), outs)
        # 不给 day 的旧调用方保持原固定句（兼容测试/旧路径）
        self.assertIn("想起他以前叮嘱过", guard.strip_fabricated_interaction(text, "", False))

    def test_food_and_file_words_are_not_scrubbed_as_names(self):
        # P2（2026-11-22）：人名兜底不能把「文档/牛肉粉」换成「一个朋友」。
        for text in ("上午整理文档、写周报", "吃了一个牛肉粉", "终于下班了",
                     "是这家店贵，我没舍得买"):
            self.assertEqual(text, guard.scrub_names_guarded(text), text)
        self.assertIn("一个朋友", guard.scrub_names_guarded("今天和李雨桐吃饭"))

    def test_hour_diary_prompt_has_no_copyable_example(self):
        # 块日记的提示词在 schedule_diary._diary_prompt（同一个根因）。
        import inspect

        source = inspect.getsource(ScheduleEngine._diary_prompt)
        self.assertNotIn("他提醒我多喝水", source)


if __name__ == "__main__":
    unittest.main()
