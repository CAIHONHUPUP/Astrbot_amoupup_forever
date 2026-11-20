# -*- coding: utf-8 -*-
"""画像关系要同步到关系网（相恋 → 恋人），关系图才不会一直停在「熟人」。"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RelationSyncTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "amo_api.py"), encoding="utf-8") as handle:
            self.source = handle.read()

    def test_profile_relation_mapping(self):
        for profile, network in (
            ("仇恨", "仇人"), ("抵触", "陌生人"), ("普通", "朋友"),
            ("熟识", "熟人"), ("相恋", "恋人"),
        ):
            self.assertIn('"%s": "%s"' % (profile, network), self.source)

    def test_profile_save_syncs_edge(self):
        self.assertIn('upsert_relation("bot", uid, mapped, "画像：关系字段")', self.source)

    def test_relations_overlay_profile(self):
        self.assertIn("profile_edges", self.source)
        self.assertIn('row["relation"] = profile_edges[other]', self.source)


if __name__ == "__main__":
    unittest.main()
