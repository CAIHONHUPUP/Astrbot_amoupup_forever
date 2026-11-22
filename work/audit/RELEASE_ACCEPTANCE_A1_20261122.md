# 7.6.4 A1 日程重构 + 零缺陷发布协议 验收报告（2026-11-22）

> 范围：日程引擎（事件时间线 + 过渡全派生）、A1 社交政策、零缺陷发布协议、P1 内容政策与块日记分级。
> 约束：不 push、不发包、不 reload；线上仍是 7.6.3，代码与数据迁移均在离线副本验证。

## 0. 结论

- 日程写侧过渡**彻底下线**：库里只存事件；过渡、坏块降级、同地点空档全部读侧派生。
- A1：自动来源社交块不再落库，旧社交块迁移工具已跑通（副本 5 天 6 块，关系数据指纹不变）。
- 8 项指标全部可采集；3 天影子 × 3 个种子全 0。
- 回滚：旧版本代码能读/写新格式库（演练通过）。
- 16 项旧守卫/补丁全部处理：删 7、降级 1、测试升级 2、保留 5（见 `SCHEDULE_GUARD_INVENTORY_20261122.md`）。

## 1. 改动清单

| 层 | 文件 | 内容 |
|---|---|---|
| 纯引擎 | `schedule_engine_v2.py` | 事件解析、过渡派生（零空档借时）、safe_view 降级、5 项静态指标、页面/聊天一致性 |
| 内容 | `schedule_content.py` | 户外长发呆变具体活动、文案贴地点、同地点发呆碎块合并、块日记分级 |
| A1 | `schedule_social_a1.py` | 自动来源社交块剥离、旧块迁移、人名归档、备份 7 天 |
| 观测 | `schedule_observer.py` | 统一视图、8 项指标落盘、schedule_shadow、派生过渡查询 |
| 写侧 | `amo_state_schedule.py` | 唯一入口强制 normalize/validate；删除写侧 transit 插入/文案刷新/读时修复；并发丢更新/写异常指标 |
| 读侧 | `amo_state_diary.py` | 当前动作在通勤空档读派生过渡（不会提前说“已到目的地”） |
| 日记 | `amo_schedule.py` | 空转块不再单独生成块日记（交给大日记） |
| 工具 | `tools/schedule_shadow.py` / `tools/migrate_social.py` / `tools/rollback_drill.py` / `tools/schedule_freeze.py` | 影子对比 / 迁移 / 回滚演练 / 冻结检查 |

## 2. 8 项指标证据

| 指标 | 定义 | 证据 |
|---|---|---|
| 非法块 | 起止缺失/倒置/零时长/空文案 | 3 天影子×3 seeds = 0（`schedule_shadow_sim3_final.json`） |
| 空洞 | 完整一天 >90 分钟空档 | 同上 0 |
| 重叠 | 同分钟两块 | 同上 0 |
| 路径断裂 | 跨地点放不下 10 分钟路程 | 同上 0；真实库 path_break 0 |
| 旧社交人名残留 | 自动社交块/旧人名 | 迁移后历史日 0；今天 1（故意不迁，保护 watch 结算） |
| 页面/聊天不一致 | 同一时间线两种口径 | `tests/test_schedule_backward_compat.py` 断言逐行一致 |
| 并发丢更新 | 进锁时版本已变 | `tests/test_schedule_concurrency_metrics.py` |
| 写异常 | 落库/影子抛错 | 同上 |

## 3. 影子对比（旧引擎 vs 新引擎）

- 事故原样（16:46→17:21）：旧数据有「刚出家，坐地铁回家」；新视图变「在家待着」，无 return 过渡。
- 真实库 8 天：`incidents_before` 有、`incidents_after` 全空。
- 迁移副本：5 天 6 块逐块替换，`problems=[]`、`relation_unchanged=true`；历史日 social_residue 5→0。
- 3 天 sim 影子：9 天 8 项全 0（zero=true）。

## 4. 回滚演练

- 旧版本（commit 5bbdcf3）读新格式库：8 块 / 24 子块 / 9 行文本，写入成功（version 32）。
- 新代码再读旧代码写回的数据：8 行、视图 9 行、8 项全 0。
- 结论：数据格式向后兼容一个版本；回滚 = reload 回上一个 7.6.x，无需改数据。
- 证据：`work/audit/rollback_drill.json`。

## 5. 门禁 / 四件套 / 真模型

- 全量门禁：见第 6 节（最终一次）。
- 四件套：daily 50/50 组、614/614 轮，residual 0、guard_introduced 0；share 500/500，damaged 0、persistent 0；39 对抗全过；184 回放 exit 0；remind 239/240（1 条 `proactive_repeated` 误杀，与日程无关，记 P1）。
- 真模型：deepseek-chat 探针 status=ok。

## 6. P0 / P1 / P2

### P0（阻断项）
| 项 | 结果 |
|---|---|
| 全量门禁 | 待填 |
| 8 项指标连续 3 天全 0 | 通过（9 天） |
| 迁移逐块核对 | 通过（5 天 6 块，关系不变） |
| 回滚演练 | 通过（旧版本读/写新库） |
| 页面/聊天一致 | 通过（逐行断言） |

### P1（发布后第一周）
1. remind 的 `proactive_repeated` 误杀 1 条干净提醒：与日程无关，独立修。
2. 写侧清理后观察真实流量 3 天：若出现“通勤空档里说已到目的地”，查 `derived_transit` 的窗口边界。
3. 块日记分级观察：stale 率与“水词”比例；必要时把块日记整体并入大日记。
4. 1b-3（3a/3b/3c）按用户补充排期，日程冻结后再做。

### P2（观察）
- 真实库 2026-11-18 仍有 4 条旧非法块（读侧已降级），迁移脚本可清。
- 今天 1 条旧社交块未迁（保护 watch 结算），次日自动进入迁移范围。
- 户外/家/公司的“发呆”文案已按 P1 政策处理，但模板丰富度仍可提升。

## 7. 切流 runbook（不 reload 由发布窗口执行）

1. 停写窗口：备份库（`tools/migrate_social.py --apply` 自带全库备份 + 每日增量 7 天）。
2. 迁移历史社交块：`python tools/migrate_social.py --db <库> --apply --today <今天>`，核对 `ok/problems/relation_unchanged`。
3. reload 到 7.6.4；观察当天/次日 `schedule_metrics:<day>` 8 项是否全 0。
4. 连续 3 天全 0 → 按天放开（方案 3：不做双引擎/线上并行开关）。
5. 任一指标 >0：reload 回上一个 7.6.x（数据格式兼容，无需回滚数据）。
6. 冻结：`python tools/schedule_freeze.py` 必须绿；改动须带不变量测试 + 影子证据。
