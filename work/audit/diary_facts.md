# 日记事实审计：块小日记 / hourly / 大日记 vs 当天日程块

- 审计日期：2026-11-22（周六）
- 范围：Astrbot_amoupup_forever（陪伴插件）日记三层 × 当天日程块（地点/动作/同伴/吃饭/健康/天气/过渡/模板）
- 数据：生产库 `D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever\amo_forever.db`（`mode=ro`，2026-11-22T10:22:20 快照）、数据插件 `amo_data.db`（`mode=ro`）、生产日记文件 `...\Astrbot_amoupup_forever\diary\YYYY\MM\DD\*.txt`、`D:\dafeiyu\astrbot\data\logs\astrbot.log`
- 只读证据：本审计全部 sqlite 连接均为 `file:...?mode=ro`；仿真在 `work/audit/_scratch/` 的代码快照副本（repo_pin）和系统 temp 中运行，不写生产库、不改生产代码、不 reload、不 commit/push。
- **代码状态说明**：task-4（fix-diary-facts）在审计期间仍在改 `amo_schedule.py` / `amo_diary_guard.py`（10:28:08 / 10:28:49 仍有写入），因此本报告对生产代码的引用固定在 10:30:45 的代码快照上（见第 7 节指纹）；行号以该快照为准，函数名可用于重新定位。

## 0. 结论摘要

| # | 问题 | 严重度 | 涉及层 | 证据 |
|---|---|---|---|---|
| F1 | 块小日记与当前日程块脱钩后仍 `status=valid`：11-18 有 25 条、11-19 5 条、11-20 2 条、11-17 3 条；同一块多版本并存（11-17 08:00 ×2、11-18 12:00/13:00 ×2、21:18 ×3、11-19 17:00 ×2） | P0 | 块小日记/大日记/digest | 3.1、3.2 |
| F2 | 三层事实源分裂：日程（JIT/23:59 快照）、watch 财务/社交/消费事件、数据插件聊天自述、账本各自写，互不合并；JIT 整份重写会丢掉 watch 刚写的块 | P0 | 全部 | 3.3、4.1 |
| F3 | 块小日记把「账本/消费事实」并入某个日程块，产生「日程在家、日记在逛街吃饭」：今天 11-22 09:00 块日记写逛街交通/奶茶/午饭 60，而日程 09:00-10:00 是「在家歇着」 | P0 | 块小日记/hourly | 3.4 |
| F4 | hourly（`schedule_diary`）与块小日记是两个并行日记表，语义不同却都叫「小日记」：hourly 是 watch 财务/基金/消费流水，块层是日程叙事；同一小时重复写同一件事或互相矛盾（11-21 h07 与块 07:00 都写「热汤面 20」；11-19 块写「公司发的奶茶白捡」hourly 写「奶茶支出 15」） | P1 | hourly/块 | 3.5 |
| F5 | 大日记与日程/聊天/账本对不上：11-18「回来路上又赶去公司一趟」与当前日程（请假在家）矛盾（来自聊天自述/孤儿块）；11-20「花了一百六十块」与账本/hourly 的火锅 200 元不符；11-21 白凝冰 19:00 约会（todo/watch 有）在日程/大日记里消失 | P1 | 大日记 | 3.6 |
| F6 | 天气事实无按天快照（11-17..21 全缺，11-22 才有），且天气城市与日程城市配置不一致（weather_city_name=清远 / weather_city=101281601，schedule_city=东莞）；日记同库出现「东莞 多云 28℃」「清远 阴 16℃」「阴天十二度」 | P1 | 块/大日记 | 3.7 |
| F7 | 健康事实无按天快照（11-17..21 全缺）：11-17 日程 23 个块的发烧/医院叙事、11-18 感冒，均无法按天对账；11-18 生成时 state 健康=健康，但块/大日记写感冒 | P1 | 块/大日记 | 3.8 |
| F8 | 守卫与自审只覆盖地点禁词/缺块/重复，不校验同伴、吃饭、金额、健康、天气、跨层一致；`is_valid` 不看 `version`，自审只审当前日程块 → 孤儿永不清洗 | P0 | 守卫/自审 | 4.2、4.3 |
| F9 | 23:59 快照 1 分钟内把同一天日程版本 bump 14 次（17→30，changed_hours 恒 [12,13]），每次把 12 点后块日记标 stale，造成 stale 风暴与孤儿 | P1 | 失效链路 | 3.2、4.3 |
| F10 | 模板/兜底：sleep 块固定「（呼呼大睡中，翻了个身又睡过去了）」21 条；生成失败兜底句把生成时刻天气写进历史日记（11-17「外面东莞 多云 28℃」、11-20「外面清远 阴 16℃」）；旧版人名兜底把食物/物件换成「一个朋友/一个同事」 | P2 | 块/大日记/守卫 | 3.9 |

3/5/7 天隔离仿真（当前快照，真实模型）：大日记 7/7 天生成、硬检查 0 失败、事实矩阵全命中；只有 1 条检查器误报（11-01「要是他在…牵着我」的愿望句被切句后误判为「面对面」）。块小日记（确定性回显 LLM）7 天 0 事实问题；旧 hourly 回滚路径除睡眠小时外全部有日记、0 事实问题；故意写错地点的守卫探针被地点闸纠正。→ **仿真链路本身能过，真实库的问题主要来自「多事实源 + 失效/版本 + 守卫覆盖」而不是生成模型。**

## 1. 三层日记的生成 / 守卫 / 落库 / 失效入口

| 层 | 存储 | 生成入口（生产） | 事实输入 | 守卫 | 落库 | 失效 |
|---|---|---|---|---|---|---|
| 块小日记 | 表 `block_diary(day,block_id,start,end,place,text,version,status,event_id)` | `_diary_scheduler_tick`(amo_schedule.py:1144) → `_backfill_blocks`(:164) → `_spawn_diary_task/_run_diary_task`(:1091) → `state.expand_block`(amo_state_diary.py:384) → `ScheduleEngine.generate_diary`(schedule_diary.py:161) | `amo_state_diary._diary_facts`(:490) = `build_fact_pool(slot)` + `day_view_facts`（**会读已有块日记**）；ctx 里的 health/weather 是生成时刻态 | `_guard_block_location`/`_guard_diary_location`（地点禁词，重写≤2 次）；`sanitize_diary`（人名/互动/极端情绪）；**无同伴/吃饭/金额/健康/天气守卫** | `_block_diary_finalize`(:674) → `db.save_block_diary`(amo_db.py:1295) | 日程变更 `commit_schedule_change` → `_publish_schedule_change` → `db.mark_diaries_stale(day,start_hour)`(amo_db.py:1200)（只有事件总线无订阅者时直接调用，线上走事件订阅）；只从 changed_hour 起标 stale；`block_diary_is_valid`(amo_db.py:1341) **只看 status+正文，不看 version** |
| hourly | 表 `schedule_diary(day,hour,text,version,status,source)` | ① 旧回滚路径（`block_diary_enabled=false`）：`_backfill_hours`(:193)→`_run_diary_task`→`state.expand_hour`(amo_state_diary.py:227)；② **生产实际写者**：watch 插件 `core/amo_bridge.py::_small_diary`(:685) 直接 `amo.db.save_diary(...,source="finance")`，同文件 `_long_diary`(:723) 写大日记；watch `core/agent_loop.py:715` 写基金决策小日记；`amo_food_event._diary`(amo_food_event.py:173) 写宵夜 | ②根本不读日程；①读 `build_fact_pool` | 只有 `sanitize_diary`；旧路径有 `_guard_diary_location` | `amo_db.save_diary`(amo_db.py:1162) | `diary_is_valid`(amo_db.py:1189) 同样只看 status+正文 |
| 大日记 | 文件 `<data>/diary/YYYY/MM/DD/我的日记YYYY年M月D日.txt` + kv `diary_written/locked/version/stale/notes` | `_maybe_daily_diary` → `_write_one_daily_diary`(:1521) → `_generate_daily_diary`(:1780)（默认 08:00 早上型，第二天任意时间补昨天；`_diary_days_to_write` 最多补 7 天） | `build_fact_pool(day)`=`amo_fact_timeline._compose`(amo_fact_timeline.py:1827)= 日程块+账本+聊天+健康/天气快照+穿搭+date/social kv；**不含块日记**；pool 失败才回退 `_diary_build_prompt(day,chat_lines,hourly_lines)`（此时 `_diary_hourly_lines` 会读 `block_diary status!='stale'`，**含孤儿**）；会拼 `day_digest`、`diary_notes`（watch 长日记） | 地点 `_guard_daily_location`；`sanitize_diary`；`diary_fact_mismatch`（只查「没吃/没出门/没生病/面对面」等反事实）；`strip_repeated_sentences/add_scene_transitions/strip_ungrounded_cooking/strip_ungrounded_overtime` | `_diary_write` → kv `diary_written:<day>`；`diary_locked:<day>=true` 定稿锁定 | 手动/块日记变更 `_mark_daily_diary_stale` 只置 stale；下次 `_write_one_daily_diary` 用 `diary_fact_mismatch` 决定重写，最多 3 次 |
| 自审 | kv `diary_audit:<day>`（通知：`diary_audit_notified`） | `amo_diary_audit.tick`(amo_diary_audit.py:744)（默认 09:00 左右，lookback 3 天） | `_day_rows`(:165) = **当前** schedule 块；`_window_index` 用当天 block_diary 行做块身份匹配 | HARD：missing/empty/stale/block_id mismatch/地点禁词；WARN：相邻块前 30 字重复、时间词超窗、过短/过长 | HARD → `mark_block_diary_stale` + `_requeue`（每 tick ≤2 条） | 不看孤儿行、不看 version、不看同伴/吃饭/金额/健康/天气 |

其它相关入口：`amo_api_diary.py::write_diary_text`(:211)/`append_diary_note`(:169) 是外部（watch 长日记/面板/命令）写大日记的入口；`diary_live_*`(amo_state_diary.py:1596) 是「当天连载小记」，由块日记落地时追加、大日记定稿时合并归档（`diary_live_archive`）。


## 2. 3/5/7 天隔离仿真（pinned 代码）

### 2.1 方法
- 代码快照：`work/audit/_scratch/repo_pin/`（生产模块 10:30 快照，哈希见第 7 节；harness 也是快照副本，输出只写 pin/work 与系统 temp）。
- 日程来源：`work.simulate_weeks.build_day_skeleton`（生产 ScheduleEngine，7 天：2026-11-01..07，golden 同款种子）。
- 大日记：生产 `_write_one_daily_diary` 全链路 + 真实模型（deepseek-chat/deepseek-flash，8 次调用，0 错误，24k tokens）；按 3/5/7 窗口统计（同一 7 天序列的前 N 天）。
- 块小日记：生产 `state.expand_block`（`block_diary_enabled=true`），生成层用确定性回显 LLM（把提示词里的本块日程+白名单事实改写成正文），用于验证素材包/地点闸/落库，不评估语义质量。
- hourly：生产 `state.expand_hour`（`block_diary_enabled=false` 回滚路径），隔离插件实例；同样回显 LLM。
- 事实核对器：`work/sim/diary_checks.py`（独立词表，和大日记 8 维一致）。

### 2.2 结果

| 窗口 | 大日记 | 大日记问题 | 块小日记（回显） | hourly（回显） |
|---|---|---|---|---|
| 3 天 | 3/3 生成 | 1（companion，检查器误报：11-01「要是他在，估计又能牵着我到处乱逛了」） | 0 | 0（非睡眠小时全有日记） |
| 5 天 | 5/5 生成 | 1（同一条） | 0 | 0 |
| 7 天 | 7/7 生成 | 1（同一条） | 0 | 0 |

- 大日记硬检查：`generation.all_days_have_diary` / `no_provider_error` / `no_pipeline_error` 全 PASS；事实矩阵（地点/出门/三餐/健康/同伴/天气）7/7 全命中；健康按天快照注入（11-03 低烧、11-05 感冒）回读正确；【前几天】digest 播种 7 天全部非空、跨天去重丢弃项符合预期；跨天复读 4-gram 最大 0.202（低）。
- 块小日记：7 天所有已结束块都有日记；地点/同伴/吃饭/健康/天气/过渡 0 问题（回显模型只扩写素材，问题会归因到素材/守卫/落库）。
- 守卫探针：对 3 个「家」块故意让 LLM 回答「在商场逛，试了两件衣服」，落库正文不含「商场」且被地点闸纠正 → 显式地点禁词防线有效。
- 仿真局限（重要）：①回显 LLM 不产生真实幻觉，也不含聊天/账本事件，因此测不出 3.3/3.4 的跨源污染；②块/hourly 层没有注入「当日病/天气偏差」等负例；③回显模型的重复句使块/hourly 的模板复读指标失真（真实模板问题见 3.9 真实库）。

## 3. 真实库不一致清单（11-17..11-22 + 今天）

每天数据量（mode=ro 快照 10:22:20）：

| 天 | 日程块 | 块日记 total/valid/stale | 孤儿 valid | 同 start 多 valid | hourly | 大日记问题 | health 快照 | weather 快照 |
|---|---|---|---|---|---|---|---|---|
| 11-17 | 23 | 30 / 26 / 4 | 3 | 1（08:00×2） | 11 | 1（transition） | 无 | 无 |
| 11-18 | 12 | 34 / 34 / 0 | 25（7 条 sleep 占位 + 18 条正文） | 3（12:00/13:00/21:18） | 9 | 4 | 无 | 无 |
| 11-19 | 14 | 19 / 19 / 0 | 5 | 1（17:00×2） | 8 | 2 | 无 | 无 |
| 11-20 | 12 | 14 / 14 / 0 | 2 | 0 | 6 | 3 | 无 | 无 |
| 11-21 | 8 | 19 / 6 / 13 | 0 | 0 | 6 | 4 | 无 | 无 |
| 11-22（今天） | 7 | 4 / 4 / 0 | 0 | 0 | 2 | 0（未写，正常） | 无（健康，符合预期） | 有（阴 18℃ 清远） |

### 3.1 F1 孤儿块日记：当前日程里没有这个块，行仍 valid
- 11-18（最严重）：当前日程 12 块，`block_diary` 34 条全 valid，其中 25 条 block_id 不在当前日程。正文明确的例子：
  - `[10:10-10:50] 路上 "打车去公司路上…"`、`[13:40-14:40] 公司 "打车到公司楼下刚好一点四十，坐回靠窗的工位…"`、`[21:18-21:23] 公司 "晚上九点多，换了衣服出门赶去公司…加班"`、`[23:32-23:46] 地铁 "夜里快十一点半才上的地铁…"`
  - 当前日程对应时段是：`13:00-14:40 请假在家休息@家`、`21:18-23:00 在家歇着@家`、`23:51-07:00 睡觉` → 地点/动作直接矛盾（在家休息却在公司加班/坐地铁）。
- 11-19：5 条孤儿正文，例如 `[15:00-16:00] 公司`、`[17:00-18:00] 公司`、`[18:00-19:00] 路上`，当前日程 17:00-17:29 路上、17:29-18:29 家和闺蜜连麦、20:00-22:00 家。
- 11-20：`[11:00-12:00] 公司 吃面`、`[12:00-12:32] 路上` 为孤儿（当前日程只有 09:40-10:40 公司、13:00-14:00 公司）。
- 11-17：`[23:30-23:50] 泡面`、`[08:00] 旧版热粉` 等 3 条。
- **为什么自审没发现**：生产自审 `diary_audit:2026-11-18` 记录 `blocks=12, hard=0`（只遍历当前 12 块），`_day_rows` 只取当前 schedule；孤儿行不在遍历里。11-19/11-20 的 `diary_audit` 同样 `hard=0`。
- **影响面**：`_diary_hourly_lines`(amo_schedule.py:1715) 直接 `SELECT ... FROM block_diary WHERE day=? AND status!='stale'` → 孤儿正文会进入大日记的 fallback 提示词；`diary_live_archive`/`_seed_day_digest` 相关的旧归档也会保留这些正文，后续可能被当作「当天素材」。

### 3.2 F1b/F9 同一块多版本并存 + 版本风暴
- 同 start 多条 valid：11-17 08:00 两条（旧 legacy id `6f89ab0c052cba2f` vs `ev_c4e7bb931b18a440`，同一件事两种写法）；11-18 12:00 两条、13:00 两条、21:18 三条（其中一条是 legacy id `5ab9813765a2bd41`，另两条 block_id 不同）；11-19 17:00 两条（公司加班 vs 路上回家）。
- 11-21 `schedule_audit:2026-11-21`：23:59:00–23:59:28 共 **14 次** `source=snapshot "23:59 快照兜底"`，version 17→30，`changed_hours` 恒 `[12,13]`，after 内容看起来相同。每一轮都把 12 点及以后的块日记标 stale → 11-21 有 13 条 stale（12:00/14:00/17:00×3/17:24/18:00/19:00/20:00/20:35/21:00/22:00…），而其中不少块的正文已经和当前日程对不上。
- 根因：`block_diary_is_valid`(amo_db.py:1341) 不比较 version；`mark_block_diaries_stale` 只按 `start >= from_hour` 粗扫，且只标 stale 不删/不核对块是否还存在；快照 commit 不幂等。

### 3.3 F2 事实源分裂：日程 vs watch 事件，JIT 覆盖丢块
`schedule_audit` 时间线（生产库 kv，只读）：
- **11-21 09:01:19** `source=watch` 新增 `12:00-13:30 妈妈中午回家吃饭@楼下小馆`（v7）。
- **11-21 17:00:02** `source=jit 到点即时决策` 把同一条改成 `妈妈中午回家吃饭@家`（v10）→ 聊天里她说「跟妈妈在小馆等着上菜呢」（11-21 12:04，数据插件 messages），日程却写家；块日记/大日记按日程写「妈妈来家里」。
- **11-21 19:00:00** `source=jit` 新增 `19:00-20:00 白凝冰下班喝一杯聊聊@楼下小馆`（v12）；**21:00:01** 下一次 jit 整份重写把它丢掉（v14 的 after 里没有 19:00 块）；最终日程 8 块里没有白凝冰。todo 表仍有 `2026-11-21 19:00 白凝冰下班喝一杯聊聊 status=done`（watch 社交事件已发生）→ **事件发生了但日程/大日记都没有**。
- 今天 **11-22 09:00:18-21** watch 财务链路写入 3 笔账本：`逛街交通 30`、`奶茶 20`、`逛街午饭 60`；`schedule_audit:2026-11-22` 09:00:34 只新增了 `19:00 白凝冰` 社交块，**没有把逛街/奶茶/午饭落成日程块**；09:00-10:00 日程仍是「在家歇着」。这些账本事件随后被当成事实写进了 09:00 块日记/hourly（见 3.4）。
- 代码层：`amo_state_schedule.patch_schedule` 注释明确「58 个写者都在构造 new_items 整段覆盖，谁漏带谁的块就丢」（pinned amo_state_schedule.py:1091 附近）；JIT 每小时、23:59 快照都用整份 `commit_schedule_change`。

### 3.4 F3 今天真实库（只读）：块小日记把账本消费当成本块事实
- 日程（当前）：`09:00-10:00 在家歇着@家`（version 4，09:00:00 由 jit 写入）。
- 账本：`09:00:18 逛街交通 -30`、`09:00:21 奶茶 -20`、`09:00:21 逛街午饭 -60`。
- 块日记 `block_diary[09:00-10:00]`（version 6，10:00:11 由生产块日记管线写入，晚于账本）：
  「九点多起来把昨晚剩的半碗米饭热了热…**逛到中午随便找了家小店对付了一顿，六十块**…**回来路上顺手买了杯奶茶，二十块**…」
  → 同一段里既有「在家热剩饭」又有「逛街/午饭 60/奶茶 20」，日程块是家、正文却出现商场/逛街/外食；地点闸只拦显式禁词（「小店」「逛街」不在词表/无地点介词），没有拦住。
- hourly `schedule_diary[hour=09]`（source=finance）： 「**逛街交通 30**；**奶茶 20**；**逛街午饭 60**；今天周末逛街，试了一件米白色针织开衫…；答应了白凝冰，今天 19:00 去…」
  → 与块日记同一批账本事实重复写了两层；`source=finance` 与块层 `source=""` 语义不同却都显示为「小日记」。
- 生成入口：块日记 `_diary_facts`(amo_state_diary.py:490) → `build_fact_pool(slot)` → `amo_fact_timeline._compose` 把 `ledger_entries` 按分钟挂到覆盖块（`_apply_ledger` amo_fact_timeline.py:1525），所以「家」块的素材里确实有逛街/奶茶；提示词同时给出「地点=家、动作=在家歇着」和白名单消费事实，模型把两者拼在一起。

### 3.5 F4 hourly 层与块层重复/矛盾
- 11-21 `hour=07`：「吃的：一碗热汤面，花了 20.00 元；饿了，买了一碗热汤面…」；同一天块 07:00-08:00 也写「点了碗热汤面，二十块」→ 同一事实在两层各写一次，`_diary_hourly_lines` 若读两层会重复。
- 11-19：块 `13:00-14:54`「公司发了奶茶…白捡的一杯，喝完整个人活过来了」；hourly `hour=12`「奶茶 × 1，花了 15.00 元」；大日记写「下午公司发了杯热的奶茶…白捡的一杯」→ 同一天「免费奶茶」与「15 元奶茶」并存。
- 11-20 `hour=22`：「他说好今天陪我吃饭的，结果人没来，是不是忘了……」；同日日程 19:09-20:00「柳如烟下班吃火锅」、账本 200 元、大日记「下班和柳如烟吃了火锅」→ hourly 的「他没来」与当天事实矛盾。
- hourly 的写者（watch）只按自然小时写，`_small_diary` 的「已有块日记就不覆盖」判断读的是 `schedule_diary` 的 source=="block"，**不查 block_diary 表**，所以块模式和 finance 模式可以同时写同一小时；实际今天 07/09 就是两层并存。
- 旧 hourly 路径（`expand_hour`）在 `block_diary_enabled=false` 时仍可一键回滚，但同一张表同时承载「日程小日记」和「财务小日记」，没有 `source` 分区消费规则。


### 3.6 F5 大日记与日程/聊天/账本不一致
- 11-18 大日记：「…傍晚出门去楼下小馆，坐地铁过去，沈幼微还问我老家修房的事…**回来路上又赶去公司一趟，夜里才到家**，在地铁上饿得不行…」
  - 当前日程 13:00-14:40 请假在家休息；21:18-23:00 家；23:51 睡觉 → 没有「去公司」块。
  - 聊天 11-18 23:00「你睡了没，我还在公司磨一份东西，好饿。」→ 她自己在聊天里说在公司。
  - 孤儿块日记 `21:18-21:23 公司`、`23:06-23:19 公司` 也写公司。
  → 大日记忠实转写了聊天/孤儿块，但**和当前日程矛盾**；这正是「聊天自述/旧块未同步进日程」的后果。
- 11-20 大日记：「花了一百六十块钱，不算便宜，但值。」；账本 `18:30:53 下班和柳如烟吃火锅 -20000`；hourly 18 也写「火锅 200.00」→ 金额 160 vs 200 不一致；守卫 `diary_fact_mismatch` 不校验金额，`validate_text` 只查「消费/花了」词是否出现。
- 11-21 大日记：没有白凝冰 19:00 约会（3.3），也没有任何「19 点出门喝一杯」；大日记把 11 点出门写成去小馆+妈妈来家，掩盖了日程块缺失。
- 11-17 大日记：「夜宵就泡面对付了，五块钱」——当前日程没有 23:30 泡面块（只有 23:05-24:00 洗漱），但块日记孤儿行/hourly 23 都写泡面 5 元 → 日程缺块、日记有事实，说明日程时间线漏事件（不是日记错）。
- 11-19 大日记：「这一个朋友跟呼吸一样自然」等旧版人名兜底污染（3.9），以及「到公司先泡了杯咖啡」被核对器误判为 place（咖啡词表=restaurant，但发生在公司茶水间）。

### 3.7 F6 天气
- 配置：`schedule_city=东莞`、`weather_city_name=清远`、`weather_city=101281601`（清远）；`weather_day:2026-11-22 = {text:阴, temp:18, city:清远}`。
- 真实日记里的天气句：11-17 13:05 块日记「外面东莞 多云 28℃」（该条是生产兜底模板，见 3.9）；11-20 09:40/11:00/12:00「阴天/清远这阴天/东莞也凉了几分」；11-20 13:00 兜底「外面清远 阴 16℃」；11-21 09:00/10:00「阴天十二度」。同库两座城市、温度 12~28℃ 并存。
- 11-17..11-21 没有 `weather_day:<day>` 快照；原因是 `read_weather` 按天快照是 2026-11-22 05:57 才落地的 P1 修复（`amo_fact_timeline.py` mtime 05:57），历史天不可回溯；只有 11-22 起有快照。
- 影响：历史大日记/块日记的天气要么来自生成时刻 state、要么是模型自写，无法对账；跨城市配置会让同一天出现「清远/东莞」两套天气。

### 3.8 F7 健康
- 11-17..11-21 没有 `health_day:<day>` 快照（同上，P1 按天读健康是今天 05:57 才落地）。当前 `health=健康`。
- 11-17 日程 23 个块包含「额头还有点烫/烧没退/去医院/吃药捂汗」→ 大日记 11-17 也写「烧了两天…去医院…」，两者一致；但无快照，`diary_fact_mismatch` 无法对账（只能拿当前健康=健康，会误判）。
- 11-18 块日记/大日记写「感冒还没好全/感冒这种事就是黏人」，而当前 state 健康=健康、当天日程没有 health 块（只有请假）；聊天 11-18 08:42「我还感冒着呢」、11-17 20:23「退了，晚上量过，正常了」→ 事实应是 11-18 仍有感冒余波，但**按天健康快照缺失**，守卫和核对器都无法稳定判定。
- 生成时态问题：块日记生成在块结束后（如 11-18 11:00 块在 12:01 生成），`_diary_facts` 里的 health 是「现在」而非「这一块」；`read_health(plugin, day)` 只有 day=今天且 active 才写快照。

### 3.9 F10 模板 / 兜底 / 人名清洗（历史数据仍在库里）
- Sleep 固定句：「（呼呼大睡中，翻了个身又睡过去了）」在 valid 块日记中出现 **21 次**（11-17..22 的睡眠块）。
- 生成失败兜底模板把生成时刻上下文写进历史日记（不是按天快照）：
  - 11-17 13:05-14:05：「这小时我在家，回家随便吃点午饭，没什么胃口。外面东莞 多云 28℃，我心情平静，只想安安静静待一会儿。」
  - 11-20 13:00-14:00：「这小时我在公司，在工位上忙工作。外面清远 阴 16℃，我心情平静，只想安安静静待一会儿。」
  - 11-20 14:00-14:38 同模板换地点「这小时我在地铁上…」。
- 高频词：valid 块日记里「一个人」60 次、「有点想老公」8 次、「心疼」4 次、「阴天」10 次。
- 旧版人名兜底（2026-11-22 已加 `scrub_names_guarded` 防误伤，但历史文本已落库）：
  - 11-17 09:00「体一个朋友又夹了五分钟」、11-17 10:00「先看一个朋友说吧」；
  - 11-18 13:40「整理一个朋友、回邮件」、16:00「茶几上摊着半一个朋友和一杯温水」；
  - 11-21 19:00（stale）「我们点了两碗一个朋友」；11-19 大日记「这一个朋友跟呼吸一样自然」。
  → 食物/物件被当人名替换，语义崩坏且会给同伴检查制造「凭空多一个人」的假象；这些文本仍在 `block_diary`/归档里，会被 3.1 的 `_diary_hourly_lines`/diary_live 继续消费。
- 大日记跨天复读：真实库大日记 4-gram 最大重叠 0.043（11-19 vs 11-20），无整句复读——这一项当前 OK（仿真模板指标也低）。

## 4. 根因

### 4.1 R1（P0）事实源没有单一真相，日程只是其中一个可被覆盖的副本
- 日程写入是「整份替换」：JIT 每小时、23:59 快照、健康巡检、聊天改日程、watch 社交/财务都走 `commit_schedule_change`；JIT 侧按当天模板重建，容易漏带 watch 刚插入的块（11-21 白凝冰、11-21 妈妈地点）。
- watch/data 的消费/社交/购物事件有的只写账本（今天 09:00 三笔消费）、有的写日程块（19:00 白凝冰）、有的只写 hourly（基金/工资）→ 同一件「今天发生的事」在 1~3 个存储里状态不同。
- 数据插件聊天里的自述（11-18「还在公司」）不保证回写日程，但大日记的事实池直接读聊天 → 大日记和日程打架原来是设计使然。

### 4.2 R2（P0）三层日记各读不同事实视图
- 块层：`build_fact_pool(slot)` + `day_view_facts`（**块日记又作为后续块的事实**）→ 旧块/孤儿块的错误会被下一块继承。
- 大日记：`build_fact_pool(day)`（不含块日记，但含**全部聊天+账本**）；pool 失败时回退 `_diary_hourly_lines`（读块日记含孤儿）→ 同一篇大日记的素材取决于 pool 是否异常，语义不稳定。
- hourly：watch 桥 `_small_diary` 完全另一条路径，不读日程、不读事实池，只拼财务/基金人话。
- 结果：同一个「今天」被三套视图各解释一次，且没有跨层对账。

### 4.3 R3（P0）失效/版本闭环不完整
- `block_diary_is_valid`/`diary_is_valid` 只判断 status+正文，`version` 列存了但不用；`mark_block_diaries_stale` 只按 `start >= from_hour` 粗扫且只标 stale，不按 block_id diff 处理「被删块」；`_apply_diaries` 虽然在视图层丢弃孤儿，但表里孤儿永久 valid。
- 23:59 快照 commit 不幂等（14 次 version bump，内容不变也 bump）→ 每次 bump 触发一次从 12 点起的 stale 标记，形成「stale 风暴」，并让下一轮日记在「旧正文 vs 新日程」之间反复。
- 自审只遍历当前块（`_day_rows`），不遍历 `list_block_diaries(day)` 的其他行 → 孤儿永远不会被自审标记。

### 4.4 R4（P1）守卫/自审维度不足
- 块/hourly：只有地点禁词（`_guard_block_location`/`_guard_diary_location` → `amo_location_rules`）+ 人名/互动清洗（`sanitize_diary`）；没有同伴、吃饭、金额、健康、天气规则。
- 自审 HARD 只有 missing/empty/stale/block_mismatch/place_forbidden；WARN 只有 dup_prefix/time_word/长度。没有事实语义核对、没有跨层核对、没有金额核对。
- 大日记 `diary_fact_mismatch` 只覆盖「没吃/没出门/没生病/面对面」等反事实句；不校验同伴具体身份、不校验金额、不校验天气。
- 结果：今天 09:00 块日记「在家中又逛街吃饭」这种**语义级跨源冲突**，所有守卫都放行。

### 4.5 R5（P1）按天健康/天气是今天刚落地的新链路
- `read_health`/`read_weather`(amo_fact_timeline.py:719/811) 今天 05:57 才提交：只有目标日=今天且状态 active 才写快照，历史天缺快照只能靠预报/当前状态兜底。
- 天气城市配置与日程城市配置来自不同字段（weather_city_name vs schedule_city），没有一致性约束。

### 4.6 R6（P2）模板与历史数据
- sleep 占位、生产兜底句（`_diary_cached_reply`/`fallback_diary`）复用固定模板，且兜底句带生成时刻天气 → 历史天出现「28℃/16℃/12℃」混用。
- 旧版 `scrub_names` 误伤食物/普通名词，新代码已有 `scrub_names_guarded`（pinned amo_diary_guard.py:715），但历史正文未清洗，仍会被后续层消费。


## 5. 统一事实源方案（建议，不含实现）

目标：**一天只有一条 DayTimeline；三层日记只是它的不同投影；任何外部事件先落时间线，禁止「只写账本/只写日记表」。**

1. **唯一事件骨架（event_id 贯穿）**
   - 日程块 `schedule` 的 `event_id` 作为当天事件唯一键；watch/data/food/JIT/聊天改日程全部通过 `add_schedule_item`/`patch_schedule`（增量）落块，禁止整份覆盖；每条块带 `source`（jit/watch/chat/finance/food）和时间粒度（分钟级）。
   - 账本/消费/社交事件如果只发生一次，必须 `event_id` 回填到覆盖块（或新增块）；今天 09:00 的「逛街交通/奶茶/午饭」应落成 `09:00-10:00 逛街+午饭@商场` 或明确标为「线上/代付」而不是挂在「家」块上。
2. **唯一视图 build_day_view 升级为唯一事实接口**
   - 合并 sources：schedule + ledger + watch social + food event + chat 消费/自述 + health/weather 快照 + date session；每条事件保留 `source`/`event_id`/`evidence`（可追溯）。
   - 冲突规则显式化：同一 event_id 多来源 → 以「主事实源优先级」合并（建议：已发生的日程块 > watch 事件 > 账本 > 聊天自述 > 模型），冲突记 `conflicts` 并触发修复而不是静默选一个。
   - 三层日记统一使用 `render_timeline(view, layer, window)`：block 层只取本块 + 直接相邻块；hourly 层只取本小时事件（或合并到块层后废弃）；大日记取全天 + 可选块摘要（标注来源，避免递归污染）。
3. **块日记不再作为事实回流**：`_diary_facts` 去掉 `day_view_facts`（只保留「上一块正文」作为文风/情绪衔接，标为 context 不是 fact），避免旧块错误被下一块继承。
4. **失效按事件 id diff，不按小时粗扫**：`commit_schedule_change` 计算 `added/changed/removed event_ids`；`removed` → delete/mark stale 孤儿行；`changed` → 按 id 精确 stale；validity = status + version + block 仍存在 + event_id 一致；快照/写入幂等（内容相等不 bump version）。
5. **自审升级为「日记事实审计」**：在 `amo_diary_audit` 增加：孤儿行、version 不一致、地点/动作/同伴/吃饭/金额/健康/天气/过渡/模板、以及跨层（block vs hourly vs big）一致性；不一致 → 标 stale → 重写；每日报告继续走 `diary_audit:<day>`/通知。
6. **健康/天气按天事实**：目标日快照必须在「当天任一事实读取」时写；补历史天时用「当天最后一次 state 快照 + 预报」；天气城市统一从 `schedule_city` 派生（或明确 `weather_city`=居住地 vs 出差地），日记提示词里只允许一个城市口径。
7. **一次性清洗**：用 `work/diary_audit_apply.py` 同类脚本（只读审计 → 人工确认 → 单独 apply）清理：孤儿 valid 行、同 start 多 valid 行、历史「一个朋友/一个同事」污染行、sleep 占位重复行；大日记的 11-18/11-20 事实错误按「事实门」重写（需人工确认，不在本审计中执行）。
8. **验收指标**：孤儿 valid=0；同 start 多 valid=0；valid 但 version/块不存在=0；自审 HARD=0 且新增维度=0；三层跨层矛盾=0；3/5/7 仿真硬检查 0、软问题 0；真实库抽查 3 天「地点/同伴/吃饭/健康/天气」逐句人工复核 0 矛盾。

### 5.1 最小落地顺序（建议给 task-4/后续）
1. P0：`block_diary_is_valid` 加 version/块存在性校验；`commit_schedule_change` 按 event_id diff 精确失效 + 清理孤儿；23:59 快照幂等。
2. P0：块日记 `_diary_facts` 去掉块日记回流；`_diary_hourly_lines` 只读与当前块 id 匹配的块日记。
3. P1：自审遍历全量 block_diary + 增加同伴/吃饭/金额/健康/天气维度；`diary_fact_mismatch` 扩展金额/同伴/天气。
4. P1：hourly 层与块层二选一（建议 block 层为唯一「小日记」，finance 事件作为 `finance_note` 附注或并入账本摘要）；统一 `source` 消费。
5. P2：历史数据清洗 + 模板替换（sleep/兜底句按天快照生成）。

## 6. 复跑与证据文件

- 真实库审计（只读）：`python work/audit/_scratch/real_audit.py` → `work/audit/_scratch/real_audit.json/.md`（本报告第 3 节数据源；快照 10:22:20）。
- 3/5/7 大日记（pinned + 真实模型）：`python work/audit/_scratch/repo_pin/work/sim/diary_sim.py --days 7` → `repo_pin/work/sim/diary_report.json/.md`（10:30:45 跑，7/7 生成）。
- 3/5/7 块小日记 + hourly（pinned + 确定性回显）：`python work/audit/_scratch/repo_pin/work/audit/_scratch/layers_sim.py` → `layers_report.json/.md`。
- 真实库三层转储：`work/audit/_scratch/real_dump.json`、`compact_real.md`；日程变更审计：`work/audit/_scratch/sched_audit2.txt`。
- 代码指纹（pinned，`repo_pin/_pin_hashes.txt`）：
  - `amo_schedule.py f9ed08f7133dc9f4`（116422B，mtime 10:28:08）
  - `amo_diary_guard.py 05e0142695a9ac5c`（54880B，10:28:49）
  - `amo_fact_timeline.py dbc9abdae5a537bd`、`amo_state_diary.py ef34f058fd9f1254`、`amo_diary_audit.py 7e212a9e0e3e62f5`、`schedule_diary.py 3f7d9fb7340d3380`
  - 注意：生产代码在审计期间仍被 task-4 修改；上表是 pinned 快照，当前工作区可能已变，复跑前请重新记录哈希。

## 7. 限制与未覆盖

- 代码在途：`amo_schedule.py`/`amo_diary_guard.py` 在 10:28 仍被 task-4 写入，本报告的代码结论固定在 10:30 快照；部分行号可能漂移。
- 真实库快照有并发写入：审计期间生产实例仍在写 `amo_forever.db`（wal），10:22:20 / 10:32:09 两次快照内容可能相差几行；本报告只读、未写库。
- 自动核对器对中文口语地点有词表误判（例：办公室里的「咖啡」被判 restaurant、「路上」块写目的地被判 unvisited）；第 3 节中已人工剔除明显误报，保留的是有真实事实证据的条目。
- 3/5/7 仿真用确定性回显 LLM 的块/hourly 层不评估语义质量，也不含聊天/账本事件；真实库证据优先于仿真。
- 未覆盖：照片/消息/主动消息里引用日记内容的链路（另一 task）、跨天 digest 对聊天的注入（另一维度）。
