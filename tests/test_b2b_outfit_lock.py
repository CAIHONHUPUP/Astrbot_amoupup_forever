# -*- coding: utf-8 -*-
"""B2b：出图穿搭锁根因回归（sidecar 只写实际单品 / action 点名不被居家服覆盖）。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from wardrobe import Wardrobe  # noqa: E402
from wardrobe_data import today_str  # noqa: E402
from photo_plan import resolve_plan  # noqa: E402


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "plugin_data", "Astrbot_Amo_forever", "amo_forever.db")
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, place="家"):
        self.weather = {"temp": 20}
        self.activity = {"place": place, "activity": "在家休息", "category": "life"}

    def current_activity(self):
        return dict(self.activity)


class FakePlugin:
    def __init__(self, root, place="家"):
        self.root = root
        self.db = FakeDB(root)
        self.state = FakeState(place)
        self.plugin_dir = os.path.join(root, "plugin")
        self.housing = None
        self.pose = None


def make_plugin(place="家"):
    root = tempfile.mkdtemp(prefix="amo-b2b-")
    os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
    # legacy fallback：旧插件目录用于回退路径测试
    os.makedirs(os.path.join(root, "plugin_data", "astrbot_plugin_omnidraw"), exist_ok=True)
    plugin = FakePlugin(root, place)
    plugin.wardrobe = Wardrobe(plugin)
    return plugin, root


def sidecar_path(root):
    return os.path.join(root, "plugin_data", "astrbot_plugin_omnidraw", "amo_outfit.json")


def read_sidecar(root):
    with open(sidecar_path(root), encoding="utf-8") as fh:
        return json.load(fh)


def concrete_text(current):
    return "、".join(part for part in (
        current.get("coat"), current.get("top"), current.get("skirt"),
        current.get("bottom"), current.get("shoes")) if part)


class SidecarLockTextTests(unittest.TestCase):
    def test_homewear_sidecar_is_concrete_items(self):
        plugin, root = make_plugin("家")
        current = plugin.wardrobe.current(today_str(), at_home=True, hour=18)
        self.assertTrue(current.get("homewear"))
        data = read_sidecar(root)
        self.assertEqual(data["text"], concrete_text(current))
        self.assertNotIn("居家便服", data["text"])
        self.assertNotIn("在家穿的", data["text"])

    def test_day_sidecar_is_concrete_items(self):
        plugin, root = make_plugin("商场")
        current = plugin.wardrobe.current(today_str(), at_home=False, hour=10)
        self.assertFalse(current.get("homewear"))
        data = read_sidecar(root)
        self.assertEqual(data["text"], concrete_text(current))
        self.assertNotIn("今日穿搭", data["text"])

    def test_sleep_sidecar_is_concrete_pajamas(self):
        plugin, root = make_plugin("家")
        current = plugin.wardrobe.current(today_str(), at_home=True, hour=21)
        self.assertTrue(current.get("sleep"))
        data = read_sidecar(root)
        self.assertEqual(data["text"], concrete_text(current))
        self.assertIn("睡衣", data["text"])

    def test_lock_text_ignores_generic_description(self):
        # 反向：哪怕 result.text 还带泛化标签，锁文本也只用单品。
        text = Wardrobe._outfit_lock_text({
            "text": "在家穿的居家便服：米白T恤、灰色运动短裤",
            "top": "米白T恤", "bottom": "灰色运动短裤", "shoes": "居家棉拖鞋",
        })
        self.assertEqual(text, "米白T恤、灰色运动短裤、居家棉拖鞋")
        self.assertNotIn("居家便服", text)
        self.assertNotIn("在家穿的", text)

    def test_no_concrete_items_no_sidecar(self):
        plugin, root = make_plugin("家")
        plugin.wardrobe._sync_sidecar({"day": today_str(), "text": "在家穿的居家便服"})
        self.assertFalse(os.path.exists(sidecar_path(root)))


class SuppressionTests(unittest.TestCase):
    def test_suppress_keeps_shared_lock_for_default_requests(self):
        plugin, root = make_plugin("家")
        plugin.wardrobe.current(today_str(), at_home=True, hour=18)
        before = read_sidecar(root)["text"]
        self.assertTrue(before)
        with plugin.wardrobe.outfit_lock_suspended("explicit_outfit"):
            # B2c：点名请求只标记自己的上下文，不再清空共享锁。
            self.assertTrue(plugin.wardrobe.outfit_lock_is_suspended())
            self.assertEqual(read_sidecar(root)["text"], before)
            # 并发的默认请求仍然能写回/保留今日穿搭。
            plugin.wardrobe.current(today_str(), at_home=False, hour=10)
            self.assertTrue(read_sidecar(root)["text"])
        self.assertFalse(plugin.wardrobe.outfit_lock_is_suspended())
        self.assertTrue(read_sidecar(root)["text"])

    def test_suppress_does_not_blank_sidecar(self):
        plugin, root = make_plugin("家")
        plugin.wardrobe.current(today_str(), at_home=True, hour=18)
        locked = read_sidecar(root)["text"]
        self.assertTrue(locked)
        with plugin.wardrobe.outfit_lock_suspended("explicit_outfit"):
            self.assertEqual(read_sidecar(root)["text"], locked)
        self.assertTrue(read_sidecar(root)["text"])


class FakePromptEngine:
    """只实现 photo_prompt 用到的接口，避免真调模型。"""

    debug_prompt = False

    def __init__(self, plugin):
        self.plugin = plugin
        self.prompts = []
        self.sidecar_texts = []
        self._outfit_day = ""
        self._outfit_text = ""

    def _cfg_str(self, key, default=""):
        return default

    def _time_light(self):
        return "现在是10点，上午明亮自然光"

    def outfit_today(self):
        return self.plugin.wardrobe.current(today_str()).get("text") or ""

    async def generate_custom(self, text):
        self.prompts.append(text)
        self.sidecar_texts.append(read_sidecar(self.plugin.root).get("text", ""))
        return None


def _engine(plugin):
    from photo_prompt import PhotoPromptMixin

    class Engine(PhotoPromptMixin, FakePromptEngine):
        pass

    return Engine(plugin)


class PhotoPromptTests(unittest.TestCase):
    def test_homewear_selfie_prompt_has_no_second_generic_clause(self):
        plugin, _ = make_plugin("家")
        worn = plugin.wardrobe.current(today_str(), at_home=True, hour=18)
        self.assertTrue(worn.get("homewear"))
        plugin.wardrobe.current = lambda *args, **kwargs: dict(worn)
        engine = _engine(plugin)
        plan = resolve_plan({"place": "家", "activity": "在餐桌边整理东西", "category": "life"},
                            mode="photo")
        # 指定非床/沙发场景，才走 homewear 分支（bed 分支会盖掉它）。
        prompt = engine._selfie_prompt_for("daily", "家里的餐桌旁", plan.activity, plan=plan)
        for key in ("top", "bottom"):
            if worn.get(key):
                self.assertIn(worn[key], prompt)
        self.assertNotIn("穿的就是这一套居家服", prompt)
        self.assertNotIn("家居服", prompt)
        self.assertIn("姿态松弛", prompt)

    def test_explicit_outfit_action_keeps_named_clothes_and_shared_lock(self):
        plugin, root = make_plugin("家")
        plugin.wardrobe.current(today_str(), at_home=True, hour=18)
        self.assertTrue(read_sidecar(root)["text"])
        engine = _engine(plugin)
        asyncio.run(engine.generate_prompt("穿宽大卫衣拍一张"))
        # B2c：本次请求按他的名字出图；共享锁留给没点名的并发请求。
        self.assertTrue(engine.sidecar_texts[0])
        self.assertIn("宽大卫衣", engine.prompts[0])
        self.assertNotIn("居家便服", engine.prompts[0])

    def test_default_action_keeps_today_lock(self):
        plugin, root = make_plugin("家")
        plugin.wardrobe.current(today_str(), at_home=True, hour=18)
        engine = _engine(plugin)
        asyncio.run(engine.generate_prompt("拍张照"))
        self.assertTrue(engine.sidecar_texts)
        self.assertTrue(engine.sidecar_texts[0])
        self.assertNotIn("居家便服", engine.sidecar_texts[0])


if __name__ == "__main__":
    unittest.main()
