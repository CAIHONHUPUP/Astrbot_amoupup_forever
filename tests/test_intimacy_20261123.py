# -*- coding: utf-8 -*-
"""2026-11-23 私聊夸/撩、先贬后夸、亲密尺度回归。"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_intimacy import (  # noqa: E402
    intimacy_prompt, is_compliment_or_flirt, is_contrast_praise,
    is_explicit, meal_missing_labels, resolve_intimacy_level,
)
from wardrobe_data import is_outfit_change_request, is_pajama_change_request  # noqa: E402


class ContrastPraiseTests(unittest.TestCase):
    def test_contrast_praise_is_not_change(self):
        text = "睡衣丑但是穿在你身上好看"
        self.assertTrue(is_contrast_praise(text))
        self.assertFalse(is_pajama_change_request(text))
        self.assertFalse(is_outfit_change_request(text))

    def test_explicit_change_still_changes(self):
        text = "睡衣不好看，换一套"
        self.assertFalse(is_contrast_praise(text))
        self.assertTrue(is_pajama_change_request(text))
        self.assertTrue(is_outfit_change_request(text))

    def test_plain_criticism_still_changes(self):
        self.assertTrue(is_pajama_change_request("睡衣不好看"))
        self.assertTrue(is_pajama_change_request("这条睡裙真丑"))

    def test_plain_praise_is_not_change(self):
        self.assertFalse(is_pajama_change_request("睡衣好看"))
        self.assertFalse(is_outfit_change_request("这件衣服很漂亮"))


class ComplimentFlirtTests(unittest.TestCase):
    def test_praise_and_flirt_detected(self):
        for text in ("你今天真好看", "这件衣服很漂亮", "没吃到你", "我睡不着", "好想你"):
            self.assertTrue(is_compliment_or_flirt(text), text)

    def test_criticism_is_not_praise(self):
        self.assertFalse(is_compliment_or_flirt("睡衣不好看"))
        self.assertFalse(is_compliment_or_flirt("这件衣服真丑"))


class IntimacyLevelTests(unittest.TestCase):
    def test_level_two_needs_explicit_switch(self):
        self.assertEqual(1, resolve_intimacy_level(2, False))
        self.assertEqual(2, resolve_intimacy_level(2, True))
        self.assertEqual(0, resolve_intimacy_level(0, True))
        self.assertEqual(1, resolve_intimacy_level(9, True))

    def test_prompts(self):
        self.assertIn("1-2 句", intimacy_prompt(0))
        self.assertIn("2-3 句", intimacy_prompt(1))
        self.assertIn("2-4 句", intimacy_prompt(2))
        self.assertIn("日记", intimacy_prompt(2))

    def test_explicit_detection(self):
        self.assertTrue(is_explicit("我想和你做爱"))
        self.assertTrue(is_explicit("今晚想睡你"))
        self.assertFalse(is_explicit("胸口有点闷"))
        self.assertFalse(is_explicit("我去插队买个煎饼"))


class ConfigAndPromptTests(unittest.TestCase):
    def test_schema_has_intimacy_keys(self):
        with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as fh:
            schema = json.load(fh)
        self.assertEqual(1, schema["intimacy_level"]["default"])
        self.assertFalse(schema["intimacy_explicit_enabled"]["default"])

    def test_catch_answer_has_reference_and_joke_rule(self):
        import amo_prompt_registry as pr
        text = pr.render("llm.catch_answer")
        self.assertIn("白便宜别人", text)
        self.assertIn("那杯咖啡", text)
        self.assertIn("别转成吃醋", text)


class IntimacyContextTests(unittest.TestCase):
    class Fake:
        def __init__(self, level=1):
            self.level = level

        def cfg_int(self, key, default=0):
            return self.level if key == "intimacy_level" else default

        def cfg_bool(self, key, default=False):
            return False

    class Event:
        def __init__(self, text):
            self.message_str = text

    def _ctx(self, text, group=False, level=1):
        from amo_runtime_context import RuntimeContextMixin
        fake = self.Fake(level)
        c = {"is_group": group, "event": self.Event(text)}
        return RuntimeContextMixin._ctx_intimacy(fake, c)

    def test_contrast_praise_context_has_no_change_complaint(self):
        parts = "\n".join(self._ctx("睡衣丑但是穿在你身上好看"))
        self.assertIn("先贬后夸", parts)
        self.assertNotIn("他嫌睡衣不好看", parts)

    def test_compliment_flirt_context_catches_it(self):
        for text in ("你今天真好看", "没吃到你，睡不着"):
            parts = "\n".join(self._ctx(text))
            self.assertIn("接住夸/撩", parts, text)
            self.assertIn("别用换衣服", parts, text)

    def test_group_gets_no_intimacy_block(self):
        self.assertEqual([], self._ctx("你今天真好看", group=True))

    def test_level_zero_has_no_compliment_block(self):
        parts = "\n".join(self._ctx("你今天真好看", level=0))
        self.assertIn("0 含蓄", parts)
        self.assertNotIn("接住夸/撩", parts)


class OutfitContrastTests(unittest.TestCase):
    class Wardrobe:
        def __init__(self):
            self.calls = []

        def set_night(self, **kwargs):
            self.calls.append("set_night")
            return {"top": "x", "bottom": "y"}

        def reroll(self):
            self.calls.append("reroll")
            return {"top": "x", "bottom": "y"}

        def at_home(self):
            return True

        def at_hotel(self):
            return False

    class State:
        def current_activity(self):
            return {"place": "家"}

    class Event:
        message_str = "睡衣丑但是穿在你身上好看"

        def is_private_chat(self):
            return True

    class Fake:
        wardrobe = None
        state = None

        def _short_place(self, value):
            return value

    def test_contrast_does_not_reroll_pajama(self):
        from amo_runtime_context import RuntimeContextMixin
        fake = self.Fake()
        fake.wardrobe = self.Wardrobe()
        fake.state = self.State()
        event = self.Event()
        parts = RuntimeContextMixin._ctx_outfit_change(fake, event)
        self.assertEqual([], parts)
        self.assertEqual([], fake.wardrobe.calls)


class ExplicitMemoryTests(unittest.TestCase):
    class Fake:
        pass

    def test_explicit_digest_is_not_written(self):
        import asyncio
        from amo_memory_bridge import BridgeMixin
        fake = self.Fake()
        result = asyncio.run(BridgeMixin._write_memory_checked(
            fake, "u1", "我想和你做爱", kind="daily_digest", scope="private"))
        self.assertFalse(result)


class MealMissingTests(unittest.TestCase):
    def test_empty_day_after_dinner_reports_both(self):
        self.assertEqual(["午饭", "晚饭"], meal_missing_labels([], 21 * 60))

    def test_lunch_only_after_lunch(self):
        self.assertEqual(["午饭"], meal_missing_labels([], 13 * 60))

    def test_existing_meal_covers_slot(self):
        items = [{"start": "12:00", "end": "13:00", "activity": "吃午饭",
                  "category": "meal"}]
        self.assertEqual([], meal_missing_labels(items, 13 * 60))

    def test_meal_words_without_category_count(self):
        items = [{"start": "18:30", "end": "19:30", "activity": "和他一起吃饭",
                  "category": "life"}]
        self.assertEqual(["午饭"], meal_missing_labels(items, 21 * 60))

    def test_lunch_covered_dinner_missing(self):
        items = [{"start": "12:00", "end": "13:00", "activity": "吃午饭",
                  "category": "meal"}]
        self.assertEqual(["晚饭"], meal_missing_labels(items, 21 * 60))


if __name__ == "__main__":
    unittest.main()
