# -*- coding: utf-8 -*-
import os, sqlite3, json
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.environ.get("AMO_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(ROOT)), "data")
F = os.path.join(DATA, "plugin_data", "Astrbot_amoupup_forever", "amo_forever.db")
c = sqlite3.connect("file:%s?mode=ro" % F.replace("\\","/"), uri=True)
cur = c.cursor()
rows = cur.execute("select key, substr(value,1,90), updated from kv order by key").fetchall()
print("N", len(rows))
for k, v, u in rows:
    print("%-42s | %s" % (str(k)[:42], str(v).replace("\n"," ")[:90]))
