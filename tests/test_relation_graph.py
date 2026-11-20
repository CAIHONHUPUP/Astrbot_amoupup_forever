# -*- coding: utf-8 -*-
"""关系扩散的单测：近的权重高、远的衰减、不重复。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from relation_graph import rank_edges, spread_scores  # noqa: E402


def edge(a, b, relation="朋友"):
    return {"a_uid": a, "b_uid": b, "relation": relation}


CHAIN = [edge("A", "B"), edge("B", "C"), edge("C", "D")]


class SpreadTests(unittest.TestCase):
    def test_decay_by_hop(self):
        scores = spread_scores(CHAIN, "A")
        self.assertEqual(scores[("A", "B")], 1.0)
        self.assertEqual(scores[("B", "C")], 0.45)
        self.assertEqual(scores[("C", "D")], 0.2)

    def test_max_hops(self):
        scores = spread_scores(CHAIN, "A", max_hops=2)
        self.assertIn(("B", "C"), scores)
        self.assertNotIn(("C", "D"), scores)

    def test_unknown_user(self):
        self.assertEqual(spread_scores(CHAIN, ""), {})
        self.assertEqual(spread_scores(CHAIN, "Z"), {})

    def test_isolated_edges_ignored(self):
        edges = CHAIN + [edge("X", "Y")]
        scores = spread_scores(edges, "A")
        self.assertNotIn(("X", "Y"), scores)


class RankTests(unittest.TestCase):
    def test_order_and_limit(self):
        picked = rank_edges(CHAIN, "A", limit=2)
        self.assertEqual([(e["a_uid"], e["b_uid"]) for e in picked],
                         [("A", "B"), ("B", "C")])

    def test_no_duplicates(self):
        edges = [edge("A", "B"), edge("B", "A")]
        picked = rank_edges(edges, "A", limit=5)
        self.assertEqual(len(picked), 1)


if __name__ == "__main__":
    unittest.main()
