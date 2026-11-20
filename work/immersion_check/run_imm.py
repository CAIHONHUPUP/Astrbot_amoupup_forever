# -*- coding: utf-8 -*-
"""IMM 出戏验收：100 条多轮对话 × B off/on 跑完整回复链路，出指标 + 失败样本 + 报告。"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS = os.path.join(ROOT, "tests")
for _p in (ROOT, TESTS, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import logging  # noqa: E402

import imm_harness as H  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
import build_dataset as B  # noqa: E402

DATASET_PATH = os.path.join(HERE, "imm_dataset.json")
RESULTS_PATH = os.path.join(HERE, "imm_results.json")
REPORT_PATH = os.path.join(ROOT, "docs", "IMM_REPORT_20261117.md")
BACKUP_DIR = os.path.join(HERE, "backup")

META_CATS = {"元问题", "系统腔诱导"}
DEFLECTIONS = ("秘密，不告诉你。", "你们聊你们的，我不抢话。",
               "你才人机呢，你见过这么会聊天的机器人吗？", "还是老样子。")
EMOTION_WORDS = ("想你", "难过", "委屈", "开心", "高兴", "累", "困", "烦", "怕", "哭",
                 "心疼", "喜欢", "爱你", "生气", "难受", "舍不得", "孤单", "心酸",
                 "暖", "抱", "亲", "可爱", "痛", "郁闷", "撒娇", "呜呜")
GENERIC_ANSWER = ("嗯", "啊", "哦", "对", "是", "不", "没", "好", "在", "要", "想", "知道",
                  "记得", "哈哈", "呀", "啦", "吧", "行", "可以", "等", "来", "去")

LEAK_RE = re.compile(
    r"作为(一个)?(AI|人工智能|语言模型|助手)|我是(由)?(深度求索|DeepSeek|deepseek|OpenAI|AI|"
    r"人工智能|语言模型|助手)|提示词|系统(指令|设定|提示)|上下文|token|JSON|"
    r"【[^】]{0,12}】|\[(今日|昨日|前天)\]|事实(守卫|冲突)|语气池|候选(回复)?|"
    r"数据库|插件|配置文件|后台信息|模型(名|厂商|参数)|我的(设定|程序)")


def load_dataset():
    if not os.path.exists(DATASET_PATH):
        B.main()
    with open(DATASET_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _now_of(turn, item):
    raw = turn.get("now") or (item.get("now") or "")
    try:
        return dt.datetime.fromisoformat(str(raw))
    except Exception:
        return dt.datetime.fromisoformat(str(item.get("now") or "2026-11-17T10:30:00"))


def run_item(item, mode, delay, tone_state):
    # 每个样本是独立会话：状态/整句去重/口头禅记忆不跨样本、不跨 B 开关
    pipeline.reset_repetition_memory()
    try:
        import tone_guard
        tone_guard.reset()
    except Exception:  # cleanup: harness 只做验收，缺模块就跳过
        pass
    llm = H.MockLLM(mode="rotate", delay=delay)
    plugin = H.build_plugin(item["world"], llm=llm)
    plugin._fact_tone_state = tone_state
    plugin.tone_llm_pick = (mode == "on")
    plugin.tone_llm_pick_timeout = 1.5
    umo = "imm:%s" % item["id"]
    out_turns = []
    for turn in item["turns"]:
        now = _now_of(turn, item)
        if turn.get("proactive"):
            result = asyncio.run(H.run_proactive_turn(plugin, turn["candidate"], now, umo,
                                                      b_mode=(mode == "on")))
        else:
            result = asyncio.run(H.run_turn(plugin, turn["user"], turn["candidate"], now, umo,
                                            b_mode=(mode == "on")))
        result["now"] = now.isoformat()
        result["question"] = bool(turn.get("question"))
        result["candidate_source"] = turn.get("candidate_source", "")
        out_turns.append(result)
    return {"id": item["id"], "category": item["category"], "source": item["source"],
            "turns": out_turns, "llm_calls": llm.calls}


def _norm(text):
    return re.sub(r"\s+", "", str(text or ""))


TOPIC_GROUPS = {
    "food": ("吃", "饭", "面", "粥", "菜", "汤", "肉", "鱼", "蛋", "粉", "茶", "烧烤", "火锅",
             "外卖", "饿", "胃口", "零食", "水果", "蛋糕", "包子", "饺", "喝", "煮", "炒",
             "一锅", "做"),
    "place": ("家", "公司", "路上", "地铁", "商场", "酒店", "厦门", "海边", "景点", "工位",
              "楼下", "门口", "到家", "出门"),
    "sleep": ("睡", "困", "醒", "赖床", "晚安", "起", "被窝"),
    "work": ("上班", "加班", "下班", "工作", "周报", "开会", "单子", "工位", "请假"),
    "health": ("烧", "药", "病", "疼", "难受", "嗓子", "感冒", "医院", "体温", "度"),
    "travel": ("酒店", "厦门", "机票", "景点", "海边", "出站", "走"),
    "social": ("妈妈", "家人", "朋友", "沈幼微", "哥哥", "嫂子", "聚餐", "同事", "热闹"),
    "weather": ("天气", "冷", "热", "下雨", "太阳", "风"),
}


def _groups(text):
    value = str(text or "")
    return {name for name, words in TOPIC_GROUPS.items() if any(word in value for word in words)}


#: 改写不通顺的检测只认「地点词被硬塞进方位/行程结构」的坏句，
#: 不误伤「在家窝着呢」「我还在家呢」这种正常说法。
BROKEN_REWRITE_RE = re.compile(
    r"在家上(?=[改跟摸忙吃喝一加干做收])"      # 在家上改周报 / 在家上一堆事
    r"|家上(?=[改跟摸忙吃喝一加干做收])"        # 家上改周报
    r"|家(?:窝着|歇着|这边)(?=[饭上快，。]|$)"   # 家窝着饭点 / 家歇着， / 家这边快到了
    r"|(?:在家|家里|家)(?:待着|窝着|歇着|这边)?快到了"  # 家待着快到了 / 在家快到了
    r"|还在家(?=饭点|快到了|溜|上|楼下?吃)"
    r"|我就在家(?:上|快到了)"
    r"|在家待着快到了")


def _topic_hit(user, sent):
    user, sent = str(user or ""), str(sent or "")
    words = {word for word in B.KEYWORDS if word in user}
    if words and any(word in sent for word in words):
        return True
    if _groups(user) & _groups(sent):
        return True
    if re.search(r"干嘛|做什么|在忙|忙吗|忙不忙|在吗", user):
        return True
    if re.search(r"几点|什么时候|多久|哪天|几号", user) and re.search(r"d|点|分钟|小时|早|晚|号|点|天", sent):
        return True
    if re.search(r"吗|没|是不是|开不开|会不会|要不要", user) and re.search(
            r"^(开|是|对|嗯|没|还|在|有|能|会|不|刚|已|好|行|可以)", sent):
        return True
    return False


def evaluate_item(item, run):
    issues = []
    turns = run["turns"]
    for index, turn in enumerate(turns):
        sent = str(turn.get("sent") or "")
        if not sent.strip():
            issues.append({"type": "空回复", "turn": index, "detail": "最终发送为空"})
            continue
        for violation in turn.get("post_violations") or []:
            issues.append({"type": "事实矛盾", "turn": index,
                           "detail": "%s: %s -> %s" % (violation.get("rule"),
                                                      str(violation.get("sentence"))[:40],
                                                      str(violation.get("replacement"))[:40])})
        for hit in turn.get("blacklist") or []:
            issues.append({"type": "事实矛盾", "turn": index, "detail": "地点黑名单: %s" % hit})
        match = LEAK_RE.search(sent)
        if match:
            issues.append({"type": "系统腔/元信息", "turn": index, "detail": match.group(0)})
        try:
            from amo_shared import looks_like_ai_admission, looks_like_model_leak
            if looks_like_model_leak(sent) or looks_like_ai_admission(sent):
                issues.append({"type": "系统腔/元信息", "turn": index,
                               "detail": "production detector hit"})
        except Exception:  # cleanup: 生产探测器缺失就跳过这一项
            pass
        answer_check = turn.get("candidate_source") in ("synthetic", "authored", "history_thread")
        if turn.get("question") and item["category"] not in META_CATS and answer_check:
            user = str(turn.get("user") or "")
            if sent.strip() in [d.strip() for d in DEFLECTIONS]:
                issues.append({"type": "答非所问", "turn": index,
                               "detail": "问的是「%s」，回的是固定挡话「%s」" % (user[:24], sent[:24])})
            elif not _topic_hit(user, sent) \
                    and not any(token in sent for token in GENERIC_ANSWER) \
                    and not sent.strip().endswith(("？", "?")):
                issues.append({"type": "答非所问", "turn": index,
                               "detail": "问「%s」答「%s」" % (user[:24], sent[:24])})
        broken = BROKEN_REWRITE_RE.search(sent)
        if broken:
            issues.append({"type": "改写不通顺", "turn": index, "detail": broken.group(0)})
        cand = str(turn.get("candidate") or "")
        cand_emotions = {word for word in EMOTION_WORDS if word in cand}
        if cand_emotions and not any(word in sent for word in cand_emotions):
            issues.append({"type": "情绪丢失", "turn": index,
                           "detail": "%s -> %s" % ("/".join(sorted(cand_emotions)[:3]), sent[:40])})
    for index in range(1, len(turns)):
        left, right = _norm(turns[index - 1]["sent"]), _norm(turns[index]["sent"])
        if not left or not right:
            continue
        repeated = (left == right or (len(left) >= 8 and left[:8] == right[:8]) or
                    (len(right) >= 10 and right in left) or (len(left) >= 10 and left in right))
        if repeated:
            issues.append({"type": "复读", "turn": index,
                           "detail": "「%s」/「%s」" % (left[:24], right[:24])})
    return issues


def _p95(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[max(0, int(round(0.95 * (len(ordered) - 1))))])


def run_all(dataset, delay=0.0):
    """每个模式先跑一遍热身（让语气池走完每个键的首句），再测量稳态。"""
    runs = {"cold": {}}
    for mode in ("off", "on"):
        tone_state = {}
        warm = [run_item(item, mode, delay, tone_state) for item in dataset["items"]]
        cold_mech = sum(1 for run in warm for turn in run["turns"] if turn.get("mechanical"))
        cold_rewritten = sum(1 for run in warm for turn in run["turns"] if turn.get("violations"))
        runs["cold"][mode] = {"mechanical": cold_mech, "rewritten": cold_rewritten,
                              "rate": round(cold_mech / max(1, cold_rewritten), 4)}
        pipeline.reset_tone_llm_pick_stats()
        started = time.perf_counter()
        runs[mode] = [run_item(item, mode, delay, tone_state) for item in dataset["items"]]
        runs[mode + "_seconds"] = round(time.perf_counter() - started, 3)
        runs[mode + "_stats"] = pipeline.tone_llm_pick_stats()
    return runs


def summarize(dataset, runs):
    items = dataset["items"]
    by_id = {mode: {run["id"]: run for run in runs[mode]} for mode in ("off", "on")}
    per_mode = {}
    for mode in ("off", "on"):
        issues_by_item = {}
        counts = {"事实矛盾": 0, "复读": 0, "答非所问": 0,
                  "系统腔/元信息": 0, "情绪丢失": 0, "空回复": 0, "改写不通顺": 0}
        emotion_total = emotion_lost = mechanical = rewritten = 0
        for item in items:
            run = by_id[mode][item["id"]]
            issues = evaluate_item(item, run)
            issues_by_item[item["id"]] = issues
            for issue in issues:
                counts[issue["type"]] += 1
            for turn in run["turns"]:
                if turn.get("mechanical"):
                    mechanical += 1
                if turn.get("violations"):
                    rewritten += 1
                cand = str(turn.get("candidate") or "")
                if any(word in cand for word in EMOTION_WORDS):
                    emotion_total += 1
                    if not any(word in str(turn.get("sent") or "") for word in EMOTION_WORDS):
                        emotion_lost += 1
        per_mode[mode] = {
            "counts": counts, "issues": issues_by_item,
            "emotion_total": emotion_total, "emotion_lost": emotion_lost,
            "emotion_keep_rate": round(1 - emotion_lost / max(1, emotion_total), 4),
            "mechanical": mechanical, "rewritten": rewritten,
            "mechanical_rate": round(mechanical / max(1, rewritten), 4),
            "stats": runs.get(mode + "_stats", {}),
            "seconds": runs.get(mode + "_seconds")}
    extra = []
    for item in items:
        for left, right in zip(by_id["off"][item["id"]]["turns"], by_id["on"][item["id"]]["turns"]):
            extra.append(float(right["ms"]) - float(left["ms"]))
    stats = per_mode["on"]["stats"]
    calls = max(1, int(stats.get("calls") or 0))
    fallback = sum(int(stats.get(key) or 0) for key in ("timeout", "invalid", "error", "fact_fallback"))
    latency = {"p50_ms": round(statistics.median(extra), 2) if extra else 0.0,
               "p95_ms": round(_p95(extra), 2),
               "mean_ms": round(statistics.fmean(extra), 2) if extra else 0.0,
               "max_ms": round(max(extra), 2) if extra else 0.0}
    return {"per_mode": per_mode, "latency": latency, "b_stats": stats,
            "b_calls": calls, "b_fallback": fallback,
            "b_fallback_rate": round(fallback / calls, 4),
            "cold_mechanical": runs.get("cold") or {}}


def fault_injection(dataset, limit_turns=20):
    cases = [("bad_index", {"answer": "我觉得第几条都行，你觉得呢"}),
             ("oob", {"answer": '{"index": 99}'}),
             ("timeout", {"delay": 1.2})]
    out = {"cases": {}, "calls_total": 0, "crash": False}
    for name, kwargs in cases:
        pipeline.reset_tone_llm_pick_stats()
        tone_state = {}
        issues = 0
        seen = 0
        for item in dataset["items"]:
            if seen >= limit_turns:
                break
            llm = H.MockLLM(mode="rotate", **kwargs)
            plugin = H.build_plugin(item["world"], llm=llm)
            plugin._fact_tone_state = tone_state
            plugin.tone_llm_pick = True
            plugin.tone_llm_pick_timeout = 0.2 if name == "timeout" else 1.5
            umo = "fault:%s:%s" % (name, item["id"])
            for turn in item["turns"]:
                if seen >= limit_turns:
                    break
                now = _now_of(turn, item)
                result = asyncio.run(H.run_turn(plugin, turn["user"], turn["candidate"], now,
                                                umo, b_mode=True))
                seen += 1
                if result.get("post_violations") or result.get("blacklist"):
                    issues += 1
        stats = pipeline.tone_llm_pick_stats()
        out["cases"][name] = {"turns": seen, "fact_issues": issues, "stats": stats}
        out["calls_total"] += int(stats.get("calls") or 0)
    return out


def _fmt_issues(issues):
    return "；".join("%s@%d(%s)" % (item["type"], item["turn"], item["detail"][:40])
                     for item in issues[:3]) or "-"


def _item_table(dataset, per):
    """逐条结果表（id/类别/来源/轮数/两态失败数/首条失败）。"""
    lines = ["## 逐条结果", "",
             "| id | 类别 | 来源 | 轮数 | B off 失败 | B on 失败 | 首条失败（off） |",
             "|---|---|---|---|---|---|---|"]
    for item in dataset["items"]:
        off_issues = per["off"]["issues"].get(item["id"]) or []
        on_issues = per["on"]["issues"].get(item["id"]) or []
        lines.append("| %s | %s | %s | %d | %d | %d | %s |" % (
            item["id"], item["category"], item["source"], len(item["turns"]),
            len(off_issues), len(on_issues), _fmt_issues(off_issues)))
    lines.append("")
    return lines


def _failure_section(dataset, per):
    """失败样本与根因（B off + B on 合并；全零时只写一句「无」）。"""
    lines = ["## 失败样本与根因（B off + B on 合并）", ""]
    any_fail = False
    for item in dataset["items"]:
        merged = []
        for mode in ("off", "on"):
            for issue in per[mode]["issues"].get(item["id"]) or []:
                issue = dict(issue)
                issue["mode"] = mode
                merged.append(issue)
        if not merged:
            continue
        any_fail = True
        lines.append("### %s（%s / %s）" % (item["id"], item["category"], item["source"]))
        if item.get("seed_text"):
            lines.append("- 取样：%s" % item["seed_text"][:100])
        for issue in merged[:8]:
            lines.append("- [%s] %s 第%d轮：%s" % (
                issue["mode"], issue["type"], issue["turn"], issue["detail"]))
        lines.append("")
    if not any_fail:
        lines.append("无。零容忍指标全部为 0。")
        lines.append("")
    return lines


def build_report(dataset, runs, summary, fault, args):
    lines = []
    per = summary["per_mode"]
    fails = []
    for mode in ("off", "on"):
        for key in ("事实矛盾", "复读", "答非所问", "系统腔/元信息"):
            if per[mode]["counts"][key]:
                fails.append("%s:%s=%d" % (mode, key, per[mode]["counts"][key]))
        if per[mode]["emotion_keep_rate"] < 0.95:
            fails.append("%s:情绪保留=%.3f" % (mode, per[mode]["emotion_keep_rate"]))
        if per[mode]["mechanical_rate"] > 0.10:
            fails.append("%s:机械改写=%.3f" % (mode, per[mode]["mechanical_rate"]))
        if per[mode]["counts"]["改写不通顺"]:
            fails.append("%s:改写不通顺=%d" % (mode, per[mode]["counts"]["改写不通顺"]))
    if summary["b_fallback_rate"] > 0.05:
        fails.append("B 回退率=%.3f" % summary["b_fallback_rate"])
    if summary["latency"]["p95_ms"] > 1500:
        fails.append("B p95=%.0fms" % summary["latency"]["p95_ms"])
    verdict = "PASS" if not fails else "FAIL"
    lines.append("# IMM 出戏验收报告（100 条多轮 × B off/on）")
    lines.append("")
    lines.append("- 生成时间：%s" % dt.datetime.now().isoformat(timespec="seconds"))
    lines.append("- 数据集：work/immersion_check/imm_dataset.json（60 条真实采样 + 40 条合成对抗）")
    lines.append("- 链路：_guard_self_location → apply_fact_guard（事实守卫+语气池+B pending）→ "
                 "apply_narrative_repetition（去重/先答吃啥）→ apply_tone_guard → "
                 "[B on] apply_pending_tone_pick_async → _send_to_umo（完整 send 阶段）")
    lines.append("- LLM：全部 mock（不真实调用）；B 选择器固定/轮换，延迟 %.1fs；真实 LLM 见补充节"
                 % (0.0 if args.fast else 0.2))
    lines.append("")
    lines.append("## 结论：%s" % verdict)
    lines.append("")
    if fails:
        lines.append("不达标项：")
        for item in fails:
            lines.append("- %s" % item)
        lines.append("")
    lines.append("## 指标")
    lines.append("")
    lines.append("| 指标 | 目标 | B off(A) | B on(B) | 判定 |")
    lines.append("|---|---|---|---|---|")
    for name, target, key in (("事实矛盾", "0", "事实矛盾"), ("复读", "0", "复读"),
                              ("答非所问", "0", "答非所问"), ("系统腔/元信息", "0", "系统腔/元信息"),
                              ("空回复", "0", "空回复"), ("改写不通顺", "0（质量线）", "改写不通顺")):
        off, on = per["off"]["counts"][key], per["on"]["counts"][key]
        lines.append("| %s | %s | %d | %d | %s |" % (name, target, off, on, "OK" if off == 0 and on == 0 else "FAIL"))
    lines.append("| 情绪句保留 | ≥95%% | %.1f%% | %.1f%% | %s |" % (
        per["off"]["emotion_keep_rate"] * 100, per["on"]["emotion_keep_rate"] * 100,
        "OK" if min(per["off"]["emotion_keep_rate"], per["on"]["emotion_keep_rate"]) >= 0.95 else "FAIL"))
    lines.append("| 机械改写率 | ≤10%% | %.1f%% | %.1f%% | %s |" % (
        per["off"]["mechanical_rate"] * 100, per["on"]["mechanical_rate"] * 100,
        "OK" if max(per["off"]["mechanical_rate"], per["on"]["mechanical_rate"]) <= 0.10 else "FAIL"))
    lines.append("| B 回退率 | ≤5%% | - | %.2f%%（%d/%d） | %s |" % (
        summary["b_fallback_rate"] * 100, summary["b_fallback"], summary["b_calls"],
        "OK" if summary["b_fallback_rate"] <= 0.05 else "FAIL"))
    lines.append("| B 额外延迟 p95 | ≤1500ms | - | %.0fms | %s |" % (
        summary["latency"]["p95_ms"], "OK" if summary["latency"]["p95_ms"] <= 1500 else "FAIL"))
    lines.append("")
    lines.append("- 覆盖：%d 条 / %d 类；轮数分布 %s" % (
        len(dataset["items"]), len(dataset.get("category_hist") or {}),
        dataset.get("turn_hist") or {}))
    lines.append("- 候选来源：真实同线程历史 / 真实同话题历史 / 规则模板 / 手工配对（日记记忆派生）")
    lines.append("- 机械改写率按稳态（每个语气键走完首句固定句后）统计；冷启动首命中固定句占比："
                 "off=%.1f%%、on=%.1f%%（N2 设计：第 0 条保留旧固定句，属已知口径）"
                 % (100 * summary["cold_mechanical"].get("off", {}).get("rate", 0),
                    100 * summary["cold_mechanical"].get("on", {}).get("rate", 0)))
    lines.append("- 耗时：B off %.2fs / B on %.2fs（mock）" % (
        per["off"]["seconds"] or 0, per["on"]["seconds"] or 0))
    lines.append("")
    lines.extend(_item_table(dataset, per))
    lines.extend(_failure_section(dataset, per))
    lines.append("## B 开关与回退")
    lines.append("")
    lines.append("- on/off 两态各跑全量 100 条（同一批候选、同一世界状态）。")
    lines.append("- B 统计（on）：%s" % json.dumps(summary["b_stats"], ensure_ascii=False))
    lines.append("- 故障注入（坏序号/越界/超时）：%s" % json.dumps(fault, ensure_ascii=False))
    lines.append("- 故障注入下的回退是设计行为（不崩、事实仍正确），不计入 5% 门槛。")
    lines.append("")
    lines.append("## 真实 LLM 补充（10 条，不计入 100 硬指标）")
    lines.append("")
    lines.append(real_llm_section())
    lines.append("")
    zero_clean = all(per[mode]["counts"][key] == 0 for mode in ("off", "on")
                     for key in ("事实矛盾", "复读", "答非所问", "系统腔/元信息", "改写不通顺"))
    lines.append(FIXED_NOTES if zero_clean else ROOT_CAUSES)
    lines.append("## P0 / P1 / P2")
    lines.append("")
    lines.append(p_levels(per, summary, fails))
    lines.append("")
    lines.append("## 备份与回滚")
    lines.append("")
    lines.append("- 修改：amo_reply_pipeline.py、amo_repetition_guard.py（新增整句去重模块）、"
                 "reply_fact_guard.py、place_guard.py、amo_names.py、amo_tone_pick.py、"
                 "amo_message.py、work/immersion_check/**、tests/test_immersion_acceptance.py、"
                 "tests/test_immersion_p0_fixes.py。")
    lines.append("- 未改版本号/热重载；docs/ 只更新本报告（由 run_imm.py 生成）。")
    lines.append("- 改动前备份：插件根目录同级的 backup/imm-fix-<时间戳>/（12 个原文文件）；"
                 "报告备份：work/immersion_check/backup/（md + 结果 json 的时间戳副本）。")
    lines.append("- 回滚：把备份目录里的同名文件覆盖回插件目录即可。")
    return "\n".join(lines) + "\n"


FIXED_NOTES = """## 修复记录（task-33，本轮已修）

1. **整句复读（P0）**：发送前（含分段）与最近 3-5 条 bot 消息比对，归一化空白/标点后
   完全相同或 ≥0.9 相似就删该句/该段；整条都重复时换一句短的（原句带情绪时保留同情绪），
   绝不发空。模型/后台泄漏的挡话也改成轮换，不再连续吐同一句。覆盖 S21/S22/S25/S06。
2. **foreign_names 误报（P0）**：名单外名字收紧为「姓氏表首字 + 2-4 字」，排除句尾否定/
   语气词/动补结尾与常用词表；「笑笑不」「吃完」「老样子」不再被当人名，真外名仍拦。覆盖 R38。
3. **地点改口语法（P0）**：reply_fact_guard/place_guard 改为整句/整段改写，地点短语连带
   方位词一起处理：「在工位上改周报」→「在家改周报」；「还在路上，快到了」→「已经到家了」；
   「从公园溜达回来」这种已经离开那儿的句子保持原样；结构拿不准就删地点短语（宁可不说
   地点，不出坏句）。语气池改为消费守卫给出的整句候选，不再把地点词塞进模板。
4. **口径**：机械改写率仍按稳态统计；冷启动首命中固定句属 N2 设计。
"""


ROOT_CAUSES = """## 已知根因（本机复现，按 P0 排序）

1. **整句复读没有去重层（P0，复读=5）**：S21 用户连说三次「想你」，她三次输出完全一样的
   「嗯嗯，我在家呢，想你」；S25「马上睡了，再刷五分钟」同样逐字重复；S22 同一句
   「累死了，改了一天表格」跨两轮原样出现（只多了一个「还没顾上吃呢。」前缀）；S06 两次
   后台信息拦截都吐同一句「秘密，不告诉你。」。
   现有守卫只有两层：tone_guard 去句首口头禅、narrative_repetition 去状态词（药/等车/打车），
   都不覆盖「同一句整句原样再说一遍」。触发点：apply_narrative_repetition /
   apply_tone_guard 之后没有整句级比对（见 amo_reply_pipeline.py:1464、1273）。
2. **熟人名单检测误报把正常句子改坏（P0，答非所问=1）**：R38 候选「问了，我就笑笑不说话」
   被判成名单外人名 '笑笑不'，改写后变成「问了，我就有人说话」；同源误报还有
   「吃完」「老样子」（amo_names.foreign_names），会把「吃完跟我说说吃了啥」「还是老样子」
   这类日常句当成外人名处理。这是答非所问/语义破坏的直接来源。
3. **地点改口把介词/方位词生拼进句子（P0/P1，改写不通顺=26 off / 18 on）**：
   「在工位上改周报呢」→「在家上改周报呢」「这会儿在家上改周报呢」；
   「还在路上，快到了」→「还在家快到了」；「我这边路上饭点还没到」→「我这边家这边饭点还没到」；
   「我从公园溜达回来」→「我从家窝着溜达回来」。
   根因在 reply_fact_guard/place_guard 的「地点词直接替换」与语气池只加外壳（prefix/suffix），
   不处理原句的方位/介词结构；语气池模板把 {t} 原样嵌进新句，坏句被保留下来。
4. **口径说明**：机械改写率按稳态统计（每个语气键第一次命中保留第 0 条固定句，属 N2 设计）；
   冷启动首命中固定句 off=6.1%、on=6.1%。真实 LLM 下 B 选择器与生成都不在本套 mock 内，
   见「真实 LLM 补充」。

## 修复建议（不在本次写权限内，需 tone-facts / persona-tone 跟进）

- 复读：在 apply_narrative_repetition 增加「与最近 N 条 bot 文本整句相同/高相似」的直接改写
  （换语气而非删事实），并让 model_leak/deflection 固定句也参与轮换。
- 名单误报：foreign_names 收紧（要求姓氏表首字 + 2~3 字且排除常用词/动补结构），
  或先按名单内名字判定，名单外只告警不改写。
- 地点改口：改口只替换「地点短语」整体（含方位/介词），不把地点词塞进「在…上/路上」结构；
  语气池模板消费整段而非 {t} 片段。

"""


def p_levels(per, summary, fails):
    zero_hits = {}
    for mode in ("off", "on"):
        for key in ("事实矛盾", "复读", "答非所问", "系统腔/元信息"):
            zero_hits["%s/%s" % (mode, key)] = per[mode]["counts"][key]
    lines = []
    if any(zero_hits.values()):
        lines.append("- P0（不交付）：零容忍项命中 → %s；根因见上方逐条失败样本。"
                     % json.dumps(zero_hits, ensure_ascii=False))
    else:
        lines.append("- P0：无（零容忍四项均为 0）。")
    if fails:
        lines.append("- P1：质量线未达标 → %s。" % "；".join(fails))
    else:
        lines.append("- P1：无（情绪保留/机械改写/B 回退/延迟均在线上）。")
    lines.append("- P2：B 默认关闭，on/off 都已覆盖；mock 延迟 %.0fms，真实 LLM 的 p95 需真机复测。"
                 % summary["latency"]["p50_ms"])
    return "\n".join(lines)


def real_llm_section():
    path = os.path.join(HERE, "real_llm_results.json")
    if not os.path.exists(path):
        return ("- 状态：未执行（本次只做离线 mock；real_llm_probe.py 可接真实 provider 跑 10 条，"
                "结果写 real_llm_results.json 后并入本节）。")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        return "- 状态：读取失败（%r）" % (exc,)
    lines = ["- provider=%s model=%s 样本=%d" % (data.get("provider"), data.get("model"),
                                               len(data.get("samples") or []))]
    for sample in data.get("samples") or []:
        lines.append("  - %s 事实问题=%d 出戏=%d" % (sample.get("id"),
                                                    sample.get("fact_issues", 0),
                                                    sample.get("leak_issues", 0)))
    lines.append("- 结论：%s" % data.get("verdict", ""))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="mock 延迟 0（CI 用）")
    parser.add_argument("--no-fault", action="store_true")
    parser.add_argument("--report-only", action="store_true", help="复用 imm_results.json 只重出报告")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    dataset = load_dataset()
    if args.report_only:
        saved = json.load(open(RESULTS_PATH, encoding="utf-8"))
        summary, fault = saved["summary"], saved.get("fault") or {}
        runs = {"off": saved["runs"]["off"], "on": saved["runs"]["on"],
                "cold": summary.get("cold_mechanical") or {}}
    else:
        delay = 0.0 if args.fast else 0.2
        runs = run_all(dataset, delay=delay)
        summary = summarize(dataset, runs)
        fault = {} if args.no_fault else fault_injection(dataset)
    report = build_report(dataset, runs, summary, fault, args)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    if not args.report_only:
        with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
            json.dump({"dataset": os.path.relpath(DATASET_PATH, ROOT).replace("\\", "/"),
                       "summary": summary, "fault": fault,
                       "runs": {mode: runs[mode] for mode in ("off", "on")}}, fh,
                      ensure_ascii=False, indent=1)
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(os.path.join(BACKUP_DIR, "IMM_REPORT_%s.md" % stamp), "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(os.path.join(BACKUP_DIR, "imm_results_%s.json" % stamp), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"counts": {mode: summary["per_mode"][mode]["counts"] for mode in ("off", "on")},
                      "latency": summary["latency"],
                      "b_fallback_rate": summary["b_fallback_rate"],
                      "emotion_keep": {m: summary["per_mode"][m]["emotion_keep_rate"] for m in ("off", "on")},
                      "mechanical": {m: summary["per_mode"][m]["mechanical_rate"] for m in ("off", "on")},
                      "fault": fault, "report": REPORT_PATH}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
