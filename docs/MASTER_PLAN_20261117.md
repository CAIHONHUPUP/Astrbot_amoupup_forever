# 三插件收尾总计划（2026-11-17）

> 规则：每项交付必须 P0/P1/P2 证据、≥2 反向验证、相关全量 pytest + gate 绿、备份 + 回滚点、未写 plugin_data（迁移除外）；不带红交付。

## Stage 0（已并行，等待交回）

| ID | 插件 | 内容 | 状态 |
|---|---|---|---|
| E1 | amo | event-id 贯穿日程/账本/块日记/记忆 | 进行中（H1–H6 已交付，待 lead 验收） |
| D1 | amo | 大日记 B（次日写昨天/启动补写/事实门/解锁） | 进行中 |
| U1 | watch+amo | 面板「决定」页（今天、2 天保留） | 进行中 |
| W1 | watch | 4 条 tzset 测试红 | 进行中 |
| W4 | watch | 宵夜单一入口验收（禁止直接扣款/写日程） | 进行中 |
| M2 | data | 真实回放召回评估 + 去重/过期质量 | 进行中 |

## Stage 1（依赖 E1/U1，可并行）

| ID | 插件 | 内容 | 依赖 |
|---|---|---|---|
| F1 | amo | 事实时间线唯一视图（面板/日记/回复/照片都读它） | E1 |
| M1 | data+amo | 记忆 `event:<id>` 标签 + 按标签查回契约 | E1 |
| W2 | watch | 契约快照按最终接口更新 | E1/W4/U1 |
| W3 | watch | `/decisions` 路由/面板 tab 门禁（routes 计数、空态、异常） | U1 |
| W5 | watch | 决定页扩展约会/社交 + 面板消费对账 | U1/F1 |


## Stage 1.5：event-id 加固（用户要求全部做，严格）

按顺序，前一项绿了才做下一项；每项 P0/P1/P2 + 反向验证 + 备份。

| ID | 内容 | 验收 | 状态（2026-11-17） |
|---|---|---|---|
| H1 | 迁移前自动 sqlite 热备 + `tools/restore_db_backup.py`（sha/manifest/integrity_check）+ 迁移失败回滚测试 | 副本恢复演练 RC=0；旧代码可读；失败不前进 user_version | ✅ 已完成（前序交付） |
| H2 | kv 快照从 `schedule` 表重建，两步同一 `BEGIN IMMEDIATE`；迁移后表/kv event_id 集合校验 | 人为改坏 kv → 迁移后对齐；非单事务测试消失 | ✅ 已完成（前序交付） |
| H3 | `block_diary` 增加 `event_id` 列（v4），从 `block_id` 回填并双写；旧代码仍读 `block_id` | 两列始终相等；旧行回填；回滚演练通过 | ✅ 已完成（v4；两列不一致=0；旧代码按 block_id 可读） |
| H4 | 旧账 `event_id` 只从可解析 `ref`（`block:ev_<16hex>`，可带时段后缀）回填，不猜 `sch_...`/旧内容哈希/自由文本 | 可解析回填、不可解析留空、回填幂等 | ✅ 已完成（v5；只认 `block:ev_<id>`；真实库 0 条此类 ref，账目零改动） |
| H5 | 显式 `split_schedule_block(day, parent, parts)`，父 id 明确传递；现有切块逐步改走它 | 一拆多/多合一/低重叠显式行为；启发式只作 fallback | ✅ 已完成（amo_timeline 提供 API；food/ensure_block_window 已接入，启发式仅兜底） |
| H6 | 全量 + gate + structure 0/0 + 迁移/恢复演练 + 文档更新 | 三插件相关全绿；回滚演练通过 | ✅ 已完成（3528 passed / 9 skipped；gate 全绿；structure 0/0；副本 v2→v5 + 恢复演练） |
## Stage 2（依赖 F1）

| ID | 插件 | 内容 |
|---|---|---|
| N1 | amo | 叙事只引用事实：日记/回复/主动统一 fact-check，矛盾才改、遗漏不改 |


## Stage 2.5：当天连载小记（diary-live，用户要求）

目标：当天像日记一样“还在写”，不是事实流水账；次日最终版收束。

- 存储：按天 `diary_live:<day>`（或新表），每条 `{ts, block_id/event_id, text, mood}`；只追加，不整篇覆盖；
- 触发：块结束/关键事件（吃饭、出门、看病、宵夜、约会）后 debounce 5–10 分钟追加一次；单飞；
- 内容：第一人称、有情绪/过渡，素材来自块日记 + 事实时间线；事实门同样生效，矛盾不写；
- 面板：`今天 · 还在写` 页，展示连载；次日最终版生成后收束（合并/去重），连载归档；
- 测试：追加不覆盖、debounce、重启恢复、最终版合并去重、矛盾拦截、event_id 关联；
- 依赖：H3（block_diary event_id）+ F1（事实时间线），放在 Stage 2 后。
## Stage 3（E1/D1/transit 定稿后）

| ID | 插件 | 内容 |
|---|---|---|
| T1 | amo | transit 收尾：commit 挂 sync、schedule_change 重算、plan/diary 车名门控、transit 字段、kv 副本 |

## Stage 4（T1 后，逐项 before/after）

| ID | 插件 | 内容 |
|---|---|---|
| P2-1 | amo | schedule GET 双读 + 修复写扫描 |
| P2-2 | amo | wardrobe `_load`/`_at_*` 重复 |
| P2-3 | amo | `_status_data` 双算 |
| P2-4 | amo | photo 链路 `current_activity` 2–3 次 |
| P2-5 | amo | price_book 每次重读盘 |
| P2-6 | amo | schedule_tree 读时修复拆成维护任务 |

## Stage 5（小 bug / 功能）

| ID | 插件 | 内容 |
|---|---|---|
| B1 | amo | `settle_due` duplicate 归入 settled 口径 |
| B2 | amo+万象 | 出图链路把穿搭改写成「居家便服」的既有问题（先定位，再决定修不修） |
| B3 | amo | 面板块删除/修改入口（功能，非欠账） |

## Stage 6（最终验收，不带红）

1) 三插件全量 pytest；2) 三 gate；3) day/24h/weeks 7/15；4) preview 三天 + clean_schedule scan；5) structure 0/0；6) mypy 基线只降不升；7) 实际热重载 200 + 日志 0 ERROR；8) 更新 REFACTOR_STATE/AGENTS；9) 打包 `--zip` 校验、上传（等用户说「上传」）。

## Stage 7（最终体检，交付前必做）

交付 `docs/HEALTH_CHECK.md`，至少包含：

1) **数字体检**：三插件 pytest（passed/skipped/subtests）、三 gate、结构 0/0、mypy 基线、路由/契约快照、CI 配置；
2) **事实层体检**：event-id 全链路抽样（日程→账本→块日记→记忆）、事实时间线与面板/日记一致率、叙事矛盾拦截率（构造 20 条矛盾样本，要求 100% 拦截/改写；遗漏样本 0 误改）；
3) **数据体检**：真实库 scan 0、preview 三天 HARD 0、备份 manifest verify/plan、回滚演练、cleanup_audit；
4) **模拟体检**：`simulate_day --trip`、`simulate_24h`、`simulate_weeks --days 7` 与 `--days 15`（不同 seed 各跑一次；要求 EXIT 0、逐日 hard=0、REAL_DB_UNCHANGED True、TEMP_CLEAN True）；必要时加 30 天压力模拟；
5) **实机体检**：热重载三插件 200 + 日志 0 ERROR；短时 soak（≥2 小时真实 tick，或至少 30 分钟加速）；异常注入（断网/缺图/坏配置）降级可见；
6) **性能体检**：P2-1..6 before/after 数字；
7) **出戏验收（沉浸）**：100 条真实风格多轮对话（吃/地点/生病/宵夜/约会/旅游/家人朋友聚餐/上班/睡觉/主动/纠正/元问题/复读），统计事实矛盾、复读、答非所问、系统腔、情绪保留、机械改写率；矛盾/复读/答非所问/系统腔必须 0，情绪保留 ≥95%，机械改写率 ≤10%，否则不交付；
8) **残留清单**：按 P0/P1/P2 分级，明确“能交付/不能交付”的判定；
8) **结论**：一句话评价 + 适用场景（自用/交接/生产）+ 不建议做的事。

判定线：任一 P0 未清、模拟任一 day hard>0、真实库被改、热重载有 ERROR → 不得交付。

## 判定线

- 任一 Stage 出现红：先修红，不进入下一 Stage；
- 每 Stage 结束由 lead 独立复跑，不采信单个子代理的“绿”；
- P0 未清零不得上传；P1 未清要列未覆盖链路；P2 未清要标「已知性能债」。