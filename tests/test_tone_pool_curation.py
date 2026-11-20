# -*- coding: utf-8 -*-
"""A 方案：离线生成语气池的质量门。

离线流程（本次实际执行）：
1. 对每个规则/场景让模型生成 20-30 条同义语气；
2. 规则筛：无数字/金额、无新人名、无新地点、无后台词、长度接近原句；
3. 人工保留 8 条进代码；
4. 运行时只做确定性轮换，不调用 LLM。

这里把「离线筛选后的结果」钉死：任何带事实/带名字/带金额/带 LLM 运行时的改动都会红。
"""
from __future__ import annotations

import inspect
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_reply_pipeline as pipeline  # noqa: E402

#: 本次离线生成使用的提示词（只作记录，运行时不会用到）
OFFLINE_PROMPT = (
    "让 LLM 为下面这个冲突改口场景生成 20-30 条中文同义语气：只改语气，不加事实；"
    "不许出现数字、金额、新人名、新地点、新活动、后台词；长度接近原句。"
)

BACKEND_WORDS = ("AI", "ai", "模型", "系统", "程序", "插件", "API", "人机",
                 "机器人", "客服", "提示词", "润色")
PERSON_NAMES = ("柳如烟", "白凝冰", "季博达", "方圆", "沈幼微", "妈妈", "爸爸")
MONEY_RE = re.compile(r"\d|块钱|元|人民币|红包|工资")
PLACE_WORDS = ("公司", "学校", "商场", "饭店", "医院", "地铁", "景区", "酒店")


def _pools():
    """所有待校验的语气组：TONE_POOLS + 三个单列表池。"""
    rows = []
    for rule, groups in pipeline.TONE_POOLS.items():
        for group, pool in groups.items():
            rows.append(("%s/%s" % (rule, group), pool))
    rows.append(("health/recovery", pipeline.RECOVERY_TONE))
    for key, pool in pipeline.MEAL_TONE.items():
        rows.append(("meal/%s" % key, pool))
    for key, pool in pipeline.ACTIVITY_TONE.items():
        rows.append(("activity/%s" % key, pool))
    for scene, pool in pipeline.PLACE_TONE.items():
        rows.append(("place/%s" % scene, pool))
    return rows


def test_curation_note_records_offline_process():
    note = pipeline.TONE_POOL_CURATION_NOTE
    assert "离线生成" in note
    assert "20-30" in note
    assert "只改语气" in note
    assert "不加事实" in note
    assert "5-8" in note
    assert "LLM" in OFFLINE_PROMPT and "不加事实" in OFFLINE_PROMPT


def test_runtime_guard_path_never_calls_llm():
    """A 方案关键反向验证：事实守卫路径里一旦出现 LLM 调用就红。"""
    source = "\n".join([
        inspect.getsource(pipeline.apply_fact_guard),
        inspect.getsource(pipeline.naturalize_fact_rewrites),
        inspect.getsource(pipeline._natural_clause),
    ])
    assert "await " not in source
    assert ".chat(" not in source
    assert ".polish(" not in source
    assert "text_chat" not in source
    # 同步守卫只记 pending（tone_llm_pick_enabled），真正的 LLM 调用在 async 模块。
    assert "apply_fact_guard_async" not in source
    assert "apply_pending_tone_pick_async" not in source


def test_each_group_is_curated_to_eight_variants():
    for name, pool in _pools():
        assert len(pool) == 8, "%s 只有 %d 条" % (name, len(pool))


def test_pool_variants_are_distinct():
    for name, pool in _pools():
        assert len(set(pool)) == len(pool), "%s 有重复: %r" % (name, pool)


def test_pool_variants_have_no_digits_or_money():
    for name, pool in _pools():
        for item in pool:
            assert not MONEY_RE.search(item), "%s 带金额/数字: %r" % (name, item)


def test_pool_variants_have_no_backend_words():
    for name, pool in _pools():
        for item in pool:
            for word in BACKEND_WORDS:
                assert word not in item, "%s 带后台词 %r: %r" % (name, word, item)


def test_pool_variants_have_no_extra_person_names():
    for name, pool in _pools():
        for item in pool:
            for person in PERSON_NAMES:
                assert person not in item, "%s 带人名 %r: %r" % (name, person, item)


def test_health_pools_do_not_claim_illness():
    pools = [
        pipeline.TONE_POOLS["health"]["tired"],
        pipeline.TONE_POOLS["health"]["sweet"],
        pipeline.TONE_POOLS["health"]["default"],
    ]
    for pool in pools:
        for item in pool:
            assert "发烧" not in item and "感冒" not in item
            assert "病" not in item and "烧" not in item and "咳" not in item


def test_companion_pools_do_not_claim_togetherness():
    for group in ("close", "busy", "default"):
        for item in pipeline.TONE_POOLS["companion"][group]:
            assert "抱" not in item and "旁边" not in item
            assert "陪我" not in item and "在我身边" not in item


def test_meal_pools_do_not_claim_eating_now():
    for pool in pipeline.MEAL_TONE.values():
        for item in pool:
            assert "正在吃" not in item and "刚吃完" not in item
            assert "吃过了" not in item and "我在吃" not in item


def test_place_templates_keep_target_placeholder():
    for scene, pool in pipeline.PLACE_TONE.items():
        for template in pool:
            assert "{t}" in template, "%s 模板丢地点: %r" % (scene, template)
            assert set(re.findall(r"\{(\w+)\}", template)) <= {"p", "t", "s"}
            assert "还在{p}" not in template


def test_first_variant_keeps_old_mechanical_sentence():
    assert pipeline.TONE_POOLS["health"]["tired"][0] == "就是有点累"
    assert pipeline.TONE_POOLS["health"]["sweet"][0] == "就是有点累"
    assert pipeline.TONE_POOLS["health"]["default"][0] == "就是有点累"
    assert pipeline.TONE_POOLS["companion"]["close"][0] == "刚才在手机上聊了几句"
    assert pipeline.RECOVERY_TONE[0] == "还是有点不舒服"
    assert pipeline.MEAL_TONE["等会儿弄点吃的"][0] == "等会儿弄点吃的"
    assert pipeline.ACTIVITY_TONE["上班"][0] == "上班"
    assert pipeline.PLACE_TONE["work"][0] == "{p}{t}{s}"


def test_non_place_pools_do_not_add_place_names():
    for rule in ("health", "companion"):
        for pool in pipeline.TONE_POOLS[rule].values():
            for item in pool:
                for place in PLACE_WORDS:
                    assert place not in item, "%s 带地点 %r: %r" % (rule, place, item)
    for pool in pipeline.MEAL_TONE.values():
        for item in pool:
            for place in PLACE_WORDS:
                assert place not in item, "meal 带地点 %r: %r" % (place, item)


def test_pool_variants_are_short():
    for name, pool in _pools():
        for item in pool:
            assert len(item) <= 16, "%s 太长: %r" % (name, item)
