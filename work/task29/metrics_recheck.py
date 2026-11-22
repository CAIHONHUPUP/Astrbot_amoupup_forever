# -*- coding: utf-8 -*-
"""task-29 指标：已知真实误伤案例 + real_llm_layer 全量回放（只读，不写生产库）。

宿主为内存假插件（sqlite :memory:），不 reload、不 commit。
- 已知误伤 = 审计 3.2.1 / 3.4.1 / test_guard_priority 的 11 条「候选答到、发出通用句」；
- real_llm_layer = work/sim/real_llm_layer.json 272 轮，按 probe 类别合成标准问句回放
  （真实 probe 文本未留档，C 级近似，不做真实模型外推）。
"""
import io
import json
import os
import sqlite3
import sys
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import amo_grounding_critic as GC  # noqa: E402
import amo_reply_pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "work", "sim", "real_llm_layer.json")
OUT_MD = os.path.join(ROOT, "work", "task29", "recheck_metrics.md")
OUT_JSON = os.path.join(ROOT, "work", "task29", "recheck_metrics.json")

#: probe 类别 -> 回放用标准问句（真实 probe 文本未留档，C 级近似）
QUESTION = {
    "eaten": "吃了吗？",
    "where": "你在哪？",
    "doing": "你在干嘛呢？",
    "who": "跟谁在一起？",
    "when_return": "今晚回不回来？",
}

#: (名称, 原候选, 他这句, 历史真实发出文本)
KNOWN_HARMS = (
    ("G09 where", "没呢，刚下班走在回家的路上，今天累死了",
     "到公司了吗？", "还是老样子。"),
    ("G22 doing", "哪有偷懒呀，我这不是刚下班回家，窝沙发上追会儿剧放松一下嘛~",
     "你是不是在偷懒？", "还是老样子。"),
    ("G29 when", "刚练完器械，还在健身房呢，估计得七点多才能到家吧，今天练得有点久。",
     "几点到家呀？", "我还在外面呢，晚点就回去。"),
    ("G31 doing", "哪有偷懒啊，我这不是刚下班回家嘛，正窝在沙发上追剧呢，累了一天总得歇会儿吧~",
     "你是不是在偷懒？", "还是老样子。"),
    ("G36 when", "在地铁上呢，晚点回去", "今晚回不回来？", "还是老样子。"),
    ("G13 when", "不回啦，我就在家歇着呢，刚到家没多久，今天真不想动了",
     "今晚回不回来？", "嗯，我在呢。"),
    ("G43 when", "17:27啦，我刚到家歇着呢，今晚不回了，明早还要早起",
     "今晚回不回来？", "我这儿一直在。"),
    ("G23 recall", "记得呀，那天你穿白衬衫，在奶茶店门口等我，我还迟到了",
     "你还记得那天我穿什么去接你的吗？", "我还在路上呢，快到了。"),
    ("G17 doing", "刚洗完澡躺床上刷手机呢，酒店空调开得挺舒服的",
     "你在干嘛呢？", "嗯，我在呢。"),
    ("G47 doing", "刚洗完澡躺床上刷手机呢，酒店空调开得挺舒服的",
     "忙什么呢", "我在在酒店休息。"),
    ("G13-2 eaten", "中午随便吃了碗云吞面，现在正走在回家路上，饿得不行了",
     "你中午吃的什么？", "还是老样子。"),
)


def _plugin(snap=None):
    snap = dict(snap or {}) if snap is not None else None

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute(
                "create table wallet_ledger (ts real, delta_cents int, item_name text)")
            self.conn.commit()

    p = SimpleNamespace()
    p.db = _DB()
    if snap is not None:
        p.state = SimpleNamespace(current_activity=lambda: dict(snap))
    p._recent = lambda umo, n=8: []
    return p


def _answers(reply, question):
    if not str(reply or "").strip():
        return False
    try:
        if P._answered_intent(reply, question):
            return True
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass
    try:
        return not GC.issues(question, reply)
    except Exception:
        return False


def _risk(text):
    body = str(text or "")
    hits = []
    if "[我发的照片]" in body or "[我发的语音]" in body:
        hits.append("media_marker")
    try:
        from amo_shared import looks_like_model_leak
        if looks_like_model_leak(body):
            hits.append("model_leak")
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass
    try:
        import amo_purchase_guard
        if amo_purchase_guard._CLAIM.search(body):
            hits.append("purchase_claim")
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass
    return hits


def known_harm_metrics():
    rows = []
    exact = 0
    answered_after = 0
    elapsed = 0.0
    for name, cand, q, sent in KNOWN_HARMS:
        P.reset_repetition_memory()
        plugin = _plugin()
        umo = "known:" + name
        P.apply_narrative_repetition(cand, plugin, q, umo)
        started = time.perf_counter()
        out = P.apply_chat_safety(sent, plugin, umo, q)
        elapsed += (time.perf_counter() - started) * 1000.0
        ok = _answers(out, q)
        exact += out.strip() == cand.strip()
        answered_after += bool(ok)
        rows.append({"name": name, "question": q, "candidate": cand, "sent": sent,
                     "out": out, "restored": out.strip() == cand.strip(),
                     "answered_after": ok, "risk": _risk(out) if ok else []})
        # 真实链：候选直接进收口（没有历史坏输出）
        P.reset_repetition_memory()
        P.apply_narrative_repetition(cand, plugin, q, "chain:" + name)
        rows[-1]["real_chain"] = P.apply_chat_safety(
            cand, plugin, "chain:" + name, q)
        rows[-1]["real_chain_answers"] = _answers(rows[-1]["real_chain"], q)
    return {
        "cases": len(KNOWN_HARMS),
        "exact_restored": exact,
        "exact_restored_rate": round(exact / float(len(KNOWN_HARMS)), 3),
        "answered_after": answered_after,
        "answered_after_rate": round(answered_after / float(len(KNOWN_HARMS)), 3),
        "real_chain_answered": sum(1 for r in rows if r["real_chain_answers"]),
        "safety_regressions": sum(1 for r in rows if r["risk"]),
        "ms_total": round(elapsed, 1),
        "rows": rows,
    }


def replay_metrics():
    with io.open(DATA, encoding="utf-8") as fh:
        rows = list((json.load(fh) or {}).get("rows") or [])
    probe_rows = [r for r in rows if r.get("probe")]
    no_probe_rows = [r for r in rows if not r.get("probe")]
    harms = []
    restored = 0
    answered_after = 0
    risky = []
    failures = []
    elapsed = 0.0
    for row in probe_rows:
        q = QUESTION.get(str(row.get("probe")))
        if not q:
            continue
        cand = str(row.get("candidate") or "")
        sent = str(row.get("sent") or "")
        if not _answers(cand, q) or _answers(sent, q):
            continue
        umo = "replay:%s:%s" % (row.get("gid"), row.get("turn"))
        P.reset_repetition_memory()
        plugin = _plugin(row.get("snap"))
        P.apply_narrative_repetition(cand, plugin, q, umo)
        started = time.perf_counter()
        out = P.apply_chat_safety(sent, plugin, umo, q)
        elapsed += (time.perf_counter() - started) * 1000.0
        ok = _answers(out, q)
        harms.append({"gid": row.get("gid"), "turn": row.get("turn"),
                      "probe": row.get("probe"), "candidate": cand, "sent": sent,
                      "out": out, "restored": out.strip() == cand.strip(),
                      "answered_after": ok})
        restored += out.strip() == cand.strip()
        answered_after += bool(ok)
        if not ok:
            failures.append({"gid": row.get("gid"), "turn": row.get("turn")})
        risk = _risk(out) if ok else []
        if risk:
            risky.append({"gid": row.get("gid"), "turn": row.get("turn"), "risk": risk})

    no_probe_changed = 0
    for row in no_probe_rows:
        umo = "replay:noprobe:%s:%s" % (row.get("gid"), row.get("turn"))
        P.reset_repetition_memory()
        plugin = _plugin(row.get("snap"))
        sent = str(row.get("sent") or "")
        P.apply_narrative_repetition(str(row.get("candidate") or ""), plugin, "", umo)
        out = P.apply_chat_safety(sent, plugin, umo, "")
        if out.strip() != sent.strip():
            no_probe_changed += 1
    harm_n = len(harms)
    return {
        "turns": len(rows),
        "probe_turns": len(probe_rows),
        "no_probe_turns": len(no_probe_rows),
        "harm_rows": harm_n,
        "restored": restored,
        "restored_rate": round(restored / float(harm_n), 3) if harm_n else 0.0,
        "answered_after": answered_after,
        "answered_after_rate": round(answered_after / float(harm_n), 3) if harm_n else 0.0,
        "recheck_failures": len(failures),
        "safety_regressions": len(risky),
        "no_probe_turns_changed": no_probe_changed,
        "ms_total": round(elapsed, 1),
        "ms_avg": round(elapsed / float(harm_n), 2) if harm_n else 0.0,
        "samples": harms[:12],
    }


def main():
    known = known_harm_metrics()
    replay = replay_metrics()
    summary = {
        "known_harms": {k: v for k, v in known.items() if k != "rows"},
        "real_llm_layer": replay,
        "last_stats": P.semantic_recheck_stats(),
        "caveat": "probe 文本未留档，按类别合成标准问句回放（C 级近似）；回放链只含 "
                  "apply_narrative_repetition+apply_chat_safety",
    }
    lines = [
        "# task-29 后置语义复检指标",
        "",
        "> 只读 work/sim/real_llm_layer.json；宿主为内存假插件（sqlite :memory:），",
        "> 未写生产库、未 reload、未 commit。脚本：work/task29/metrics_recheck.py。",
        "",
        "## 一、已知真实误伤（审计 3.2.1 / 3.4.1 / test_guard_priority 的 11 例）",
        "",
        "判定口径：原候选按 answered_intent/grounding 答到他这句（或接住最后一句），",
        "历史发出文本答不到 → 把历史文本喂 apply_chat_safety 看能否还原候选。",
        "",
        "| 指标 | 值 |",
        "| --- | ---: |",
        "| 案例数 | %d |" % known["cases"],
        "| 精确还原候选 | %d（%.1f%%） |" % (known["exact_restored"],
                                              100.0 * known["exact_restored_rate"]),
        "| 复检后答到他这句 | %d（%.1f%%） |" % (known["answered_after"],
                                                 100.0 * known["answered_after_rate"]),
        "| 真实链（候选直入收口）答到 | %d/%d |" % (known["real_chain_answered"],
                                                    known["cases"]),
        "| 安全回归（泄漏/假消费/占位符） | %d |" % known["safety_regressions"],
        "| 复检后仍答不到 | %d |" % (known["cases"] - known["answered_after"]),
        "",
        "| 案例 | 他这句 | 历史发出 | 复检后 | 答到 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in known["rows"]:
        lines.append("| %s | %s | %s | %s | %s |" % (
            row["name"], row["question"][:16], row["sent"][:20],
            row["out"][:26], "是" if row["answered_after"] else "否"))
    lines += [
        "",
        "## 二、real_llm_layer 272 轮回放",
        "",
        "probe 只记类别（eaten/where/doing/who/when_return），真实 probe 文本未留档：",
        "按类别合成标准问句回放，**C 级近似**，不做真实模型外推。",
        "",
        "| 指标 | 值 |",
        "| --- | ---: |",
        "| 总轮数 | %d |" % replay["turns"],
        "| 有 probe 的轮数 | %d |" % replay["probe_turns"],
        "| 命中（候选答到、sent 答不到） | %d |" % replay["harm_rows"],
        "| 还原候选 | %d（%s%%） |" % (replay["restored"],
                                        round(100.0 * replay["restored_rate"], 1)),
        "| 回放后答到 | %d/%d |" % (replay["answered_after"], replay["harm_rows"]),
        "| 复检失败（还原后仍答不到） | %d |" % replay["recheck_failures"],
        "| 安全回归 | %d |" % replay["safety_regressions"],
        "| 非问句轮被改动 | %d / %d |" % (replay["no_probe_turns_changed"],
                                           replay["no_probe_turns"]),
        "| 回放耗时（apply_chat_safety） | %.1f ms 总 / %.2f ms 均 |" % (
            replay["ms_total"], replay["ms_avg"]),
        "",
        "## 三、验收对照（审计 §6）",
        "",
        "| 指标 | 目标 | 本次 |",
        "| --- | --- | --- |",
        "| 守卫改写后语义复检失败 | 0 | %d（已知 11 例 + 回放） |"
        % (known["cases"] - known["answered_after"] + replay["recheck_failures"]),
        "| 候选正确但被通用句替换（已知样例） | 0 | %d/%d 还原 |"
        % (known["exact_restored"], known["cases"]),
        "| 复检引入安全回归 | 0 | %d |"
        % (known["safety_regressions"] + replay["safety_regressions"]),
        "| 复检改写非问句轮 | 0 | %d / %d |" % (replay["no_probe_turns_changed"],
                                                replay["no_probe_turns"]),
    ]
    text = "\n".join(lines) + "\n"
    with io.open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(text)
    with io.open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(text)
    print("written:", OUT_MD, OUT_JSON)


if __name__ == "__main__":
    main()
