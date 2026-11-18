# -*- coding: utf-8 -*-
import os, sqlite3, json
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.environ.get("AMO_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(ROOT)), "data")
PLUGIN_DATA = os.path.join(DATA, "plugin_data")
F = os.path.join(PLUGIN_DATA, "Astrbot_amoupup_forever", "amo_forever.db")
D = os.path.join(PLUGIN_DATA, "Astrbot_amoupup_data", "amo_data.db")
def con(p): return sqlite3.connect("file:%s?mode=ro" % p.replace("\\","/"), uri=True, timeout=3)
out = {}
c = con(F); cur=c.cursor()
def dump(t, n=6, order="rowid desc"):
    cols=[r[1] for r in cur.execute("pragma table_info('%s')"%t)]
    rows=cur.execute("select * from '%s' order by %s limit %d"%(t,order,n)).fetchall()
    return {"cols":cols,"rows":[[str(x)[:160] for x in r] for r in rows]}
for t in ["messages","dialects","block_diary","schedule_diary","proactive_log","schedule","wallet_ledger","kv"]:
    out[t]=dump(t)
c.close()
c=con(D); cur=c.cursor()
for t in ["memories","messages"]:
    out["data_"+t]=dump(t, 5)
c.close()
print(json.dumps(out, ensure_ascii=False, indent=1)[:16000])
