# 日程旧守卫/旧补丁清单与处理方式（2026-11-22，A1 换引擎后）

统计口径：仓库内（不含 tests/work 快照）命中以下符号的文件与次数——
`insert_transit_blocks / refresh_transit_texts / repair_cross_place_blocks /
_repair_transit_blocks / place_continuity_problems / _TRANSIT_SOURCES /
friend_dinner_segments / restore_appointments / _guard_activity_subjects` 共 **21 个文件**，
其中运行时代码 9 个、测试 12 个。下面按「新引擎是否已接管」分四类。

## A. 写侧过渡生成：已被读侧派生取代（切流后删）

| # | 守卫/补丁 | 位置 | 现状 | 处理 |
|---|---|---|---|---|
| 1 | `insert_transit_blocks`（含 `_transit_row/_push_after/_carve`） | amo_transition.py:1430 | 仍在 commit 里为“今天+白名单”补通勤块 | **切流后删调用**，函数降级为 legacy 一个版本（回滚用） |
| 2 | `refresh_transit_texts` | amo_transition.py:1274 | 归一化后重写文案，是“刚出家…回家”的直接来源 | **切流后删调用**；新引擎派生时已保证 return⇒前块非家 |
| 3 | `_repair_transit_blocks` / `repair_cross_place_blocks` | amo_state_schedule.py:960 / amo_transition.py:1623 | 读时修复历史跨地点瞬移 | **停用调用**（读侧 safe_view 已兜底），函数保留给迁移/回滚 |
| 4 | `place_continuity_problems` | amo_state_schedule.py:395 | 只 warning，不拦写入 | **删调用**，由 validate 的 path_break 指标替代 |
| 5 | `_TRANSIT_SOURCES` 白名单 | amo_state_schedule.py:1291 | 只为第 1 项服务 | 随第 1 项一起删 |

**为什么不一刀切今晚删**：第 1/2 项仍被 `test_trip_10b1`（7 处）、`test_transit_repair`（9 处）、
`test_transit_closeout` 等 16+ 条断言依赖“落库里有 transit 行”。删调用必须同步把这些断言
改成“safe_view 派生行”，一次 commit 完成并跑 3 天影子；否则会把“过渡不落库”误判成回归。

## B. A1 社交相关：只删“自动”，保留“人/约会/外部”

| # | 守卫/补丁 | 位置 | 处理 |
|---|---|---|---|
| 6 | `_pick_event` 休息日 friend_dinner 分支 | schedule_skeleton.py:616 | **已删**（本次），休息日不再发社交事件 |
| 7 | `friend_dinner_segments` + `schedule_engine` 导出表 | domains/schedule/social_segments.py | **保留文件但不可达**（显式事件/历史回放）；下一版删文件与导出 |
| 8 | `restore_appointments` 的 social/date 分支 | amo_state_schedule.py:323 | **保留**：保护 watch/date 的合法预约，A1 只禁自动生成 |
| 9 | `_guard_activity_subjects` 的 social/date 跳过 | amo_state_schedule.py:1318 | **保留**：约会主语由约会会话负责 |
| 10 | JIT 对 social+老公 的截断 | amo_state_jit.py:206 | **保留**：约会结束不能显示“进行中” |

## C. 日记：分级后不再需要“每块必写”的补丁

| # | 守卫/补丁 | 位置 | 处理 |
|---|---|---|---|
| 11 | 块日记调度入口 | amo_schedule.py:_spawn_diary_task | **新加分级**：`schedule_content.is_informative` 为假（发呆/待着/睡觉/纯休息）不生成小日记，交给大日记 |
| 12 | `_block_digest_worthy` 的 social/date/health/meal | amo_schedule.py:404 | **保留**：大日记仍需要约会/吃饭素材 |
| 13 | `schedule_diary.is_date`（依赖 category social） | schedule_diary.py:193 | **保留**：watch/date 块仍是合法约会 |

## D. 测试/仿真里的旧补丁（随 A 一起清理）

| # | 补丁 | 处理 |
|---|---|---|
| 14 | `work/sim/schedule_checks.py` R12 friend_dinner 专项（“事件必须落进日程”） | **删除**：自动社交已下线，检查无意义 |
| 15 | `test_schedule_drift_fixes.TransitTextRefreshTests` 只断言“含回家” | **升级**为强断言（本次已新增 16:46 用例），旧两条标 legacy |
| 16 | `test_trip_10b1/test_transit_repair` 断言落库 transit 行 | 删写侧时**同步改成派生断言**（约 16 条） |

## 执行顺序（每步独立 commit，退化即回滚）

1. **现在（切流前）**：只冻结，不删；读侧已由 safe_view 接管，P1 内容政策 + 日记分级已上。
2. **切流后第 1 周**：删写侧 1/2/4/5 与停用 3，同步改 16 条断言；跑 3 天影子 + 四件套。
3. **第 2 周**：删 friend_dinner segments / 导出 / sim R12，更新 7 天 golden。
4. **观察块日记**：stale 率从 42% 降到 <10% 则维持分级；否则把块日记整体并入大日记（P2）。

> 结论：需要处理 16 项，其中**删除 7 项**（1/2/3/4/5/6/14）、**降级/延后 1 项**（7，保留文件一个版本）、
> **合法保留 5 项**（8/9/10/12/13）、**测试升级 2 项**（15/16）、**新增分级 1 项**（11）。冻结期只做第 1 步。
