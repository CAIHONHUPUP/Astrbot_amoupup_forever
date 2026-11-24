# -*- coding: utf-8 -*-
"""事实单一源契约（2026-11-24 第 2 刀）。

这张表就是“同一件事只允许一个来源”的清单。测试只盯两件事：
1. 表里的 owner 必须存在；
2. 表里的读者/写者必须真的路由到 owner，不能自己再写一套。
新增事实时先改这张表，再改代码。
"""
from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FACTS = {
    "place_zone": {
        "owner_file": "domains/schedule/place.py",
        "owner_symbols": ("resolve_zone", "repair_zone_rows"),
        "route_tokens": ("resolve_zone", "zone_of"),
        "readers": (
            "amo_current_action.py", "schedule_engine_v2.py", "schedule_content.py",
            "amo_food_cost.py", "amo_scene_ledger.py", "amo_household.py",
            "amo_diary_guard.py", "photo_scene.py", "photo_plan.py",
        ),
        "writers": ("amo_state_schedule.py", "amo_transition.py", "schedule_slots.py"),
    },
    "meal": {
        "owner_file": "amo_food_delivery.py",
        "owner_symbols": ("pending_order", "consume_arrival", "today_facts"),
        "route_tokens": ("pending_order", "pending_state", "consume_arrival", "today_facts", "meal_facts"),
        "readers": ("amo_runtime_context.py", "amo_runtime_context_agenda.py",
                    "amo_fact_binder.py", "amo_diary_guard.py"),
        "writers": ("amo_food_delivery.py",),
    },
    "fallback": {
        "owner_file": "domains/reply/repetition_guard.py",
        "owner_symbols": ("contextual_fallback",),
        "route_tokens": ("contextual_fallback",),
        "readers": ("segment_engine.py", "amo_persona_guard.py"),
        "writers": (),
    },
    "action_narration": {
        "owner_file": "amo_persona_guard.py",
        "owner_symbols": ("strip_action_narration",),
        "route_tokens": ("strip_action_narration", "prepare_outbound", "clean_voice_text"),
        "readers": ("segment_engine.py", "amo_voice.py", "amo_tts_guard.py"),
        "writers": (),
    },
    # 2026-11-24 P4：场景（她在哪/在做什么）与居住（他能不能在场）各自只有一个 owner。
    "scene": {
        "owner_file": "amo_scene_ledger.py",
        "owner_symbols": ("scene_at", "write", "constraints"),
        "route_tokens": ("scene_ledger", "scene_at"),
        "readers": ("amo_household.py", "amo_presence.py", "amo_state_consistency.py",
                    "amo_diary_guard.py", "amo_fact_timeline.py"),
        "writers": ("amo_current_action.py", "amo_state_schedule.py", "micro_events.py",
                    "amo_transition.py", "amo_feature_api.py"),
    },
    "residence": {
        "owner_file": "amo_household.py",
        "owner_symbols": ("scene_at", "scene_now", "wording"),
        "route_tokens": ("amo_household", "wording("),
        "readers": ("amo_llm.py", "amo_runtime_context_extra.py", "amo_schedule.py",
                    "schedule_diary.py", "amo_date_context.py", "draw_engine.py"),
        "writers": ("amo_commands.py",),
    },
}


def _source(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def _production_sources():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in
                   ("tests", "work", "__pycache__", ".git", ".mypy_cache", ".pytest_cache")]
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(base, name)
                yield os.path.relpath(path, ROOT).replace(os.sep, "/"), open(
                    path, encoding="utf-8").read()


class FactContractTests(unittest.TestCase):
    def test_owner_symbols_exist(self):
        for fact, spec in FACTS.items():
            source = _source(spec["owner_file"])
            for symbol in spec["owner_symbols"]:
                self.assertIn("def %s" % symbol, source, "%s owner %s" % (fact, symbol))

    def test_readers_route_to_owner(self):
        for fact, spec in FACTS.items():
            for name in spec["readers"]:
                source = _source(name)
                self.assertTrue(
                    any(token in source for token in spec["route_tokens"]),
                    "%s 没有路由到 owner: %s" % (name, fact))

    def test_writers_route_to_owner(self):
        for fact, spec in FACTS.items():
            for name in spec["writers"]:
                source = _source(name)
                self.assertTrue(
                    any(token in source for token in spec["route_tokens"]),
                    "%s 没有路由到 owner: %s" % (name, fact))

    def test_food_delivery_never_writes_schedule_block(self):
        source = _source("amo_food_delivery.py")
        self.assertNotIn("ensure_block_window", source)
        self.assertNotIn("ensure_food_block", source)

    def test_addressee_guard_is_gone(self):
        hits = []
        for rel, source in _production_sources():
            if "amo_addressee_guard" in source:
                hits.append(rel)
        self.assertEqual([], hits)

    def test_health_check_covers_owner_modules(self):
        import amo_guard_health as health
        for fact, spec in FACTS.items():
            module = spec["owner_file"][:-3].replace("/", ".")
            self.assertIn(module, health.CRITICAL_GUARDS, "%s owner 未纳入健康检查" % fact)


    def test_photo_engine_does_not_import_legacy_scene_tables(self):
        import ast
        tree = ast.parse(_source("photo_engine.py"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("photo_data"):
                imported.update(alias.name for alias in node.names)
        forbidden = {"SCENE_POLICY", "SCENE_ALLOWED", "OUTDOOR_POSES",
                     "scene_allowed_line", "outdoor_pose_line"}
        self.assertEqual(set(), imported & forbidden)


if __name__ == "__main__":
    unittest.main()
