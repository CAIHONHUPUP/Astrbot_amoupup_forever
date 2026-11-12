import os
import re
import sys
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_source  # noqa: E402


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PanelTests(unittest.TestCase):
    def test_panel_has_single_gift_tab_and_containers(self):
        with open(os.path.join(ROOT, "views", "dashboard", "index.html"), encoding="utf-8") as handle:
            html = handle.read()
        self.assertEqual(html.count('data-tab="gift"'), 1)
        for element_id in ("giftWishes", "giftList", "giftSub", "giftRefresh"):
            self.assertEqual(html.count('id="%s"' % element_id), 1, element_id)
        self.assertIn('if (name === "gift") loadGift();', html)
        self.assertIn('call("gift")', html)

    def test_panel_ids_are_unique(self):
        with open(os.path.join(ROOT, "views", "dashboard", "index.html"), encoding="utf-8") as handle:
            html = handle.read()
        ids = re.findall(r'id="([^"]+)"', html)
        duplicates = {item for item in ids if ids.count(item) > 1}
        self.assertEqual(duplicates, set())

    def test_main_registers_gift_api(self):
        source = _plugin_source.all_source(ROOT)
        self.assertEqual(source.count('("/gift", self.api_gift, ["GET"])'), 1)
        self.assertEqual(source.count("async def api_gift"), 1)


if __name__ == "__main__":
    unittest.main()
