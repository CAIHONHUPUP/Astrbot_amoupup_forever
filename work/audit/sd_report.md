# sd_report：日程 + 日记回归（只读验证）

- 生成时间：2026-11-22 16:35（Asia/Shanghai）
- 仓库：`D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever`
- 角色：只读验证员。**未改生产源码、未写生产库、未 reload、未 commit/push**。
- 数据方式：日程仿真用内存 FakeDB + `clock.freeze`；日记仿真先对线上库做 sqlite `mode=ro` 快照再审计；改日程一致性用 `FakeDB(:memory:)`。
- 本次只新增 `work/audit/sd_*` 证据文件；未覆盖 `work/sim/` 下既有报告（wrapper 把 OUT_JSON/OUT_MD 重定向到 `work/audit/sd_*`）。

## 0. 结论速览

1. **日程 3/5/7 天：全部 0 失败。** 3 天 5 组 / 15 天、5 天 5 组 / 25 天、7 天 5 组 / 35 天，fails=0、warns=0；连同 7 个定向场景共 22 run / 85 天，fails=0、run_errors=0、生产库 sha256 未变。
2. **日记 3/5/7 天：真实问题 0。** 独立重跑 3 天=0、5 天=0（第 2 次；第 1 次 1 条为核对器否定句误判）、7 天=0；7 天一次跑内的 3/5/7 累积窗口也都是 0。所有 run 都是 4/4 检查通过、provider error=0、生产库未变。
3. **改日程 3 个规定场景（下午外出→在家休息、晚饭→餐厅、加一段加班）三者一致：通过。** 场景块、场景账本、提醒事实、日记提示词/正文全部指向同一个新 activity/地点；旧提醒被 `schedule_fact_current` 判失效，旧块日记被 `sync_block_diaries` 标 stale 后按同一 event_id 重生成。
4. **但发现一个真实对话旁路 bug（附加探针 S2b）：** 用聊天「我们出去吃吧」改晚饭，块会变成 **「和他一起出去吃饭 / 地点=家 / zone=home」**——activity 与地点自相矛盾，日记跟着写「在家…出去吃饭」。根因是 `_sync_zone` 不认「外面」，zone 留在 home，落库前 `normalize_place("外面","home")` 又把它折回「家」。**这条对话路径不达标，需要用 /改日程 或修 `_sync_zone`。**
5. 失败明细定位：日程没有失败明细；日记唯一一条是核对器把「没什么阳光」误判成「晴」；改日程唯一失败是 S2b 的地点归一链。

## 1. 版本钉定（跑批前后代码未变）

| 文件 | sha256 前 16 位 |
|---|---|
| schedule_engine.py | 1adf3cdd2ff480a3 |
| schedule_change.py | 941e0faf7d4b535c |
| amo_state_schedule.py | 7b92e9939c9b7249 |
| amo_state_diary.py | ca14201bb1c1879f |
| amo_proactive.py | 6ba14917599239eb |
| amo_scene_ledger.py | a0ff4aaa490c356b |
| amo_location_rules.py | 3f389b9e35b5e0fe |
| domains/schedule/place.py | 291c4d8cff0890d7 |
| work/sim/schedule_sim.py | b1ce4e2f90b5036e |
| work/sim/diary_sim.py | 9d8b66d565dc9373 |
| work/sim/diary_checks.py | 8151156a4d626a75 |

## 2. 任务一：日程 3/5/7 天回归

### 2.1 入口与命令（复用现有脚本，只换输出路径）

- 现有入口：`work/sim/schedule_sim.py`（默认 `HORIZONS=(3,5,7)`、`SEEDS=(7,11,42,123,2026)`，含 7 个定向场景）。
- 本次 wrapper：`work/audit/sd_run_schedule_sim.py`，只把 `OUT_JSON/OUT_MD` 重定向到 `work/audit/sd_schedule_report.json/.md`，再调用原 `run_all`。
- 命令：`python work/audit/sd_run_schedule_sim.py --horizons 3 5 7`
- 复用依据：`work/audit/_release_prep_logs/schedule_sim.log` 的 `random-h{3,5,7}-s{seed}-*` 命令行口径与本次一致（同一 5 个 seed、同一 city=东莞、同一 profile 规则）。
- 入口筛选：`work/simulate_day.py` 是单日一致性审计（不是 3/5/7 多日）；`tools/quality_gate.py` 相关项只有 schema/文件体量断言（`schedule_holiday_min_days`、`diary_engine.py` 体量、`_ensure_schedule_locked` 体量），没有多日日程仿真入口，故 3/5/7 以 `schedule_sim.py` 为准。

### 2.2 结果

| 窗口 | run 数 | 天数 | fails | warns |
|---|---:|---:|---:|---:|
| 3 天 | 5 | 15 | **0** | 0 |
| 5 天 | 5 | 25 | **0** | 0 |
| 7 天 | 5 | 35 | **0** | 0 |
| 定向场景（1/2 天） | 7 | 10 | 0 | 0 |
| **合计** | **22** | **85** | **0** | **0** |

逐 seed（每格 = fails/warns）：

| seed | 3 天 | 5 天 | 7 天 |
|---|---|---|---|
| 7 | 0/0 | 0/0 | 0/0 |
| 11 | 0/0 | 0/0 | 0/0 |
| 42 | 0/0 | 0/0 | 0/0 |
| 123 | 0/0 | 0/0 | 0/0 |
| 2026 | 0/0 | 0/0 | 0/0 |

- `FAILS_BY_CATEGORY`：drift 0、overlap 0、gap 0、transition_missing 0、meal_over 0、meal_duplicate 0。
- `RUN_ERRORS 0`；`REAL_DB_UNCHANGED True`；`GUARD_BLOCKED {'open':0,'sqlite':0,'amo_db':0}`、`VIOLATIONS 0`；`TEMP_CLEAN True`。
- **失败明细：无（0 条）**。若以后出现失败，明细在 `sd_schedule_report.json` 的 `runs[*].days[*].issues` / `samples`，判据在 `work/sim/schedule_checks.py`。

补充：按任务点名的 `work/schedule_7day.py` 也跑了一次（**不带 `--write`，未改 golden**）。当前代码生成的 7 天 payload 与 `tests/golden/schedule_7day_seed7.json` **逐天 sha256/block_count 全部一致（7/7）**，见 `sd_schedule_7day_check.json`。

## 3. 任务二：日记 3/5/7 天回归

### 3.1 入口与命令

- 现有入口：`work/sim/diary_sim.py`（生产大日记链路 + 事实池 + 守卫 + 落库；只读守卫自带）。
- 本次 wrapper：`work/audit/sd_run_diary_sim.py`，输出 `work/audit/sd_diary_report_<N>d.json/.md`。
- 命令：
  - `python work/audit/sd_run_diary_sim.py --days 3`
  - `python work/audit/sd_run_diary_sim.py --days 5`（跑两次，见 3.3）
  - `python work/audit/sd_run_diary_sim.py --days 7`
- 模型：真实 `deepseek-chat`（`work/sim/provider.env`，temperature=0.8 单次采样）；不是 mock。
- 入口筛选：`work/sim/diary_sim.py` 自带 3/5/7 累积窗口；`work/diary_audit.py` 是线上库块日记质检（读 mode=ro 快照，判 HARD/WARN），不是 3/5/7 日记生成仿真，故本次以 `diary_sim.py` 为准。
- 说明：diary_sim 日志里 PowerShell 报 `exit code 1` 是因为 python 的 logger 写 stderr 被当成 NativeCommandError；进程实际正常跑完并写了报告（JSON `fatal=false`、4/4 checks true、`wrote ...` 均在日志里）。判定以 JSON 为准。

### 3.2 结果

| 运行 | 天数 | 日记数 | 问题数 | 检查 | provider error | 生产库未变 |
|---|---|---:|---:|---|---:|---|
| 3d | 3 | 3 | **0** | 4/4 | 0 | True |
| 5d run1 | 5 | 5 | **1（误判）** | 4/4 | 0 | True |
| 5d run2 | 5 | 5 | **0** | 4/4 | 0 | True |
| 7d | 7 | 7 | **0** | 4/4 | 0 | True |

7 天那次跑批内部按 3/5/7 累积窗口统计：3 天窗口 0 条、5 天窗口 0 条、7 天窗口 0 条（`window_summary`）；4 条检查（有日记/无 provider error/无管线异常/健康快照注入）全部 True。

### 3.3 唯一一条问题明细（5d run1，判据误判，不是真实矛盾）

- 日期：2026-11-04；类别 `weather`，子类 `conflict:clear`。
- 核对器 detail：`不冷但也没什么阳光`；evidence：`当天天气=阴，日记写晴/多云`。
- 日记原文（节选）：`…阴天里吃这个还挺舒服…`、`天气阴阴的，十六度，不冷但也没什么阳光。`
- 判定：**真实天气=阴/16℃，日记写的是「阴天」「阴阴的」「没什么阳光」，语义完全一致。** 误判来自 `work/sim/diary_checks.py:183` 把「阳光」无条件记成 clear 词，`:604` 再与 `day_tag=overcast` 比对；没有处理「没什么阳光/没有太阳」这类否定句。5d 第二次重跑（run2）同一判据下 0 条，进一步说明是采样+否定句误判。
- 因此：**日记真实问题数=0；3/5/7 天各自问题数为 3d=0、5d=0（run2；run1 首跑 1 条误判）、7d=0。**

## 4. 任务三：改日程后「场景块 / 日记 / 提醒」一致性

### 4.1 怎么做的（全部走生产入口）

- 日程来源：`work/simulate_weeks.build_day_skeleton`（current code 生成 11-01 rest / 11-02 workday），再用生产 `commit_schedule_change(source='ensure')` 落进 FakeDB。
- 改日程入口：
  - S1/S2b：生产聊天判定 `schedule_change.detect_schedule_change` + `apply_change`，经 `state.apply_schedule(..., source='chat')` 提交（等价 `_apply_user_schedule_change` 的写路径）。
  - S2：生产命令式精确改 `schedule_change.apply_at`（等价 `/改日程 19:00 去餐厅吃晚饭` 组装出的 `{activity,place,category}`），经 `state.apply_schedule(..., source='command')` 提交。
  - S3：`state.apply_schedule(..., source='chat')` 在 17:00-19:00 追加「加班/公司/work」并替换窗口内旧块（等价聊天里加一段）。
- 三者读取口径：
  - 场景块：`amo_scene_ledger.scene_at(plugin, now)`（内部 `state.current_activity_info()`）。
  - 日记：生产 `state.expand_block(day, item, items)`（`_diary_prompt` → 生成 → 地点守卫 → `save_block_diary`）。LLM 用确定性 echo：只把提示词「这一小时：…在X」里的地点/动作写回正文，保证测的是生产提示词/守卫/落库，不是模型发挥。
  - 提醒：生产 `amo_proactive.schedule_reminder_fact(db, now=块开始-15min)`（提醒事实源），改后用 `schedule_fact_current(db, 旧fact)` 复核旧提醒是否作废。
- 场景时钟：改前取旧块中点/开始前 15 分钟；改后取新块中点/开始前 15 分钟；日记在块结束后 10 分钟生成（冻结时钟）。

### 4.2 三个规定场景 before/after 关键字段

| 场景 | 阶段 | 块 activity | place / zone | 场景账本 | 提醒事实（event / place / time） | 日记「这一小时」 |
|---|---|---|---|---|---|---|
| **S1 下午外出→在家休息**（chat「不去了」，11-01 15:00） | before | 继续逛、找地方坐着 | 东莞·商场 / mall | 同块 | 继续逛、找地方坐着 / 东莞·商场 / 14:00 | — |
| | after | 不去了，改回家待着 | 家 / home | 同块（quality=covering） | 不去了，改回家待着 / 家 / 14:00 | 14:00-15:28，不去了，改回家待着，在家（home） |
| **S2 晚饭→餐厅**（/改日程 结构化 19:00，11-01 17:30） | before | 炒两个菜 | 家 / home | 同块 | 炒两个菜 / 家 / 19:00 | — |
| | after | 去餐厅吃晚饭 | 餐厅 / restaurant | 同块 | 去餐厅吃晚饭 / 餐厅 / 19:25 | 19:25-20:01，去餐厅吃晚饭，在餐厅（restaurant） |
| **S3 加一段加班**（11-02 17:00-19:00） | before | 泡杯咖啡续命 | 公司 / office | 同块 | 泡杯咖啡续命 / 公司 / 17:00 | — |
| | after | 加班 | 公司 / office | 同块 | 加班 / 公司 / 17:00 | 17:00-18:37，加班，在公司（office） |

> S2 的新块起点从 19:00 变成 19:25，是生产落库时补了「家→餐厅」通勤块（19:00-19:25）后的真实结果；提醒/日记/场景都跟着 19:25 这条块走，三者仍一致。

### 4.3 三个规定场景的检查矩阵（11 项全 True）

| 检查 | S1 | S2 | S3 |
|---|---|---|---|
| block_activity_place_consistent | ✅ | ✅ | ✅ |
| scene_matches_block | ✅ | ✅ | ✅ |
| reminder_matches_block | ✅ | ✅ | ✅ |
| diary_prompt_matches_block | ✅ | ✅ | ✅ |
| diary_text_matches_block | ✅ | ✅ | ✅ |
| diary_guard_clean | ✅ | ✅ | ✅ |
| diary_not_pending | ✅ | ✅ | ✅ |
| old_place_absent_from_current_hour | ✅ | ✅ | ✅ |
| old_block_diary_invalidated | ✅ | ✅ | ✅ |
| old_reminder_invalidated | ✅ | ✅ | ✅ |
| new_diary_record_valid | ✅ | ✅ | ✅ |

- 旧提醒：改后 `schedule_fact_current(旧fact)=False`（key 里的 activity|place|start 已不存在），新提醒取到新块。
- 旧日记：`commit_schedule_change` 的 `_sync_block_diaries` 把旧 block_id 放进 stale_ids（S1 还包含被通勤切开的相邻块），随后同一 event_id 的 `expand_block` 重新生成并写回 valid；JSON 里保留了 `old_text_seed`（旧正文）与 `new_record`（新正文）的 before/after。
- **结论：3 个规定场景全部一致（`required_consistent=true`）。**

### 4.4 附加探针 S2b：同一句话走聊天「出去吃」→ 不一致（真实 bug）

| 阶段 | activity | place / zone | 场景/提醒 | 日记 |
|---|---|---|---|---|
| before | 炒两个菜 | 家 / home | 一致 | — |
| after（chat「我们出去吃吧」） | **和他一起出去吃饭** | **家 / home** | 场景、提醒都跟着「家」 | 「这一会儿我在家，和老公一起出去吃饭…」 |

- 检查结果：`block_activity_place_consistent=false`、`diary_text_matches_block=false`（正文里地点=家、动作=出去吃饭，自相矛盾）；其余「三方互相一致」的检查为 true——也就是说**三个来源都一致地错了**。
- 根因链（已用 `sd_s2b_rootcause.json` 固化）：
  1. `schedule_change.py:221-228` 的「出去吃/一起吃/去吃/请你吃饭」规则把 place 硬编码成 `外面`；
  2. `schedule_change.py:52-54` `_sync_zone` 调 `zone_for_place("外面")`，而 `domains/schedule/place.py:102-115` 的 `PLACE_ZONE_RULES` 没有「外面」→ 返回 `""` → `if not zone: return item`，zone 保留旧值 `home`；
  3. `amo_state_schedule.py:1636-1641` 落库前 `normalize_place("外面", "home")`，`domains/schedule/place.py:75-76` 对 `zone=home` 直接返回「家」；
  4. 结果块是「出去吃饭 / 家」，日记/提醒/场景都忠实地跟着这个错块。
- 讽刺的是 `photo_rules.py:39-46` 的 `EXPLICIT_PLACE_ZONES` 里其实有 `"外面": "road"`，但 `_sync_zone` 没有用它。
- 影响面：`sd_s2b_rootcause.json` 验证了「我们出去吃吧 / 出去吃 / 一起出去吃 / 去吃 / 请你吃饭」5 种说法全部命中；「去餐厅吃饭 / 我们去餐厅吃」则根本不触发改日程。
- 只读建议（本次未改）：`_sync_zone` 增加 `EXPLICIT_PLACE_ZONES` 兜底，或在 `PLACE_ZONE_RULES` 补 `("外面", "street"/"road")`，并让日志/断言覆盖「activity 含出去/餐厅时 place 不得为家」。

## 5. 失败明细定位汇总

| 项 | 结果 | 定位 |
|---|---|---|
| 日程 3/5/7 | 0 失败、0 warn、0 run error | 无失败明细；判据 `work/sim/schedule_checks.py`，报告 `sd_schedule_report.json` |
| 日记 3 天 / 5 天 run2 / 7 天 | 0 问题 | — |
| 日记 5 天首跑 | 1 条 `weather/conflict:clear`，**误判** | `work/sim/diary_checks.py:183`（`阳光` 正则）+`:604`（与 `day_tag` 比对）；原文明确写「阴天/阴阴的/没什么阳光」 |
| 改日程 S1/S2/S3 | 一致 | `sd_schedule_change_scenarios.json` 的 `checks` 全 true |
| 改日程 S2b（附加） | 不一致 | `schedule_change.py:221-228` → `:34-62` → `amo_state_schedule.py:1636-1641` → `domains/schedule/place.py:75-76/102-115` |

## 6. 证据文件

- `work/audit/sd_report.md`（本文件）
- `work/audit/sd_summary.json`（3/5/7 汇总 + 代码 pin + 场景检查矩阵）
- 日程：`sd_run_schedule_sim.py`、`sd_schedule_report.json`、`sd_schedule_report.md`、`sd_schedule_sim.log`、`sd_schedule_7day_payload.json`、`sd_schedule_7day_check.json`
- 日记：`sd_run_diary_sim.py`、`sd_diary_report_3d.json/.md`、`sd_diary_report_5d.json/.md`（run2）、`sd_diary_report_5d_run1.json/.md`（首跑，含那条误判）、`sd_diary_report_7d.json/.md`、`sd_diary_sim_3d.log`、`sd_diary_sim_5d.log`、`sd_diary_sim_5d_run2.log`、`sd_diary_sim_7d.log`
- 改日程：`sd_schedule_change_consistency.py`、`sd_schedule_change_scenarios.json`、`sd_schedule_change.log`、`sd_s2b_rootcause.json`

## 7. 限制

1. 日程是一次 22 run / 85 天的跑批，其中 3/5/7 各 5 组；不是每个窗口单独起 5 个进程，但每组 run 独立 make_plugin/临时库。
2. 日记是真实模型 temperature=0.8 单次采样；3/5/7 是三次独立重跑，不是同一批样本的前缀（7 天那次跑批内部的 3/5/7 累积窗口才是前缀）。5d 首跑那条是核对器否定句误判，不能算生产问题。
3. 改日程一致性只验证「写入口 → 场景/日记/提醒」的事实链，没有跑聊天 proposal 的 LLM 同意/拒绝分支（`_judge_schedule_proposal`）、没有发真实通知；日记用确定性 echo LLM 隔离提示词/守卫，不代表语义质量。
4. 未覆盖 reload/多进程 WAL 并发；但每次仿真都记录了生产库 sha256/size/mtime，全部 unchanged。 
