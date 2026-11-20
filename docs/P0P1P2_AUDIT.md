# 能陪（Astrbot_amoupup_forever）22 项重构 · P0/P1/P2 事后审计

> 性质：**只读事后审计（retro-audit）**。未改生产代码、未写 plugin_data、未热重载、未动版本号；本仓库本次只新增本文与 `docs/FEATURE_CHECKLIST.md`。
> 审计时间：2026-11-17 09:2x–09:5x。工作副本：`D:/dafeiyu/astrbot/data/plugins/Astrbot_amoupup_forever`。
> 解释器：`D:/dafeiyu/uv/tools/astrbot/Scripts/python.exe`（Python 3.12.16 / pytest 9.2.0）。
> 对照材料：`REFACTOR_STATE.md` / `AGENTS.md`（C:/Users/123/Documents/deepseek-harness/default-workspace/）、`docs/ARCHITECTURE.md`。

## 0. 一句话结论

22 项**代码交付基本都在、结构指标可复核（814 / 0 / 0 / 0）**；但 **P0 的「可回滚」是形式上的**——22 个备份目录全部存在，**却没有任何一个带 `manifest.json`**，`tools/rollback.py` 的 `verify/apply` 对所有既有备份一律拒绝；**P1 的跨模块不变量明显偏薄**（约会↔聊天地点、物价↔账本两条链路几乎没有测试）；**P2 只做了 helper 去重，热路径的重复读库基本没动**。此外**审计时 `gate --fast` 是红的**（`photo_room.py` 新增 1 处无作用域 `except Exception: pass`），照现状**不能打包/上传**。

> **⚠ 已被 §7 推翻（2026-11-17 09:47–09:57）**：36 份阶段备份已全部补上 `manifest.json`（`verify` 36/36 RC=0），默认参数不再拒绝，副本 `apply` 也跑通 3 份。下面 §0–§6 保留**审计当时**的实测口径，不再代表现状；**P0 的最终状态只看 §7**（§3 的「回滚演练」列一并作废）。

## 1. 审计方法（全部命令可复跑）

| 证据 | 命令 | 结果 |
|---|---|---|
| 备份目录 | `ls -d D:/dafeiyu/backup/*/` | 143 个目录；22 项对应目录 22/22 存在 |
| 回滚清单 | `ls D:/dafeiyu/backup/**/manifest.json` | **0 个** |
| 回滚演练 | `python tools/rollback.py plan --root D:/dafeiyu --backup <备份> --target <插件>` | 22/22 `RC=0` |
| 默认调用 | `python tools/rollback.py plan --backup <备份> --target <插件>` | 4/4 **拒绝：路径越界**（备份在 `D:/dafeiyu/backup`，不在默认 root `…\plugins` 内） |
| 恢复演练 | `python tools/rollback.py apply …`（不带 --confirm，dry-run） | **EXIT 2：清单不存在**（`manifest.json` 缺） |
| 测试清点 | `pytest --collect-only -q tests/`、`-q work/` | **3273 + 7 = 3280 collected** |
| gate | `python tools/quality_gate.py --fast` | **EXIT 1**（`check_silent_except`：`photo_room.py 1>0`） |
| 结构 | `python work/structure_audit.py` | `main.py 814 / >1800 行 0 / >120 行函数 0 / TODO 0` |
| 反向验证留档 | `grep -r "turns_it_red|negative_control|removing"` tests/ | 25 个文件 57 处「改坏必红」断言 |

**口径说明**：任务要求「不跑全量」，所以 `3250 passed` 这类**运行态数字没有被复跑**，只能用 collect 计数与现状对照；差异见 §6。

## 2. 总判定（先看结论再看表）

1. **备份：目录齐、清单缺。** 22/22 目录存在；`**/manifest.json` = 0。`verify/apply` 依赖清单，因此「回滚」目前**只有 plan 可用、apply 不可演练**。
2. **plan 需要放宽 root。** `rollback.py` 默认 `--root` = 插件父目录（`…/data/plugins`），而所有备份都在 `D:/dafeiyu/backup`——**默认参数一律拒绝**。加 `--root D:/dafeiyu` 后 22/22 通过，说明「路径可解析、清单可生成」，但这是一条**只有作者知道的隐性前置条件**，交接文档没写。
3. **布局不统一。** 7 个备份是「容器」布局（`plugin/`、`plugins/`、`Astrbot_amoupup_forever/` 套一层），plan 显示「新增 N、覆盖 0」——**这种目录直接 apply 会把备份内容摊进插件根目录**，等于污染而不是还原。P0 的基线备份（全量安全网）恰好就是最不统一的那个。
4. **P1 测试面广，但跨模块链路没有对账测试。** 单个模块的正确性覆盖很好（`test_consistency_audit.py` 30 项、`test_schedule_invariants_storage.py` 4 项、`test_add_schedule_item_overlap.py` 6 项…），缺的是「A 的写入 / B 的读取」之间的不变量。
5. **P2 只做了一半。** helper 去重（clock/number/place/idem/money）是真的；**热路径每次请求重复读库没处理**（§4）。
6. **交付流水线两处红灯**：gate 现红（`photo_room.py` 第 33 行 `except Exception: pass`，无作用域、无 `# cleanup`）；`price_book.py`（117 行）**零测试**却已被 `amo_trip_cost.py:91/318` 调用。

## 3. 22 项逐项表

**列说明**
- *备份* = 目录名 / 实测文件数 / 布局（「根」= 即插件根，可直接对插作 target；「容器」= 套了一层子目录）。
- *回滚* = `plan` 实测「新增/覆盖」（`--root D:/dafeiyu`）+ 是否可直接 apply。**全部 22 项 apply 均不可行（无清单），下表不再重复**。
- *反向验证* 一律是 `REFACTOR_STATE.md` 的文字记录（**无脚本化留档**），除标注 ✅ 的以外本次未复跑。

| # | 项 | P0 备份（ls 实测） | 回滚演练（plan 实测） | P1 测试证据 | 反向验证（记录） | P2 | 缺口 / 风险 |
|---|---|---|---|---|---|---|---|
| P0 | 基线/全量备份 | `full-refactor-pre-20261116-195014` / 704 / **容器**（`plugins/,db/,README-回滚.txt`） | 新增 704 / 覆盖 0 → **不可直接 apply** | —（本项无测试） | — | — | **高**：全量安全网是容器布局且无清单；真出事只能手工 `cp -r` |
| P1 | Timeline | `phase1-timeline-20261116-201736` / 418 / 容器（`db,plugin`） | 新增 400 / 覆盖 0 → 不可直接 apply | `test_timeline.py`、`test_block_diary.py`、`test_detail_hour_key.py` | block_id→整点键 8 红；去空 end 防御 2 红 | 删 `_sorted_blocks` 等 3 处旧径 | **中**：容器布局；旧写入仅留 `block_diary_enabled=false` 回滚 |
| P2 | DateSession | `date-session-pre-20261116-2015` / 621 / 容器（两插件目录） | 新增 603 / 覆盖 0 → 不可直接 apply | `test_date_session.py`、`test_date_context.py`、`test_date_facts.py`、`test_date_ending.py`、`test_transition_finish.py` | 5 条（finish 不清 together 等） | 老键投影只读，不再写 | **高**：`date_session` 在 `place_guard/reply_fact_guard/location_rules/diary_guard` 测试中 **0 命中**（§3.1 第 2 条） |
| P3 | FactTimeline | `fact-timeline-pre-20261116-2045` / 462 / 根 | 新增 1 / 覆盖 112 → 可直接 apply（补清单后） | `test_fact_timeline.py`、`test_fact_pool.py`、`test_fact_binder.py` | 4 条（去窗口裁剪等）；9 场景逐字节 A/B | `fact_pool` 662→246、`binder` 476→422 | 低 |
| P4 | ConfigModel + PromptRegistry | `config-prompt-pre-20261116-2101` / 403 / 根 | 新增 0 / 覆盖 113 → 可直接 apply | `test_config_model.py`、`test_prompt_registry.py`、`test_dynamic_context_snapshot.py`、`tests/golden/prompts_golden.json` | 3 条（清空 lean/改块序/不校验类型） | schema 259 键单一真相；注册表 64 块 13 lean | 低（残留：`load_schema` 宽 except 无日志；schema 缓存需重载） |
| P5 | StateStore | `state-store-pre-20261116-2125` / 410 / 根 | 新增 0 / 覆盖 109 → 可直接 apply | `test_state_store.py`（25 项） | 3 条（TTL 失效/prune 直接删/放行非法 key） | 28 域登记、56 老前缀归类、prune 需 confirm_token | 低（`prune` 生产无调用点，设计如此） |
| P6 | ReplyPipeline | `reply-pipeline-pre-20261116-2135` / 412 / 根 **+ `-evidence` 目录**（A/B json/diff） | 新增 0 / 覆盖 106 → 可直接 apply | `test_reply_pipeline.py`（40 项） | 4 条（调换 clean/names 顺序等） | generate/decorate 只登记不接管 | **中**：守卫跨阶段可复跑两遍（已知、幂等为主） |
| P7 | DB/JSON schema_version + 保留 | `schema-retention-pre-20261116-2200` / 2201 / 根 | 新增 860 / 覆盖 105 → 混合，需人工挑目录 | `test_schema_retention.py`、`test_models_schema.py` | 3 条（迁移跳过 v2 等） | `PRAGMA user_version` 0→2；9 表登记 | 低（`todo/proactive_log/relation_network` 只统计不删） |
| P8 | 旅游拆分 | `trip-split-pre-20261116-2214` / 2220 / **容器**（`插件目录/_splitter`） | 新增 1290 / 覆盖 0 → 不可直接 apply | `test_trip_split.py`（61 命中）、`test_trip*.py` | 3 条（门面漏 re-export 等） | `amo_trip` 1748→397，四模块 379/426/582/209 | **中**：`state.load()` 依赖门面注册 tick（单独 import 不推进） |
| P9 | PhotoPlan | `photo-plan-pre-20261116-2232` / 1991 / 根 | 新增 632 / 覆盖 106 → 混合 | `test_photo_plan.py`（28 项） | 4 条（回二次解析等）；8 组 prompt/lock 逐字节 A/B | `photo_rules` 625→524 | 中（`PhotoPlan(zone=)` 别名失效，仓库内无用法） |
| P10 | 面板拆分 | `panel-split-pre-20261116-2255` / 2001 / 根 | 新增 637 / 覆盖 107 → 混合 | `test_panel_split.py`（18 项）、`tests/panel_source.py` | 3 条（api-client 改名/审计按钮 id/绝对路径） | `index.html` 5554→**151**，assets 4 文件；routes 仍 93 | 低（本机无 node，gate 的 dashboard js 仍 skip） |
| P11 | 能陪 P2 | `amo-p2-pre-20261116-2310` / 2257 / 根 | 新增 650 / 覆盖 100 → 混合 | `test_p2_helpers.py`、`test_p2_regressions.py`、`test_numeric_helpers.py`、`test_clock.py`、`test_source_guard.py` | 3 条（yuan 少一位/裸 time.time/FakeWallet 负余额） | **裸 clock 127→9**；30 处 helper→import；numbers/place/idem | 低（`amo_hunger._extra_rate` 仍裸 `date.today()`，已登记） |
| P12 | 能记 P1 | `data-p1-pre-20261116-2328` / 6096 / **容器**（`Astrbot_amoupup_data` + 日志/脚本） | 新增 6012 / 覆盖 0（**对能陪 target 无意义，需对能记 target 重跑**） | 能记 `tests/test_data_p1.py`（其它插件） | 记录在 REFACTOR_STATE | 能记 db/memory schema 版本 | **中**：本轮未复核能记侧 |
| P13 | 能记 P2 | `data-p2-pre-20261117-0024` / 4071 / **容器嵌套**（`Astrbot_amoupup_data/Astrbot_amoupup_data`） | 新增 2374 / 覆盖 0（同上，target 不同） | 能记 recall/handlers 测试 | 4 条（RECALL_W_REL 等） | 34 handler 拆分、候选池 4000、四段 recall | 中（同上） |
| P14 | 守护 P1 | `watch-p1-pre-20261117-0045` / 487 / 根（watch） | 新增 180 / 覆盖 20（**对能陪 target 无意义**） | 守护 `tests/test_watch_p1.py`（24 项） | 4 条（bridge 降级/重复收尾等） | `date_engine` 1288→996、`social_engine` 1175→1114 | 中（同上） |
| P15 | 守护 P2 | `watch-p2-pre-20261117-0058` / 496 / 根（watch） | 新增 184 / 覆盖 20（同上） | 守护 `test_watch_p2.py`（48）、`test_contract_surface.py` | 3 条（money float/idem 少拼/审计摘 idem） | `utils/money.py`、`ledger_audit` 6 规则、22 文件去重 | 中（同上） |
| P16 | JobManager | `jobmanager-pei-20261117-0116` / 2313 / 根 | 新增 688 / 覆盖 49 → 混合 | `test_jobs.py`（11 项）；守护/能记各 11 项 | 3 条（去单飞锁/去超时/stop 不 cancel） | main 792→788；`_spawn` 受管 | 中（`report()` 用 monotonic；`loop_interval` 注册时读一次） |
| P17 | 可观测性/PII/错误码 | `obs-pii-peiban-20261117-0207` / 445 / 根 | 新增 0 / 覆盖 50 → **可直接 apply** | `test_obs_pii.py`（17）、`test_observability.py` | 3 条（脱敏返原文/health 抛/错误码错） | 三份 `observability.py` sha256 一致 | 低 |
| P18 | 测试基建 | `test-infra-nengpei-20261117-0224` / 861 / 根 | 新增 10 / 覆盖 47 → 可直接 apply | `test_test_infra.py`（13）、`test_fixtures_contract.py` | 4 条（stub 追加注释/删 Image 符号等） | 三份 canonical stub 逐字节一致；`work/sim_lib.py` | 低（gate 走 unittest 不加载 conftest，已登记） |
| P19 | 协议版本化 + 发布回滚 | `protocol-amoupup-20261117-0248` / 863 / 根 | 新增 0 / 覆盖 40 → **可直接 apply** | `test_protocol_rollback.py`（15）、`test_cross_plugin_contract.py` | —（工具类，无「改坏必红」记录） | `tools/rollback.py`、`release_check.py` 三份 sha256 一致 | **高**：交付了回滚工具，**但没给任何既有备份生成清单**，工具对 22 个备份全部不可用 |
| P20 | clean_schedule + 历史审计 | `clean-schedule-code-20261117-0304` / 873 / **容器**（`plugin/`）+ `-db-0304`、`-report-0310`、`-live-0657` | 新增 457 / 覆盖 0 → 不可直接 apply | `test_clean_schedule.py`（18）、`test_diary_audit.py` | 3 条（去 overlap 规则/apply 不校验 digest/不写审计） | `work/clean_schedule.py` 654、`diary_audit_apply.py` 381 | **中**：AstrBot 仍持内存日程，可能再写回重叠（06:12 即此） |
| P21 | 最终回归 | `residual-Astrbot_amoupup_forever-20261117-0745` / 884 / 根；另 `Astrbot_amoupup_forever-20261117-0846(49)` | 新增 3 / 覆盖 41 → **可直接 apply** | `test_schedule_invariants*.py`、`test_schedule_save_entry.py`、`test_add_schedule_item_overlap.py`、`test_consistency_audit.py` | 结构/契约/回滚 plan 复跑（记录） | 结构 814 / 0 / 0 / 0（**本次复核一致**） | **高（现状）**：gate 现红 `photo_room.py:33`；测试 3280 collected 与记录的 3250 passed 口径未对齐 |

### 3.1 跨模块不变量缺口（本节是这次事故的直接教训）

> 逐条标「已有测试」= 能指出文件/行号；「缺口」= 找不到任何测试把两端**同时**断言。

| # | 链路 | 已有测试 | 缺口 | 等级 |
|---|---|---|---|---|
| 1 | 钱包 ↔ 日程消费 | ✅ `test_consistency_audit.py:268`（当日支出不为负、同 idem 单笔）、`test_add_schedule_item_overlap.py:145/158/169/191`（财务写日程过闸、table/kv 一致、**负向对照**）、`amo_api_schedule.py:222 _detail_spending` 只读 `wallet.rows_between` | 日程块的消费金额与账本条目**无「同刻同额」对账**；面板详情只聚合 wallet，不校验与块 detail 金额一致 | 中 |
| 2 | 约会 ↔ 聊天地点 | ✅ `test_consistency_audit.py:251/256/261`（约会块面对面、约会后共处一室、约会后独行）——但都在**日程侧** | `date_session` 在 `test_place_guard.py` / `test_reply_fact_guard.py` / `test_location_rules.py` / `test_diary_guard.py` 中 **0 命中**：没有测试断言「聊天里说地点时按会话地点校验」。**这就是 06:12 类事件的同构风险** | **高** |
| 3 | 旅游 ↔ 账本 | ✅ `test_consistency_audit.py:283`（旅行溢价只 food/transport）、`:290`（财务义务不走旅行溢价）、`:420`（旅行期间账单/工资照常）、`test_trip*.py` 多处 cost | 行程单汇总（` amo_trip_cost`）与 `wallet.ledger` 总额**无对账测试**；退款/重算后两侧是否同额无断言 | 中 |
| 4 | 面板 ↔ 库 | ✅ `test_api_read.py`、`test_api_write.py`、`test_panel_split.py`（前端契约 18 项）、`test_panel_doing.py`、`test_route_table.py`（97 路由） | 面板 GET 的**数字**与 canonical 表的一致性无跨层测试（`api_schedule` 读 Timeline→normalize→展示，断言只到形状不到数值）；清理/合并后「面板看到的条数 == schedule 表条数」无人守 | 中 |
| 5 | 物价 ↔ 账本 / 历史 | ❌ **无**：`tests/test_price*.py` **不存在**；`price_book.py`（117 行）零测试 | 仅被 `amo_trip_cost.py:91`（multiplier）与 `:318`（`quote(None, kind)`，**city/item 被丢弃**）调用；`price_book.py:4` 声称「v1 与 TRIP_COST_RANGES 逐项一致（A/B 测试锁）」，但**锁的测试并不存在**；`load()` 每次调用重读盘+全表校验（`:53-76` 明确「不缓存」） | **高** |
| 6 | housing ↔ 照片 | ✅ `test_photo_room.py`（12 项：sleep→bedroom、sofa/desk/meal→living、housing config 驱动、room_ref 顺序、空镜 allow_local_refs、旧 omni 重试）、`test_home_room_refs.py`（6 项） | `photo_room.py:33` 的 `except Exception: pass` **把 housing 故障静默吞掉**（gate 因此变红）；无测试断言「housing 抛异常时 room_label 的来源与告警」 | 中 |

**为什么 06:12 那类会漏**：单模块测试全绿（各自都说自己对的），而**唯一能发现「kv 与 schedule 表不同步」的测试**是事后补的 `test_schedule_invariants_storage.py:109/122/135` 与 `test_add_schedule_item_overlap.py:179/184`。P1 当时没有「写入口唯一性」的清单，所以 `add_schedule_item` 绕闸整整存在了一整轮。

## 4. P2 热点候选（只读 grep，**未改任何代码**）

| 位置 | 现象 | 估计收益 | 风险/备注 |
|---|---|---|---|
| `amo_api_schedule.py:360-422` `api_schedule` | 一次面板 GET 依次：`Timeline(...).blocks(day)`（:368）→ `schedule_tree(day)`（:401，内部又跑 `_repair_transit_blocks` + `settle_date_end` + `get_schedule`，见 `amo_state_schedule.py:1228-1250`）→ `current_activity()`（:419）→ `schedule_context()`（:420）→ `blind_hint()`（:421） | 省 1 次全日程读 + 1 次修复扫描/请求。面板 30s 刷新 ≈ 2880 次/天（单标签页），按当天 25 块估 ~2–5 ms/次，日省数秒 CPU 与写事务 | **中**：`schedule_tree` 带写副作用（repair/settle），只读接口里做修复，本身是 P1 隐患 |
| `wardrobe_queries.py:178-262` `snapshot` | `_load()` 至少 3 次（:179 自身、`today()` :167、`current()` :517）；`_at_home/_at_swim/_at_hotel` 又被 `today()/night()/current()` 反复调用（:188/190/191 与 :494/496/504） | 5→1 次 JSON 读、日程判定 4→1 次；衣柜面板每次刷新省 3–4 次读盘 | 低（纯读，缓存需带日界失效） |
| `amo_api.py:671/676` `api_status`/`api_get_status` | 两个路由各自完整跑一遍 `_status_data()`；面板同时轮询两者时**整份状态算两次** | 每次刷新省一半状态计算（含 weather/period/health 聚合） | 低 |
| `photo_engine.py:364-383` `_activity/_candidates`（+ `:694 status_text`） | 每次候选/生成/状态各调一次 `state.current_activity()`（内部读日程），一次出图链路 2–3 次 | 2–3→1 次日程读/出图 | 低 |
| `price_book.py:53-76` `load` + `amo_trip_cost.py:318` | 每次 `quote()` 重读 JSON + 全表校验（注释明确「不缓存」）；旅行结算按条目调用 | 20 条账目/天 → 20 次读盘变 1 次（<1 ms 级，但 IO 抖动可省） | 低（静态表，缓存安全） |
| `amo_trip_cost.py:318` `quote(None, kind)` | 传 `None` 作 plugin、**丢弃 city/item**，永远命中 `*` 通用价 | 不是性能问题而是**正确性**：多城市/多档价目生效后这里会静默取错价 | **高**（并入 §3.1 第 5 条） |

## 5. 结论：哪些完整、哪些走过场、补什么

### 5.1 实际完整（代码 + 测试 + 可复核数字都在）
P3 FactTimeline、P4 ConfigModel/PromptRegistry、P5 StateStore、P7 schema/retention、P8 旅游拆分、P10 面板拆分、P11 能陪 P2、P17 可观测性、P19 协议版本化、P20 clean_schedule（限工具与 dry-run）、P21 结构/契约/回滚 plan。

### 5.2 走过场（交付了形态，没交付能力）
1. **P0/P19 的回滚**：目录有、工具在、**清单没有**；`apply` 对 22/22 备份不可用；默认参数还拒绝一切备份路径。「可回滚」目前是**文档级**的。
2. **P1 的「跨模块不变量」**：每条链路都有单边测试，双边对账几乎没有（§3.1 第 2、5 条为空白）。
3. **P2**：只做了 helper 收敛；「去重复调用/重复读库」这条**没有真正开工**（§4 全是现成的重复）。
4. **反向验证**：57 处断言是好的，但**只有文字记录、没有脚本/日志留档**，事后无法一键重放。
5. **物价（新增 `price_book.py`）**：代码先行、测试为零、调用点已经接上（`amo_trip_cost`），docstring 里承诺的 A/B 锁不存在。

### 5.3 建议补什么（按优先级）

**P0（先做，否则不许发布）**
1. 给「准备用于回退的」备份补 `manifest.json`（至少 P0 全量、P20 live 热备、P21 收尾），并对**副本**跑一次 `plan → apply --confirm` 演练，留下退出码。
2. 修 `rollback.py` 的默认 root 或写进文档：`--root D:/dafeiyu`；并对容器布局备份加「自动下钻一层」的识别，避免 apply 摊平。
3. 修 `photo_room.py:33`（改成 `_note_swallowed("photo_room.room_profiles", exc)` 或加 `# cleanup`），让 `gate --fast` 回绿。
4. 明确测试基线口径：`collect 3280` vs 记录 `3250 passed` 的差 = 收尾后新增用例；跑一次全量后写死新基线。

**P1（本轮重构的真正欠账）**
5. 补「约会↔聊天地点」双向测试：会话地点变更后，`place_guard/reply_fact_guard` 必须按新地点判否（至少 4 条：面对面/各回各家/途中/旧库无会话）。
6. 给 `price_book.py` 补测试（至少：v1 与 `TRIP_COST_RANGES` 逐项一致、city/item 命中顺序、未知城市回退、版本有效期、非法 JSON），并把 `amo_trip_cost.py:318` 改为传 city/item。
7. 补「面板数字 == 表条数」「行程单总额 == ledger 支出」两条对账测试。
8. 反向验证脚本化：每阶段一个 `work/reverse_check_<phase>.py`，记录「改坏→红→恢复→绿」的 pytest 退出码。

**P2（排最后，但别再跳过）**
9. §4 的 5 处热路径按收益从高到低做，每处给前后耗时（至少 1 条 before/after 数字）。
10. `schedule_tree` 的读时修复拆成显式维护任务，只读接口不带写副作用。

## 6. 需 lead 补证 / 本次无法完成的事项

| 事项 | 为什么本次做不了 | 建议 |
|---|---|---|
| `3250 passed / 7 skipped / 77 subtests` 的复跑 | 任务要求「不跑全量」；只做了 collect（3280） | 由 lead 跑一次全量并写入新基线 |
| 能记 508 / 守护 702 | 本会话限制在能陪目录 | lead 或对应子代理复核 |
| P12–P15 的 `plan` 有意义结果 | 备份分别是能记/守护，`--target` 必须是各自目录 | 对各自 target 重跑 plan（3 条命令） |
| 反向验证的原始输出 | 只有 REFACTOR_STATE 的文字结论，无日志文件 | 找 `.log`/spill，或重做脚本化反向验证 |
| `manifest.json` 与真实 restore | 生成清单是写操作（本次只读）；apply 会改生产目录 | 在副本上演练，绝不直接对生产 apply |
| `photo_room.py` 属于 P22 在制品 | 它不在 22 项内（09:15 后新增） | 由 P22 负责人修 gate，别混进本次重构结论 |

---
*本审计为只读结论；除本文与 `docs/FEATURE_CHECKLIST.md` 外未修改仓库任何文件。*

## 7. P0 收尾状态（2026-11-17 09:47–09:57，p0-backlog 子代理补测）

> 本节推翻 §0/§2/§3 里「22 个备份**没有任何** `manifest.json`」「apply 全部 EXIT 2」「默认参数一律拒绝」的结论——那是**审计当时（09:2x）**的快照，现已失效。**P0 的最终状态只看本节**，§3 的「回滚演练」列同样作废。
> 工具：`tools/make_manifest.py`（批量生成/校验）、`tools/rollback.py`（manifest/verify/plan/apply）。**未改插件运行代码、未写 `plugin_data`、未动版本号。**
> 解释器：`D:/dafeiyu/uv/tools/astrbot/Scripts/python.exe`（Python 3.12.16）。

### 7.1 这轮改了什么（只动 tools/，不动插件）

| 改动 | 为什么 | 证据 |
|---|---|---|
| 收紧 `rollback.py: _looks_plugin` | 根上多放一个散装 `.py`（`data-p1-pre` 的 `compare_tree.py`）就被当成插件根，apply 会把整棵容器摊进 target | 该备份 `plugin_root` 由 `.` 修正为 `Astrbot_amoupup_data`（6007 文件）；plan 由「新增 6012/覆盖 0」→「新增 5905/覆盖 24/保留 30」 |
| 新增 `_has_markers` / `_descend`，接到 `layout_root` 四个返回点 | 容器套容器（`data-p2-pre` 是「插件目录整包快照」里再套插件）、多子目录容器（`phase1-timeline` 的 `<bk>/plugin/`）定位不到插件根 | `data-p2` plan 由「新增 2370/覆盖 1」→「新增 0/覆盖 22/保留 26」；`phase1-timeline` 由「新增 400/覆盖 0」→「新增 0/覆盖 113/保留 145」 |
| 下钻最多 4 层；同名匹配不到就**拒绝**而不是猜 | 多插件容器里选错根 = 污染 target | 见 §7.5 第 5 条：给容器备份配一个改名过的 target 时，`apply` 直接 `EXIT 2 清单不存在`，不写任何文件 |

`tests/test_rollback_manifest.py` **8/8 通过**（覆盖：清单 schema/sha、篡改检出、容器布局、多插件容器、默认根自动识别、缺清单拒绝、副本 apply、批量 scan）。

### 7.2 22 项逐项状态（manifest✓ / verify✓ / plan✓ / apply✓）

**读法**：manifest✓ = `manifest.json` 已落盘；verify✓ = `rollback.py verify --dir` RC=0；plan✓ = `rollback.py plan --backup <bk> --target <插件>` RC=0；apply✓ = 在**副本**上 `--confirm` 真跑过。
「新增/覆盖/保留」为 plan 的 dry-run 数字；目标文件数会随插件在制漂移（本轮实测 541→547→551），**以 RC 为准**。

| # | 备份目录 | manifest 文件数 | 指纹(sha256/12) | verify RC | plan RC | 新增/覆盖/保留 | apply✓ |
|---|---|---|---|---|---|---|---|
| P0 | `full-refactor-pre-20261116-195014` | 101+397+202 | 388183d8350f | 0 | 0 | 0/114/146 | ✅ 已演练 |
| P1 | `phase1-timeline-20261116-201736` | 399 | 28ffbef99315 | 0 | 0 | 0/113/145 | — |
| P2 | `date-session-pre-20261116-2015` | 400+203 | bbacb9f57cc1 | 0 | 0 | 1/112/144 | — |
| P3 | `fact-timeline-pre-20261116-2045` | 402 | 5c8f8b325f0b | 0 | 0 | 1/112/142 | — |
| P4 | `config-prompt-pre-20261116-2101` | 403 | 3ee82192fc75 | 0 | 0 | 0/113/141 | — |
| P5 | `state-store-pre-20261116-2125` | 410 | 866aaa5a0838 | 0 | 0 | 0/112/137 | — |
| P6 | `reply-pipeline-pre-20261116-2135` | 412 | 1379b977f45a | 0 | 0 | 0/108/132 | — |
| P7 | `schema-retention-pre-20261116-2200` | 1275 | d8fbbc2859bf | 0 | 0 | 860/108/129 | — |
| P8 | `trip-split-pre-20261116-2214` | 1286+4 | 4f780fdfa0bf | 0 | 0 | 867/108/125 | — |
| P9 | `photo-plan-pre-20261116-2232` | 1056 | 4e3de5c1d74f | 0 | 0 | 632/109/120 | — |
| P10 | `panel-split-pre-20261116-2255` | 1064 | 1c827c24ea1e | 0 | 0 | 637/110/117 | — |
| P11 | `amo-p2-pre-20261116-2310` | 1085 | 3de116cfd944 | 0 | 0 | 650/103/109 | — |
| P12 | `data-p1-pre-20261116-2328` | 6007 | 169dd7296795 | 0 | 0 | 5905/24/30 | — |
| P13 | `data-p2-pre-20261117-0024` | 2374 | 336029af852e | 0 | 0 | 0/22/26 | — |
| P14 | `watch-p1-pre-20261117-0045` | 204 | c6849848d8fc | 0 | 0 | 0/31/22 | — |
| P15 | `watch-p2-pre-20261117-0058` | 208 | b27c901af8ac | 0 | 0 | 0/30/18 | — |
| P16 | `jobmanager-pei-20261117-0116` | 1131 | 3d4c9406c029 | 0 | 0 | 688/52/101 | — |
| P17 | `obs-pii-peiban-20261117-0207` | 445 | 94e492c70490 | 0 | 0 | 0/53/99 | — |
| P18 | `test-infra-nengpei-20261117-0224` | 458 | e7200c23eb3f | 0 | 0 | 10/50/96 | — |
| P19 | `protocol-amoupup-20261117-0248` | 454 | 00b1f89048b4 | 0 | 0 | 0/43/90 | ✅ 已演练 |
| P20 | `clean-schedule-code-20261117-0304` | 457 | bdd1d4cfec3f | 0 | 0 | 0/43/87 | — |
| P21 | `residual-Astrbot_amoupup_forever-20261117-0745` | 463 | 325e8c5c4610 | 0 | 0 | 3/45/84 | — |

- P0 的 `full-refactor-pre-*` 是**三分清单**：`plugins/Astrbot_amoupup_data` 101 + `plugins/Astrbot_amoupup_forever` 397 + `plugins/Astrbot_amoupup_watch` 202；apply 按 `plugin_root` 定位，**不再摊平**。
- P6 另有 `reply-pipeline-pre-20261116-2135-evidence`（8 个 A/B 证据文件，独立清单）；P20 另有 `-db`/`-report`/`-live` 三份数据侧备份 —— 都在 §7.3。

### 7.3 其余 14 份阶段备份（同一批生成）

| 备份目录 | manifest 文件数 | 指纹 | verify RC | plan RC | 新增/覆盖/保留 | 备注 |
|---|---|---|---|---|---|---|
| `reply-pipeline-pre-20261116-2135-evidence` | 8 | 14f0ccdbbff8 | 0 | 0 | 8/0/544 | — |
| `nightly-tests-pre-20261117-0011` | 254 | 04a8cf9434da | 0 | 0 | 250/1/540 | — |
| `jobmanager-ji-20261117-0116` | 121 | 0e71f38b3787 | 0 | 0 | 0/14/11 | — |
| `jobmanager-watch-20261117-0116` | 213 | 45d448c419d5 | 0 | 0 | 0/13/13 | — |
| `obs-pii-nengji-20261117-0207` | 123 | cc36d1efe1b8 | 0 | 0 | 91/27/512 | — |
| `obs-pii-shouhu-20261117-0207` | 214 | ec88642f8edb | 0 | 0 | 0/12/12 | — |
| `test-infra-nengji-20261117-0224` | 127 | 5ec1e7db142e | 0 | 0 | 94/28/511 | — |
| `test-infra-shouhu-20261117-0224` | 218 | 0ed6fca79d5c | 0 | 0 | 1/12/9 | — |
| `protocol-data-20261117-0248` | 129 | 51ef2df1ae50 | 0 | 0 | 0/4/3 | — |
| `protocol-watch-20261117-0248` | 223 | 54c5f0c1dc66 | 0 | 0 | 0/8/3 | — |
| `clean-schedule-db-20261117-0304` | 3 | 156797a61bd8 | 0 | 0 | 3/0/544 | **已 apply 演练** |
| `clean-schedule-live-20261117-0657` | 21 | f1944afd43ab | 0 | 0 | 21/0/544 | — |
| `clean-schedule-report-20261117-0310` | 3 | 50d350827f32 | 0 | 0 | 3/0/544 | — |
| `residual-Astrbot_amoupup_watch-20261117-0745` | 226 | 352a3e090c7c | 0 | 0 | 0/2/0 | — |

合计：**36 个目录 → 41 份 `manifest.json`**（`full-refactor` 3 份 + `date-session` 2 份 + `trip-split` 2 份 + `data-p2` 顶层/内层各 1 份 + 其余各 1 份），verify **36/36 RC=0**，plan **36/36 RC=0**。
备份树上现在共 **43 份** `manifest.json` = 本轮的 41 份 + 并行的 `photo-bg-20261117011923`、`price-book-20261117-092018`（由对应子代理生成，不在本表计数内）。

### 7.4 apply 副本演练（3 份；真实备份只读、真实插件未动）

| 演练 | 备份 | 副本 target | pre-plan | apply RC | copied | 新增/覆盖/保留 | 后置复检 | shelter |
|---|---|---|---|---|---|---|---|---|
| 数据 | `clean-schedule-db-20261117-0304`（3 个：`amo_forever.db`+`-shm`+`-wal`） | `_p0_drill/db`（3 个真备份文件各追加 7 字节模拟漂移） | 0/3/0 | **0** | 3 | 0/3/0 | 再 plan = **0/0/0** | `_p0_drill/db.rollback-20261117-095328` |
| 代码 | `protocol-amoupup-20261117-0248`（454） | `_p0_drill/protocol/target_forever`（真实插件 `cp -a` 副本） | 0/43/93 | **0** | 454 | 0/43/93 | 再 plan = **0/0/93**；py_compile 20/20 **通过** | `…target_forever.rollback-20261117-095330` |
| 全量基线 | `full-refactor-pre-20261116-195014`（容器，取 `plugins/Astrbot_amoupup_forever` 397） | `_p0_drill/full/Astrbot_amoupup_forever`（真实插件副本） | 0/115/154 | **0** | 397 | 0/115/154 | 再 plan = **0/0/154**；py_compile 20/20 **通过** | `…Astrbot_amoupup_forever.rollback-20261117-095436` |

- 演练后复跑备份 `verify`：`clean-schedule-db`=`156797a61bd8`、`protocol-amoupup`=`00b1f89048b4`、`full-refactor`（三份 388183d8350f/cb9b3bdee0e3/57fce02372b1）全部 **RC=0** → 真实备份目录本轮没被写坏。
- 生产目录旁**没有**任何 `*.rollback-*`（`ls -d data/plugins/*.rollback-*` 为空）→ 没对生产 apply 过。
- 三次演练都是「pre-plan → `apply --confirm` → 后置 plan 归零」，且每次都在 target 旁生成完整 shelter（可二次回退）。

### 7.5 存疑 / 需要 lead 定夺

| # | 事项 | 现状 | 建议 |
|---|---|---|---|
| 1 | `data-p1-pre` 的 6007 个文件里混着 `.pytest_cache`/`pytest-cache-files-*`/`.test-tmp`/`data/` | 备份是当时整目录快照，工具如实反映 → plan 仍「新增 5905」 | 真要回退能记 P1 会把这些缓存也带回去；要干净回退，先给这份备份出一版剔除缓存的清单 |
| 2 | `data-p2-pre` 顶层 `Astrbot_amoupup_data/` 其实是**整个插件目录快照**（内含能记/能陪/守护/…），真插件根在再下一层 | 顶层清单按容器记录（2374 文件，整包校验用）；另给内层 `…/Astrbot_amoupup_data/Astrbot_amoupup_data` 单独生成 106 文件清单 | apply 请**用内层目录当 `--backup`**（顶层 apply 会摊平）；建议这类快照加 `README-容器布局.txt` |
| 3 | `apply` 的 `--target` 目录名必须与备份里的插件目录同名（`full-refactor` 实测把副本改名 `target_forever` 后 `EXIT 2`） | 这是刻意的保守：多插件容器 + 名字对不上 → 拒绝而不是瞎选 | 真回退时 target 天然叫 `Astrbot_amoupup_forever`，不受影响；若要做「异地恢复」需加 `--plugin` 显式参数 |
| 4 | 目标文件数在漂移（541→547→551），因为 `photo-bg`/`price-book` 等仍在改同一插件 | plan 的「新增/覆盖/保留」每跑一次都不一样 | 以 RC 与「新增 0」为准；真正回退应在冻结窗口内做 |
| 5 | 本轮有一次 `plan` RC=1：`state-store-pre` 首次跑撞上 `D:/…/Astrbot_amoupup_forever/.dsh-ops-update-*.lock` 瞬时锁文件被删 | 重跑 **RC=0**（0/112/137） | 不是工具缺陷，是并发编辑窗口；跑 verify/plan 尽量避开其他子代理写文件的时段 |
| 6 | 全量 pytest 本轮 **10 failed / 3383 passed / 9 skipped**（43s） | 失败全在 `test_photo_plan`(3)、`test_photo_room`(1)、`test_prompt_registry`(5)、`test_trip_ledger_consistency`(1) | 与本轮改动无关：这些测试**零处**引用 `rollback`/`make_manifest`，且 photo/prompt 正是 `photo-bg` 等并行任务在制；`tests/test_rollback_manifest.py` **8/8 通过** |

### 7.6 结论

- **P0-3 达成**：36 份阶段备份（含审计 22 项全部）都有 `manifest.json`，`verify` 36/36 RC=0。
- **P0-4 达成**：默认参数（不给 `--root`）即可解析 `D:/dafeiyu/backup/**`；`plugins/<plugin>`、`<bk>/<plugin>`、`<bk>/plugin/`、`<bk>/<快照>/<plugin>/` 四种容器布局都能定位到真正的插件根。
- **P0-5 达成（3 份副本 apply RC=0）**：`clean-schedule-db`（3 文件）、`protocol-amoupup`（454）、`full-refactor-pre`（397），每份都「pre-plan → apply → 后置 plan 归零 → py_compile 通过」。
- **P0-7 达成**：P12–P15 按各自 target 重跑：`data-p1`→能记 `0/24/30`、`data-p2`→能记 `0/22/26`、`watch-p1`→守护 `0/31/22`、`watch-p2`→守护 `0/30/18`，4/4 RC=0，文件集合正确（不再把能记/守护的文件往能陪里倒）。

---
*本节为 P0 收尾补测。写操作仅限：备份目录新增 `manifest.json`、`tools/rollback.py` 的容器布局识别、`docs/` 两份文档、`D:/dafeiyu/_p0_drill/` 演练副本。*

## 8. event-id 加固 H3–H6 状态（2026-11-17 11:37–12:05，event-id-hardening-2 子代理）

> 本节只补 Stage 1.5（H3–H6）的交付状态与证据，不改 §0–§7 的事后审计口径。
> 实施前备份：`D:/dafeiyu/backup/event-id-hardening2-20261117-113732/plugin`（1363 文件）。
> 真实库仍 `user_version=2`（未迁移）；所有迁移/恢复只在副本演练。

| 项 | 交付 | 证据 |
|---|---|---|
| H3 | `block_diary.event_id` 列（迁移 v4）：`ALTER TABLE` + 从 `block_id` 回填；`AmoDB.save_block_diary` 双写同一身份；v4 之前的老库读/写自动退回旧列 | `test_event_id.py`（旧行回填、两列相等、旧代码按 block_id 可读、只给 event_id 时两列对齐、v4 中途失败回滚且保留已提交的 v3）；`block_diary_identity_mismatches()` 迁移后=0 |
| H4 | 旧账 `event_id` 回填（迁移 v5）：只认 `block:ev_<16hex>`（食品 ref 允许带时段后缀，如 `block:ev_xxx 22:10-22:30`）；`sch_...`/旧内容哈希/自由文本/空 id 不猜；只碰空列、不改金额/旧 ref | `test_ledger_ref_block_prefix_backfilled_others_left_empty`、`test_ledger_ref_backfill_is_idempotent_and_keeps_existing_ids`；真实库 72 条流水里 `block:`=0、`sch_`=0（63 空 ref、9 其它），迁移零改动 |
| H5 | `amo_timeline.split_schedule_block(day, parent, parts, inherit_index=0)`：父块/父 id 显式传入、指定哪段继承；`amo_food_cost.ensure_block_window` 切块改走它，`stamp_event_ids` 只作 fallback | `ExplicitSplitTests`（一拆多、多段复制父 id 只留一段、低重叠显式继承、父 id 字符串/缺失时确定性补）；`test_food_split_keeps_parent_id_on_earliest_piece`（22:00-23:00 切三段：父 id 落 22:00 段、新块返回自己的 event_id） |
| H6 | 全量 pytest + gate + structure；`amo_db_schema._migration_event_id` 已由 H1/H2 拆成 helper（当前 35 行，structure 全仓 >120 行函数 0）；副本 v2→v5 迁移 + v2 备份恢复演练 | `3528 passed, 9 skipped, 77 subtests`（43.9s）；`quality_gate.py` 全绿（含 tests+work）；structure `main.py 808 / >1800 行 0 / >120 行函数 0 / TODO 0`；副本演练：`user_version 2→5`、3 份热备 `[2→3,3→4,4→5]`、180 条块日记两列一致且正文不改、账本身份不改、恢复回 v2 后 `block_diary` 无 event_id、真实库 db/-wal/-shm sha256 前后一致 |

**本轮改动文件**：`amo_db_schema.py`、`amo_db.py`、`amo_timeline.py`、`amo_food_cost.py`、`tests/test_event_id.py`、`tests/test_db_backup_restore.py`、`tests/test_schema_retention.py`、`docs/MASTER_PLAN_20261117.md`、本文。**未动**：版本号、README、CHANGELOG、热重载、真实库。

