# task-21 修复报告：餐次 / 扣费 / 钱包对账 / 提醒单飞（2026-11-22）

> 约束遵守：未 reload、未 commit/push、未写生产库。生产库只用 `file:...?mode=ro` 只读核对；所有验证用隔离内存数据库 / 测试假件。
> 依据：`work/audit/time_facts.md`（task-12）。与其它 task 共享的文件只做函数级 edit，改前已备份到 `work/fix-money-food/before/`，行号以 2026-11-22 11:25 快照为准（其它 task 仍在并发修改）。

## 0. 结论摘要

| P0 问题 | 修复 | 验证 |
| --- | --- | --- |
| 11-16 一天 9 个 meal 块、11-15 6 个；同日 ≤45min 重复餐费 12 对 | 餐次实例 `meal:<day>:<slot>`（早/午/晚/宵夜，宵夜跨零点归前一天）；同实例 45 分钟内只许一笔；一个实例只复用同一个日程块；JIT/微型事件/宵夜/待办统一走 `pay_food` | 新测试 12 项；模拟 11-16 的 11 次扣款：实扣 6、拦 5、实例 4、meal 块 4、≤45min 已扣重复 0 |
| JIT/hunger/micro/social 各自扣费/各自切块 | `pay_food` 成为唯一餐饮扣费入口（价目表 + 实例 event_id + 块）；JIT 餐费、micro 餐饮事件、late_snack、todo 餐饮待办全部改走它；不再各造 `jit:`/`micro:`/`late_snack:` 餐费幂等键 | `test_meal_instances` 断言账本 `event_id=meal:<day>:<slot>`、日程 meal 块与账本同 id |
| 账本 cash 5628.57 vs 账户 3962.25（差 1666.32，含 6×275.22 无原扣退款） | 新增只读 `Wallet.reconcile()`：账户 vs 流水不变量 + 无原扣退款/重复退款识别；结果进 `snapshot()` 与 `kv wallet_reconcile_status`（运行时每 6h 巡检一次并 warning） | 生产只读核对：cash 差 166632 分、withdrawable 差 1700379 分、`refund_without_debit` 1 条聚合（6 笔 ×275.22=165132 分） |
| amo_wallet.py:261-268 把可取钳成现金 | 删除“直接改写 withdrawable 账户”的实现；改为记一条 `account=withdrawable` 的调整流水，保持 `sum(ledger)==account` | `test_wallet_reconcile`：现金下降后 withdrawable 流水合计=账户余额，且可取≤现金 |
| expense_summary 48h 缓存“今日总支出 0.00” | 钱包每次现金变动立即按当天账本重写（until=当天结束，排除存款/投资/转账/退款）；`amo_feature_api.note_expense_summary` 不再信外部文本，改由钱包实时口径重算 | 扣款后缓存 line=110.00，until=次日 0 点；定期存款 10000 不计支出 |
| 11-21 20:00 天气提醒 4 秒 4 次 | `_maybe_weather_alert` 单飞：同进程 asyncio.Lock + 跨重载 kv 原子占位（120s TTL），先占位再请求 | 并发 2 次调用 → 预报接口只请求 1 次、只写 1 次 checked |
| 11-20 09:54 迟到补跑 / 11-22 失败 12 次静默 | 晚间槽位迟到 >15 分钟记 `missed` 不补发；接口异常/没城市/没会话/发送失败都写 `weather_alert_status` 并 warning | 20:30 跑 → 0 次请求、status=missed/late/30；接口 Timeout → status=error/TimeoutError |
| 11-19 的 weather_alert_open 留到 11-22 | `_cleanup_weather_state()` 按日清 open/sent，claim 按 TTL 清；每次 tick 先清再判断 | 单测：11-19 的 open/sent 在 11-22 被清空 |
| 日程提醒开关实际关闭、状态不明 | `_schedule_reminder_switch_enabled()` 唯一读取点 + 状态变化日志（默认关闭说明）；配置 schema 已有 `proactive_schedule_reminder`（默认 false） | 单测：关/开状态明确返回；日志打最终生效值 |
| 日程提醒幂等键不含开始时间 / 迟到仍提醒 / 3 连 | 键改 `活动|地点|开始时间`；发送前 kv 单飞占位、发送前落幂等标记（失败回滚）；`schedule_fact_current` 增加迟到窗口（>5 分钟不发）；天气提醒已发后主动/晚安禁止再提天气/带伞 | 单测：键=复诊拿药|医院|15:10；第二次占位被拦；迟到 20 分钟拦下；3 分钟放行；天气重复话术被拦 |

## 1. 代码改动（我的 edit 范围 / 行号）

### 1.1 仅本任务修改的文件（有 before 快照，diff 完整）

| 文件 | 关键改动 | diff（+/-） |
| --- | --- | --- |
| `amo_wallet.py` | 今日支出实时重算（:278/:321）、可取额度账本化（:343）、`reconcile()`（:378）、`_insert` 写后对账/缓存刷新（:473）、`snapshot` 带对账（:581） | +251 / -13 |
| `amo_food_cost.py` | 餐次常量（:43）、`meal_slot_at`（:426）、`meal_instance_rows/charged`（:458）、`coalesce_meal_blocks`（:510）、`ensure_block_window(instance_id=)`（:574）、`pay_food` 重写（:1044） | +352 / -33 |
| `amo_food_event.py` | 宵夜扣费改走 `pay_food`（:286），不再自建块/自扣款 | +23 / -30 |
| `micro_events.py` | 餐饮事件 `_pay` 改走 `pay_food`（:560），非餐饮仍原逻辑 | +16 / -16 |

### 1.2 与其它 task 共享、只做函数级 edit 的文件

| 文件 | 我的改动 | 并发方 / 说明 |
| --- | --- | --- |
| `amo_schedule.py` | 天气提醒：`WEATHER_ALERT_LATE_MINUTES`（:2403）、`_cleanup_weather_state`（:2415）、`_claim_weather_slot/_release`（:2453）、`_maybe_weather_alert` 重写（:2481）、`_weather_alert_slot`（:2539） | task-20/task-3 在改日记/照片区；未动。整文件 diff 含他们的改动 |
| `amo_proactive.py` | 幂等键含开始时间（:157）、claim 单飞（:194/:224/:258）、`schedule_fact_current` 迟到窗口（:265）、天气重复话术拦截（:1362）、开关状态日志（:1375） | task-3/task-16 已收口发送链；只动提醒相关函数 |
| `amo_state_jit.py` | JIT 餐费改走 `pay_food`（:736），餐块继承 `event_id=meal 实例`（:624），退款 ref 指向实际扣款 idem（:777）；只改扣费/身份行，不写场景 | task-25（fix-scene-writers）改场景写者；其 `_jit_scene_constrain` 行保留 |
| `amo_feature_api.py` | `note_expense_summary` 改为钱包权威口径 + 当天 TTL（:222） | task-3 范围；函数级 edit |
| `amo_state_todo.py` | 餐饮类待办扣费改走 `pay_food`（:193）；只改扣费分支 | 未与他人冲突 |
| `amo_runtime_tick.py` | 每 6h 只读对账巡检 `_wallet_reconcile_check`（:205/:234），异常写 kv + warning | 未与他人冲突 |

### 1.3 测试

- 新增：`tests/test_meal_instances.py`（12 项：4 餐次/跨零点/45min/加餐/饮料/块复用/coalesce/微事件/JIT/待办）、`tests/test_wallet_reconcile.py`（6 项：不变量/可取账本化/无原扣退款/重复退款/支出缓存/快照）、`tests/test_reminder_singleflight.py`（11 项：天气并发/迟到/失败可见/过期清理/日程键/claim/迟到窗口/开关/天气重复话术）。
- 更新：`test_food_event.py`（健康门收口到 pay_food 的反向证明）、`test_food_ledger_guards.py`（duplicate_slot、45 分钟加餐、饮料不占餐次）、`test_proactive_wiring.py`（键含开始时间）。
- `tests/architecture_baseline.json`：只补我新增的两条 import 边（`amo_state_jit→amo_food_cost`、`amo_state_todo→amo_food_cost`）。

## 2. 指标（`work/fix-money-food/metrics.json`，只读生产 + 隔离模拟）

### 2.1 生产只读对账（修复后能 detect 的现存异常）

```
cash:          sum(ledger)=561357 分  account=394725 分  delta=+166632 分
withdrawable:  sum(ledger)=2095104 分 account=394725 分  delta=+1700379 分
refund_without_debit: 1 条聚合（6 笔同金额同分钟 27522 分；合计 165132 分；
  idem 全部为 refund:social:accept:soc-2026-11-15-<hash>，ref 指向的原始扣款在账本里不存在）
```

这与审计的 6×275.22=1651.32 元完全对上；剩余 15.00 元差额仍在 `account_mismatch` 里暴露（历史 repair 脚本直写账户造成，修复方案见第 4 节）。**未动生产库**。

### 2.2 11-16 历史 vs 新规则模拟

| 指标 | 生产历史（11-16） | 新规则模拟（同样 11 次扣款意图） |
| --- | ---: | ---: |
| 餐费扣款笔数 | 9 | 6（5 笔 duplicate_slot 被拦） |
| 餐次实例数 | 9（无实例概念） | 4（早/午/晚/宵夜） |
| 日程 meal 块 | 9 | 4 |
| ≤45min 同餐次已扣重复对 | 4 | **0** |
| 账本与账户对账 | 不一致 | **一致** |

同日 11-15 生产只读：7 笔餐费、7 个“伪实例”、6 个 meal 块；按新规则同样会收敛到 4。

### 2.3 提醒

```
weather 占位可重入: True（第一次占位成功、第二次被拦、释放后可再次占位）
schedule 幂等键: 复诊拿药|医院|15:10
schedule 第二次占位被拦: True
schedule 迟到 3 分放行: True / 迟到 20 分拦下: True
```

## 3. 测试结果

- 本任务相关全跑（meal/food/wallet/money/price/late_snack/hunger/micro_events/jit/weather/proactive/remind/runtime_tick/todo/state/social）：
  **871 passed / 1 failed（79.3s）**。
- 唯一失败 `test_proactive_coherence.TestPhotoSharing::test_normal_photo_still_bumps_and_remembers` 属照片分享链路（task-3/task-16 正在改 `photo_share.py`/`amo_presence.py`），非本任务改动；本任务 `_proactive_one` 只加提醒去重，不碰 `_share`。
- 新增 29 个测试全绿；修改后的 `test_food_event/test_food_ledger_guards/test_proactive_wiring` 全绿。
- 外部失败（其它 task 未收口，均不在本任务范围）：
  - `test_source_guard :: test_production_has_no_bare_clock_calls`：`amo_media.py:95 time.time()`（fix-media-gateway）；
  - `test_architecture_ratchet :: test_no_new_internal_imports`：`amo_scene_ledger/amo_persona_guard/amo_memory_ledger/amo_media` 等新 import 边（task-3/16/17/25）未更新 baseline；我这边的两条边已补；
  - `test_cov2_runtime_context :: test_schedule_tool_and_rain`（amo_runtime_context 场景注入，task-17 在改）。

## 4. 残余风险 / 需后续接手

1. **生产历史数据不自动修**（遵守“禁止写生产库”）：检测入口已具备（`Wallet.reconcile()` → kv `wallet_reconcile_status` / 面板 `snapshot()`）。若要修 1666.32 差额与 withdrawable 账，需另开“对账修复”任务：所有修复必须走 `wallet.credit/debit`（带 idem/ref）或新增 `wallet.reconcile(apply=True)`，不能再直写账户。
2. **watch 插件（Astrbot_amoupup_watch）** 仍是 6×275.22 的扣款来源、也仍会写自己的 `expense_summary`；本次在其调用入口 `amo_feature_api.note_expense_summary` 改为钱包权威口径，但要彻底消除双写需 watch 侧同步（跨插件）。
3. **社交饭局预扣/退/再扣（11-20 火锅）**：watch social 引擎的行为，不在本仓库；forever 侧的 todo/微型事件饭局已统一走餐次实例。
4. **“3 连带伞”**：本任务已拦主动消息/晚安里的天气重复话术，并保留 `weather_alert_open` 当天的【天气提醒之后】注入；20:01 那条“回复里重复”属回复链路（task-2/task-15 的 outbound 收口范围）。
5. 共享文件（amo_schedule/amo_proactive/amo_state_jit/amo_feature_api）可能在其它 task 收口后行号漂移，以函数名定位。

## 5. 复现命令

```powershell
# 指标（只读生产 + 隔离模拟）
& D:\dafeiyu\uv\tools\astrbot\Scripts\python.exe work/fix-money-food/metrics.py

# 新测试
& D:\dafeiyu\uv\tools\astrbot\Scripts\python.exe -m pytest tests/test_meal_instances.py tests/test_wallet_reconcile.py tests/test_reminder_singleflight.py -q

# 相关全量（本次 871 passed / 1 外部失败）
& D:\dafeiyu\uv\tools\astrbot\Scripts\python.exe -m pytest <meal|food|wallet|price|late|hunger|micro|jit|weather|proactive|remind|runtime_tick|todo 相关 tests> -q
```

## 6. 产物

- `work/fix-money-food/before/`：改前文件快照（8 个共享/生产文件）。
- `work/fix-money-food/diff/*.diff`：unified diff（共享文件包含其它 task 的并发改动，按函数名甄别）。
- `work/fix-money-food/metrics.json`、`work/fix-money-food/metrics.py`：本报告指标与复现脚本。
