# -*- coding: utf-8 -*-
import sqlite3, os, json, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.environ.get("AMO_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(ROOT)), "data")
PLUGIN_DATA = os.path.join(DATA, "plugin_data")
DBS = {
 "forever": os.path.join(PLUGIN_DATA, "Astrbot_amoupup_forever", "amo_forever.db"),
 "data": os.path.join(PLUGIN_DATA, "Astrbot_amoupup_data", "amo_data.db"),
 "astrbot": os.path.join(DATA, "data_v4.db"),
}
out = {}
for name, path in DBS.items():
    info = {"path": path, "exists": os.path.exists(path)}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\","/"), uri=True, timeout=3)
        cur = con.cursor()
        tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table' order by name")]
        info["tables"] = tables[:60]
        info["n_tables"] = len(tables)
        counts = {}
        for t in tables[:60]:
            try:
                counts[t] = cur.execute("select count(*) from '%s'" % t).fetchone()[0]
            except Exception as exc:
                counts[t] = "ERR:%s" % exc
        info["counts"] = counts
        con.close()
    except Exception as exc:
        info["error"] = repr(exc)
    out[name] = info
print(json.dumps(out, ensure_ascii=False, indent=1)[:12000])
