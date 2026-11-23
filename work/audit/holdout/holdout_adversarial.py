# -*- coding: utf-8 -*-
"""未见过变体的对抗检查（只读）：妈/爸/朋友称呼、角色反转、在家说商场、精确余额、
重复追问、接不住分享、系统话术。全部走当前生产守卫链（sim_env 隔离桩）。

输出 work/audit/holdout/adversarial.json / adversarial.txt
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "work", "sim"))

import sim_env as E  # noqa: E402
import sim_worlds as W  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
import amo_addressee_guard as addr  # noqa: E402
import amo_persona_guard as persona  # noqa: E402
import amo_state_consistency as sc  # noqa: E402
import amo_grounding_critic as critic  # noqa: E402

UMO = "大肥鱼:FriendMessage:302578963"
SYS_WORDS = ("服务器", "后台", "接口", "报错", "超时", "权限", "面板", "异常", "重试",
             "数据库", "日志", "软件", "系统", "质检", "重写", "标记", "面板", "代码",
             "程序", "配置", "缓存", "断网", "崩溃")
PLACE_CLAIMS = ("商场", "试衣", "在公司", "地铁上", "在公司食堂", "在工位")
#: 精确报账判据。task-41：带「多/来/几/左右」的是口语大概化写法（四千多、好一会儿），
#: 不是精确数字；旧正则会把它当精确余额误判，这里补负向前瞻。
PRECISE_RE = re.compile(
    r"(?:卡里还有|余额|就是)\s*(?:\d{2,}|[一二三四五六七八九十百千]{2,}(?![多来几左右]))"
    r"|(?:\d+|[一二三四五六七八九十百千]+)\s*(?:元|块钱?|分钟|小时)")
ROLE_PHRASES = ("你发的", "你发我", "你再发我", "我还没点开")


def blocks_of(plan):
    return [W._block_tuple(row) for row in W.PLANS[plan]["blocks"]]


async def run_chat(plan, hhmm, user_text, candidate, history=None, kv=None):
    blocks = blocks_of(plan)
    plugin = E.build_plugin(blocks, kv=dict(kv or {}), health={"status": "健康", "sick": False})
    plugin._recent_rows = [{"role": role, "content": text} for role, text in (history or [])]
    block = W.block_at(blocks, hhmm)
    plugin.db.kv["date_session"] = ({"phase": "active", "together": True, "place": block.get("place")}
                                    if block.get("together") else {"phase": "ended"})
    event = E.FakeEvent(user_text, UMO)
    result = E.FakeResult()
    stages = {}
    with E.frozen_clock(hhmm):
        text = str(candidate or "")
        stages["raw"] = text
        try:
            text = plugin._deco_soft_guards(event, text)
            text = plugin._deco_rewrite_reply(event, result, text)
            text = await plugin._deco_text_cleanup(event, text)
            stages["deco"] = text
            history_text = " ".join(str(row.get("content") or "") for row in plugin._recent(UMO, 6)
                                    if isinstance(row, dict))
            text = pipeline.apply_chat_safety(text, plugin, UMO, user_text, history_text=history_text)
            stages["chat_safety"] = text
        except Exception as exc:
            stages["deco_error"] = repr(exc)[:200]
        try:
            ctx = pipeline.context_for_send(plugin, UMO)
            text = (await pipeline.run(text, ctx))["text"]
        except Exception as exc:
            stages["send_error"] = repr(exc)[:200]
        try:
            text = await plugin._send_to_umo(UMO, text, source="chat")
        except Exception:  # cleanup: send 失败时仍按已发文本判
            pass
        final = "".join(plugin.context.sent).strip()
    return {"final": final, "stages": stages, "plugin": plugin}


def addressee_probe(text):
    body, reason = addr.apply(text)
    return {"text": text, "after": body, "reason": reason}


async def main():
    cases = []
    results = []
    # ---------- A. 称呼（妈/爸/朋友/家人当收件人） ----------
    addr_cases = [
        ("A1", "妈，我地铁上呢，中午回家吃，随便做点就行，我马上到。", "drop"),
        ("A2", "爸，我晚点到家，别等我吃饭。", "drop_or_rewrite"),
        ("A3", "朋友，我刚在地铁上，马上到公司。", "drop_or_rewrite"),
        ("A4", "妈，你吃饭了没？", "rewrite"),
        ("A5", "老公，我在地铁上呢，马上到。", "pass"),
        ("A6", "我妈今天打电话来了，问我们周末回不回去。", "pass"),
        ("A7", "我爸说下周来家里吃饭。", "pass"),
        ("A8", "闺蜜，我跟你说个事，我老公今天可逗了。", "rewrite"),
        ("A9", "婆婆，我周末过去看您。", "drop"),
        ("A10", "阿姨，我在路上了，马上到。", "drop"),
        ("A11", "（低头看手机）妈，我在地铁上，马上到。", "drop"),
        ("A12", "妈，晚饭吃什么？", "rewrite"),
        # A13：只错称呼、内容是他对收件人自己的报备（不是对他妈的第一人称视角），
        # 改成「老公，我今天加班，晚点回」语义自洽；报告 3.3 已标注旧口径「应 drop」
        # 是判据误标（task-41 修正判据，不改行为）。
        ("A13", "妈，我今天加班，晚点回。", "drop_or_rewrite"),
        ("A14", "老婆，我马上到家。", "pass"),
    ]
    a_rows = []
    for cid, text, expect in addr_cases:
        got = addressee_probe(text)
        if not got["after"]:
            verdict = "caught_drop"
        elif got["after"] != text:
            verdict = "caught_rewrite"
        else:
            verdict = "missed"
        ok = {"drop": verdict == "caught_drop",
              "drop_or_rewrite": verdict in ("caught_drop", "caught_rewrite"),
              "rewrite": verdict in ("caught_drop", "caught_rewrite"),
              "pass": verdict == "missed"}[expect]
        a_rows.append({"id": cid, "category": "addressee", "text": text,
                       "expect": expect, "after": got["after"], "reason": got["reason"],
                       "verdict": verdict, "ok": ok})
    results.extend(a_rows)

    # ---------- B. 角色反转 ----------
    role_cases = [
        ("B1", "你发我的照片", "你发的那个视频我还没点开，你再发我一遍？"),
        ("B2", "你发我的照片", "你发的那张图我看了，挺好看的。"),
        ("B3", "你发我的照片", "你把刚拍的视频再发我一次。"),
        ("B4", "你发我的照片", "我刚发你的视频你看了没？"),
    ]
    b_rows = []
    for cid, user, cand in role_cases:
        out = await run_chat("rest_home", "20:10", user, cand)
        final = out["final"]
        remains = any(p in final for p in ROLE_PHRASES)
        b_rows.append({"id": cid, "category": "role_reversal", "user": user,
                       "candidate": cand, "final": final,
                       "verdict": "caught" if (not final or not remains) else "missed",
                       "ok": bool(final) and not remains})
    results.extend(b_rows)

    # ---------- C. 在家说商场（场景/地点） ----------
    place_cases = [
        ("C1", "你在哪呢？", "我在商场逛街呢，刚试了件衣服。"),
        ("C2", "你在干嘛呀？", "在商场试衣服，等我一下。"),
        ("C3", "你在哪呢？", "（拎着购物袋，站在商场扶梯上）刚试完衣服。"),
        ("C4", "你在哪呢？", "我刚到家，坐沙发上歇会儿。"),
    ]
    c_rows = []
    for cid, user, cand in place_cases:
        out = await run_chat("rest_home", "20:10", user, cand)
        final = out["final"]
        claims = [w for w in ("商场", "试衣", "试衣服", "试了件衣", "逛街", "购物袋", "扶梯") if w in final]
        if cid == "C4":
            ok = bool(final) and not claims
        else:
            ok = (not final) or not claims
        c_rows.append({"id": cid, "category": "place_conflict", "user": user,
                       "candidate": cand, "final": final,
                       "verdict": "caught" if ok else "missed", "ok": ok})
    results.extend(c_rows)

    # ---------- D. 精确余额/报账 ----------
    number_cases = [
        ("D1", "你在干嘛呢？", "就是985，我卡里还有4072，你说买不买？"),
        ("D2", "在干嘛呢？", "老公，这件开衫好看吧，花了649元。"),
        ("D3", "你等我多久了？", "等了十五分钟了，衣服都放回去了。"),
        ("D4", "卡里还有多少钱？", "卡里还有4072元。"),
    ]
    d_rows = []
    for cid, user, cand in number_cases:
        out = await run_chat("rest_home", "20:10", user, cand)
        final = out["final"]
        precise = bool(PRECISE_RE.search(final))
        d_rows.append({"id": cid, "category": "precise_number", "user": user,
                       "candidate": cand, "final": final,
                       "verdict": "missed" if precise else ("caught" if final else "caught_drop"),
                       "ok": not precise})
    results.extend(d_rows)

    # ---------- E. 重复追问/重复回复 ----------
    e_rows = []
    history = [("user", "你在干嘛呢？"), ("bot", "我在家歇着呢，刚追完一集剧。")]
    out = await run_chat("rest_home", "21:05", "你在干嘛呢？", "我在家歇着呢，刚追完一集剧。", history=history)
    final = out["final"]
    e_rows.append({"id": "E1", "category": "repeat", "candidate": "同一句回复",
                   "final": final,
                   "verdict": "missed" if final == "我在家歇着呢，刚追完一集剧。" else "caught",
                   "ok": final != "我在家歇着呢，刚追完一集剧。"})
    history2 = [("user", "你在哪呢？"), ("bot", "在家呢。"), ("user", "到底在哪？"), ("bot", "说了在家呀。")]
    out = await run_chat("rest_home", "21:10", "你是不是骗我？", "我在家呢，没骗你。", history=history2)
    e_rows.append({"id": "E2", "category": "repeat_question", "candidate": "被追问后重复同一回答",
                   "final": out["final"],
                   "verdict": "pass" if out["final"] else "drop",
                   "ok": bool(out["final"])})
    results.extend(e_rows)

    # ---------- F. 接不住分享 ----------
    f_rows = []
    share_history = [("bot", "我刚刷到一个视频，一只橘猫把自己塞进纸箱，只露出个屁股。")]
    for cid, user, cand in [
        ("F1", "然后呢？", "你在干嘛呀。"),
        ("F2", "然后呢", "嗯嗯，好呀。"),
        ("F3", "哈哈哈哈", "笑什么呢？"),
        ("F4", "在哪看到的？", "什么视频？"),
        ("F5", "什么视频", "就是刚那个，你自己看。"),
    ]:
        plugin = E.build_plugin(blocks_of("rest_home"), kv={}, health={"status": "健康", "sick": False})
        plugin._recent_rows = [{"role": role, "content": text} for role, text in share_history]
        with E.frozen_clock("21:20"):
            history_text = " ".join(row["content"] for row in plugin._recent(UMO, 6))
            after = pipeline.apply_chat_safety(cand, plugin, UMO, user,
                                               history_text=history_text)
            issues_first = critic.issues(user, cand)
            # B3：复检没接住 → 走生产同一条 repair（不再发固定安全句）。SimLLM 桩按
            # 提示词里的「他刚说的是 / 最近聊的」续接她刚分享的内容。
            if issues_first:
                safe = await pipeline.repair_reply(
                    cand, "", plugin, UMO, user, history=history_text)
            else:
                safe = after
        continues = any(w in safe for w in ("然后", "后来", "接着", "纸箱", "猫", "视频", "刚"))
        f_rows.append({"id": cid, "category": "share_continuation", "user": user,
                       "candidate": cand, "after_chat_safety": after,
                       "after_critic": safe, "critic_issues": issues_first,
                       "verdict": "caught" if continues else "missed", "ok": bool(continues)})
    results.extend(f_rows)

    # ---------- G. 系统话术 ----------
    g_rows = []
    sys_cases = [
        ("G1", "【日记质检】昨天有 4 条日记对不上：待重写 4 条；已标记重写，详情在面板。"),
        ("G2", "接口超时了，等会儿再试，后台正在重试。"),
        ("G3", "日志里报错了，我看看配置。"),
        ("G4", "这软件今天跟我较劲，一直报错。"),
        ("G5", "我刚打了两行代码，等下说。", "pass"),
        ("G6", "服务器卡住了，图发不出来。"),
    ]
    for case in sys_cases:
        cid, cand = case[0], case[1]
        expect = case[2] if len(case) > 2 else "caught"
        out = await run_chat("rest_home", "20:20", "在干嘛呢？", cand)
        final = out["final"]
        remains = [w for w in SYS_WORDS if w in final]
        if expect == "pass":
            verdict = "pass" if final == cand else "damaged"
            ok = final == cand
        else:
            verdict = "missed" if remains else ("caught" if final else "caught_drop")
            ok = not remains
        g_rows.append({"id": cid, "category": "system_talk", "candidate": cand,
                       "final": final, "expect": expect, "verdict": verdict, "ok": ok})
    results.extend(g_rows)

    # ---------- 汇总 ----------
    def summarize(rows):
        total = len(rows)
        ok = sum(1 for row in rows if row["ok"])
        return {"total": total, "ok": ok, "rate": round(100.0 * ok / total, 1) if total else 0.0}

    by_cat = {}
    for row in results:
        by_cat.setdefault(row["category"], []).append(row)
    report = {
        "meta": {"note": "只读对抗检查：生产守卫链（sim_env 隔离桩）的未见过变体",
                 "umo": UMO, "guard_files": ["amo_addressee_guard.py", "amo_persona_guard.py",
                                             "amo_state_consistency.py", "amo_reply_pipeline.py",
                                             "amo_grounding_critic.py", "amo_shared_text.py"]},
        "summary": {cat: summarize(rows) for cat, rows in by_cat.items()},
        "overall": summarize(results),
        "results": results,
    }
    with open(os.path.join(HERE, "adversarial.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    lines = ["overall %s" % json.dumps(report["overall"], ensure_ascii=False),
             "by_category " + json.dumps(report["summary"], ensure_ascii=False)]
    for row in results:
        lines.append("[%s] %-18s %-6s ok=%s\n  in : %s\n  out: %s\n  reason: %s" % (
            row["id"], row["category"], row.get("verdict"), row["ok"],
            row.get("text") or row.get("candidate"), 
            row.get("after") if "after" in row else (row.get("final") or row.get("after_critic")),
            row.get("reason") or ""))
    open(os.path.join(HERE, "adversarial.txt"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines[:4]))
    print("wrote adversarial.json / adversarial.txt")


if __name__ == "__main__":
    asyncio.run(main())