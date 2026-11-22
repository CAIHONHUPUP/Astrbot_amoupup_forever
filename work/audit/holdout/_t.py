# -*- coding: utf-8 -*-
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE))); os.chdir(ROOT); sys.path.insert(0, ROOT)
import amo_rewrite_safety as S
cand = S._comparable("（把手机往桌上一放，叹了口气）还是不行，这回真是出不来图了。不是我不给看，是它死活不愿意。")
cur = S._comparable("（把手机往桌上一放，叹了口气）还是不行，这回真是出不来图了。是它死活不愿意。")
print(repr(cand), repr(cur))
print("norm equal", S._norm(cand) == S._norm(cur))
print("units", S._clauses(cand))
print("sentences", S._sentences(cand))
print("unit_dropped", S._unit_dropped(cand, cur))
for u in list(S._clauses(cand)) + list(S._sentences(cand)):
    rest = S._remove_unit(cand, u)
    print("  remove", repr(u), "->", repr(rest), "eq", S._norm(rest) == S._norm(cur))
