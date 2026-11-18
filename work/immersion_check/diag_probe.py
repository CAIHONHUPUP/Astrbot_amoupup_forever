# -*- coding: utf-8 -*-
import sys, os, json, sqlite3
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, os.path.join(ROOT, "tests")):
    sys.path.insert(0, p)
import astrbot_stub; astrbot_stub.install()
import amo_names
for text in ["问了，我就笑笑不说话", "可以啊，好好吃。吃完跟我说说吃了啥。", "她挺好的，还是老样子"]:
    print("foreign:", repr(text), "->", amo_names.foreign_names(text))
print("has sanitize:", hasattr(amo_names, "sanitize"), "| scrub_names:", hasattr(amo_names, "scrub_names"))
if hasattr(amo_names, "scrub_names"):
    for text in ["问了，我就笑笑不说话", "可以啊，好好吃。吃完跟我说说吃了啥。"]:
        print("scrub:", repr(text), "->", repr(amo_names.scrub_names(text)))

# 破坏性改写样本
d = json.load(open(os.path.join(HERE, "imm_results.json"), encoding="utf-8"))
seen = 0
for run in d["runs"]["off"]:
    for idx, t in enumerate(run["turns"]):
        if "在家上" in t["sent"] or "家快到了" in t["sent"] or "家窝着" in t["sent"] or "家这边" in t["sent"]:
            print("BROKEN", run["id"], run["category"], idx, "|", t["sent"][:60],
                  "| cand:", t["candidate"][:40])
            seen += 1
            if seen >= 6:
                break
    if seen >= 6:
        break

# api key
DATA = os.environ.get("AMO_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(ROOT)), "data")
db = sqlite3.connect("file:%s?mode=ro" % os.path.join(DATA, "data_v4.db").replace("\\","/"), uri=True)
cols = [r[1] for r in db.execute("pragma table_info('api_keys')")]
rows = db.execute("select * from api_keys").fetchall()
print("API_COLS", cols)
for row in rows:
    row = dict(zip(cols, row))
    print("API", {k: (str(v)[:22] + ("..." if v and len(str(v)) > 22 else "")) for k, v in row.items() if k not in ("api_key", "key")}, "has_key=", bool(row.get("key") or row.get("api_key")))
