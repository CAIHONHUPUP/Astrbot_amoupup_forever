# -*- coding: utf-8 -*-
import sys, os, json
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
os.chdir(ROOT); sys.path.insert(0, ROOT)
import amo_purchase_guard as PG
cases = [
 "老公，下午茶又发奶茶了 也不知道谁定的，我上午自己还买了一杯，一天两杯这谁顶得住啊",
 "我回来啦！今天买了米白色针织开衫，花了649元，穿上真的超好看！",
 "（拎着购物袋，往电梯口走，笑着摇摇头） 行，那我不给你买了。柳如烟还在里面试，我等等她。",
]
out = []
for c in cases:
    m = PG._CLAIM.search(c)
    out.append({"text": c, "claim": m.group(0) if m else None})
open(os.path.join(HERE, "task40_direct.json"), "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
print("ok")
