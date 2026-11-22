import sqlite3, json
p = r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever\amo_forever.db"
con = sqlite3.connect("file:" + p.replace("\\","/") + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row
print("--- social:accept debits ---")
for r in con.execute("select idem,account,delta_cents,reason,ref,ts from wallet_ledger where idem like 'social:accept:%' or ref like 'social:accept:%' order by ts"):
    d=dict(r); d.pop('reason',None); print(json.dumps(d,ensure_ascii=False))
print("--- all refund rows count by ref match ---")
for r in con.execute("select idem,delta_cents,ref from wallet_ledger where delta_cents>0 and idem like 'refund:%'"):
    d=dict(r); matched=con.execute("select count(*) from wallet_ledger where idem=? and delta_cents<0",(d['ref'],)).fetchone()[0]
    print(json.dumps({"idem":d["idem"],"ref":d["ref"],"matched_debits":matched}))
