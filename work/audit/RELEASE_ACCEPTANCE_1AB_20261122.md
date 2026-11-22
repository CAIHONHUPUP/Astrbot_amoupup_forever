# 1A / 1B 验收报告（2026-11-22，v7.6.4 候选）

范围：撒娇版自然度（1a）+ 守卫收口/罐头句修复（1b）。版本 7.6.4，本轮**不 push、不发包、不 reload**。

补充：判词检测已加「这就对了」这类上位者评词；线上 20:43「你早点躺下」属命令式，归 P1 的说话方式回归。

## P0（阻断项，必须清零）

| 项 | 结果 | 证据 |
|---|---|---|
| 全量门禁（pytest + mypy + pyflakes + schema/routes/silent/async + unittest） | **6309 passed / mypy 0 / unittest ok** | `_diag/gate_forever_r14_1ab.txt` |
| 固定回归 daily 50/50 | 50/50，614/614 轮，residual 0，guard_introduced 0 | `work/sim/daily_report.json` |
| 固定回归 share 500/500 | 500/500，damaged 0，persistent 0 | `work/sim/share_report.json` |
| 固定回归 remind 240/240 | 240/240（clean 120/120，defect 120/120） | `work/sim/remind_report.json` |
| 39 条对抗 | 39/39（addressee 14、role 4、place 4、number 4、repeat 1、repeat_question 1、share 5、system 6） | `work/audit/holdout/adversarial.json` |
| 184 生产回放 | 175 bot（重建语料），fixed 25，persistent 2，**clean 新误伤 0**，narration before 85 → after 0 | `work/audit/holdout/replay_prod.json` |
| 1a 真模型 A/B | 30 对：原词重合 0.16 → 0.221，语气词 86.7% → 100%，判词 0，长度 20.6 → 19.0 | `_diag/ab_naturalness2.json` |

## 1a 改动

- `persona_packs/style_examples.json`：33 条（从 45 条池筛，fit2 为主 + 3 条人工）。
- `amo_persona_pack.py`：意图路由（care/praise/miss/food/place/playful/daily）+ 确定性取 2 条 + `style_block`。
- `amo_prompt_registry.py`：新增 `llm.rule.style`（撒娇版 6 条准则），私聊专用。
- `amo_llm._reply_system`：风格块放 system 末尾（权重更高）+ 动态 few-shot。
- `amo_persona_guard`：`naturalness_issues` / `note_naturalness`（只判不改）+ `apply_chat_safety` 埋点。
- 测试：`tests/test_naturalness_style.py` 7 项；prompt golden 仅 private_system / private_system_note 变化。

## 1b 改动

### 1b-1 候选优先收口
- 删除 `_prefer_answered_candidate`（裸还原，和 recover 重叠）。
- 新增 `_restore_partial_candidate`：只在“候选被局部删除成残句、且候选答到”时还原，并入同一出口。
- `_TURN_SOURCE` 仍由 final recheck 使用，不再三层叠加。

### 1b-2 罐头句 / 删句不变量
- `reply_fact_guard._health_rule`：问句/关心句（多久/吃药/看医生/要不要/？）不再当她的健康声明；修掉「就是有点累？」。
- `amo_reply_pipeline._keep_answer`：删句后若只剩“再谈/那就/才能…”孤立结论，保留原句；修掉「再谈收拾我这件事」缺“等你病好了”。
- 测试：`tests/test_reply_fact_guard.py` care 问句 4 例、`tests/test_dedupe_orphan.py` 3 例。

## P1（验收后必须排期，不进本轮）

1. `prepare_outbound` 与 `sanitize_candidate` 约 35 行同序逻辑合并。
2. `repetition_guard` 的动作兜底池与 `amo_message._ACTION_FALLBACK` 统一。
3. `place_guard` 与 `reply_fact_guard` 地点纠正二选一（保留事实守卫）。
4. 收件人守卫 5 个调用点收敛到 2 个。
5. `_SELF_THIRD` 子串替换（“儿半天了”）改整句丢弃/重生成。
6. health 语气池首项“就是有点累”仍可能在“她真的累”时固定出现，需按心情/关系轮换。

## P2（观察/记录）

- 1a 的 A/B 只有 30 对，且是固定系统提示词的离线对比；上线后看真实聊天 50 条的判词/语气词/长度。
- 1b 的 184 回放语料 dump 在清理 `_scratch` 时丢失，本轮用 DB+备份重建（175 bot），fixed/persistent/clean 与 7.6.3 一致。
- 未 reload：线上仍是 7.6.3（含 A，不含 1a/1b）。
- 回滚点：7.6.3 tag `v7.6.3` / commit `d11c86d`；1a/1b 未提交。

## 补充（20:5x）

- 7.6.4 快速门禁：**mypy 0 / pyflakes clean / silent except 0 / function sizes ok**（`_diag` 输出）。
- 判词检测补充「这就对了」这类上位者评词；线上 20:43「你早点躺下」是命令式，归入 P1 说话方式回归。
- 日程问题根因：`amo_transition.py:1228/1313` 回程模板无条件用前块 place，`from_place=家` 时拼出「刚出家，坐地铁回家」；3/5/7 天测试只查块合法性，未查路径连续性。**根修方案**：连续时间线 + 转换校验（同地点不生成交通、回家后不再生成出门回程、同行人必须带到场时间），不是再加 if。
- 社交日程待定：A1 默认无社交（朋友只手动/聊天出现）、A2 每周一次匿名“和朋友”、B 删除关系。**未改**，等用户选择。
- 1b-3（3a/3b/3c）未做，保留在 P1：prepare_outbound/sanitize_candidate 合并、动作兜底池统一、place_guard/reply_fact_guard 二选一、5 个收件人调用点收敛、_SELF_THIRD 整句处理。
