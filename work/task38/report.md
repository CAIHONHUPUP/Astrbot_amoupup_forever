# task-38 修提醒 Mock 残留 7 条

- 时间：2026-11-22 13:13（Asia/Shanghai）
- 范围：`amo_proactive.py`（提醒/追问闸门）、`work/sim/remind_sim.py`（仿真接线保真）、`tests`
- 约束遵守：未 reload/commit/push，未写生产库；仿真全程内存 sqlite（`sim_lib.FakeDB`）。

## 1. 指标（work/sim/remind_report.json）

| 指标 | 修复前 | 修复后 | Δ |
| --- | --- | --- | --- |
| 判定消息 | 240 | 240 | 0 |
| 通过 | 233 | **240** | **+7** |
| 失败 | 7 | **0** | -7 |
| clean 过/败 | 119/1 | 120/0 | +1/-1 |
| defect 拦/漏 | 114/6 | 120/0 | +6/-6 |

- 8 项逐项检查全部 0 违反（remind 80 / followup 80 / reply 80 / road 24 / cancelled 39）。
- 失败分类：`{repeat_same_question:6, double_synonym_nag:6, false_positive_suppressed:1}` → `{}`。
- 定向复核验证 4/4 通过；clean 追问在 G06 恢复发送（原文「我这边也在忙，等你忙完再说。」）。
- 复现命令：`python work/sim/remind_sim.py`（报告自动写 work/sim/remind_report.{json,md}）。

## 2. 定位（不是样例补丁，按链路收口）

### RC10 追问重复上一句没人拦
- 现场：6 条 defect 泄漏全在 cycle2，交付文本就是「你忙完了没？呀？」（上一句追问 + 语气词）。
- 根因：原去重只比「最近 5 条主动消息」，上一句追问在第 7 条时窗口抓不到；生产追问生成只跟
  「上一条主动消息（提醒）」比，`proactive_followup_last_*` 只写不读，等于没有追问状态。
- 修法（统一规则）：新增 `followup_repeated(text, previous)` —— 复用 `merge_synonym_questions`
  的问句核心归一（剥标点/语气词/人称 + `_same_question`），问句核心相同即同一问；
  追问发送前作为**唯一闸门**比「上一次真发出去的追问 + 上一条主动消息」，重复不发并回滚占坑。
- 状态收口：追问**发送成功后**才写 `proactive_followup_last_*` + `log_proactive(uid,"followup",text)`
  + `pending["followup_text"]`；没发出去的句子不算说过。
- 生成侧：同一条里同义连问也走 `merge_synonym_questions`；上一句追问进 prompt（「这次别再问同一件事」）。

### RC11 追问复核误用提醒的迟到窗口 + 旧键不兼容
- 现场：clean 变体 1 条被 `amo_proactive.schedule_fact_current` 误杀（false_positive_suppressed）。
- 根因：追问前复核复用提醒的迟到窗口（开始后 5 分钟），而追问本来就发生在提醒之后——日程一开始
  追问就被当过期作废、pending 被清空；复核还只认「活动｜地点｜开始时间」三段键，旧版 pending
  两段键一律判取消（仿真 harness_fallback 的 2 段键即被误杀）。
- 修法：`schedule_fact_current(db, fact, now=None, late_window=True)` 新增 `late_window`；
  追问链路传 `late_window=False`（只复核还在/没改期），迟到窗口只约束提醒发送；
  复核匹配兼容旧两段键（活动｜地点，开始时间仍要一致，改期照样拦）。

## 3. 代码改动（production）

`amo_proactive.py`
1. `schedule_fact_current`：+`late_window` 参数；+旧两段键兼容匹配。
2. 新增 `_question_cores` / `followup_repeated`（复用 `_question_core`/`_is_question_clause`/`_same_question`）。
3. `_followup_text_live`：先 `merge_synonym_questions` 再跟上一句比；`last_followup` 进 prompt；
   生成阶段不再写 `proactive_followup_last_*`（改为发送成功写，避免「没发出去算说过」）。
4. `_check_proactive_pending_once`：追问前复核改 `late_window=False`；发送前追问闸门
   （`followup_repeated`）；发送成功后写 kv + proactive_log + `pending["followup_text"]`。

`work/sim/remind_sim.py`（仿真接线保真，不是降低判定）
1. `guard_stack` 增加 `prev_followups` 实参并调用生产 `P.followup_repeated`（近聊 5 条窗口抓不到两轮前的追问）。
2. 追问复核调用传 `late_window=(action=="remind")`，与生产一致。
3. 报告渲染：0 失败时的结论/空表/根因补充 RC10/RC11，去掉过期行号。

`tests`（+13 用例）
- `test_proactive_wiring.py`：`FollowupRepeatedTests`(6)、`schedule_fact_current` 旧键/late_window(2)。
- `test_cov2_proactive.py`：追问闸门拦截并回滚、发送后记录 kv+log、追问复核不吃迟到窗口(3)。
- `test_followup_style.py`：同义问句合并、上一句追问进 prompt(2)。

## 4. 验证

- `pytest tests -k "proactive or outbound or followup or reminder or message or anti_nag or schedule"` → **1295 passed**。
- `pytest tests -k "golden or prompt or contract or naming_contract"` → **379 passed**。
- 本次改动文件相关（wiring/cov2/followup_style/outbound_gateway/proactive_consistency/
  silence_gate/reminder_singleflight/architecture_ratchet/lint）→ 218 passed / 1 failed。
- 唯一红项 `test_no_new_internal_imports`（`amo_state_consistency.py -> amo_scene_ledger / amo_question_guard`）
  是并行任务正在改的文件，不在本次改动内（同一红项在改动前就存在，只是并行改动让它变多）。
- `work/sim/remind_sim.py` → 240/240，delta 见上表。

## 5. 残留/交接

1. 真实模型未复跑（隔离环境无 provider）：追问 prompt 避重复 / 去重后的措辞自然度未覆盖。
2. `proactive_schedule_reminder` 仍默认 False 且不在 `_conf_schema.json`（任务前既有，未动）。
3. 报告 §2.9 定向检查「提醒路径：到家跟我说一声 → 我还在路上呢」仍会改口（`amo_state_consistency.rewrite`
   缺预计/请求豁免），属独立残留、不计入 240 条主指标，本次未改。
4. 追问被判重复时回滚占坑，下一 tick 会重新生成（与既有「生成不出来就不发」同一重试循环）；
   prompt 已把上一句追问给模型，正常不会再生成同问。
