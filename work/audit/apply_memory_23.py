# -*- coding: utf-8 -*-
"""只归档 23 条纯运维记忆（dirdata_plan 分层结果）。写前热备 + 内容指纹校验 + 单事务。"""
import hashlib, json, os, sqlite3, time

PLAN = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever\work\audit\dirdata_plan.json"
DB = r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_data\amo_data.db"
BACKUP_ROOT = r"D:\dafeiyu\backup\migrations"
EVIDENCE = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever\work\audit\dirdata_memory_apply.json"

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def ro(path):
    return sqlite3.connect("file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro", uri=True)

plan = json.load(open(PLAN, encoding="utf-8"))
apply_rows = plan["category_2_memory_backend_talk"]["apply"]
ids = [int(x) for x in apply_rows["ids"]]
expected = {int(row["id"]): str(row.get("content") or "") for row in apply_rows.get("tier1_rows") or []}
if not expected:
    raise SystemExit("plan 缺少 tier1_rows 内容指纹，拒绝 apply")

conn = ro(DB)
before = dict(conn.execute("SELECT COALESCE(status,'active'), COUNT(*) FROM memories GROUP BY 1"))
mismatch, missing = [], []
for mid in ids:
    row = conn.execute("SELECT content, COALESCE(status,'active') FROM memories WHERE id=?", (mid,)).fetchone()
    if row is None:
        missing.append(mid)
        continue
    if mid in expected and str(row[0] or "") != expected[mid]:
        mismatch.append(mid)
    if str(row[1]) != "active":
        missing.append(mid)
conn.close()
if missing or mismatch:
    raise SystemExit("拒绝 apply：missing/non-active=%s mismatch=%s" % (missing, mismatch))

stamp = time.strftime("%Y%m%d-%H%M%S")
backup = os.path.join(BACKUP_ROOT, "data-pre-memory23-%s.db" % stamp)
src = ro(DB)
dest = sqlite3.connect(backup)
try:
    src.backup(dest)
    check = dest.execute("PRAGMA quick_check").fetchone()
finally:
    dest.close()
    src.close()
integrity = str((check[0] if check else "") or "")
if integrity.lower() != "ok":
    os.remove(backup)
    raise SystemExit("备份完整性失败：%s" % integrity)
manifest = {
    "schema": 1, "tool": "apply_memory_23", "source": os.path.abspath(DB),
    "backup": backup, "name": os.path.basename(backup),
    "sha256": sha256_file(backup), "bytes": os.path.getsize(backup),
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "reason": "archive 23 pure-ops memories (dirdata plan)", "integrity": integrity,
    "user_version": 3, "from_version": 3, "to_version": 3,
}
with open(backup + ".manifest.json", "w", encoding="utf-8", newline="") as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
    fh.write("\n")

conn = sqlite3.connect(DB, timeout=20, isolation_level=None)
changed = 0
try:
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("BEGIN IMMEDIATE")
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        "UPDATE memories SET status='archived' WHERE id IN (%s) AND COALESCE(status,'active')='active'" % placeholders,
        ids)
    changed = int(cur.rowcount or 0)
    if changed != len(ids):
        conn.rollback()
        raise SystemExit("改行数不符：%d != %d，已回滚" % (changed, len(ids)))
    conn.commit()
except Exception:
    try:
        conn.rollback()
    except Exception:  # cleanup: 回滚失败也继续抛原错
        pass
    raise
finally:
    conn.close()

conn = ro(DB)
after = dict(conn.execute("SELECT COALESCE(status,'active'), COUNT(*) FROM memories GROUP BY 1"))
residue = 0
words = ("token", "模型", "插件", "API", "后台", "配置", "上下文", "服务器", "报错", "面板", "程序", "代码", "bug")
for (text,) in conn.execute("SELECT content FROM memories WHERE COALESCE(status,'active')='active'"):
    if any(w in str(text or "") for w in words):
        residue += 1
conn.close()
evidence = {"applied": True, "changed": changed, "ids": ids, "backup": manifest,
            "before": before, "after": after, "audit82_residue": residue,
            "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
with open(EVIDENCE, "w", encoding="utf-8", newline="") as fh:
    json.dump(evidence, fh, ensure_ascii=False, indent=2)
print(json.dumps(evidence, ensure_ascii=False, indent=1))
