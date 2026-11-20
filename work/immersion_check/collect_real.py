# -*- coding: utf-8 -*-
"""把真实数据库/日记/记忆抽成 real_seed.json（只读，不写 plugin_data）。"""
import json, os, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.environ.get("AMO_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(ROOT)), "data")
PLUGIN_DATA = os.path.join(DATA, "plugin_data")
F = os.path.join(PLUGIN_DATA, "Astrbot_amoupup_forever", "amo_forever.db")
D = os.path.join(PLUGIN_DATA, "Astrbot_amoupup_data", "amo_data.db")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "real_seed.json")


def con(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=5)


def rows(cur, sql, args=()):
    return cur.execute(sql, args).fetchall()


def main():
    c = con(F)
    cur = c.cursor()
    msgs = [{"id": r[0], "umo": r[1], "role": r[4], "content": r[5], "ts": float(r[6])}
            for r in rows(cur, "select id,umo,uid,name,role,content,created_at from messages order by created_at")]
    schedule = {}
    for day, items in rows(cur, "select day, items from schedule"):
        try:
            schedule[day] = json.loads(items)
        except Exception:
            schedule[day] = []
    ledger = [{"ts": float(r[5] or 0), "reason": r[3], "item_name": r[8], "category": r[6]}
              for r in rows(cur, "select idem,account,delta_cents,reason,ref,ts,category,sub_category,item_name from wallet_ledger")]
    proactive = [{"ts": float(r[3] or 0), "text": r[2]} for r in rows(cur, "select id,uid,content,created from proactive_log")]
    kv = {}
    for k, v in rows(cur, "select key, value from kv"):
        if str(k).startswith(("diary_live:", "diary_together:", "diary_mood:", "daily_digest:", "day_type_")):
            kv[str(k)] = str(v)[:600]
    c.close()

    c = con(D)
    cur = c.cursor()
    cols = [r[1] for r in cur.execute("pragma table_info('memories')")]
    mem = []
    for r in cur.execute("select * from memories order by rowid"):
        row = dict(zip(cols, r))
        mem.append({k: (str(row.get(k))[:300] if row.get(k) is not None else "") for k in cols})
    c.close()

    diaries = {}
    base = os.path.join(PLUGIN_DATA, "Astrbot_amoupup_forever", "diary", "2026", "11")
    for day in ("11", "12", "13", "14", "15", "16"):
        path = os.path.join(base, day, "我的日记2026年11月%s日.txt" % day)
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                diaries["2026-11-%s" % day] = fh.read()[:4000]

    data = {"messages": msgs, "schedule": schedule, "ledger": ledger,
            "proactive": proactive, "kv": kv, "memories": mem, "diaries": diaries}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print("messages", len(msgs), "user", sum(1 for m in msgs if m["role"] == "user"),
          "bot", sum(1 for m in msgs if m["role"] == "bot"))
    print("schedule_days", len(schedule), "ledger", len(ledger), "proactive", len(proactive),
          "memories", len(mem), "diaries", len(diaries))
    print("UMOS", sorted({m["umo"] for m in msgs}))
    print("USER_SAMPLES")
    for m in [x for x in msgs if x["role"] == "user"][:25]:
        print("  ", m["ts"], repr(m["content"])[:90])
    print("MEM_COLS", cols)
    for m in mem[:15]:
        print("  MEM", repr(m.get("content"))[:120], "| tags", m.get("tags"))


if __name__ == "__main__":
    main()
