# -*- coding: utf-8 -*-
"""生产 bot 消息回放（只读）v2：184 条审计 bot 消息 + 审计口径问题判定 + 逐段归因。

输出：work/audit/holdout/replay_prod.json / replay_prod.txt
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import difflib
import io
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "work", "sim"))

import sim_env as E  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
import amo_message as M  # noqa: E402

DUMP = os.path.join(ROOT, "work", "audit", "_scratch", "mem_persona", "dump_amo_data.json")
PROD_DB = r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever\amo_forever.db"
MEDIA_RE = re.compile(r"^\s*\[(?:我发的照片|图片|照片|语音|视频|表情|文件|卡片|我发的语音)\]")

SYS_WORDS = ("服务器", "后台", "接口", "报错", "超时", "权限", "面板", "异常", "重试",
             "网络连接", "数据库", "日志", "错误码", "节点", "降级", "软件", "系统",
             "traceback", "timeout", "exception", "质检", "重写", "标记", "配置",
             "重启", "部署", "插件", "缓存", "字段", "索引", "代码", "程序", "版本",
             "token", "上下文", "断网", "崩溃", "这软件", "这网络", "没权限")
ROLE_ASK = ("你发我", "你发过", "你发的", "你分享", "你刚发", "你给我发", "你再发",
            "你发那个", "你那条", "点开", "发我一遍", "发我看看", "我再发")
ROLE_OBJ = ("视频", "图", "照片", "语音", "分享", "链接", "表情")
MEM_META = ("收到你后来说的", "你后来说的", "你刚才说", "你上次说", "你之前说", "刚刚你说",
            "你说过", "你之前提过", "你不是说", "我跟你说过了")
FORBIDDEN = ("你美丽的老婆", "你们男人", "收到你后来", "收到你那句",
             "这有啥好笑的", "我哪生气了", "别给我扣帽子")
PRECISE_RE = re.compile(r"(?:卡里还有|余额|就是)\s*\d{2,}"
                        r"|\d+\s*(?:元|块钱?|分钟|小时)"
                        r"|等了(?:十|半|\d+)")
NARRATION_RE = re.compile(r"（[^）]{2,120}）|\([^)]{2,120}\)")

STAGE_ORDER = ("soft_guards", "rewrite_reply", "text_cleanup", "chat_safety",
               "send_pipeline", "strip_media", "addressee", "anti_nag", "purchase",
               "validate_assembly", "tech_terms", "segment_repeat", "persona")


def _fill(text):
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or ""))


def _strip_parens(text):
    return NARRATION_RE.sub("", str(text or ""))


def base_problems(text):
    body = str(text or "")
    if not body.strip():
        return ["empty"]
    out = []
    for word in SYS_WORDS:
        if word in body:
            out.append("system_talk:" + word)
            break
    if any(w in body for w in ROLE_ASK) and any(w in body for w in ROLE_OBJ):
        out.append("role_reversal")
    for phrase in MEM_META:
        if phrase in body:
            out.append("memory_meta")
            break
    for phrase in FORBIDDEN:
        if phrase in body:
            out.append("forbidden_phrase:" + phrase)
            break
    if PRECISE_RE.search(body):
        out.append("precise_number")
    stripped = _strip_parens(body).strip()
    if NARRATION_RE.search(body) and len(_fill(stripped)) <= 4:
        out.append("narration_only")
    return out


def repeat_problems(text, history, when, window_min=1440):
    body = _fill(text)
    if len(body) < 5:
        return []
    for row in history:
        if row.get("role") != "bot":
            continue
        gap = (when - row["when"]).total_seconds() / 60.0
        if gap < 0 or gap > window_min:
            continue
        other = _fill(row.get("content"))
        if len(other) < 5:
            continue
        if body == other:
            return ["repeat_exact"]
        if difflib.SequenceMatcher(None, body, other).ratio() >= 0.88:
            return ["repeat_near"]
    return []


def intended_change(raw, final):
    """把「新守卫明确想做的改写」与「意外改写」分开。"""
    if final == raw:
        return "unchanged"
    if not final:
        return "dropped"
    if _fill(_strip_parens(raw)) == _fill(final):
        return "narration_strip"
    if any(word in raw for word in SYS_WORDS) and not any(word in final for word in SYS_WORDS):
        return "system_strip"
    if any(p in raw for p in FORBIDDEN) and not any(p in final for p in FORBIDDEN):
        return "forbidden_strip"
    if PRECISE_RE.search(raw) and not PRECISE_RE.search(final):
        return "number_strip"
    ratio = difflib.SequenceMatcher(None, _fill(raw), _fill(final)).ratio()
    return "minor_rewrite" if ratio >= 0.72 else "other_rewrite"


def _ts(value):
    return datetime.datetime.fromtimestamp(int(value))


def load_schedule():
    con = sqlite3.connect("file:%s?mode=ro" % PROD_DB.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    blocks = {}
    for row in con.execute("select day, items from schedule order by day"):
        items = json.loads(row["items"] or "[]")
        rows = []
        for item in items:
            if not item.get("start") or not item.get("end"):
                continue
            people = [str(x) for x in (item.get("people") or []) + (item.get("companions") or [])]
            rows.append({
                "start": item.get("start"), "end": item.get("end"),
                "zone": item.get("zone") or "", "place": item.get("place") or "",
                "activity": item.get("activity") or "", "verb": item.get("activity") or "",
                "category": item.get("category") or "", "people": people,
                "together": "老公" in "".join(people) or "他" in people,
            })
        blocks[row["day"]] = rows
    health = {"status": "健康", "sick": False}
    try:
        row = con.execute("select value from kv where key='health'").fetchone()
        if row:
            data = json.loads(row[0])
            health = {"status": data.get("status") or "健康",
                      "sick": bool(data.get("status") and data.get("status") != "健康")}
    except Exception:  # cleanup: 快照缺失就按无健康数据处理
        pass
    con.close()
    return blocks, health


def load_messages():
    data = json.load(open(DUMP, encoding="utf-8"))
    rows = [m for m in data["messages"] if m.get("role") in ("bot", "user")]
    rows.sort(key=lambda m: m["id"])
    return rows


async def run_one(plugin, umo, raw, user_text, when):
    import amo_addressee_guard as addr
    import amo_anti_nag as anti
    import amo_purchase_guard as purchase
    import amo_state_consistency as sc
    import amo_persona_guard as persona
    stages = [("raw", str(raw or ""))]
    drop, reason = "", ""
    event = E.FakeEvent(user_text, umo)
    result = E.FakeResult()
    with E.frozen_clock(when.strftime("%H:%M"), day=when.strftime("%Y-%m-%d")):
        text = str(raw or "")
        try:
            text = plugin._deco_soft_guards(event, text)
            stages.append(("soft_guards", text))
            text = plugin._deco_rewrite_reply(event, result, text)
            stages.append(("rewrite_reply", text))
            text = await plugin._deco_text_cleanup(event, text)
            stages.append(("text_cleanup", text))
            history = " ".join(str(row.get("content") or "") for row in plugin._recent(umo, 6)
                               if isinstance(row, dict))
            text = pipeline.apply_chat_safety(text, plugin, umo, user_text, history_text=history)
            stages.append(("chat_safety", text))
        except Exception as exc:
            stages.append(("deco_error", repr(exc)[:200]))
        try:
            ctx = pipeline.context_for_send(plugin, umo)
            text = (await pipeline.run(text, ctx))["text"]
            stages.append(("send_pipeline", text))
            text = M.strip_media_markers(text)
            stages.append(("strip_media", text))
            text, reason = addr.enforce(plugin, umo, text, source="chat")
            if not text:
                raise StopIteration("addressee")
            stages.append(("addressee", text))
            text = anti.rewrite(text, user_text)
            stages.append(("anti_nag", text))
            text = purchase.rewrite(text, plugin)
            stages.append(("purchase", text))
            text = sc.validate_reply_assembly(text, plugin, umo=umo, user_text=user_text)
            stages.append(("validate_assembly", text))
            text = M.strip_tech_terms(text, source="chat")
            stages.append(("tech_terms", text))
            segments = plugin._segment(text)
            segments = sc.dedupe_reply_segments(segments, plugin=plugin, umo=umo) or ([text] if text else [])
            text = "\n".join(pipeline.apply_segment_repeat_guard(seg, plugin, umo) for seg in segments)
            stages.append(("segment_repeat", text))
            text, reason = persona.prepare_outbound(plugin, umo, text, "chat")
            if not text:
                raise StopIteration("persona")
            stages.append(("persona", text))
        except StopIteration as stop:
            drop = str(stop) or "drop"
        except Exception as exc:
            stages.append(("send_error", repr(exc)[:300]))
            drop = drop or "error"
    # 逐段归因：第一处实质变化发生在哪个阶段
    source = ""
    stage_map = dict(stages)
    prev = str(raw or "")
    for name in STAGE_ORDER:
        if name not in stage_map:
            continue
        now_text = stage_map[name]
        if now_text != prev:
            source = source or name
        prev = now_text
    return {"final": str(text or ""), "stages": stages, "dropped_by": drop,
            "drop_reason": reason, "damage_source": source,
            "stage_text": stage_map}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    blocks_by_day, health = load_schedule()
    messages = load_messages()
    results = []
    recent = []
    last_user = {}
    media_skipped = []
    for msg in messages:
        when = _ts(msg["created_at"])
        umo = str(msg.get("umo") or "")
        content = str(msg.get("content") or "")
        if msg["role"] == "user":
            last_user[umo] = content
            recent.append({"role": "user", "content": content, "when": when})
            recent[:] = recent[-14:]
            continue
        if MEDIA_RE.match(content):
            media_skipped.append({"id": msg.get("id"), "raw": content})
            recent.append({"role": "bot", "content": content, "when": when})
            recent[:] = recent[-14:]
            continue
        user_text = last_user.get(umo, "")
        history = list(recent)
        blocks = blocks_by_day.get(when.strftime("%Y-%m-%d"), [])
        plugin = E.build_plugin(blocks, kv={}, health=dict(health))
        plugin._recent_rows = [{"role": row["role"], "content": row["content"]}
                               for row in recent[-8:]]
        try:
            out = await run_one(plugin, umo, content, user_text, when)
        except Exception as exc:
            out = {"final": "", "stages": [("error", repr(exc)[:300])],
                   "dropped_by": "exception", "drop_reason": repr(exc)[:200],
                   "damage_source": "exception", "stage_text": {}}
        before = base_problems(content) + repeat_problems(content, history, when)
        after = (base_problems(out["final"]) + repeat_problems(out["final"], history, when)
                 if out["final"] else ["empty"])
        kind = intended_change(content, out["final"])
        results.append({
            "id": msg.get("id"), "time": when.strftime("%Y-%m-%d %H:%M:%S"), "umo": umo,
            "raw": content, "final": out["final"], "user_text": user_text,
            "before_problems": before, "after_problems": after,
            "changed": out["final"] != content, "diff_kind": kind,
            "dropped_by": out["dropped_by"], "drop_reason": out["drop_reason"],
            "damage_source": out["damage_source"],
            "stage_text": out["stage_text"],
        })
        recent.append({"role": "bot", "content": content, "when": when})
        recent[:] = recent[-14:]
        if args.limit and len(results) >= args.limit:
            break

    def cat_count(rows, key):
        counter = {}
        for row in rows:
            for item in row[key]:
                counter[item.split(":")[0]] = counter.get(item.split(":")[0], 0) + 1
        return dict(sorted(counter.items(), key=lambda kv: -kv[1]))

    bad_before = [r for r in results if r["before_problems"]]
    clean = [r for r in results if not r["before_problems"]]
    fixed = [r for r in bad_before if not r["after_problems"]]
    persistent = [r for r in bad_before if r["after_problems"]]
    clean_changed = [r for r in clean if r["changed"]]
    clean_intended = [r for r in clean if r["diff_kind"] in
                      ("narration_strip", "system_strip", "forbidden_strip", "number_strip")]
    clean_narration = [r for r in clean if r["diff_kind"] == "narration_strip"]
    clean_other = [r for r in clean if r["final"] and r["diff_kind"] in
                   ("minor_rewrite", "other_rewrite")]
    clean_dropped = [r for r in clean if not r["final"]]
    reasons = {}
    for row in results:
        if row["dropped_by"]:
            key = "%s:%s" % (row["dropped_by"], row["drop_reason"][:30])
            reasons[key] = reasons.get(key, 0) + 1
    sources = {}
    for row in results:
        if row["changed"]:
            sources[row["damage_source"] or "?"] = sources.get(row["damage_source"] or "?", 0) + 1
    report = {
        "meta": {
            "dataset": DUMP, "bot_messages": len(results),
            "media_placeholder_skipped": len(media_skipped),
            "window": [results[0]["time"], results[-1]["time"]] if results else [],
            "schedule_source": PROD_DB,
            "note": ("消息=审计导出 dump_amo_data.json 的 bot 条数（184，≈任务口径 186）；"
                     "每条场景按 amo_forever.db schedule 的真实时段重建；只读，未写生产库。"),
        },
        "counts": {
            "bot_messages": len(results), "before_bad": len(bad_before),
            "before_clean": len(clean), "fixed": len(fixed),
            "persistent": len(persistent),
            "clean_untouched": len([r for r in clean if not r["changed"]]),
            "clean_changed": len(clean_changed),
            "clean_narration_strip": len(clean_narration),
            "clean_intended_rewrite": len(clean_intended),
            "clean_other_rewrite": len(clean_other),
            "clean_dropped": len(clean_dropped),
            "narration_present_before": sum(1 for r in results if NARRATION_RE.search(r["raw"])),
            "narration_present_after": sum(1 for r in results if NARRATION_RE.search(r["final"])),
            "change_source": sources, "diff_kind": {},
        },
        "before_categories": cat_count(bad_before, "before_problems"),
        "after_categories": cat_count(results, "after_problems"),
        "drop_reasons": reasons,
        "samples": {"fixed": fixed, "persistent": persistent,
                    "clean_other_rewrite": clean_other,
                    "clean_dropped": clean_dropped,
                    "clean_changed": clean_changed},
    }
    kinds = {}
    for row in results:
        kinds[row["diff_kind"]] = kinds.get(row["diff_kind"], 0) + 1
    report["counts"]["diff_kind"] = kinds
    with open(os.path.join(HERE, "replay_prod.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    out = io.StringIO()
    out.write(json.dumps(report["counts"], ensure_ascii=False, indent=1) + "\n")
    out.write(json.dumps({"before": report["before_categories"],
                          "after": report["after_categories"],
                          "drops": report["drop_reasons"]}, ensure_ascii=False, indent=1) + "\n")
    for bucket in ("fixed", "persistent", "clean_other_rewrite", "clean_dropped"):
        out.write("\n==== %s (%d) ====\n" % (bucket, len(report["samples"][bucket])))
        for r in report["samples"][bucket]:
            out.write("-- #%s %s [%s|%s]\n  raw: %s\n  fin: %s\n" % (
                r["id"], r["time"], r["diff_kind"], r["damage_source"],
                r["raw"][:220], r["final"][:220]))
    open(os.path.join(HERE, "replay_prod.txt"), "w", encoding="utf-8").write(out.getvalue())
    print(json.dumps(report["counts"], ensure_ascii=True, indent=1))
    print("wrote replay_prod.json / replay_prod.txt")


if __name__ == "__main__":
    asyncio.run(main())