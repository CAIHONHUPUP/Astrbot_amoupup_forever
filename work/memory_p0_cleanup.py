# -*- coding: utf-8 -*-
"""记忆库 P0 清理计划（task-28，只读 dry-run）。

对应 work/audit/memory_persona.md §3.3/§3.6：
- 82 条 active 记忆含系统/运维/后台词（token/模型/插件/API/后台/配置/上下文/服务器/报错/面板…）；
- 同一件商品 5 版本并存（开衫 649/没买/649/试穿/985 放回）；
- active 记忆精确金额 105 条 / 余额·卡里 48 条；
- 12 组重复前缀（多出 28 行）。

默认只读 + 只输出计划；--apply 必须同时给 --backup-dir，会先备份再清理。
本任务按要求不执行 --apply，不写生产库。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_memory_ledger as ledger  # noqa: E402

DEFAULT_DB = r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_data\amo_data.db"
OUT_DIR = os.path.join(ROOT, "work", "audit")
PREFIX_LEN = 12


def _connect_readonly(path):
    uri = "file:" + str(path).replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def _rows(conn):
    conn.row_factory = sqlite3.Row
    sql = ("SELECT id, uid, content, kind, subject, status, created_at, superseded_by "
           "FROM memories WHERE COALESCE(status, 'active') = 'active'")
    return [dict(row) for row in conn.execute(sql).fetchall()]


def build_plan(db_path):
    conn = _connect_readonly(db_path)
    try:
        rows = _rows(conn)
    finally:
        conn.close()

    ops = []
    by_word = {}
    for row in rows:
        hit = ledger.system_word_hit(row.get("content") or "")
        if not hit:
            continue
        ops.append(row["id"])
        by_word[hit] = by_word.get(hit, 0) + 1

    groups = {}
    for row in rows:
        for fact in ledger.extract_facts(row.get("content") or ""):
            if not fact.conflict_key.startswith("item:"):
                continue
            groups.setdefault(fact.conflict_key, []).append({
                "id": int(row["id"]),
                "state": fact.state,
                "created_at": int(row.get("created_at") or 0),
            })
    conflicts = []
    generic_groups = []
    for key, items in groups.items():
        states = {item["state"] for item in items}
        if len(states) < 2:
            continue
        keep = max(items, key=lambda item: item["created_at"])
        row = {
            "conflict_key": key,
            "states": sorted(states),
            "ids": sorted(item["id"] for item in items),
            "keep": keep["id"],
            "supersede": sorted(item["id"] for item in items if item["id"] != keep["id"]),
        }
        # 泛称「衣服/上衣」没有唯一商品身份：只记录、不自动 supersede
        if any(key == "item:" + name for name in ledger.GENERIC_ITEMS):
            generic_groups.append(row)
        else:
            conflicts.append(row)

    import re as _re
    amount_re = _re.compile(r"\d{2,7}(?:\.\d+)?\s*(?:元|块钱|块)")
    amount_all_re = _re.compile(r"\d{2,7}(?:\.\d+)?\s*(?:元|块钱|块|¥)|余额|卡里|卡上|卡内")
    balance_re = _re.compile(r"余额|卡里|卡上|卡内")
    amount_ids = [row["id"] for row in rows if amount_re.search(row.get("content") or "")]
    balance_ids = [row["id"] for row in rows if balance_re.search(row.get("content") or "")]
    amount_all_ids = [row["id"] for row in rows
                      if amount_all_re.search(row.get("content") or "")]

    # 审计口径的后台词表（附录 B），用来复核「82 条」这个数字
    audit_words = ("token", "模型", "插件", "API", "后台", "配置", "上下文",
                   "服务器", "报错", "面板", "程序", "代码", "bug")
    audit_hits = [row for row in rows
                  if any(word in str(row.get("content") or "") for word in audit_words)]

    # 重复前缀：按归一化全文分组（去掉空白/标点），对应审计里 12 组、多出 28 行
    prefixes = {}
    for row in rows:
        content = _re.sub(r"[\s，。！？!?、；;：:　]+", "", str(row.get("content") or ""))
        if not content:
            continue
        prefixes.setdefault(content, []).append(int(row["id"]))
    duplicate_groups = [
        {"prefix": key[:PREFIX_LEN], "ids": sorted(ids), "rows": len(ids)}
        for key, ids in prefixes.items() if len(ids) > 1
    ]
    extra_rows = sum(item["rows"] - 1 for item in duplicate_groups)

    return {
        "generated_at": int(time.time()),
        "db": str(db_path),
        "mode": "readonly-dry-run",
        "active_total": len(rows),
        "ops_word": {"count": len(ops), "ids": sorted(ops), "by_word": by_word},
        "shopping_conflicts": conflicts,
        "generic_clothing_groups": generic_groups,
        "exact_amount": {
            "count": len(amount_ids),
            "audit_count": len(amount_all_ids),
            "ids": sorted(amount_ids)[:50],
        },
        "balance": {"count": len(balance_ids)},
        "ops_word_audit82": {
            "count": len(audit_hits),
            "words": list(audit_words),
            "by_word": {word: sum(1 for row in audit_hits
                                  if word in str(row.get("content") or ""))
                        for word in audit_words},
        },
        "duplicate_prefix": {
            "groups": len(duplicate_groups),
            "extra_rows": extra_rows,
            "detail": duplicate_groups,
        },
    }


def write_reports(plan):
    os.makedirs(OUT_DIR, exist_ok=True)
    json_path = os.path.join(OUT_DIR, "memory_p0_plan.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=1)
    lines = [
        "# 记忆库 P0 清理计划（dry-run，只读）",
        "",
        "生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(plan["generated_at"])),
        "数据库：%s" % plan["db"],
        "模式：**只读**；本计划不修改生产库。",
        "",
        "| 项 | 数量 |",
        "| --- | ---: |",
        "| active 记忆 | %d |" % plan["active_total"],
        "| 系统/运维/后台词（扩展口径） | %d |" % plan["ops_word"]["count"],
        "| 后台词（审计 82 条口径） | %d |" % plan["ops_word_audit82"]["count"],
        "| 同商品多状态冲突组 | %d |" % len(plan["shopping_conflicts"]),
        "| 精确金额（元/块） | %d |" % plan["exact_amount"]["count"],
        "| 精确金额（审计口径含余额/¥） | %d |" % plan["exact_amount"]["audit_count"],
        "| 余额/卡里 | %d |" % plan["balance"]["count"],
        "| 重复前缀组 / 多出行 | %d / %d |" % (
            plan["duplicate_prefix"]["groups"], plan["duplicate_prefix"]["extra_rows"]),
        "",
        "## 系统词命中分布",
        "",
    ]
    for word, count in sorted(plan["ops_word"]["by_word"].items(), key=lambda item: -item[1]):
        lines.append("- %s：%d" % (word, count))
    lines.extend(["", "## 同商品多状态（应 supersede 的旧状态）", ""])
    for item in plan["shopping_conflicts"]:
        lines.append("- %s：保留 #%s，supersede %s（states=%s）" % (
            item["conflict_key"], item["keep"],
            ",".join("#%d" % mid for mid in item["supersede"]),
            "/".join(item["states"])))
    if plan.get("generic_clothing_groups"):
        lines.extend(["", "## 泛称衣物（仅记录，不自动 supersede）", ""])
        for item in plan["generic_clothing_groups"]:
            lines.append("- %s：states=%s ids=%s" % (
                item["conflict_key"], "/".join(item["states"]),
                ",".join("#%d" % mid for mid in item["ids"])))
    md_path = os.path.join(OUT_DIR, "memory_p0_plan.md")
    with open(md_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return json_path, md_path


def apply_plan(plan, db_path, backup_dir):
    """手动执行用：先备份，再把系统词记忆 archived、旧商品状态 superseded。"""
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(backup_dir, "amo_data.db.bak-memory-p0-%s" % stamp)
    shutil.copy2(db_path, backup)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("BEGIN")
        for mid in plan["ops_word"]["ids"]:
            conn.execute("UPDATE memories SET status='archived' WHERE id=?", (int(mid),))
        for item in plan["shopping_conflicts"]:
            for mid in item["supersede"]:
                conn.execute(
                    "UPDATE memories SET status='superseded', superseded_by=? WHERE id=?",
                    (int(item["keep"]), int(mid)),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description="记忆库 P0 清理计划（默认只读）")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", default=os.path.join(ROOT, "work", "backup"))
    args = parser.parse_args(argv)
    if not os.path.exists(args.db):
        print("DB NOT FOUND: %s" % args.db)
        return 2
    plan = build_plan(args.db)
    json_path, md_path = write_reports(plan)
    print("PLAN %s" % json_path)
    print("REPORT %s" % md_path)
    print("active=%d ops_word=%d audit82=%d conflicts=%d amount=%d balance=%d dup_groups=%d extra_rows=%d" % (
        plan["active_total"], plan["ops_word"]["count"],
        plan["ops_word_audit82"]["count"],
        len(plan["shopping_conflicts"]), plan["exact_amount"]["count"],
        plan["balance"]["count"], plan["duplicate_prefix"]["groups"],
        plan["duplicate_prefix"]["extra_rows"]))
    if args.apply:
        if not args.backup_dir:
            print("REFUSE: --apply 必须给 --backup-dir")
            return 3
        backup = apply_plan(plan, args.db, args.backup_dir)
        print("APPLIED backup=%s" % backup)
    else:
        print("DRY-RUN：未写生产库（加 --apply --backup-dir 才会执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
