# -*- coding: utf-8 -*-
import asyncio
import base64
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import outfit_refs  # noqa: E402

PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


from outfit_refs import _as_png  # noqa: E402


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "x.db")


class FakePlugin:
    def __init__(self, root):
        self.db = FakeDB(root)
        self.plugin_dir = root


class OutfitRefTests(unittest.TestCase):
    def test_ref_path_uses_safe_name(self):
        root = tempfile.mkdtemp(prefix="amo-ref-")
        plugin = FakePlugin(root)
        path = outfit_refs.item_ref_path(
            plugin, {"name": "米色/雪纺衬衫", "category": "top"})
        self.assertTrue(path.endswith(os.path.join(
            "outfit_refs", "白天搭配", "上衣（外套）", "米色_雪纺衬衫.png")))

    def test_ensure_ref_saves_and_reuses(self):
        root = tempfile.mkdtemp(prefix="amo-ref-")
        plugin = FakePlugin(root)

        class FakeOmni:
            calls = 0
            async def generate_images_for_plugin(self, **kwargs):
                FakeOmni.calls += 1
                return {"images": [{"data_url": "data:image/png;base64," + PNG_1PX}]}

        original = outfit_refs.find_omni
        outfit_refs.find_omni = lambda *args, **kwargs: FakeOmni()
        try:
            path = asyncio.run(outfit_refs.ensure_ref(plugin, "米色雪纺衬衫"))
            self.assertTrue(path and os.path.isfile(path))
            again = asyncio.run(outfit_refs.ensure_ref(plugin, "米色雪纺衬衫"))
            self.assertEqual(path, again)
            self.assertEqual(FakeOmni.calls, 1)
        finally:
            outfit_refs.find_omni = original


    def test_pending_skips_existing(self):
        root = tempfile.mkdtemp(prefix="amo-ref-")
        plugin = FakePlugin(root)
        item = {"name": "米色雪纺衬衫", "category": "top"}
        path = outfit_refs.item_ref_path(plugin, item)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"png")
        self.assertEqual(outfit_refs.pending_items(plugin, [item]), [])

    def test_pending_keeps_missing(self):
        root = tempfile.mkdtemp(prefix="amo-ref-")
        plugin = FakePlugin(root)
        item = {"name": "没有生成过的外套", "category": "coat"}
        self.assertEqual(len(outfit_refs.pending_items(plugin, [item])), 1)

class PngConvertTests(unittest.TestCase):
    def test_jpeg_converted_to_png(self):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (2, 2), (255, 0, 0)).save(buf, "JPEG")
        data = _as_png(buf.getvalue())
        self.assertTrue(data.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
