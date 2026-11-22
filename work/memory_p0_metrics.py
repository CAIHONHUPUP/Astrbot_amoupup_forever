# -*- coding: utf-8 -*-
"""task-28 P0 指标：用生产只读数据复算「修前 / 守卫后」。

只读 amo_data.db；不 reload、不写生产库。输出 work/audit/memory_p0_metrics_20261122.json。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_memory_ledger as ledger  # noqa: E402
import amo_persona_guard as persona  # noqa: E402

DB = r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_data\amo_data.db"
OUT = os.path.join(ROOT, "work", "audit", "memory_p0_metrics_20261122.json")
EXACT_RE = re.compile(r"\d{2,7}\s*(?:元|块钱|块)|余额|卡里[^。！？\n]{0,8}\d|等了[^。！？\n]{0,6}\d+\s*分钟|就是\s*\d{3,4}")
AUDIT_WORDS = ("token", "模型", "插件", "API", "后台", "配置", "上下文",
               "服务器", "报错", "面板", "程序", "代码", "bug")


def _rows(sql, args=()):
    conn = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def message_metrics():
    rows = _rows(
        "SELECT id, content FROM messages WHERE role='bot' AND COALESCE(compressed,0)=0 "
        "ORDER BY id DESC LIMIT 186")
    rows.reverse()
    texts = [str(row.get("content") or "") for row in rows]
    before = {
        "messages": len(texts),
        "action_narration": sum(1 for text in texts if persona.has_action_narration(text)),
        "action_only": sum(1 for text in texts if persona.action_only(text)),
        "action_ratio": round(persona.action_ratio(texts), 4),
        "system_word": sum(1 for text in texts if ledger.system_word_hit(text)),
        "exact_number": sum(1 for text in texts if EXACT_RE.search(text)),
        "forbidden": sum(1 for text in texts if any(w in text for w in persona.FORBIDDEN_PHRASES)),
    }
    cleaned = []
    dropped = 0
    for text in texts:
        body, _reason = persona.prepare_outbound(object(), "umo-metrics", text, "chat")
        if not body:
            dropped += 1
            continue
        cleaned.append(body)
    after = {
        "messages": len(cleaned),
        "dropped": dropped,
        "action_narration": sum(1 for text in cleaned if persona.has_action_narration(text)),
        "action_only": sum(1 for text in cleaned if persona.action_only(text)),
        "action_ratio": round(persona.action_ratio(cleaned), 4),
        "system_word": sum(1 for text in cleaned if ledger.system_word_hit(text)),
        "exact_number": sum(1 for text in cleaned if EXACT_RE.search(text)),
        "forbidden": sum(1 for text in cleaned if any(w in text for w in persona.FORBIDDEN_PHRASES)),
    }
    return {"before": before, "after_guard": after}


def memory_metrics():
    rows = _rows(
        "SELECT id, content, created_at FROM memories "
        "WHERE COALESCE(status,'active')='active'")
    audit_hits = [row for row in rows
                  if any(word in str(row.get("content") or "") for word in AUDIT_WORDS)]
    all_hits = [row for row in rows if ledger.system_word_hit(row.get("content") or "")]
    kept = ledger.filter_recalled(object(), "u", rows)
    kept_ids = {int(row.get("id")) for row in kept}
    sweater = [row for row in rows if "米白色针织开衫" in str(row.get("content") or "")]
    sweater_kept = [row for row in sweater if int(row.get("id")) in kept_ids]
    exact = [row for row in rows
             if re.search(r"\d{2,7}\s*(?:元|块钱|块)", str(row.get("content") or ""))]
    return {
        "active_total": len(rows),
        "ops_word_audit82_before": len(audit_hits),
        "ops_word_audit82_after_recall_filter": sum(
            1 for row in audit_hits if int(row.get("id")) in kept_ids),
        "ops_word_extended_before": len(all_hits),
        "ops_word_extended_after_recall_filter": sum(
            1 for row in all_hits if int(row.get("id")) in kept_ids),
        "sweater_memories_before": len(sweater),
        "sweater_memories_after_recall_filter": len(sweater_kept),
        "sweater_kept_ids": sorted(int(row.get("id")) for row in sweater_kept),
        "exact_money_before": len(exact),
        "exact_money_after_approximate": sum(
            1 for row in exact
            if re.search(r"\d{2,7}\s*(?:元|块钱|块)",
                         ledger.format_memory_text(row.get("content") or ""))),
    }


def main():
    report = {
        "generated_at": 1795310000,
        "db": DB,
        "readonly": True,
        "messages": message_metrics(),
        "memories": memory_metrics(),
    }
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
