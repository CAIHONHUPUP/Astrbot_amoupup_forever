# 日记 P0 修复报告（task-20 / fix-diary-orphans，2026-11-22）

对应审计：work/audit/diary_facts.md（F1/F3/F6/F7/F8/F9）。本任务只做 P0 五项 + 测试 +
dry-run 清理脚本，不 reload、不 commit/push；生产库全程只读；仿真/测试都在临时库。

## 0. 结论

| P0 要求 | 状态 | 落地 |
|---|---|---|
| 1) is_valid 加 block 存在 + version + status | 完成 | AmoDB.block_diary_is_valid |
| 2) event_id diff 精确失效 + 孤儿清理 | 完成 | commit_schedule_change 按 event_id diff -> AmoDB.sync_block_diaries；mark_diaries_stale 不再按小时粗扫块表 |
| 3) 自审扩到孤儿/version/地点/同伴/吃饭/金额/健康/天气 | 完成 | amo_diary_audit 新增 orphan/version_mismatch/place_mismatch 三个 HARD 与 companion/meal/money/health/weather/weather_city/weather_missing/health_missing 报告项 |
| 4) 禁止块日记事实回流 | 完成 | _diary_facts 去掉 day_view_facts（块日记不再给下一块当事实）；_diary_hourly_lines 只读身份仍在当前日程里的块日记（孤儿不再进大日记 fallback） |
| 5) 生产清理脚本 dry-run + 备份、不执行 | 完成 | work/audit/diary_p0_cleanup.py：scan 只读；apply 默认拒绝 plugin_data、写前三重备份、单事务复扫；本任务只跑 scan |

## 1. 代码改动

### amo_db.py
- block_diary_is_valid(day, block_id, version)：
  - 仍要求 status=valid 且正文非空；
  - 新增 version 校验：记录 version 比传入的当前日程 version 还新（回滚/时间倒流）判无效；
  - 新增块存在性 + 身份/窗口校验：从 schedule 表读当前块，身份（event_id 优先、旧数据内容哈希）
    必须还在，且记录的 start/end 与当前块一致；读不到 schedule 表的老库/假件保持旧行为。
- 新增 _current_block_ids(day, items)：当前块身份集合（唯一口径）。
- 新增 sync_block_diaries(day, current_items, stale_ids, version)：日程落库点按 diff 精确失效
  + 孤儿清理。只标 stale（frozen/archived 不动、不删正文），返回 stale/orphans/invalidated 统计。
- mark_diaries_stale(day, from_hour, version)：只负责旧整点表 schedule_diary；
  去掉从 from_hour 起粗扫整张 block_diary 的旧逻辑（这是 11-21 快照 1 分钟 14 次 version bump
  造成 stale 风暴的直接原因）。mark_block_diaries_stale 保留给人工/回滚用，不再是每轮自动调用。

### amo_state_schedule.py
- 新增模块级 _DIARY_BLOCK_FIELDS / _diary_block_signature：只比较日记相关字段
  （start/end/activity/place/zone/category/detail/people/companions），
  快照补 cost_settled/locked/source 这类附加字段不再让日记失效。
- 新增 ScheduleStateMixin._changed_block_ids(day, old_items, new_items)：
  按 event_id 算 added/changed/removed；改块继承父 event_id 但日记字段变了 -> changed。
- 新增 _sync_block_diaries(day, old_items, new_items, version)：
  调用 db.sync_block_diaries；无 diff 时也清一次孤儿（升级后旧内容哈希行）。
- _commit_schedule_change_locked 两处调用：内容相同只补 event_id 的 early-return 分支、
  正常落库分支（save_day 之后、发布 ScheduleChanged 之前）。日志输出失效统计。

### amo_diary_audit.py
- run() 新增：
  - 孤儿扫描 _orphan_entries：当天全部 block_diary 行（不再只遍历当前块），
    valid 且身份不在当前日程 -> HARD orphan，apply 时 mark_block_diary_stale；
  - 版本校验 _record_newer：记录 version 大于当前日程 version -> HARD version_mismatch；
  - 每块事实核对 _block_fact_entries：地点（家块第一人称「我坐地铁/去公司/逛街」-> HARD
    place_mismatch，标 stale 重写）、同伴（面对面断言或没有同伴事实却用「我们」）、
    吃饭（meal 块说没吃）、金额（正文金额与块窗口账本差 >1 元）、健康（当天病 vs 说好了/没病）、
    天气（正文温度与当天快照差 >8 度；正文写日程城市但快照城市不同）；
  - 按天事实覆盖 _day_fact_warns：weather_day/health_day 快照缺失、天气城市与日程城市不一致；
  - counts 增加 orphans / facts；hard 条目仍保持 {start,end,kind} 形状（面板/通知兼容）。
- 新增只读辅助：_health_facts（快照优先，缺了用日程身体块兜底）、_weather_facts（只认按天快照）、
  _amounts_in_window（块窗口账本金额）、_canonical_city（schedule_city 优先）。
  为不突破架构棘轮，没有新增模块依赖（全部走 db.kv_get / cfg_str / wallet.ledger 读）。

### amo_state_diary.py
- _diary_facts：去掉 day_view_facts 叠加。块日记正文不再作为下一块的事实素材；
  上一块正文仍作为 prev_diary 文风/情绪锚点（context 不是 fact）。

### amo_schedule.py
- 新增 _current_block_diary_rows(day)：块表行按 block_id 对齐当前日程块身份，
  孤儿/旧内容哈希行不读；日程读不到（空）时退回旧行为。
- _diary_hourly_lines：块模式只读上述对齐行；旧整点表回滚分支兼容 (hour,text) 两列。

## 2. 真实库 dry-run 指标（2026-11-22 11:04 快照，只读）

命令：
    python work/audit/diary_p0_cleanup.py --today 2026-11-22 --out work/audit/diary_p0_plan.json --report work/audit/diary_p0_cleanup_report.md

- 计划 digest=105c52c950dc（以最后一次 scan 重算为准），可清理 42 条，全部是
  orphan_valid（标 stale、正文保留）；same_start 多余 valid 6 条，其中都已被孤儿动作覆盖。
- P0 审计窗口：11-17 3 条、11-18 24 条、11-19 5 条、11-20 2 条（审计快照报 3/25/5/2，
  11-18 差 1 条来自两次快照之间生产实例仍在写库/补块）。
- 另外历史天（11-11 1、11-15 2、11-16 5）也有身份对不上的 valid 行，按同一口径一并给出计划；
  是否 apply 由人工复核后决定。
- 版本回滚 0、空正文 valid 0、身份两列不一致 0。
- 按天事实缺口：weather_day 快照缺 15 天（11-07..11-21），health_day 缺 16 天
  （11-07..11-22）；城市口径冲突 schedule_city=东莞 / weather_city_name=清远。
- 详细逐日表见 work/audit/diary_p0_cleanup_report.md，原始计划见 diary_p0_plan.json。

## 3. 清理脚本安全验证（隔离副本，未写生产）

- 把生产库 sqlite 只读 backup 到系统 temp，再对副本 scan -> apply -> 复扫：
  备份生成 amo_forever.db.bak-diaryp0-*、审计键 diary_p0_cleanup:* 写入副本，
  apply 后复扫 actionable=0；
- 对 plugin_data 真实路径 apply（不带 --allow-live）直接 [refused] exit=2，生产库零改动；
- --allow-live 必须同时给 --backup-dir，写前用 sqlite backup 热备（含 wal/shm）再执行；
- digest 被改 / confirm 不对 -> refused；操作在单事务里，复扫不为 0 整体回滚。

## 4. 测试证据

新增测试：
- tests/test_diary_p0_orphan_version.py（21 个用例）：is_valid 的块存在/窗口/version、
  sync 精确失效与孤儿清理、commit diff（改一块只失效该块；删块清孤儿；快照补字段不失效）、
  自审孤儿/version/地点/同伴/吃饭/金额/健康/天气、_diary_facts 与 _diary_hourly_lines 不回孤儿。
- tests/test_diary_p0_cleanup.py（5 个用例）：scan 五类问题 + digest 稳定、副本 apply 备份/复扫 0、
  真实库默认拒绝、--allow-live+backup-dir 备份、篡改计划/错误 confirm 拒绝。

回归（本机 python D:\dafeiyu\uv\tools\astrbot\Scripts\python.exe）：
- 关键组（test_diary_p0_orphan_version + test_diary_p0_cleanup + test_diary_audit +
  test_block_diary + test_spec_block_diary_consistency + test_schedule_diary_chain）：
  130 passed；
- 最终扩展清单（95 个含 diary|audit|schedule|commit|event_id|fact|day_view|narrative|
  timeline|block_diary|clean_schedule 的测试文件）：1295 passed、57 subtests passed、
  1 failed；失败是 tests/test_schedule_audit_invariants.py 发现 amo_scene_ledger.py
  （另一个任务新加的场景账本模块）源码里出现 _commit_schedule_change_locked，
  与日记改动无关；完整日志 work/audit/diary_p0_tests_final.log；
- 期间其他任务在并发改 amo_shared/amo_schedule/amo_scene_ledger 等文件，出现过
  短暂的 collection error；等它们写完重跑即为上面的 1295 passed。
  我对 amo_db / amo_diary_audit / amo_schedule / amo_state_diary / amo_state_schedule
  逐个比对 architecture_baseline 的 fanout extra 均为空（没有新增模块依赖）。

## 5. 没做 / 需人工决定

- 生产库 apply 未执行（按任务要求只 dry-run）。执行前建议：
  1. 人工复核 diary_p0_plan.json，重点看 11-11/15/16 的历史行是否也要清；
  2. 在副本上先 apply 验证，再对生产库用 --allow-live --backup-dir（备份目录放在
     plugin_data 之外）执行；
  3. 执行后重跑 scan 确认 0。
- 天气城市口径冲突（清远 vs 东莞）是配置问题，属于生产配置，不在仓库里改：
  需要把 weather_city_name/weather_city 与 schedule_city 对齐（或明确weather=居住地）。
- 健康/天气历史按天快照无法回溯（写入逻辑只对当天写快照），11-17..21 只能靠日程身体块兜底；
  这部分已进自审报告（weather_missing/health_missing）。
- 跨源冲突的根因（账本/社交事件不进日程、JIT 整份覆盖、大日记 pool 含全部聊天/账本）
  不在本任务 P0 范围：自审现在能报 place_mismatch/money 等维度，但块日记重建时事实池仍会
  把账本挂到覆盖块上，09:00「在家+逛街消费」可能复现。建议下一任务做统一 DayTimeline
  （审计报告第 5 节方案）。
- hourly（schedule_diary）与块层仍并存；本次只保证孤儿不进大日记，两种「小日记」语义合并
  留给后续任务。
