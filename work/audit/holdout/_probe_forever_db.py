# -*- coding: utf-8 -*-
import sqlite3, json
p = r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever\amo_forever.db"
con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
cur = con.cursor()
keys = [r[0] for r in cur.execute("select key from kv order by key")]
print(len(keys))
print(keys[:80])
for k in ("health", "trip", "date_session", "mood"):
    row = cur.execute("select value from kv where key=?", (k,)).fetchone()
    print(k, (row[0][:300] if row else None))
con.close()
