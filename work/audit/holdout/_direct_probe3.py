# -*- coding: utf-8 -*-
import sys, os, json, re
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
os.chdir(ROOT); sys.path.insert(0, ROOT)
import amo_purchase_guard as PG
s = "老公，下午茶又发奶茶了 也不知道谁定的，我上午自己还买了一杯，一天两杯这谁顶得住啊"
res = {"len": len(s), "chars": [c for c in s], "find_买了": s.find("买了"), "find_买": [i for i,c in enumerate(s) if c=="买"], "search_span": None}
m = PG._CLAIM.search(s)
res["search_span"] = [m.start(), m.end(), m.group(0)] if m else None
print(json.dumps(res, ensure_ascii=False))
