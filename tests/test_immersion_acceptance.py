# -*- coding: utf-8 -*-
"""IMM 出戏验收（task-16）：100 条 × B off/on 的离线回归门。

链路由 work/immersion_check/imm_harness.py 复刻生产顺序：
_guard_self_location -> apply_fact_guard(A+语气池+B pending) -> apply_narrative_repetition
-> apply_tone_guard -> [B on] apply_pending_tone_pick_async -> _send_to_umo(send 阶段)。

这里断言的是「产品口径」，不是「当前实现」：
- 零容忍：事实矛盾=0、复读=0、答非所问=0、系统腔/元信息=0；
- 质量线：情绪保留>=95%、机械改写<=10%、B 回退<=5%、B p95<=1500ms；
- 已知缺陷（P0）在 tests/golden 报告里列明，未修前本文件的 gate 会是红的——这正是验收结论。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work", "immersion_check")
for _p in (ROOT, os.path.join(ROOT, "tests"), WORK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import imm_harness as H  # noqa: E402
import run_imm as IMM  # noqa: E402


def _dataset():
    if not os.path.exists(IMM.DATASET_PATH):
        pytest.skip("imm_dataset.json 不存在：先跑 work/immersion_check/build_dataset.py（需要本机 amo 数据）")
    with open(IMM.DATASET_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _run(dataset, mode="off"):
    state = {}
    return [IMM.run_item(item, mode, 0.0, state) for item in dataset["items"]]


@pytest.fixture(scope="module")
def dataset():
    return _dataset()


@pytest.fixture(scope="module")
def off_runs(dataset):
    return _run(dataset, "off")


@pytest.fixture(scope="module")
def on_runs(dataset):
    return _run(dataset, "on")


def test_dataset_has_100_items_60_real_40_adversarial(dataset):
    assert len(dataset["items"]) == 100
    real = [item for item in dataset["items"] if item["source"].startswith("real")]
    synth = [item for item in dataset["items"] if item["source"] == "synthetic"]
    assert len(real) == 60 and len(synth) == 40
    assert len(dataset["synthetic"]) == 40
    for item in dataset["items"]:
        assert 4 <= len(item["turns"]) <= 6, item["id"]


def test_dataset_covers_16_categories(dataset):
    required = {"吃", "地点", "生病", "宵夜", "约会", "旅游", "家人朋友聚餐", "上班",
                "睡觉", "主动消息", "用户纠正", "元问题", "复读", "诱导说错地点",
                "系统腔诱导", "连发追问"}
    assert set(dataset["category_hist"]) == required
    assert all(dataset["category_hist"][name] > 0 for name in required)


def test_chain_produces_text_for_every_turn(off_runs, on_runs):
    for runs in (off_runs, on_runs):
        for run in runs:
            for turn in run["turns"]:
                assert turn["sent"].strip(), (run["id"], turn.get("user"))


def test_fact_and_leak_zero_in_both_modes(dataset, off_runs, on_runs):
    for mode, runs in (("off", off_runs), ("on", on_runs)):
        summary = IMM.summarize(dataset, {"off": off_runs if mode == "off" else off_runs,
                                          "on": on_runs, "cold": {}})
        counts = summary["per_mode"][mode]["counts"]
        assert counts["事实矛盾"] == 0, summary["per_mode"][mode]["issues"]
        assert counts["系统腔/元信息"] == 0, summary["per_mode"][mode]["issues"]


def test_zero_tolerance_gate(dataset, off_runs, on_runs):
    """零容忍四项：当前实现是否达标（P0 未清时这里就是红的）。"""
    summary = IMM.summarize(dataset, {"off": off_runs, "on": on_runs, "cold": {}})
    for mode in ("off", "on"):
        per = summary["per_mode"][mode]
        assert per["counts"]["复读"] == 0, per["issues"]
        assert per["counts"]["答非所问"] == 0, per["issues"]
        assert per["counts"]["改写不通顺"] == 0, per["issues"]


def test_quality_lines(dataset, off_runs, on_runs):
    summary = IMM.summarize(dataset, {"off": off_runs, "on": on_runs, "cold": {}})
    for mode in ("off", "on"):
        per = summary["per_mode"][mode]
        assert per["emotion_keep_rate"] >= 0.95
        assert per["mechanical_rate"] <= 0.10
    assert summary["b_fallback_rate"] <= 0.05
    assert summary["latency"]["p95_ms"] <= 1500


def test_b_disabled_is_byte_identical_to_a(dataset):
    a_state, b_state = {}, {}
    sample = dataset["items"][:20]
    left = [IMM.run_item(item, "off", 0.0, a_state) for item in sample]
    right = [IMM.run_item(item, "off", 0.0, b_state) for item in sample]
    for x, y in zip(left, right):
        assert [t["sent"] for t in x["turns"]] == [t["sent"] for t in y["turns"]]


def test_b_fault_injection_falls_back_without_crash(dataset):
    fault = IMM.fault_injection(dataset, limit_turns=8)
    for name, case in fault["cases"].items():
        assert case["fact_issues"] == 0, (name, case)
    assert fault["calls_total"] > 0


def test_p0_samples_are_fixed(dataset, off_runs, on_runs):
    """task-33 的四个复读样本 + 一个答非所问样本：逐条钉死。"""
    summary = IMM.summarize(dataset, {"off": off_runs, "on": on_runs, "cold": {}})
    for mode in ("off", "on"):
        issues = summary["per_mode"][mode]["issues"]
        for item_id in ("S06", "S21", "S22", "S25"):
            bad = [issue for issue in issues[item_id] if issue["type"] == "复读"]
            assert not bad, (mode, item_id, bad)
        bad = [issue for issue in issues["R38"] if issue["type"] == "答非所问"]
        assert not bad, (mode, "R38", bad)


def test_p0_samples_really_have_duplicate_candidates(off_runs):
    """反向验证：样本自己就有多轮同句，去掉去重层就会复读（不是测试太松）。"""
    by_id = {run["id"]: run for run in off_runs}
    candidates = [turn["candidate"] for turn in by_id["S21"]["turns"]]
    assert candidates.count(candidates[1]) >= 2
    sent = [turn["sent"] for turn in by_id["S21"]["turns"]]
    for before, after in zip(sent, sent[1:]):
        assert before != after


def test_report_is_generated_and_backed_up():
    assert os.path.exists(IMM.REPORT_PATH)
    assert os.path.isdir(IMM.BACKUP_DIR)
    assert any(name.startswith("IMM_REPORT_") for name in os.listdir(IMM.BACKUP_DIR))
