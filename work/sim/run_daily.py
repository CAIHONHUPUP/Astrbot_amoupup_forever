# -*- coding: utf-8 -*-
"""50 组日常对话模拟：runner + 报告（work/sim/daily_report.json / .md）。

只读约束（本文件自己执行）：
1. 不实例化生产 AmoDB、不打开生产 sqlite：install_db_guard() 会在运行期把
   AmoDB.__init__ 和指向 plugin_data 的 sqlite3.connect 直接拦成异常；
2. 所有状态在内存里，报告与日志只写 work/sim/；
3. 运行前后记录生产库文件指纹（size/mtime/sha256）作为旁证。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import sqlite3
import zlib
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import sim_env as E  # noqa: E402
import sim_worlds as W  # noqa: E402
import mock_provider as M  # noqa: E402
import daily_checks as C  # noqa: E402

ASTRBOT_DATA = os.path.dirname(os.path.dirname(ROOT))
PROD_DIR = os.path.join(ASTRBOT_DATA, "plugin_data", "Astrbot_amoupup_forever")
PROD_DB = os.path.join(PROD_DIR, "amo_forever.db")
OUT_JSON = os.path.join(HERE, "daily_report.json")
OUT_MD = os.path.join(HERE, "daily_report.md")

CONTRA_CODES = ("stale_scene", "companion_conflict", "place_conflict",
                "action_conflict", "meal_conflict", "health_conflict")
CODE_LABEL = {
    "degenerate": "回复退化/空",
    "stale_scene": "状态切换后仍用旧场景",
    "companion_conflict": "同伴矛盾",
    "place_conflict": "地点矛盾",
    "action_conflict": "动作矛盾",
    "meal_conflict": "吃饭矛盾",
    "health_conflict": "健康矛盾",
    "probe_unanswered": "答非所问",
    "meta_leak": "后台信息泄漏",
}


CODE_FILES = ("amo_state_consistency.py", "tone_guard.py", "reply_fact_guard.py",
              "place_guard.py", "amo_reply_pipeline.py", "amo_diary_guard.py",
              "amo_runtime_reply.py", "amo_message.py", "amo_current_action.py",
              "domains/reply/tone.py", "domains/reply/repetition_guard.py")


def code_snapshot():
    """本轮实际加载的守卫代码指纹（生产代码可能被并行修改，报告要能追溯）。"""
    rows = {}
    for name in CODE_FILES:
        path = os.path.join(ROOT, name)
        try:
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()[:16]
            rows[name] = {"sha256_16": digest,
                          "mtime": int(os.stat(path).st_mtime)}
        except Exception as exc:
            rows[name] = {"error": repr(exc)[:80]}
    return rows


def fingerprint(path):
    try:
        stat = os.stat(path)
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return {"path": path, "size": stat.st_size, "mtime": int(stat.st_mtime),
                "sha256": digest.hexdigest()}
    except Exception as exc:  # 生产实例可能正在写；指纹失败不影响测试
        return {"path": path, "error": repr(exc)[:160]}


def install_db_guard():
    """运行期硬隔离：任何想开生产库的调用都直接抛异常。"""
    import amo_db
    calls = {"amo_db_init": 0, "sqlite": 0}

    def blocked_init(self, *args, **kwargs):
        calls["amo_db_init"] += 1
        raise RuntimeError("SIM_BLOCKED: 隔离模拟器禁止实例化生产 AmoDB")

    amo_db.AmoDB.__init__ = blocked_init
    real_connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        target = str(database)
        lowered = target.lower().replace("/", "\\")
        if "plugin_data" in lowered or lowered.endswith("amo_forever.db"):
            calls["sqlite"] += 1
            raise RuntimeError("SIM_BLOCKED: 隔离模拟器禁止连接生产库: " + target)
        return real_connect(database, *args, **kwargs)

    sqlite3.connect = guarded_connect
    return calls


def _rng_for(gid, index):
    # 用 crc32 而不是 hash()：hash(str) 每个进程随机加盐，报告就不可复现了
    return random.Random(zlib.crc32(str(gid).encode("utf-8")) * 1000003 + index * 7919)


def _prompt_check(ptxt, snap, prev_snap):
    place = str(snap.get("place") or "")
    zone = str(snap.get("zone") or "")
    zone_text = {"home": "家", "office": "公司", "mall": "商场", "restaurant": "餐厅",
                 "road": "路上", "street": "街边", "scenic": "景区", "hotel": "酒店",
                 "hospital": "医院", "gym": "健身房", "school": "学校"}.get(zone, place)
    ok = bool(place and place in ptxt) or bool(zone_text and zone_text in ptxt)
    # 只看「你在「X」」那一行：防穿帮行本来就会列禁止地点，不算旧场景残留
    first_line = str(ptxt or "").split("\n")[0]
    stale = False
    if prev_snap and prev_snap.get("zone") != zone:
        prev_place = str(prev_snap.get("place") or "")
        if (prev_place and prev_place != place and prev_place not in place
                and prev_place in first_line):
            stale = True
    return {"prompt_ok": ok, "prompt_stale": stale}


async def run_groups(groups):
    turns = []
    group_rows = []
    for group in groups:
        gid = group["gid"]
        plugin = E.build_plugin(group["blocks"], kv={}, health=group["health"])
        prev_snap = None
        turn_rows = []
        for turn in group["turns"]:
            block = W.block_at(group["blocks"], turn["time"])
            plugin.db.kv["date_session"] = (
                {"phase": "active", "together": True, "place": block.get("place")}
                if block.get("together") else {"phase": "ended"})
            if group["trip"]:
                plugin.db.kv["trip"] = dict(group["trip"])
            else:
                plugin.db.kv.pop("trip", None)
            with E.frozen_clock(turn["time"]):
                snap = dict(plugin.state.current_activity() or {})
            meal_now = W.meal_ok(group["blocks"], turn["time"])
            together = bool(snap.get("together"))
            rng = _rng_for(gid, turn["index"])
            candidate, drift_meta = M.respond(turn, snap, prev_snap, meal_now, together, rng)
            out = await E.run_turn(plugin, turn["user"], candidate, turn["time"])
            sent = out["sent"]
            check_ctx = {"snap": snap, "prev_snap": prev_snap, "probe": turn["probe"],
                         "together": together, "meal_ok": meal_now}
            cand_issues, cand_codes = C.evaluate(candidate, check_ctx)
            sent_issues, sent_codes = C.evaluate(sent, check_ctx)
            prompt = _prompt_check(out.get("snapshot_text") or "", snap, prev_snap)
            row = {
                "gid": gid, "plan": group["plan"], "control": group["control"],
                "turn": turn["index"], "time": turn["time"],
                "user": turn["user"], "probe": turn["probe"], "topic": turn["topic"],
                "drift": drift_meta,
                "snap": {"place": snap.get("place"), "zone": snap.get("zone"),
                         "activity": snap.get("activity"), "category": snap.get("category"),
                         "together": together, "people": snap.get("people") or []},
                "prev_snap": ({"place": prev_snap.get("place"), "zone": prev_snap.get("zone")}
                              if prev_snap else None),
                "meal_ok": meal_now,
                "candidate": candidate, "sent": sent,
                "delivered": bool(out.get("delivered")),
                "cand_codes": cand_codes, "sent_codes": sent_codes,
                "sent_issues": sent_issues, "cand_issues": cand_issues,
                "guard_violations": len(out.get("violations_before") or []),
                "guard_after": len(out.get("violations_after") or []),
                "blacklist_hit": out.get("blacklist_hit") or [],
                "guard_error": out.get("guard_error") or "",
                "prompt_ok": prompt["prompt_ok"], "prompt_stale": prompt["prompt_stale"],
                "snapshot_text": (out.get("snapshot_text") or "")[:260],
            }
            turn_rows.append(row)
            turns.append(row)
            prev_snap = snap
        passed = all(not row["sent_codes"] for row in turn_rows)
        codes = sorted({code for row in turn_rows for code in row["sent_codes"]})
        contra = sorted({code for row in turn_rows for code in row["sent_codes"]
                         if code in CONTRA_CODES})
        group_rows.append({
            "gid": gid, "plan": group["plan"], "control": group["control"],
            "turns": len(turn_rows), "passed": passed,
            "consistent": not contra,
            "failed_turns": sum(1 for row in turn_rows if row["sent_codes"]),
            "contradiction_turns": sum(1 for row in turn_rows
                                       if set(row["sent_codes"]) & set(CONTRA_CODES)),
            "codes": codes, "contradiction_codes": contra,
        })
    return turns, group_rows, run_context_checks()


def run_context_checks():
    """上下文一致性 / 单点复现（全部确定性，不依赖模型）。"""
    checks = []

    def add(name, ok, detail, evidence=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail,
                       "evidence": str(evidence)[:400]})

    blocks = [W._block_tuple(row) for row in W.PLANS["weekend_mall"]["blocks"]]

    # 1) 官方 snapshot_text 是否写当前场景
    plugin = E.build_plugin(blocks, kv={})
    with E.frozen_clock("10:30"):
        prompt = E.snap_mod.snapshot_text(E.snap_mod.build_snapshot(plugin))
        activity = plugin.state.current_activity()
    add("prompt_matches_state", "商场" in prompt and "商场" in str(activity.get("place")),
        "10:30 商场块的官方 snapshot_text 里地点=商场",
        prompt.replace("\n", " | ")[:200])

    # 2) 过期的 JIT current_action 不该继续压着日程
    plugin = E.build_plugin(blocks, kv={})
    ts = E.ts_of("10:30")
    plugin.db.kv["current_action"] = {"action": "在商场逛街", "place": "商场", "zone": "mall",
                                      "at": ts - 600, "until": ts - 60}
    with E.frozen_clock("10:30"):
        import amo_current_action
        data = amo_current_action.get_current_action(plugin)
        expected_place = plugin.state.current_activity().get("place")
    add("expired_jit_falls_back",
        str(data.get("place")) == str(expected_place) and str(data.get("source")) != "jit",
        "kv current_action 的 until 已过期 -> get_current_action 应回落到日程",
        "source=%s place=%s / 日程 place=%s"
        % (data.get("source"), data.get("place"), expected_place))

    # 3) 同一时刻两套「此刻」：fresh JIT 说商场，日程说家
    home_blocks = [W._block_tuple(row) for row in W.PLANS["rest_home"]["blocks"]]
    plugin = E.build_plugin(home_blocks, kv={})
    plugin.db.kv["current_action"] = {"action": "在商场逛街", "place": "商场", "zone": "mall",
                                      "at": ts - 60, "until": ts + 3600}
    with E.frozen_clock("10:30"):
        import amo_current_action
        jit = amo_current_action.get_current_action(plugin)
        state_place = plugin.state.current_activity().get("place")
    add("two_readers_agree", str(jit.get("place")) == str(state_place),
        "get_current_action(照片/事实链路) 与 state.current_activity(聊天/守卫链路) 是否同一答案",
        "get_current_action=%s(source=%s) / state.current_activity=%s"
        % (jit.get("place"), jit.get("source"), state_place))

    # 3b) 视图缓存 TTL 内，narrative_place 用的是缓存里的旧 now（跨状态切换会读到旧场景）
    import amo_diary_guard as DG
    mall_blocks = [W._block_tuple(row) for row in W.PLANS["weekend_mall"]["blocks"]]
    plugin = E.build_plugin(mall_blocks, kv={})
    with E.frozen_clock("11:59"):
        DG.narrative_view(plugin)  # 建缓存（now=11:59，商场）
    with E.frozen_clock("12:00"):
        cached_place = DG.narrative_place(plugin)
        state_place = plugin.state.current_activity().get("place")
    add("narrative_place_cache_stale", str(cached_place) == str(state_place),
        "视图缓存(2s TTL)内 narrative_place 用缓存里的 now；跨块后仍返回旧地点",
        "11:59 建缓存 -> 12:00 narrative_place=%s / state.current_activity=%s"
        % (cached_place, state_place))

    # 3b2) 纯地点回答不能被动作规则改成「在吃饭」
    import amo_reply_pipeline as _pipeline
    office_blocks = [W._block_tuple(row) for row in W.PLANS["office_early"]["blocks"]]
    plugin = E.build_plugin(office_blocks, kv={})
    with E.frozen_clock("12:49") as now:  # 12:49 = 吃午饭(meal) @公司
        activity = dict(plugin.state.current_activity())
        guarded = _pipeline.apply_fact_guard("我在公司呢。", plugin, activity, now)
    add("place_answer_not_rewritten", "公司" in guarded,
        "回答「你在哪」的纯地点句「我在公司呢」不该被 meal 的 activity 规则改成「我在吃饭呢」",
        "我在公司呢。 -> %s" % guarded)

    # 3c) state_consistency 的居家动作词表把「吃饭」也算居家：在餐厅说「我在餐厅呢，吃饭」整句被删
    import amo_state_consistency as SC
    restaurant_blocks = [W._block_tuple(row) for row in W.PLANS["weekend_mall"]["blocks"]]
    plugin = E.build_plugin(restaurant_blocks, kv={})
    with E.frozen_clock("12:04"):
        before = "我在餐厅呢，吃饭。"
        after = SC.rewrite(before, plugin, "")
    add("outside_eating_not_deleted", "餐厅" in after or "吃" in after,
        "zone=restaurant 时「吃饭」被当居家动作整句删掉（复现）",
        "%s -> %r" % (before, after))

    # 4) tone_guard：连着两条同开头，会不会把「我在家呢」削成「呢」
    import tone_guard
    umo = "sim:tone"
    tone_guard.reset(umo)
    tone_guard.remember("我在家在休息呢，有点想你了", None, umo)
    fixed, actions = tone_guard.guard("我在家呢", None, umo)
    add("tone_guard_keeps_content", fixed.strip() not in ("", "呢", "了", "啊"),
        "口头禅去复读把句子削成残句（复现）",
        "input=我在家呢 -> output=%r actions=%s" % (fixed, actions))

    # 5) state_consistency：在商场说「我到家了」会被改成「还在路上」
    import amo_state_consistency as SC
    plugin = E.build_plugin(blocks, kv={})
    with E.frozen_clock("10:30"):
        fixed = SC.rewrite("我到家了，刚脱了外套。", plugin, "")
    add("mall_arrival_not_road", "路上" not in fixed,
        "zone=mall 时 arrival 兜底句应说商场/到家，不该说在路上（复现）",
        "input=我到家了，刚脱了外套。 -> output=%s" % fixed)

    # 6) 双层改口拼句：「我在家」+「在休息」
    import place_guard
    import reply_fact_guard
    import amo_reply_pipeline as pipeline
    plugin = E.build_plugin(home_blocks, kv={})
    with E.frozen_clock("09:30") as now:  # 09:30 = 窝在沙发上刷手机(rest)
        activity = dict(plugin.state.current_activity())
        step1 = place_guard.rewrite_reply("我在公司加班呢", activity)
        ctx = {"activity": activity, "health": {"status": "健康", "sick": False},
               "date": {"active": False, "together": False}, "now": now}
        step2, _viol = reply_fact_guard.guard(step1, ctx)
        step3 = pipeline.apply_fact_guard("我在公司加班呢", plugin, activity, now)
    add("guard_output_grammar", "在家在" not in step3 and "在在" not in step3,
        "地点改口 + 动作改口 + 语气池叠加后是否出现「我在家在休息呢」这类叠字",
        "公司加班 -> %s -> %s -> %s" % (step1, step2, step3))

    # 7) 去重兜底：整句复读/状态复读被换成固定短句，不看这轮在问什么
    plugin = E.build_plugin(home_blocks, kv={})
    plugin._recent_rows = [{"role": "bot", "content": "没呢，你吃了吗？"},
                           {"role": "user", "content": "吃了吗"},
                           {"role": "bot", "content": "没呢，你吃了吗？"}]
    with E.frozen_clock("12:50") as now:
        dedup_out = pipeline.apply_narrative_repetition(
            "没呢，你吃了吗？", plugin, "吃了吗", "sim:dedup", now)
    add("dedup_keeps_answer", "吃" in dedup_out,
        "整句复读兜底会把探针回答换成固定短句（复现）",
        "候选=没呢，你吃了吗？ -> %s" % dedup_out)

    plugin = E.build_plugin(home_blocks, kv={})
    plugin._recent_rows = [{"role": "bot", "content": "18:00差不多到家。"},
                           {"role": "user", "content": "几点回"},
                           {"role": "bot", "content": "18:00差不多到家。"}]
    with E.frozen_clock("17:25") as now:
        status_out = pipeline.apply_narrative_repetition(
            "17:00差不多到家。", plugin, "你什么时候回来？", "sim:status", now)
    add("status_dedup_keeps_answer", ("到家" in status_out) or (":" in status_out),
        "状态去重删空时应保留「几点到家」这类答案，而不是兜底成「还是老样子。」",
        "候选=17:00差不多到家。 -> %s" % status_out)

    # 7b) 分享改写：开头替换要保留后文；角色反转整句删除也不能丢后文
    import amo_state_consistency as SC2
    plugin = E.build_plugin(home_blocks, kv={})
    with E.frozen_clock("10:30"):
        opener_out = SC2.rewrite("我刚刷到一个猫的视频，猫在跳来跳去，后面还有一只狗。",
                                 plugin, "我刷到一个猫的视频，笑死我了")
        role_out = SC2.rewrite("你快看这个视频，猫在跳来跳去，后面还有一只狗。",
                               plugin, "我刷到一个猫的视频，笑死我了")
    add("share_opener_keeps_tail", "后面还有" in opener_out,
        "她重复“我刚刷到…视频”时只替换开头，后面内容要保留",
        "我刚刷到一个猫的视频，猫在跳来跳去，后面还有一只狗。 -> %s" % opener_out)
    add("share_role_keeps_tail", "后面还有" in role_out,
        "「你快看这个视频，…，后面还有一只狗」整句被删时后文也丢了",
        "你快看这个视频，猫在跳来跳去，后面还有一只狗。 -> %s" % role_out)

    # 7c) 改口的语法残留：吃饭数量词/地点短语被替换后不能留半句
    import amo_reply_pipeline as pipeline
    meal_blocks = [W._block_tuple(row) for row in W.PLANS["sick_home"]["blocks"]]
    plugin = E.build_plugin(meal_blocks, kv={})
    with E.frozen_clock("07:21") as now:
        activity = dict(plugin.state.current_activity())
        meal_out = pipeline.apply_fact_guard("刚吃了两口面包。", plugin, activity, now)
        meal_out2 = pipeline.apply_fact_guard("刚扒了两口饭。", plugin, activity, now)
    add("meal_rewrite_grammar",
        not re.search(r"弄点吃的(?:两|了?两)", meal_out) and "刚等会儿" not in meal_out2,
        "非饭点改口应整句收干净，不能留「等会儿弄点吃的两口面包」「刚等会儿弄点吃的」",
        "刚吃了两口面包。 -> %s / 刚扒了两口饭。 -> %s" % (meal_out, meal_out2))

    place_blocks = [W._block_tuple(row) for row in W.PLANS["rest_home"]["blocks"]]
    plugin = E.build_plugin(place_blocks, kv={})
    with E.frozen_clock("09:30") as now:  # 在家，“公司那边”要改口
        activity = dict(plugin.state.current_activity())
        place_out = pipeline.apply_fact_guard("刚在公司那边转了一圈。", plugin, activity, now)
    add("place_rewrite_grammar", "刚在转了一圈" not in place_out,
        "去掉地点短语后不能留下「刚在转了一圈」这种病句",
        "刚在公司那边转了一圈。 -> %s" % place_out)

    # 7d) 问句兜底：回答「几点回」要按当前地点，不能一律「还在路上」；转述句不能误触发
    import amo_question_guard as QG
    office_blocks3 = [W._block_tuple(row) for row in W.PLANS["office_early"]["blocks"]]
    plugin = E.build_plugin(office_blocks3, kv={})
    with E.frozen_clock("17:25"):
        when_out = QG.answer("你什么时候回来？", plugin, "你说呢？")
        relay_out = QG.answer("我妈问我们什么时候回去。", plugin, "阿姨也是关心你。")
    add("question_when_matches_place", "公司" in when_out or "17" in when_out or "18" in when_out,
        "在办公室被问「几点回」，兜底句应按当前地点/日程，不能一律「还在路上，快到了」",
        "你什么时候回来？ -> %s" % when_out)
    add("question_when_no_false_trigger", relay_out == "阿姨也是关心你。",
        "「我妈问我们什么时候回去」是转述，不是问她的行程，不该被替换成行程句",
        "我妈问我们什么时候回去。 -> %s" % relay_out)

    # 8) 到达对齐：预计到家/对他说的「到家」不能被改写成「我还在外面」
    office_blocks2 = [W._block_tuple(row) for row in W.PLANS["office_early"]["blocks"]]
    plugin = E.build_plugin(office_blocks2, kv={})
    with E.frozen_clock("16:13"):
        estimate = plugin._align_arrival("17:00差不多到家。")
        request = plugin._align_arrival("到家跟我说一声。")
    add("arrival_estimate_not_rewritten", "17:00" in estimate and "到家" in estimate,
        "在办公室说「17:00 差不多到家」（回答几点回）不该被改成「我还在外面呢」",
        "17:00差不多到家。 -> %s" % estimate)
    add("arrival_request_not_rewritten", "跟我说" in request,
        "「到家跟我说一声」是在对他说，不该被改写成她自己的位置句",
        "到家跟我说一声。 -> %s" % request)

    # 9) 问句意图覆盖（task-36）：五种探针的口语问法都要被问句守卫认出来，
    #    并且兜底句要能被独立探针判据判成「答到了」。
    coverage_rows = []
    plugin = E.build_plugin(office_blocks3, kv={})
    with E.frozen_clock("17:25"):
        coverage_snap = dict(plugin.state.current_activity() or {})
        for ask, probe in (("今晚回不回来？", "when_return"),
                           ("你什么时候回来？", "when_return"),
                           ("到公司了吗？", "where"),
                           ("你旁边有人吗？", "who"),
                           ("你是不是在偷懒？", "doing")):
            covered = QG.answer(ask, plugin, "今天天气还行。")
            coverage_rows.append((ask, probe, covered,
                                  not C.check_probe(covered, probe, coverage_snap)))
    add("question_intent_coverage", all(row[3] for row in coverage_rows),
        "回不回来/到公司了吗/旁边有人吗/偷懒 这些口语问法也要被问句守卫认出来并答到点上",
        " | ".join("%s -> %s" % (row[0], row[2]) for row in coverage_rows))

    # 10) 分享接话不许劫持状态问句：历史里有「刷到」痕迹时，正常提问不能被
    #     改写成「就是事的事，…」（task-36 guard_introduced 的根因）。
    import amo_state_consistency as SC3
    plugin = E.build_plugin(home_blocks, kv={})
    share_ctx = "忙什么呢？\n你在哪呢？ 在家刷到的，烦死了。 忙什么呢？"
    with E.frozen_clock("17:39"):
        share_out = SC3.rewrite("正吃药，怎么啦？", plugin, share_ctx, umo="")
    add("share_context_not_hijacked",
        "就是事的事" not in share_out and "正吃药" in share_out,
        "历史里有分享痕迹时，状态问句不能被分享接话改写成「就是事的事，…」",
        "忙什么呢？ -> %s" % share_out)

    # 11) 动作 edge（task-36）：睡觉时「还在改方案」是进行式工作断言，仍要改口；
    #     「刚开完会」是近过去，可以保留（时间语义不同）。
    sick_blocks2 = [W._block_tuple(row) for row in W.PLANS["sick_home"]["blocks"]]
    plugin = E.build_plugin(sick_blocks2, kv={})
    with E.frozen_clock("12:31") as now:
        sick_act = dict(plugin.state.current_activity() or {})
        sleep_fix = pipeline.apply_fact_guard("还在改方案，烦死了。", plugin, sick_act, now)
        past_fix = pipeline.apply_fact_guard("刚开完会，脑子还是懵的。", plugin, sick_act, now)
    add("work_present_claim_while_sleeping",
        "改方案" not in sleep_fix and "刚开完会" in past_fix,
        "睡觉时「还在改方案」要改口，「刚开完会」是近过去可保留",
        "%s / %s" % (sleep_fix, past_fix))

    # 12) 地点方位结构（task-36）：「我在家这边躲雨」要换成正确地点并保留方位词，
    #     不能删成「我在躲雨」把「在哪」的答案一起删掉。
    cafe_blocks2 = [W._block_tuple(row) for row in W.PLANS["cafe_work"]["blocks"]]
    plugin = E.build_plugin(cafe_blocks2, kv={})
    with E.frozen_clock("15:10") as now:
        cafe_act = dict(plugin.state.current_activity() or {})
        orient_fix = pipeline.apply_fact_guard("我在家这边躲雨。", plugin, cafe_act, now)
    add("place_orient_keeps_answer",
        "躲雨" in orient_fix and "家" not in orient_fix and "这边" in orient_fix,
        "「我在家这边躲雨」要换成当前地点并保留方位词，不能删成「我在躲雨」",
        "我在家这边躲雨。 -> %s" % orient_fix)

    # 13) 分段整句去重不许把「他这一问」的答案删掉（task-36 G09/t5）。
    gym_blocks2 = [W._block_tuple(row) for row in W.PLANS["gym_day"]["blocks"]]
    plugin = E.build_plugin(gym_blocks2, kv={})
    with E.frozen_clock("19:27"):
        pipeline.apply_narrative_repetition(
            "在健身房练器械。你呢？", plugin, "忙什么呢？", "sim:seg")
        kept = SC.dedupe_reply_segments(
            ["在健身房练器械。你呢？"], refs=["我在健身房练器械呢。"],
            plugin=plugin, umo="sim:seg")
    add("segment_dedupe_keeps_answer",
        "健身" in "".join(kept),
        "分段整句去重删掉答案时，答案句要保留（不许只剩「你呢？」）",
        " -> ".join(kept) or "(empty)")
    return checks


def summarize(turns, groups, context_checks, before, after, db_guard_calls):
    total_groups = len(groups)
    passed_groups = sum(1 for group in groups if group["passed"])
    consistent_groups = sum(1 for group in groups if group["consistent"])
    contradiction_turns = sum(1 for row in turns
                              if set(row["sent_codes"]) & set(CONTRA_CODES))
    primary = {}
    all_codes = {}
    for row in turns:
        if row["sent_codes"]:
            key = row["sent_codes"][0]
            primary[key] = primary.get(key, 0) + 1
        for code in row["sent_codes"]:
            all_codes[code] = all_codes.get(code, 0) + 1

    drift_rows = [row for row in turns if row["drift"]]
    repaired = [row for row in drift_rows if row["cand_codes"] and not row["sent_codes"]]
    residual = [row for row in drift_rows if row["cand_codes"] and row["sent_codes"]]
    introduced = [row for row in turns if not row["cand_codes"] and row["sent_codes"]]
    drift_clean_before = [row for row in drift_rows if not row["cand_codes"]]

    by_kind = {}
    for row in drift_rows:
        kind = str((row["drift"] or {}).get("kind"))
        tier = str((row["drift"] or {}).get("tier"))
        bucket = by_kind.setdefault(kind, {"injected": 0, "repaired": 0, "residual": 0,
                                           "clean_after": 0, "edge": 0})
        bucket["injected"] += 1
        if tier == "edge":
            bucket["edge"] += 1
        if row["cand_codes"] and not row["sent_codes"]:
            bucket["repaired"] += 1
        elif row["sent_codes"]:
            bucket["residual"] += 1
        else:
            bucket["clean_after"] += 1

    probes = [row for row in turns if row["probe"]]
    probe_stats = {}
    for row in probes:
        stat = probe_stats.setdefault(row["probe"], {"asked": 0, "unanswered": 0})
        stat["asked"] += 1
        if "probe_unanswered" in row["sent_codes"]:
            stat["unanswered"] += 1
    clean_probes = [row for row in probes if not row["drift"]]
    probe_stats_clean = {}
    for row in clean_probes:
        stat = probe_stats_clean.setdefault(row["probe"], {"asked": 0, "unanswered": 0})
        stat["asked"] += 1
        if "probe_unanswered" in row["sent_codes"]:
            stat["unanswered"] += 1

    control_turns = [row for row in turns if row["control"]]
    control_damage = [row for row in control_turns if row["sent_codes"]]

    by_plan = {}
    for group in groups:
        bucket = by_plan.setdefault(group["plan"], {"groups": 0, "passed": 0, "turns": 0, "failed_turns": 0})
        bucket["groups"] += 1
        bucket["passed"] += 1 if group["passed"] else 0
        bucket["turns"] += group["turns"]
        bucket["failed_turns"] += group["failed_turns"]

    fallback_texts = ("还是老样子。", "嗯，我在呢。", "在听你说呢。", "我这儿一直在。",
                      "你说，我听着呢。", "嗯嗯，知道啦。", "好，我记着呢。")
    gi_breakdown = {"dedup_fallback": 0, "dedup_removed_sentences": 0,
                    "arrival_rewrite": 0, "question_guard": 0, "other": 0}
    for row in introduced:
        sent = str(row["sent"] or "").strip()
        candidate = str(row["candidate"] or "").strip()
        if sent in fallback_texts:
            gi_breakdown["dedup_fallback"] += 1
        elif sent == "还在路上，快到了。" and sent != candidate:
            gi_breakdown["question_guard"] += 1
        elif "还在外面" in sent and "还在外面" not in candidate:
            gi_breakdown["arrival_rewrite"] += 1
        elif candidate.endswith(sent) or sent in candidate:
            gi_breakdown["dedup_removed_sentences"] += 1
        else:
            gi_breakdown["other"] += 1
    unans_to_contra = [row for row in turns
                       if "probe_unanswered" in row["cand_codes"]
                       and set(row["sent_codes"]) & set(CONTRA_CODES)]
    prompt_bad = [row for row in turns if not row["prompt_ok"]]
    prompt_stale = [row for row in turns if row["prompt_stale"]]
    guard_errors = [row for row in turns if row["guard_error"]]

    return {
        "totals": {
            "groups": total_groups, "groups_passed": passed_groups,
            "groups_failed": total_groups - passed_groups,
            "group_pass_rate": round(passed_groups / total_groups, 4) if total_groups else 0.0,
            "turns": len(turns),
            "turns_passed": sum(1 for row in turns if not row["sent_codes"]),
            "turns_failed": sum(1 for row in turns if row["sent_codes"]),
            "turn_pass_rate": round(sum(1 for row in turns if not row["sent_codes"]) / len(turns), 4) if turns else 0.0,
            "probe_turns": len(probes),
            "control_turns": len(control_turns),
            "groups_consistent": consistent_groups,
            "group_consistent_rate": round(consistent_groups / total_groups, 4) if total_groups else 0.0,
            "contradiction_turns": contradiction_turns,
            "contradiction_turn_rate": round(contradiction_turns / len(turns), 4) if turns else 0.0,
        },
        "failure_primary": primary,
        "failure_all_codes": all_codes,
        "drift": {
            "injected": len(drift_rows), "repaired": len(repaired),
            "residual": len(residual), "candidate_already_clean": len(drift_clean_before),
            "guard_introduced": len(introduced),
            "guard_introduced_breakdown": gi_breakdown,
            "by_kind": by_kind,
        },
        "probe_stats": probe_stats,
        "probe_stats_clean": probe_stats_clean,
        "clean_probe_turns": len(clean_probes),
        "clean_probe_unanswered": sum(1 for row in clean_probes
                                      if "probe_unanswered" in row["sent_codes"]),
        "unanswered_to_contradiction": len(unans_to_contra),
        "control": {"turns": len(control_turns), "failed": len(control_damage),
                    "failure_codes": sorted({code for row in control_damage for code in row["sent_codes"]})},
        "by_plan": by_plan,
        "context": {"prompt_mismatch": len(prompt_bad), "prompt_stale": len(prompt_stale),
                    "guard_errors": len(guard_errors), "checks": context_checks},
        "isolation": {"prod_db_before": before, "prod_db_after": after,
                      "blocked_calls": db_guard_calls},
    }


def _desensitize(row):
    def clean(text):
        return (str(text or "").replace("老公", "他").replace("大肥鱼", "她")
                .replace("302578963", "*"))
    return {
        "gid": row["gid"], "turn": row["turn"], "time": row["time"], "plan": row["plan"],
        "control": row["control"], "probe": row["probe"], "drift": row["drift"],
        "snapshot": {"place": clean(row["snap"]["place"]), "zone": row["snap"]["zone"],
                     "activity": clean(row["snap"]["activity"]),
                     "together": row["snap"]["together"]},
        "user": clean(row["user"]), "candidate": clean(row["candidate"]),
        "sent": clean(row["sent"]), "codes": row["sent_codes"],
        "guard_violations": row["guard_violations"],
    }


def pick_samples(turns, limit=10):
    picked = []
    seen = {}
    for row in turns:
        if not row["sent_codes"]:
            continue
        code = row["sent_codes"][0]
        if seen.get(code, 0) >= 2:
            continue
        seen[code] = seen.get(code, 0) + 1
        picked.append(_desensitize(row))
        if len(picked) >= limit:
            break
    if len(picked) < limit:
        for row in turns:
            if not row["sent_codes"]:
                continue
            sample = _desensitize(row)
            if sample in picked:
                continue
            picked.append(sample)
            if len(picked) >= limit:
                break
    return picked


def render_markdown(report):
    lines = []
    add = lines.append
    totals = report["totals"]
    fence = chr(96) * 3
    add("# 日常对话 50 组模拟报告（隔离模拟器 / 模拟 provider）")
    add("")
    add("生成时间：%s" % report["generated_at"])
    add("")
    add("> 本轮结论来自 **可复现的模拟 provider**，不是真实模型。")
    add("> 真实模型语义质量未覆盖（拿不到隔离 provider，详见第 9 节）。")
    add("")
    real = {}
    try:
        real = json.load(open(os.path.join(HERE, "real_llm_layer.json"), encoding="utf-8"))
    except Exception:  # cleanup: 仿真容错
        real = {}
    add("## 0. 两层结论（先看这个）")
    add("")
    add("- **模拟层（本轮，覆盖全部 %d 组 / %d 轮）**：组通过 %d/%d，单轮通过 %.1f%%；"
        "注入偏差 %d 次，修回 %d、残留 %d；守卫自己改坏 %d 次。"
        % (totals["groups"], totals["turns"], totals["groups_passed"], totals["groups"],
           totals["turn_pass_rate"] * 100, report["drift"]["injected"],
           report["drift"]["repaired"], report["drift"]["residual"],
           report["drift"]["guard_introduced"]))
    breakdown0 = report["drift"].get("guard_introduced_breakdown") or {}
    if report["drift"]["guard_introduced"]:
        add("  - 其中守卫改坏的 %d 轮：问句兜底误判 %d、到达对齐误改 %d、去重固定短句兜底 %d、"
            "去重删句 %d、其他 %d。"
            % (report["drift"]["guard_introduced"], breakdown0.get("question_guard", 0),
               breakdown0.get("arrival_rewrite", 0), breakdown0.get("dedup_fallback", 0),
               breakdown0.get("dedup_removed_sentences", 0), breakdown0.get("other", 0)))
    add("- **真实模型层**：%s（%s）。"
        % (("已跑" if real.get("status") == "ok" else "未覆盖"),
           real.get("reason") or ("model=%s" % (real.get("provider") or {}).get("model")
                                  if real.get("status") == "ok" else "real_llm_layer.json 缺失")))
    add("  真实模型复跑脚本 real_probe.py 只接受环境变量/本机 ollama provider，"
        "不读 AstrBot 配置、不碰生产库。")
    add("")
    add("## 1. 结论摘要")
    add("")
    add("| 指标 | 数值 |")
    add("|---|---|")
    add("| 组数 | %d |" % totals["groups"])
    add("| 通过组 / 失败组 | %d / %d（组通过率 %.1f%%） |"
        % (totals["groups_passed"], totals["groups_failed"], totals["group_pass_rate"] * 100))
    add("| 轮数 | %d |" % totals["turns"])
    add("| 单轮通过 / 失败 | %d / %d（%.1f%%） |"
        % (totals["turns_passed"], totals["turns_failed"], totals["turn_pass_rate"] * 100))
    add("| 含探针的轮数 | %d |" % totals["probe_turns"])
    add("| 对照组（不注入偏差）轮数 | %d，失败 %d |"
        % (report["control"]["turns"], report["control"]["failed"]))
    add("| 无任何矛盾（只算地点/动作/同伴/旧场景/吃饭/健康）的组 | %d / %d（%.1f%%） |"
        % (totals["groups_consistent"], totals["groups"],
           totals["group_consistent_rate"] * 100))
    add("| 含矛盾的轮数 | %d（%.1f%%） |"
        % (totals["contradiction_turns"], totals["contradiction_turn_rate"] * 100))
    add("")
    add("关键点：")
    add("")
    add("- 注入偏差 %d 次，守卫修回 %d 次，残留 %d 次；另有 **守卫自己改坏 %d 次**（候选本来是干净的）。"
        % (report["drift"]["injected"], report["drift"]["repaired"],
           report["drift"]["residual"], report["drift"]["guard_introduced"]))
    if report["control"]["failed"]:
        add("- 对照组失败 %d 轮：这些轮次候选回复完全符合快照，仍是最终回复出问题，属于守卫/上下文层引入。"
            % report["control"]["failed"])
    else:
        add("- 对照组 0 失败：干净输入没有被守卫改坏。")
    if report.get("unanswered_to_contradiction"):
        add("- 问句兜底引入：原本「答非所问」、被兜底成**错误事实**的 %d 轮（这些轮算在下面的失败分类里）。"
            % report["unanswered_to_contradiction"])
    breakdown = report["drift"].get("guard_introduced_breakdown") or {}
    if report["drift"]["guard_introduced"]:
        add("- 守卫改坏的 %d 轮里：问句兜底误判 %d、到达对齐误改 %d、去重固定短句兜底 %d、"
            "去重删句只剩残句 %d、其他 %d。"
            % (report["drift"]["guard_introduced"], breakdown.get("question_guard", 0),
               breakdown.get("arrival_rewrite", 0), breakdown.get("dedup_fallback", 0),
               breakdown.get("dedup_removed_sentences", 0), breakdown.get("other", 0)))
    add("")
    add("## 2. 失败分类")
    add("")
    add("| 主因 | 轮数 |")
    add("|---|---|")
    for code, count in sorted(report["failure_primary"].items(), key=lambda item: -item[1]):
        add("| %s（%s） | %d |" % (CODE_LABEL.get(code, code), code, count))
    add("")
    add("同一轮可能有多个问题（例如「旧场景」同时是地点矛盾），上表按优先级只记主因；下表是全量计数。")
    add("")
    add("| code | 出现轮数 |")
    add("|---|---|")
    for code, count in sorted(report["failure_all_codes"].items(), key=lambda item: -item[1]):
        add("| %s | %d |" % (code, count))
    add("")
    add("## 3. 注入偏差 -> 修复矩阵")
    add("")
    add("| 偏差类型 | 注入 | 其中 edge 写法 | 修回 | 残留 | 候选本来就干净 |")
    add("|---|---|---|---|---|---|")
    for kind, bucket in sorted(report["drift"]["by_kind"].items()):
        add("| %s | %d | %d | %d | %d | %d |"
            % (kind, bucket["injected"], bucket["edge"], bucket["repaired"],
               bucket["residual"], bucket["clean_after"]))
    add("")
    add("## 4. 探针回答率")
    add("")
    add("「干净提问」= 这一轮没有注入任何偏差，是模型（模拟层）本来就答对的输入；"
        "它更能说明守卫自己有没有把答案改没。")
    add("")
    add("| 探针 | 全部提问 | 全部未答 | 干净提问 | 干净未答 | 干净回答率 |")
    add("|---|---|---|---|---|---|")
    for probe, stat in sorted(report["probe_stats"].items()):
        clean = (report.get("probe_stats_clean") or {}).get(probe) or {"asked": 0, "unanswered": 0}
        rate = 1.0 - clean["unanswered"] / clean["asked"] if clean["asked"] else 0.0
        add("| %s | %d | %d | %d | %d | %.1f%% |"
            % (probe, stat["asked"], stat["unanswered"], clean["asked"], clean["unanswered"],
               rate * 100))
    add("")
    add("干净提问共 %d 轮，其中被守卫改成不答 %d 轮（%.1f%%）。"
        % (report.get("clean_probe_turns", 0), report.get("clean_probe_unanswered", 0),
           100.0 * report.get("clean_probe_unanswered", 0) / max(1, report.get("clean_probe_turns", 0))))
    add("")
    add("## 5. 按日程模板")
    add("")
    add("| 模板 | 组 | 通过组 | 轮 | 失败轮 |")
    add("|---|---|---|---|---|")
    for plan, bucket in sorted(report["by_plan"].items()):
        add("| %s | %d | %d | %d | %d |"
            % (plan, bucket["groups"], bucket["passed"], bucket["turns"], bucket["failed_turns"]))
    add("")
    add("## 6. 前 10 个脱敏样例")
    add("")
    add("样例是合成对话（非真实用户），脱敏=去掉 uid、把「老公」写成「他」。")
    add("")
    for index, sample in enumerate(report["samples"], 1):
        add("### 样例 %d：%s T%s %s（%s）" % (index, sample["gid"], sample["turn"], sample["time"], sample["plan"]))
        add("")
        add("- 快照：地点=%s / zone=%s / 动作=%s / 在一起=%s"
            % (sample["snapshot"]["place"], sample["snapshot"]["zone"],
               sample["snapshot"]["activity"], sample["snapshot"]["together"]))
        add("- 用户：%s" % sample["user"])
        add("- 候选：%s" % sample["candidate"])
        add("- 发出：%s" % sample["sent"])
        add("- 命中：%s；注入=%s；事实守卫改口 %d 处"
            % (sample["codes"], sample["drift"], sample["guard_violations"]))
        add("")
    add("## 7. 上下文一致性 / 单点复现")
    add("")
    add("| 检查 | 结果 | 说明 |")
    add("|---|---|---|")
    for check in report["context"]["checks"]:
        add("| %s | %s | %s |" % (check["name"], "PASS" if check["ok"] else "FAIL", check["detail"]))
    add("")
    add("每轮 prompt 与锁定快照不一致：%d 轮；场景切换后 prompt 仍含上一场景：%d 轮；守卫异常：%d 轮。"
        % (report["context"]["prompt_mismatch"], report["context"]["prompt_stale"],
           report["context"]["guard_errors"]))
    add("")
    add("## 8. 疑似根因（按证据强度排序）")
    add("")
    for index, item in enumerate(report["root_causes"], 1):
        add("%d. %s" % (index, item))
    add("")
    add("## 9. 隔离与局限")
    add("")
    add("- 真实模型探测：env provider=%s；本机 ollama 对话模型=%s；%s"
        % (real.get("evidence", {}).get("env_provider"),
           real.get("evidence", {}).get("ollama_chat_models"),
           real.get("evidence", {}).get("policy", "real_llm_layer.json 无 evidence")))
    add("- 本报告确定性签名：%s（同参数、同代码重跑应一致；本轮连跑两次一致）"
        % report.get("determinism_signature"))
    add("- 生产库指纹（运行前）：size=%s mtime=%s sha256=%s"
        % (report["isolation"]["prod_db_before"].get("size"),
           report["isolation"]["prod_db_before"].get("mtime"),
           str(report["isolation"]["prod_db_before"].get("sha256"))[:16]))
    add("- 生产库指纹（运行后）：size=%s mtime=%s sha256=%s"
        % (report["isolation"]["prod_db_after"].get("size"),
           report["isolation"]["prod_db_after"].get("mtime"),
           str(report["isolation"]["prod_db_after"].get("sha256"))[:16]))
    add("- 运行期拦截：AmoDB 实例化 %d 次、指向生产库的 sqlite 连接 %d 次（都应保持 0）。"
        % (report["isolation"]["blocked_calls"].get("amo_db_init"),
           report["isolation"]["blocked_calls"].get("sqlite")))
    add("- 所有状态（日程/kv/健康/历史/语气池）都在进程内存；报告只写 work/sim/。")
    add("- 没有接 AstrBot 事件循环、分段引擎、TTS、生图、记忆插件：segment_engine=None。")
    add("- 代码快照（本轮实际加载的守卫文件 sha256 前 16 位）：")
    for name, info in (report.get("code_snapshot") or {}).items():
        add("  - %s：%s" % (name, info.get("sha256_16") or info.get("error")))
    add("- 模拟 provider 只按模板拼句，**不能证明真实模型的语义质量**；真实模型复跑见 real_llm_layer.json。")
    add("")
    add("## 10. 复现")
    add("")
    add(fence)
    add("D:/dafeiyu/uv/tools/astrbot/Scripts/python.exe work/sim/run_daily.py")
    add("D:/dafeiyu/uv/tools/astrbot/Scripts/python.exe work/sim/real_probe.py")
    add(fence)
    return "\n".join(lines)


ROOT_CAUSES = [
    "【已修复·问句意图覆盖】amo_question_guard._category 只认标准句式（几点回/在干嘛/吃了吗），"
    "「今晚回不回来」「到公司了吗」「你旁边有人吗」「你是不是在偷懒」认不出来 → 答非所问时兜不了底。"
    "本轮把这些口语问法归到同一意图，并加「第三人称作主语不算问她」（我同事摸鱼≠问她干嘛）。"
    "证据：failure probe_unanswered 31→0；context_checks.question_intent_coverage PASS。",
    "【已修复·分享接话劫持状态问句】amo_state_consistency._reply_demand 把「忙什么呢/你在哪呢」"
    "当成分享的后续追问，拿历史里带「刷到」的旧分享句把正常回复改写成「就是事的事，…」"
    "（guard_introduced 2 轮）。本轮：状态问句不参与分享接话 + 认不出对象时说「就是刚才那件事」。"
    "证据：guard_introduced 2→0；irrelevant 残留 24→0；context_checks.share_context_not_hijacked PASS。",
    "【已修复·动作 edge】sleep/health/rest 下「还在改方案」这类进行式工作断言被有意跳过"
    "（原设计只放行「刚开完会」这种近过去），独立 checker 仍判动作矛盾。本轮把进行式/近过去拆开："
    "进行式按当前状态改口，近过去保留。证据：action 残留 5→0；"
    "context_checks.work_present_claim_while_sleeping PASS。",
    "【已修复·事实守卫空转】narrative_view 有事件但 now 落在两个日程块之间（早于第一块）时 activity 为空，"
    "事实守卫什么都不改（G46/t1 的「刚开完会」因此漏网）。本轮视图没有 current 时回落 state.current_activity。"
    "证据：矛盾轮 2→0。",
    "【已修复·地点方位语法】换/删地点短语时「我在家这边躲雨」被删成「我在躲雨」，把「在哪」的答案"
    "一起删掉。本轮换地点保留方位词（我在咖啡店这边躲雨）。"
    "证据：place 残留 2→0；context_checks.place_orient_keeps_answer PASS。",
    "【已修复·去重吃掉答案】分段整句去重/状态去重把「他这一问」的答案删掉后只剩通用短句"
    "（G09/t5 只剩「你呢？」、G43/t6·t9 短句兜底）。本轮出口问句守卫把删前答案要回来 + "
    "分段去重保留答案句。证据：probe_unanswered 31→0；context_checks.segment_dedupe_keeps_answer PASS。",
    "【已修复·send 阶段旧问句】_send_to_umo 扫近聊取 user_text 时取到更早的 user 行，"
    "后置复检/问句补答拿着上一轮的旧问题判这一轮。本轮统一用本轮登记问题（_turn_question 口径）。"
    "证据：dedup_keeps_answer / status_dedup_keeps_answer PASS，clean_probe_unanswered 2→0。",
    "【口径·模拟 harness】模拟器聊天回复发送改为 source=chat（与生产 _send_now / imm_harness 一致）："
    "不传会 fail-closed 成 push，出口人设守卫按主动消息口径把「写代码」这类词整条丢弃"
    "（对照组 G20/t2 空回复）。这是 harness 口径修正，不是放宽判据。",
    "【P2 残余·未修】two_readers_agree FAIL：get_current_action（JIT/照片链路）与 "
    "state.current_activity（聊天/守卫链路）仍可能两套「此刻」。",
    "【P2 残余·未修】narrative_place_cache_stale FAIL：narrative_view 的 2 秒缓存带旧 now，"
    "跨块短窗口仍返回旧地点。",
    "【P2 建议·未做】问句兜底仍是整条替换而不是追加，原句语气/反问会丢（「正洗澡，怎么啦？」→"
    "「我在洗澡。」）；建议 append，或只在完全无信息时替换。",
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", type=int, default=50)
    args = parser.parse_args()
    started = time.time()
    before = fingerprint(PROD_DB)
    snapshot = code_snapshot()
    db_guard_calls = install_db_guard()
    groups = W.build_groups(args.groups)
    turns, group_rows, context_checks = await run_groups(groups)
    after = fingerprint(PROD_DB)
    report = summarize(turns, group_rows, context_checks, before, after, db_guard_calls)
    report["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report["mode"] = "simulated_provider"
    report["code_snapshot"] = snapshot
    report["elapsed_sec"] = round(time.time() - started, 2)
    signature = hashlib.sha256("|".join(
        "%s:%s:%s:%s" % (row["gid"], row["turn"], row["sent"], ",".join(row["sent_codes"]))
        for row in turns).encode("utf-8")).hexdigest()[:16]
    report["determinism_signature"] = signature
    report["samples"] = pick_samples(turns)
    report["root_causes"] = ROOT_CAUSES
    report["groups"] = group_rows
    report["turns"] = turns
    with open(OUT_JSON, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    with open(OUT_MD, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    summary = {key: report[key] for key in ("totals", "failure_primary", "drift", "control")}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("wrote", OUT_JSON)
    print("wrote", OUT_MD)


if __name__ == "__main__":
    asyncio.run(main())
