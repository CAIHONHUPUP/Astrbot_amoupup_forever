# -*- coding: utf-8 -*-
"""三视图参考图（A）：上传一张图，生成三视图时按它来，不再「凭人设描述盲生成」。

覆盖：data URL / 本地文件落盘、清理、persona_sheet 优先用参考图、接口 0/1。
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_routes  # noqa: E402
from amo_api import ApiMixin  # noqa: E402
from photo_generate import PhotoGenerateMixin  # noqa: E402

PNG_1PX = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class FakeEngine(PhotoGenerateMixin):
    """只装模板目录/落盘/生成桩，不连生图插件。"""

    def __init__(self, root):
        self._root = root
        self.calls = []

    def template_dir(self):
        path = os.path.join(self._root, "amo_templates")
        os.makedirs(path, exist_ok=True)
        return path

    def preview_data_url(self, item, size=512):
        return "data:image/png;base64,stub" if item.get("file_path") else ""

    def persona_refs(self):
        return ["old_front.png"]

    async def adopt_persona(self, url, view="front"):
        self.calls.append({"adopt": view, "path": url, "exists": os.path.isfile(url)})
        return "✅ 已保存%s模板" % view

    async def generate_custom(self, prompt, refs=None):
        self.calls.append({"prompt": prompt, "refs": list(refs or [])})
        return {"file_path": "x.png"}


class SheetRefTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.engine = FakeEngine(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_data_url_is_saved_and_cleared(self):
        raw = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()
        path = self.engine.set_sheet_ref(raw)
        self.assertTrue(path and os.path.isfile(path), path)
        self.assertEqual(self.engine.sheet_ref_path(), path)
        self.engine.clear_sheet_ref()
        self.assertEqual(self.engine.sheet_ref_path(), "")

    def test_local_file_is_accepted(self):
        src = os.path.join(self.tmp, "ref.png")
        with open(src, "wb") as fh:
            fh.write(PNG_1PX)
        path = self.engine.set_sheet_ref(src)
        self.assertTrue(path and os.path.isfile(path))
        self.assertEqual(os.path.basename(path), "amo_sheet_ref.png")

    def test_bad_payload_is_rejected(self):
        self.assertEqual(self.engine.set_sheet_ref("data:image/png;base64,!!!!"), "")
        self.assertEqual(self.engine.set_sheet_ref(""), "")

    def test_persona_sheet_prefers_sheet_ref(self):
        raw = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()
        self.engine.set_sheet_ref(raw)
        asyncio.run(self.engine.persona_sheet("front"))
        self.assertEqual(self.engine.calls[-1]["refs"], [self.engine.sheet_ref_path()])

    def test_persona_sheet_falls_back_to_templates(self):
        asyncio.run(self.engine.persona_sheet("front"))
        self.assertEqual(self.engine.calls[-1]["refs"], ["old_front.png"])

    def test_api_round_trip(self):
        raw = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()

        class Fake:
            photo = self.engine
            payload = {"data": raw}

            async def _ensure_ready(self):
                return None

            async def _body(self):
                return self.payload

        fake = Fake()
        saved = asyncio.run(ApiMixin.api_photo_sheet_ref_save(fake))
        self.assertEqual(saved["code"], 0, saved)
        self.assertTrue(saved["data"]["has_ref"])

        status = asyncio.run(ApiMixin.api_photo_sheet_ref(fake))
        self.assertEqual(status["code"], 0)
        self.assertTrue(status["data"]["has_ref"])

        fake.payload = {"clear": True}
        cleared = asyncio.run(ApiMixin.api_photo_sheet_ref_save(fake))
        self.assertEqual(cleared["code"], 0)
        self.assertFalse(engine_has_ref(self.engine))

    def test_import_persona_saves_and_enables(self):
        raw = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()
        message = asyncio.run(self.engine.import_persona("side", raw))
        self.assertTrue(message.startswith("✅"), message)
        call = self.engine.calls[-1]
        self.assertEqual(call["adopt"], "side")
        self.assertTrue(call["exists"])
        self.assertFalse(os.path.exists(call["path"]), "临时文件要清掉")

    def test_import_persona_rejects_bad_payload(self):
        self.assertFalse(asyncio.run(self.engine.import_persona("front", "data:image/png;base64,!!"))
                         .startswith("✅"))
        self.assertFalse(asyncio.run(self.engine.import_persona("front", "")).startswith("✅"))

    def test_import_api_round_trip(self):
        raw = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()

        class Fake:
            photo = self.engine
            payload = {"view": "full", "data": raw}

            async def _ensure_ready(self):
                return None

            async def _body(self):
                return self.payload

        fake = Fake()
        res = asyncio.run(ApiMixin.api_photo_persona_import(fake))
        self.assertEqual(res["code"], 0, res)
        self.assertEqual(self.engine.calls[-1]["adopt"], "full")
        fake.payload = {"view": "full", "data": ""}
        self.assertEqual(asyncio.run(ApiMixin.api_photo_persona_import(fake))["code"], 1)

    def test_route_is_write_only(self):
        self.assertIn("/photo/sheet_ref/save", amo_routes.WRITE_ROUTES)


def engine_has_ref(engine):
    return bool(engine.sheet_ref_path())


if __name__ == "__main__":
    unittest.main()
