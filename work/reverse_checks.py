# -*- coding: utf-8 -*-
"""P1-7 反向验证一键脚本：改坏 → 必红 → 恢复 → 必绿。

不碰生产目录：每次把插件复制到临时目录，在副本里改源码、跑 pytest、还原、再跑。
10 条核心检查覆盖：地点（冲突/改口）、健康、吃饭、companion、日程重叠、
文案地点、存储同步、日记编造、旅游账本幂等。

用法：
    python work/reverse_checks.py            # 全跑，打印红/绿，写 work/reverse_checks_last.json
    python work/reverse_checks.py --list     # 只列清单并校验改坏锚点
    python work/reverse_checks.py --only place_conflict
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable
LOG_PATH = os.environ.get("AMO_REVERSE_LOG") or os.path.join(
    tempfile.gettempdir(), "amo_reverse_checks_last.json")
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git", ".test-tmp",
                                "_test_tmp", "*.pyc", "plugin_data")

CHECKS = [
    {
        "id": "place_conflict", "area": "地点",
        "label": "把「地点一致就不算冲突」改成误报冲突",
        "target": "place_guard.py",
        "edits": [{
            "old": "        if group in allowed:\n            continue\n        if word in act_text:\n            continue\n        index = clause.find(word)",
            "new": "        if group in allowed:\n            return {\"said\": word, \"group\": group}\n        if word in act_text:\n            continue\n        index = clause.find(word)",
        }],
        "tests": ["tests/test_place_guard.py::ConflictTests::test_matching_place_is_not_a_conflict"],
    },
    {
        "id": "leave_day_company", "area": "健康",
        "label": "把病假当天锁关掉（病好当天就允许回公司）",
        "target": "domains/schedule/jit_plan.py",
        "edits": [{
            "old": "    if leave_day and not sick:\n        # 请了病假就是全天在家：哪怕病提前好了，当天也不回公司（2026-11-18 用户要求）。",
            "new": "    if leave_day and not sick and False:\n        # 请了病假就是全天在家：哪怕病提前好了，当天也不回公司（2026-11-18 用户要求）。",
        }],
        "tests": ["tests/test_leave_day.py::LeaveDayPlanTests::test_recovered_leave_day_lunch_is_at_home"],
    },
    {
        "id": "holiday_rest", "area": "日程",
        "label": "把节假日休息日改回按星期几判断（周中放假就排上班）",
        "target": "domains/schedule/jit_plan.py",
        "edits": [{
            "old": "    if rest_day is None:\n        is_workday = weekday is not None and int(weekday) < 5\n    else:\n        is_workday = not bool(rest_day)",
            "new": "    if True:\n        is_workday = weekday is not None and int(weekday) < 5\n    else:\n        is_workday = not bool(rest_day)",
        }],
        "tests": ["tests/test_jit_facts.py::HolidayRestTests::test_holiday_noon_stays_home"],
    },
    {
        "id": "period_home", "area": "健康",
        "label": "把生理期在家关掉（痛经日照常上班）",
        "target": "domains/schedule/jit_plan.py",
        "edits": [{
            "old": "    if period_home:\n        return _period_day_plan(hour)",
            "new": "    if period_home and False:\n        return _period_day_plan(hour)",
        }],
        "tests": ["tests/test_jit_facts.py::PeriodTests::test_period_stays_home"],
    },
    {
        "id": "place_rewrite", "area": "地点",
        # 3a-2b C2：出站地点改口已收敛到 reply_fact_guard（place_guard.rewrite_reply 删除），
        # 反向检查跟着锚到 B 的改口目标词：清空它 → 地点矛盾不再改口 → 必红。
        # 防回流（rewrite_reply/_guard_self_location 不许回生产源码）由
        # tests/test_place_guard.py::WiringTests 与 tests/test_reply_fact_guard.py::WiringTests
        # 常驻断言，不在本清单里重复。
        "label": "把 reply_fact_guard 的改口目标词清空（地点矛盾不再改口）",
        "target": "reply_fact_guard.py",
        "edits": [{
            "old": "def _place_target(activity, zone):\n    return (TARGET_WORD.get(zone) or _EXTRA_TARGET.get(zone)\n            or spoken_place(activity) or \"在外面\")",
            "new": "def _place_target(activity, zone):\n    return \"\"  # reverse-check：把改口目标词清空\n    return (TARGET_WORD.get(zone) or _EXTRA_TARGET.get(zone)\n            or spoken_place(activity) or \"在外面\")",
        }],
        "tests": ["tests/test_reply_fact_guard.py::RewriteTests::test_home_while_at_work_is_rewritten_to_office"],
    },
    {
        "id": "health_rule", "area": "健康",
        "label": "摘掉回复里的健康规则（病中说「我已经好了」不再改口）",
        "target": "reply_fact_guard.py",
        "edits": [{
            "old": "def _health_rule(clause, ctx):\n    health = ctx.get(\"health\") or {}",
            "new": "def _health_rule(clause, ctx):\n    return clause, []  # reverse-check：摘掉健康规则\n    health = ctx.get(\"health\") or {}",
        }],
        "tests": ["tests/test_reply_fact_guard.py::RewriteTests::test_recovery_claim_while_sick_is_softened"],
    },
    {
        "id": "meal_done", "area": "吃饭",
        "label": "摘掉「这一餐已经在家吃过」的判断",
        "target": "micro_events.py",
        "edits": [{
            "old": "        # 日程 + 账本一起看：日程写了做饭，或者账上有一笔\"自己做饭\"，都算这一餐吃过了\n        try:\n            items = _schedule_items(self.p, self._today(now))",
            "new": "        return False  # reverse-check：摘掉吃饭守卫\n        # 日程 + 账本一起看：日程写了做饭，或者账上有一笔\"自己做饭\"，都算这一餐吃过了\n        try:\n            items = _schedule_items(self.p, self._today(now))",
        }],
        "tests": ["tests/test_meal_guard.py::MealEventFilterTests::test_meal_done_from_schedule"],
    },
    {
        "id": "companion", "area": "companion",
        "label": "摘掉「和老公/他陪我」的约会主语判定",
        "target": "domains/schedule/subject.py",  # 2026-11-18 实现搬进 domains
        "edits": [{
            "old": "    if _COMPANION.search(body):\n        return WITH_OTHER",
            "new": "    if False:  # reverse-check：摘掉 companion 判定\n        return WITH_OTHER",
        }],
        "tests": ["tests/test_schedule_subject.py::SubjectPureTests::test_companion_activities_need_a_date"],
    },
    {
        "id": "schedule_overlap", "area": "日程重叠",
        "label": "摘掉 add_schedule_item 的追加去重叠闸门",
        "target": "amo_state_schedule.py",
        "edits": [{
            "old": "        重叠静默写进库（2026-11-17 06:12 finance 事故）。\n        \"\"\"\n        try:",
            "new": "        重叠静默写进库（2026-11-17 06:12 finance 事故）。\n        \"\"\"\n        return merged, dict(item)  # reverse-check：摘掉追加去重叠闸门\n        try:",
        }],
        "tests": ["tests/test_add_schedule_item_overlap.py::AddScheduleItemOverlapTests::test_finance_on_sleep_is_not_persisted_as_overlap"],
    },
    {
        "id": "location_text", "area": "文案地点",
        "label": "摘掉地点禁止词校验（医院里写床/茶几不再拦）",
        "target": "amo_location_rules.py",
        "edits": [{
            "old": "    return _match_forbidden(value, rule_for(place, zone))",
            "new": "    return []  # reverse-check：摘掉地点禁止词校验",
        }],
        "tests": ["tests/test_location_rules.py::RuleTableTests::test_hospital_blocks_home_props"],
    },
    {
        "id": "storage_sync", "area": "存储同步",
        "label": "摘掉 save_day 的 kv['schedule'] 镜像",
        "target": "amo_state_schedule.py",
        "edits": [{
            "old": "        mirror = self._schedule_mirror_from_table()\n        self.db.kv_set(\"schedule\", mirror if mirror is not None else self.schedule)",
            "new": "        # reverse-check：摘掉 kv['schedule'] 镜像\n        _ = self._schedule_mirror_from_table()",
        }],
        "tests": ["tests/test_schedule_invariants_storage.py::SingleStorageTests::test_save_day_keeps_table_and_kv_identical"],
    },
    {
        "id": "diary_fabrication", "area": "日记编造",
        "label": "摘掉「素材没有聊天却写今天他提醒我」的判定",
        "target": "amo_diary_guard.py",
        "edits": [{
            "old": "    text = str(sentence or \"\")\n    if not text.strip():\n        return False\n    if not any(word in text for word in _SUBJECTS):",
            "new": "    text = str(sentence or \"\")\n    if not text.strip():\n        return False\n    return False  # reverse-check：摘掉虚构互动判定\n    if not any(word in text for word in _SUBJECTS):",
        }],
        "tests": ["tests/test_diary_fabrication_guard.py::DeterministicGuardTests::test_empty_chat_strips_today_interaction"],
    },
    {
        "id": "trip_cost_idem", "area": "账本幂等",
        "label": "摘掉旅游消费的三道幂等键（done/pending/落盘）",
        "target": "amo_trip_cost.py",
        "edits": [
            {
                "old": "    if _kv_get(plugin, DONE_PREFIX + idem, False):\n        return {\"ok\": False, \"reason\": \"duplicate\"}",
                "new": "    if False:  # reverse-check：摘掉已消费幂等键\n        return {\"ok\": False, \"reason\": \"duplicate\"}",
            },
            {
                "old": "    if _kv_get(plugin, pending_key, None) is not None and _wallet_has_idem(wallet, idem):\n        return _record_spent(plugin, trip, value, idem, duplicate=True)",
                "new": "    if False:  # reverse-check：摘掉 pending 补记\n        return _record_spent(plugin, trip, value, idem, duplicate=True)",
            },
            {
                "old": "    done_key = DONE_PREFIX + idem\n    if _kv_get(plugin, done_key, False):\n        return {\"ok\": False, \"reason\": \"duplicate\"}",
                "new": "    done_key = DONE_PREFIX + idem\n    if False:  # reverse-check：摘掉落盘幂等键\n        return {\"ok\": False, \"reason\": \"duplicate\"}",
            },
        ],
        "tests": ["tests/test_trip.py::SpendTests::test_idem_dedupe"],
    },
]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def validate(check):
    """校验改坏锚点在原文件里恰好出现一次。"""
    path = os.path.join(ROOT, check["target"])
    if not os.path.isfile(path):
        return ["目标文件不存在: %s" % check["target"]]
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    problems = []
    for index, edit in enumerate(check["edits"], 1):
        count = text.count(edit["old"])
        if count != 1:
            problems.append("第 %d 处 anchor 出现 %d 次（必须 1 次）" % (index, count))
    for node in check["tests"]:
        test_file = node.split("::", 1)[0]
        if not os.path.isfile(os.path.join(ROOT, test_file)):
            problems.append("测试文件不存在: %s" % test_file)
    return problems


def _summary(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        match = re.search(r"(\d+ failed[^\r\n]*|\d+ passed[^\r\n]*)", line)
        if match:
            return match.group(1)
    return lines[-1] if lines else ""


def _run(copy_root, nodes, timeout):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([copy_root, os.path.join(copy_root, "tests")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("TZ", "Asia/Shanghai")
    command = [PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + list(nodes)
    try:
        proc = subprocess.run(command, cwd=copy_root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=timeout)
        return proc.returncode, _summary(proc.stdout + "\n" + proc.stderr), proc.stdout
    except subprocess.TimeoutExpired:
        return 124, "timeout", ""


def _copy_tree():
    dest = tempfile.mkdtemp(prefix="amo-reverse-")
    shutil.copytree(ROOT, dest, ignore=IGNORE, dirs_exist_ok=True)
    return dest


def run_check(copy_root, check, timeout=300, baseline=True):
    target = os.path.join(copy_root, check["target"])
    original = _read(target)
    original_sha = _sha(original)
    result = {"id": check["id"], "area": check["area"], "label": check["label"],
              "target": check["target"], "tests": check["tests"], "baseline": None,
              "red": None, "green": None, "ok": False, "errors": validate(check)}
    if result["errors"]:
        return result

    if baseline:
        code, summary, _out = _run(copy_root, check["tests"], timeout)
        result["baseline"] = {"rc": code, "summary": summary}
        if code != 0:
            result["errors"].append("基线不是绿的（rc=%s），反向验证无意义" % code)
            return result

    text = original.decode("utf-8")
    # Windows 仓库源码是 CRLF：锚点按 LF 写，先归一化再替换，否则改坏会静默不生效。
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
    for edit in check["edits"]:
        text = text.replace(edit["old"], edit["new"], 1)
    _write(target, text.encode("utf-8"))
    try:
        code, summary, output = _run(copy_root, check["tests"], timeout)
        result["red"] = {"rc": code, "summary": summary, "tail": output[-1500:] if code == 0 else ""}
        if code == 0:
            result["errors"].append("改坏后仍然绿，反向验证失败")
        _write(target, original)
        if _sha(_read(target)) != original_sha:
            result["errors"].append("副本还原失败（sha 不一致）")
            return result
        code, summary, _out = _run(copy_root, check["tests"], timeout)
        result["green"] = {"rc": code, "summary": summary}
        if code != 0:
            result["errors"].append("恢复后不是绿的（rc=%s）" % code)
    finally:
        if _sha(_read(target)) != original_sha:
            _write(target, original)
    result["ok"] = not result["errors"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="P1-7 反向验证：改坏必红、恢复必绿")
    parser.add_argument("--list", action="store_true", help="只列清单并校验锚点")
    parser.add_argument("--only", default="", help="只跑某个 id")
    parser.add_argument("--keep", action="store_true", help="保留临时副本目录")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)

    checks = [check for check in CHECKS if not args.only or check["id"] == args.only]
    if not checks:
        print("没有匹配的检查 id: %s" % args.only)
        return 2

    if args.list:
        failed = 0
        for check in CHECKS:
            problems = validate(check)
            status = "OK" if not problems else "BAD: " + "；".join(problems)
            print("%-20s %-6s %-34s %s" % (check["id"], check["area"], check["target"], status))
            failed += 1 if problems else 0
        print("\n覆盖 %d 条；锚点异常 %d 条" % (len(CHECKS), failed))
        return 1 if failed else 0

    started = time.time()
    copy_root = _copy_tree()
    results = []
    try:
        for index, check in enumerate(checks, 1):
            print("[%02d/%02d] %s · %s" % (index, len(checks), check["id"], check["area"]))
            print("        改坏 %s：%s" % (check["target"], check["label"]))
            result = run_check(copy_root, check, timeout=args.timeout)
            results.append(result)
            if result["baseline"]:
                print("        基线  %s (rc=%s)" % (result["baseline"]["summary"],
                                                    result["baseline"]["rc"]))
            if result["red"]:
                print("        改坏  %s (rc=%s)  -> %s" % (
                    result["red"]["summary"], result["red"]["rc"],
                    "RED 符合预期" if result["red"]["rc"] != 0 else "仍然是绿，失败"))
            if result["green"]:
                print("        恢复  %s (rc=%s)  -> %s" % (
                    result["green"]["summary"], result["green"]["rc"],
                    "GREEN" if result["green"]["rc"] == 0 else "恢复失败"))
            if result["errors"]:
                for problem in result["errors"]:
                    print("        ERROR %s" % problem)
            print("        %s" % ("OK" if result["ok"] else "FAIL"))
            sys.stdout.flush()
    finally:
        if args.keep:
            print("临时副本保留在：%s" % copy_root)
        else:
            shutil.rmtree(copy_root, ignore_errors=True)

    ok = sum(1 for item in results if item["ok"])
    # 报告里不写绝对路径（仓库守卫禁止作者本机路径落盘）。
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "root": ".",
              "checks": results, "ok": ok, "total": len(results),
              "elapsed_sec": round(time.time() - started, 1)}
    with open(LOG_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("\n反向验证：%d/%d 通过；红/绿日志已写 %s" % (ok, len(results), LOG_PATH))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
