# -*- coding: utf-8 -*-
"""换房子/买家具：旧场景图必须删掉、标记重画；下一个 tick 用新家具重画。"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import home_refs  # noqa: E402
from housing import Housing  # noqa: E402

PNG_1PX = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class FakeDB:
    def __init__(self):
        self.data = {}
        self.path = os.path.join(tempfile.mkdtemp(prefix="amo-hrefs-"), "amo_forever.db")

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()

    def cfg_str(self, key, default=""):
        return default


class FakeOmni:
    def __init__(self):
        self.calls = 0

    async def generate_images_for_plugin(self, prompt="", count=1, record_usage=False,
                                         negative_prompt=None):
        self.calls += 1
        return {"success": True, "images": [{
            "data_url": "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()}]}


def make():
    plugin = FakePlugin()
    housing = Housing(plugin)
    return plugin, housing


class InvalidateTests(unittest.TestCase):
    def _write_room_images(self, housing):
        root = housing.image_dir()
        os.makedirs(root, exist_ok=True)
        home_id = housing.current()["id"]
        paths = {}
        for room in ("living", "bedroom"):
            path = os.path.join(root, "%s-%s.png" % (home_id, room))
            with open(path, "wb") as fh:
                fh.write(PNG_1PX)
            housing.set_room_image(room, path)
            paths[room] = path
        return paths

    def test_add_furniture_deletes_old_room_images(self):
        plugin, housing = make()
        paths = self._write_room_images(housing)
        result = housing.add_furniture("三人位沙发")
        self.assertTrue(result.get("ok"), result)
        # 先画后删：新图没出来前，旧客厅图还留着，不会出现“无参考图”窗口
        self.assertTrue(os.path.isfile(paths["living"]), "旧图要留到新图生成成功")
        self.assertTrue(os.path.isfile(paths["bedroom"]), "卧室图不该动")
        home = housing.current()
        self.assertTrue(home["images_needed"])
        self.assertEqual(home["image_status"], "pending")
        self.assertIn("living", home["rooms_needed"])
        self.assertNotIn("bedroom", home["rooms_needed"])

    def test_move_in_deletes_old_home_images(self):
        plugin, housing = make()
        paths = self._write_room_images(housing)
        new_home = housing.add_home("新家", kind="own", style="原木风", colors="暖白")
        ok, _, _ = housing.move_in(new_home["id"])
        self.assertTrue(ok)
        for path in paths.values():
            self.assertFalse(os.path.isfile(path))
        self.assertTrue(housing.current()["images_needed"])

    def test_style_change_deletes_old_images(self):
        plugin, housing = make()
        paths = self._write_room_images(housing)
        housing.set_style("工业风", "灰白")
        for path in paths.values():
            self.assertTrue(os.path.isfile(path), "装修也要先画后删")
        self.assertTrue(housing.current()["images_needed"])
        self.assertEqual(set(housing.current()["rooms_needed"]),
                         {"living", "bedroom", "bathroom", "kitchen"})


class FurnitureReplaceTests(unittest.TestCase):
    def test_same_type_furniture_replaces_old(self):
        plugin, housing = make()
        housing.add_furniture("灰色布艺沙发")
        self.assertIn("灰色布艺沙发", housing.current()["furniture"])
        housing.add_furniture("红色三人位沙发")
        furniture = housing.current()["furniture"]
        self.assertIn("红色三人位沙发", furniture)
        self.assertNotIn("灰色布艺沙发", furniture, "旧的同类沙发必须从清单里去掉")
        living_items = housing.current()["room_furniture"]["living"]
        self.assertNotIn("灰色布艺沙发", living_items)
        self.assertIn("红色三人位沙发", living_items)

    def test_prompt_uses_new_furniture_only(self):
        plugin, housing = make()
        housing.add_furniture("灰色布艺沙发")
        housing.add_furniture("红色三人位沙发")
        prompt, _neg = housing.room_prompt("客厅")
        self.assertIn("红色布艺沙发", prompt)
        self.assertNotIn("棕色布艺沙发", prompt)


class RegenerateTests(unittest.TestCase):
    def test_ensure_room_images_redraws_when_stale(self):
        plugin, housing = make()
        old = housing.image_dir()
        os.makedirs(old, exist_ok=True)
        stale = os.path.join(old, "%s-living.png" % housing.current()["id"])
        with open(stale, "wb") as fh:
            fh.write(PNG_1PX)
        bedroom = os.path.join(old, "%s-bedroom.png" % housing.current()["id"])
        with open(bedroom, "wb") as fh:
            fh.write(PNG_1PX)
        os.remove(stale)  # 模拟生成前旧图缺失（或刚被替换）
        housing.current()["images_needed"] = True
        housing.current()["rooms_needed"] = ["living"]
        housing.current()["images"] = {"living": "", "bedroom": bedroom}
        omni = FakeOmni()
        original = home_refs.find_omni
        home_refs.find_omni = lambda *args, **kwargs: omni
        try:
            out = asyncio.run(home_refs.ensure_room_images(plugin, housing))
        finally:
            home_refs.find_omni = original
        # 客厅图删了要补，浴室/厨房本来没图也要补；卧室图还在，直接复用
        self.assertEqual(omni.calls, 3, omni.calls)
        self.assertEqual(set(out), {"living", "bedroom", "bathroom", "kitchen"})
        self.assertFalse(housing.current()["images_needed"])
        for path in out.values():
            self.assertTrue(os.path.getsize(path) > 0, path)


class EcommerceFurnitureTests(unittest.TestCase):
    def test_furniture_order_goes_into_housing(self):
        from ecommerce_engine import EcommerceEngine
        plugin, housing = make()
        engine = EcommerceEngine.__new__(EcommerceEngine)
        engine.plugin = plugin
        plugin.housing = housing
        engine._receive({}, {"id": "o1", "items": [
            {"type": "furniture", "name": "三人位沙发"}]})
        self.assertIn("三人位沙发", housing.current()["furniture"])
        self.assertTrue(housing.current()["images_needed"])


if __name__ == "__main__":
    unittest.main()
