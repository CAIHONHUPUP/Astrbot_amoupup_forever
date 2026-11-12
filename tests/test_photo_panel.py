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

    def test_no_duplicate_ids(self):
        ids = re.findall(r'id="([^"]+)"', self.html)
        duplicates = {item for item in ids if ids.count(item) > 1}
        self.assertEqual(duplicates, set())


if __name__ == "__main__":
    unittest.main()
