import sqlite3, json
p = r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever\amo_forever.db"
con = sqlite3.connect("file:" + p.replace("\\","/") + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row
print("--- account ---")
for r in con.execute("select * from wallet_account"): print(dict(r))
print("--- credits ---")
for r in con.execute("select idem,account,delta_cents,reason,ref,event_id,ts,category,sub_category from wallet_ledger where delta_cents>0 order by ts"):
    d=dict(r); print(json.dumps(d,ensure_ascii=False))
print("--- cash sum ---", con.execute("select sum(delta_cents) from wallet_ledger where account='cash'").fetchone()[0])
print("--- withdrawable sum ---", con.execute("select sum(delta_cents) from wallet_ledger where account='withdrawable'").fetchone()[0])
