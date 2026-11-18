# P0/P1/P2 待修清单（2026-11-17 审计后）

> 规则：每项修完必须按 `docs/FEATURE_CHECKLIST.md` 填 P0/P1/P2 证据；三栏不全不算完成，不得打包/上传。
> 来源：`docs/P0P1P2_AUDIT.md`（retro-audit）。本文件只排期，不代表已修。

## 0. 当前在跑（先等它们交回）

| 任务 | 内容 | 交回后动作 |
|---|---|---|
| `photo-bg` | P22 房间背景 + 自拍 6 张 A/B；修 `photo_room.py:33` 裸 except | 看 A/B、确认默认是否切换 |
| `price-book` | 静态价目表 v1 + 严格 A/B/联动/历史测试 | 审 v1 是否与现状逐项一致 |
| `audit-fixes` | 备份 manifest + rollback 默认 root/容器 + 约会↔地点/面板↔表测试 | 复跑 rollback 演练（备份/回滚部分已由 `p0-backlog` 补完并演练，见 §1 的 P0-3/4/5/7） |

## 1. P0（阻断/回归）——必须先清

| ID | 项 | 证据/来源 | 验收 |
|---|---|---|---|
| P0-1 | `photo_room.py:33` 裸 except 修掉，gate 回绿 | audit：`check_silent_except 1>0` | `quality_gate.py` EXIT 0，silent 0 文件 |
| P0-2 | 全量基线定死：等 P22/物价/人设交回后跑三插件全量，记录新数字 | 当前 collect 3280 vs 记录 3250 | 三插件 pytest/gate 绿，数字写回 REFACTOR_STATE |
| P0-3 ✅ | **全部 22 份备份生成 `manifest.json`**（不只 3 份） | 0 个 manifest，apply 全 EXIT 2 | **已完成**（`p0-backlog`，2026-11-17 09:47–09:52）：36 个阶段目录 → 41 份清单，`verify` **36/36 RC=0**、`plan` **36/36 RC=0**；明细 `docs/P0P1P2_AUDIT.md` §7.2/§7.3 |
| P0-4 ✅ | `rollback.py` 默认 root/容器布局修复 + 文档 | 默认 plan 4/4 拒绝；full-refactor 为容器布局 | **已完成**：默认参数（不给 `--root`）自动认到 `D:/dafeiyu`，36/36 RC=0；新增 `_has_markers`/`_descend` + 收紧 `_looks_plugin`，`plugins/<plugin>`、`<bk>/<plugin>`、`<bk>/plugin/`、`<bk>/<快照>/<plugin>/` 四种布局都定位到真插件根（`data-p1`/`data-p2`/`phase1-timeline` 的 plan 数字已纠正，见 AUDIT §7.1） |
| P0-5 ✅ | 回滚演练：至少 3 份（full / P20 / 最近 photo）在副本上 `apply` 成功并校验 | 从未真正 apply 过 | **已完成 3 份（副本）**：`clean-schedule-db`（copied 3，0/3/0，后置 plan 0/0/0）、`protocol-amoupup`（copied 454，0/43/93，py_compile 20/20）、`full-refactor-pre`（copied 397，0/115/154，py_compile 20/20）——每份都 pre-plan→`--confirm`→后置 plan 归零，且各留 `*.rollback-*` shelter；真实备份 verify 仍 RC=0、生产目录旁无 shelter。注：第 2 份用的是 `protocol-*` 而非「最近 photo」，见 AUDIT §7.4 |
| P0-6 | `price_book` v1 与现状逐项 A/B 一致；`amo_trip_cost.quote(None,kind)` 丢 city/item 修掉 | audit：price_book 零测试、quote 丢参 | A/B 金额/幂等键全同；城市/版本测试绿 |
| P0-7 ✅ | P12–P15 备份按各自 target 重新 `plan`（能记/守护，不是能陪） | audit：拿能陪 target 跑无意义 | **已完成**：`data-p1`→`Astrbot_amoupup_data` `0/24/30`、`data-p2`→同 `0/22/26`、`watch-p1`→`Astrbot_amoupup_watch` `0/31/22`、`watch-p2`→同 `0/30/18`，**4/4 RC=0**，文件集合正确（不再把能记/守护的文件往能陪里倒） |

## 2. P1（正确性/一致性）

| ID | 项 | 验收 |
|---|---|---|
| P1-1 | 约会 ↔ 聊天地点：`date_session` 在 place_guard / reply_fact_guard / location_rules / diary_guard 的 together 分支测试（≥4） | 去掉 together 传参 → 红；恢复绿 |
| P1-2 | 物价 ↔ 账本/历史：新价不重算旧账、旧日记不改、`cost_idem` 不重复扣、余额非负 | 切 v2 后旧账不变、新账带版本 |
| P1-3 | 面板数字 ↔ schedule 表：条数/汇总对账 | 临时库 N 条 → 面板返回一致 |
| P1-4 | 旅游行程总额 ↔ `wallet.ledger` 对账 | 行程单总额 = 账本分类合计 |
| P1-5 | 钱包 ↔ 日程同刻同额消费对账（不重复、不漏） | 构造同刻消费 → 账本/日程一致 |
| P1-6 | housing ↔ 照片：housing 故障不再静默吞；失败有 fallback + 日志 + 测试 | 故障注入 → 有日志/回退，不崩不静默 |
| P1-7 | 反向验证脚本化：57 处文字记录改为可复跑脚本/测试（至少核心 10 条） | 一键跑反向验证并留日志 |

## 3. P2（性能/冗余/可读性）——按收益做，每项给前后数字

| ID | 项 | 预期收益 |
|---|---|---|
| P2-1 | `amo_api_schedule.py:360-422` 一次 GET 两次全日程读 + 修复写扫描 | 面板 30s 刷新降到 1 次读；减少写扫描 |
| P2-2 | `wardrobe_queries.py:178-262` `_load` ≥3 次、`_at_home/_at_swim/_at_hotel` 重复 4 次 | 合并为 1 次，快照接口耗时下降 |
| P2-3 | `amo_api.py:671/676` `_status_data()` 算两遍 | 1 次，/status 延迟下降 |
| P2-4 | `photo_engine.py:364-383` 出图链路 2–3 次 `current_activity` | 缓存 1 次，减少读库 |
| P2-5 | `price_book.py:53-76` 每次 quote 重读盘+全表校验 | 进程内缓存 + 版本失效 |
| P2-6 | `schedule_tree` 读时修复拆成显式维护任务（避免 GET 触发写） | 读接口纯读；维护任务单独跑 |

## 4. 执行顺序（建议）

1. 等 `photo-bg` / `price-book` / `audit-fixes` 交回，做整合验收（P0-1/P0-2/P0-6 优先）；
2. 开 `persona-tone`（人设语气，自带 P0/P1/P2）；
3. ~~统一修 P0-3/4/5/7（备份清单 + 回滚演练 + 各自 target）~~ ✅ 已完成（2026-11-17 09:5x，见 §1 与 `docs/P0P1P2_AUDIT.md` §7）；
4. 修 P1-1 ~ P1-7（跨模块不变量 + 对账 + 反向脚本）；
5. 修 P2-1 ~ P2-6（每项 before/after）；
6. 全量定死基线 → 三 gate → 7/15 天 → preview/scan → 结构/mypy → **最后打包/上传**（等用户说「上传」）。


## 6. 追加：宵夜自主决定 + 主动性（2026-11-17 用户提出）

- 现状：`watch/services/late_night_engine.py` 在 22:00–24:00 按 `active + hunger<40 + cash>=2000` **自动扣款**并写小日记，没有「她决定/主动说」这一层，也没有对应日程块；02:12 的餐饮扣款来自 amo 的 hunger 路径。
- 目标：**她自主决定吃不吃** → 决定吃才扣款 + 写日程块 + 主动说（22–24 可主动；00–06 不吵醒用户，只写日程/日记）→ 决定不吃则不扣款，可留一句「忍住了」。
- 验收：决定层可测（饿/心情/余额/健康/概率种子）；扣款必须有 schedule 块且块 id 可追溯；主动消息只在允许时段发；不重复扣；存量历史按 P1 清理。
- **日程变化（用户追问）**：现状只写 ledger/小日记/micro action，**不改 schedule 块**；要求决定吃时自动把原块拆成「前段休息 → 宵夜块 → 后段休息」（如 22:00-22:10 休息 / 22:10-22:30 宵夜 / 22:30-23:00 休息），宵夜块带 food/金额/来源；决定不吃则不动日程。
- 归属：`late-snack-decision`（等 p1-backlog 的 food_cost 接口稳定后开）。
- **架构收口（补丁 vs 重构）**：决定/日程/扣款/日记目前跨 watch `late_night_engine`、amo `micro_events`、`amo_food_cost`、`amo_state_schedule`、日记链路；要求 late-snack-decision 收成单一领域入口（watch 只调用），并把 `amo_trip_cost` 与 `amo_food_cost` 的价目表适配收敛成一个 cost 适配层；否则属于“补丁式扩展”，后续容易漂移。

## 7. 事实层统一（2026-11-17 用户确认「开始做这三条」）

1. **event_id 贯穿**：日程块/账本/块日记/记忆同一事件 id；旧库迁移；测试全链路；
2. **事实时间线**：`amo_fact_timeline` 收口成「她今天发生了什么」唯一事实视图，面板/日记/回复/照片都读它；
3. **叙事只引用事实**：统一 fact-check（地点/活动/吃饭/健康/陪伴），叙事不得反推事实；矛盾才改写，遗漏不算；
顺序：event-id → fact-view → narrative-guard，逐个交回验收。
## 5. 判定线

- P0 未清零：不允许打包/上传；
- P1 未清零：允许继续开发，但发布说明必须列出未覆盖链路；
- P2 未清零：允许发布，但需在报告里标注「已知性能债」。