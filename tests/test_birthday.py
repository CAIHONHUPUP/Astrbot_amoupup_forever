# -*- coding: utf-8 -*-
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class BirthdayTests(unittest.TestCase):
    def test_schema_has_toggle(self):
        with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as fh:
            schema = json.load(fh)
        meta = schema.get("birthday_greet_enabled")
        self.assertTrue(meta)
        self.assertEqual(meta.get("type"), "bool")
        self.assertTrue(meta.get("default"))

    def test_ritual_reads_toggle_and_sends_birthday(self):
        with open(os.path.join(ROOT, "amo_proactive.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("birthday_greet_enabled", source)
        self.assertIn('gen_proactive(targets[0], profile, snap, "birthday")', source)


if __name__ == "__main__":
    unittest.main()
