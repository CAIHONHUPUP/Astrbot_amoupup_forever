# -*- coding: utf-8 -*-
"""面板「照片」页的静态检查：标签、容器、加载与测试按钮都接上了。"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PhotoPanelTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "views", "dashboard", "index.html"), encoding="utf-8") as handle:
            self.html = handle.read()

    def test_tab_and_panel(self):
        self.assertEqual(self.html.count('data-tab="photo"'), 1)
        for element_id in ("tab-photo", "photoSettings", "photoSave", "photoTest", "photoTestKind"):
            self.assertEqual(self.html.count('id="%s"' % element_id), 1, element_id)
        self.assertIn('if (name === "photo") loadPhoto();', self.html)
        self.assertIn('call("photo/test"', self.html)
        self.assertIn('renderPhoto', self.html)

    def test_photo_function_submenu(self):
        self.assertEqual(self.html.count('id="photoNav"'), 1)
        for pane in ("share", "generate", "persona", "advanced"):
            self.assertIn('data-photo-pane="%s"' % pane, self.html)
        self.assertEqual(self.html.count('data-photo-pane="persona"'), 3)  # nav + 2 cards
        self.assertIn("function showPhotoPane", self.html)
        self.assertIn('showPhotoPane("share")', self.html)

    def test_skyblue_theme(self):
        self.assertIn('id="amo-skyblue-theme"', self.html)
        self.assertIn("--bg1: #eaf8ff", self.html)
        self.assertIn("background-attachment: fixed", self.html)
        self.assertGreaterEqual(self.html.count('class="tab-ico"'), 16)
        self.assertIn('data-group="语音"', self.html)
        self.assertIn('data-group="心流"', self.html)
        self.assertLess(self.html.find('data-group="语音"'), self.html.find('data-group="心流"'))
        self.assertIn("function settingsCategory", self.html)
        self.assertIn('className = "settings-group"', self.html)
        self.assertIn('className = "settings-count"', self.html)
        self.assertIn(".settings-count", self.html)
        self.assertIn("font-size: 13px", self.html)

    def test_world_document_compact_layout(self):
        self.assertIn('id="worldExpandAll"', self.html)
        self.assertIn("world-hero", self.html)
        self.assertIn("world-facts", self.html)
        self.assertIn("worldSection", self.html)
        self.assertIn("details.world-section", self.html)
        self.assertIn("world-section-title", self.html)
        self.assertIn("word-break: keep-all", self.html)
        self.assertIn('[["简介", summaryText], ["日常", daily], ["世界", rules]]', self.html)
        self.assertIn("flex: 0 0 3em", self.html)

    def test_no_duplicate_ids(self):
        ids = re.findall(r'id="([^"]+)"', self.html)
        duplicates = {item for item in ids if ids.count(item) > 1}
        self.assertEqual(duplicates, set())


if __name__ == "__main__":
    unittest.main()
