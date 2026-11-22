# 旧逻辑 / 死代码清理清单（task-31，只读审计）

> 审计对象：`D:/dafeiyu/astrbot/data/plugins/` 下的 **forever / watch / data / photo** 四个插件。  
> 审计时点：**2026-11-22 11:35–11:55 (Asia/Shanghai)** 的工作树快照；审计期间 task-23/24/27/29/30 仍在并行改文件，**行号以本快照为准、符号名优先**。  
> 方式：只读 + AST 静态扫描 + grep 交叉验证；未改生产代码、未 reload、未 commit/push、未写生产库。  
> 证据文件：`work/audit/dead_code_scan.py`（扫描器）、`work/audit/dead_code_scan.json`（461 模块 / 9227 定义原始数据）、`work/audit/dead_code_lists.json`（本报告表格数据）。

## 0. 口径与方法

- **生产引用**：四插件生产 `.py`（根目录 + `domains/` `infra/` `models/` `services/` `tools/` + watch/data/photo 包）里，经 AST 解析出的 import / 模块属性 / `self.` / `cls.` / 跨模块属性引用；字符串字面量等于符号名（`getattr(x,"name")`、注册表字符串、watch `_load(pkg,"module","attr")`、路由表 handler 名）单独计为动态引用。
- **测试引用 / work 引用**：`*/tests/**`、`watch|data|photo` 根目录 `test_*.py`、`*/work/**` 的引用；**不计入生产调用**，只用于判断“删了会不会弄坏测试/脚本”。
- **排除的“动态调用”**：AstrBot `@filter.*`/事件装饰器方法（本报告已过滤 decorated）、`__all__` 导出（exported）、AstrBot 插件 `main.py`/`__init__.py` 入口、以及 `getattr` 字符串/路由表字符串命中的符号。
- **范围说明**：`tools/*.py` 是 CLI 入口，零 import 属正常，未纳入死代码判定；`views/` 无 py；`tests/`、`work/` 不作为“生产是否死”的判断依据。
- **引用数列**：`生产/测试/work`；函数的“生产”= import+属性+字符串+动态 之和。**0/0/0 表示全仓库只有定义本身**（AST+grep 双验）。

### 0.1 明确保留（本任务硬约束，不在任何删除建议内）

| 位置 | 内容 | 为什么保留 |
|---|---|---|
| `amo_proactive.py:552-568` | `forced_reason` 的**情绪极值/大病/余额<5%** 红线 | 人设与照顾语义；其中“超过 8 小时没交流”分支已于 2026-11-22 删除，**禁止回滚**（沉默只决定能不能打扰，不是红线） |
| `amo_proactive.py:508-519` | `share_drive` / `missing_score` 评分 | 已与沉默时长解耦（分享欲=今天主动/被冷落，想念值固定 40）；评分是主动话术输入，不是旧逻辑 |
| `amo_state_schedule.py:1667,1726+` | `_enforce_zone_activity` 夜间/下班/病假兜底总闸 | 所有写日程路径的最后一道物理一致性闸；`decorated=True` 说明是公开闸门 |
| `amo_presence.py:97-99,226-237` | `is_silent`/`can_proactive` 沉默 6h 唯一策略 | 与 forced_reason 配合；删了会重新出现“越不理越催” |
| `amo_scene_ledger.py:476,500` | `scene_at` / `write` | 唯一场景读写入口（task-17/25/30 的收口目标），不是旧逻辑 |
| `amo_outbound.py` + `amo_message._send_to_umo` | 统一出站出口 | task-16 唯一发送 API；weather/photo/proactive 都只经它 |
| `domains/reply/repetition_guard.py:48-116` | `FALLBACKS` 唯一兜底注册表 | task-18 收口；`MODEL_LEAK_DEFLECTIONS` 等是同一对象的别名，测试用 `assertIs` 钉死 |
| `amo_state_snapshot.snapshot_with_scene` | task-30 兼容读（45 处生产调用） | 仍有大量读方依赖 `state.snapshot()` 嵌套形状；等所有读方迁到 `scene_at` 后才可谈删除 |
| `photo_share.PHOTO_FAILURE_LINES` / `amo_media.failure_line` | 失败话术唯一出口 | draw/photo 的失败补话仍经它；不要按“旧通知”误删 |
| `amo_schedule._maybe_weather_alert` / `_weather_alert_umos` / `_cleanup_weather_state` | 天气提醒唯一链路 | 唯一发送点走 `_send_to_umo(source="system", kind="weather")`；跨平台逐个尝试是 2026-11-22 P0 修复 |

## 1. 死模块（生产零 import / 零字符串引用）

判定来源：模块被谁 import（生产/测试/work）+ 字符串提及（`importlib`、`_load`、`type("...")` 等）。空包 `__init__.py`、`main.py`、`tools/` 不计。

| 文件 | 生产引用 | 测试 | work | 判定 | 说明 / 删除前置 |
|---|---|---|---|---|---|
| `amo_food_audit.py` | 0 | 2 | 10 | **需先迁移**（移出生产包） | 只被 work 审计脚本 import；功能=只读账单审计。建议移到 `work/` 或由 `work/wallet_audit_today.py` 接管后删；不要留在插件根目录。 |
| `amo_idem.py` | 0 | 1 | 1 | **安全删** | Phase 11 幂等键构造器，生产已直接用字符串键；仅测试引用。同步删 `tests/test_idem.py`。 |
| `amo_ports.py` | 0 | 1 | 1 | **保留（架构预留）** | docstring 明说“运行时还没有接入任何实现”；删它无收益但会让端口契约消失。若确定不接外部端口，可整模块+测试删。 |
| `compat.py` | 0 | 1 | 1 | **安全删** | 双路径 `load()` 工具；生产已全部改成 inline `try: from .x import y`。与 `amo_compat.py`（旧名兼容层，生产在用）**不是**同一个东西。 |
| `domains/memory/__init__.py` | 0 | 1 | 1 | **保留（契约）** | `MemoryTerminal`/`RecallScope` 是跨插件记忆契约，`test_domain_boundaries` 依赖；task-27/28 还在扩跨插件契约。 |
| `models/database.py` | 0 | 1 | 1 | **安全删** | pilot 薄封装，生产全走 `amo_db`；仅 `tests/test_models_schema.py` 引用。 |
| `models/schema.py` | 0 | 1 | 1 | **安全删** | 同上；`HP/SICK` kv 的直接读写生产不用。 |
| `domains/photo/room_ab.py` | 0 | 0 | 0 | **安全删** | 一次性房间参考图 A/B 工具，docstring 说“显式脚本调用”，但全仓库零调用；直接删。 |
| `domains/__init__.py、domains/diary/__init__.py、domains/kernel/__init__.py、infra/__init__.py、models/__init__.py` | — | — | — | **保留（包结构）** | 空包但承载 import 路径（`domains.*`/`infra.config`）；不删。 |
| `watch/services/ledger_audit.py` | 0 | 1 | 0 | **保留（只读 QA 工具）** | 账本审计入口，docstring 承诺面板/QA 可调；当前未接线。要么接面板，要么在确认无人用后连同测试删。 |

> 说明：`amo_food_audit` / `amo_idem` / `amo_ports` / `compat` / `models.*` / `domains/photo/room_ab` 是本轮**唯一**生产 import 为零的实模块；其余“零 import”都是 `main.py`、包 `__init__`、`tools/`，属正常入口。

## 2. 无调用符号清单

### 2.1 生产/测试/work 全零引用的顶层函数 —— **安全删**（39 个）

| 文件:符号 | 定义行 | 引用数(生产/测试/work) | 判定 / 备注 |
|---|---|---|---|
| `amo_config_model.py:register_migration` | 62 | 0/0/0 | 安全删；若保留需在 MIGRATION_HOOKS 注释里标明是外部扩展 API |
| `amo_current_action.py:action_text` | 126 | 0/0/0 | 安全删（读侧统一 get_current_action） |
| `amo_current_action.py:clear_action` | 198 | 0/0/0 | 可删；或把 3 处 current_action 清空点改调它（收口），再决定保留 |
| `amo_date_facts.py:dating_day` | 181 | 0/0/0 | 安全删 |
| `amo_diary_audit.py:parse_time_words` | 330 | 0/0/0 | 安全删 |
| `amo_fact_pool.py:_slot_window` | 62 | 0/0/0 | 安全删 |
| `amo_fact_pool.py:_filter_routine_facts` | 77 | 0/0/0 | 安全删 |
| `amo_food_cost.py:meal_charged` | 478 | 0/0/0 | 安全删 |
| `amo_food_event.py:_place_block` | 180 | 0/0/0 | 安全删 |
| `amo_grounding_critic.py:fallback_bodies` | 108 | 0/0/0 | 安全删；⏳task-29 收口后再动 |
| `amo_household.py:is_home` | 280 | 0/0/0 | 安全删 |
| `amo_json_schema.py:upgrade_all` | 269 | 0/0/0 | 安全删 |
| `amo_names.py:is_male_npc` | 77 | 0/0/0 | 安全删 |
| `amo_reply_pipeline.py:set_tone_llm_pick` | 859 | 0/0/0 | 安全删；⏳task-29 收口后再动 |
| `amo_schedule.py:_day_text_of` | 596 | 0/0/0 | 安全删；⏳task-30 收口后再动 |
| `amo_scope.py:is_group_row` | 21 | 0/0/0 | 安全删 |
| `amo_shared_time.py:day_label_text` | 256 | 0/0/0 | 安全删 |
| `amo_state_context.py:state_allowed` | 140 | 0/0/0 | 安全删 |
| `amo_timeline.py:for_plugin` | 467 | 0/0/0 | 安全删 |
| `draw_engine.py:_parse_minutes` | 194 | 0/0/0 | 安全删；⏳task-23/24 收口后再动 |
| `errors.py:is_known` | 72 | 0/0/0 | 安全删 |
| `micro_events.py:_hhmm_minutes` | 255 | 0/0/0 | 安全删；⏳task-30 收口后再动 |
| `outfit_refs.py:ensure_many` | 368 | 0/0/0 | 安全删 |
| `wander.py:plan_text` | 200 | 0/0/0 | 安全删 |
| `domains/photo/room_ab.py:ab_output_dir` | 18 | 0/0/0 | 安全删；安全删（一次性 A/B 工具，零调用） |
| `domains/photo/room_ab.py:run_room_ab` | 63 | 0/0/0 | 安全删；安全删（一次性 A/B 工具，零调用） |
| `watch/api/decisions_ext.py:extras_total_cents` | 298 | 0/0/0 | 安全删 |
| `watch/core/amo_contract.py:surfaces` | 109 | 0/0/0 | 安全删 |
| `watch/core/errors.py:is_known` | 72 | 0/0/0 | 安全删 |
| `watch/services/health_bridge.py:get_sick_duration` | 55 | 0/0/0 | 安全删 |
| `watch/services/npc_roster.py:_surname_like` | 128 | 0/0/0 | 安全删 |
| `watch/services/npc_roster.py:roster_hint` | 204 | 0/0/0 | 安全删 |
| `watch/services/npc_roster.py:status_map` | 219 | 0/0/0 | 安全删 |
| `watch/services/shopping_engine.py:_money_words` | 98 | 0/0/0 | 安全删 |
| `watch/ui/report.py:_month_of` | 35 | 0/0/0 | 安全删 |
| `watch/utils/helpers.py:prev_trading_day` | 185 | 0/0/0 | 安全删 |
| `data/services/memory_decay.py:is_noise` | 67 | 0/0/0 | 安全删 |
| `data/services/person_model.py:name_key` | 43 | 0/0/0 | 安全删 |
| `photo/providers/base.py:_replace_url_path` | 58 | 0/0/0 | 安全删 |

### 2.2 仅 work 脚本引用的顶层函数 —— **需先迁移**（3 个）

| 文件:符号 | 定义行 | 引用数(生产/测试/work) | 判定 / 备注 |
|---|---|---|---|
| `amo_food_cost.py:ensure_food_block` | 713 | 0/0/3 | 生产死；work 脚本用完即止。迁/删 work 调用后可删 |
| `amo_food_cost.py:night_quiet` | 730 | 0/0/3 | 生产死；work 脚本用完即止。迁/删 work 调用后可删 |
| `pose_library.py:trim_poses_data` | 381 | 0/0/2 | 生产死；work 脚本用完即止。迁/删 work 调用后可删；⏳task-23/24 |

### 2.3 死类（4 个）

| 文件:类 | 定义行 | 引用数(生产/测试/work) | 判定 |
|---|---|---|---|
| `domains/finance/__init__.py:WalletLike` | 12 | 0/0/0 | 安全删；随模块一起删 |
| `domains/memory/__init__.py:MemoryTerminal` | 15 | 0/2/2 | 需先迁移（仅测试引用） |
| `domains/memory/__init__.py:RecallScope` | 28 | 0/0/0 | 安全删；随模块一起删 |
| `models/database.py:Database` | 13 | 0/2/2 | 需先迁移（仅测试引用） |

### 2.4 仅测试引用的顶层函数 —— **需先迁移/保留（87 个）**

这些**不是**可以直接删的：要么是测试钩子/观测 API（保留），要么是生产已改用新实现、旧入口只剩测试在钉（删前必须同步改测试）。

| 文件:符号 | 定义行 | 测试/work 引用 | 判定 |
|---|---|---|---|
| `amo_compat.py:is_legacy_name` | 44 | 6/6 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_compat.py:image_data_dirs` | 68 | 2/2 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_config_model.py:register_legacy_key` | 58 | 1/1 | 需先迁移：删/改测试，或接回生产（生产已无调用） |
| `amo_diary_guard.py:invalidate_narrative_view` | 223 | 1/5 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_diary_guard.py:diary_day_facts` | 597 | 4/4 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_fact_binder.py:_kv` | 73 | 11/11 | 需先迁移：删/改测试，或接回生产（生产已无调用） |
| `amo_fact_binder.py:view_detail_lines` | 323 | 1/1 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_fact_pool.py:chat_facts` | 47 | 2/2 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_fact_pool.py:activity_facts` | 52 | 5/5 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_fact_pool.py:_routine_facts` | 57 | 1/1 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_fact_pool.py:_filter_window_facts` | 71 | 1/1 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_fact_timeline.py:day_view_facts` | 1745 | 2/11 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_food_audit.py:audit_meals` | 98 | 3/5 | 需先迁移：删/改测试，或接回生产（生产已无调用） |
| `amo_food_audit.py:audit_rows` | 125 | 5/9 | 需先迁移：删/改测试，或接回生产（生产已无调用） |
| `amo_food_cost.py:meal_instances_today` | 483 | 3/0 | 需先迁移：删/改测试，或接回生产（生产已无调用） |
| `amo_food_cost.py:commute_mode` | 948 | 2/2 | 需先迁移：删/改测试，或接回生产（生产已无调用） |
| `amo_food_cost.py:traffic_spent` | 973 | 2/2 | 需先迁移：删/改测试，或接回生产（生产已无调用） |
| `amo_food_cost.py:drink_spent` | 982 | 3/3 | 需先迁移：删/改测试，或接回生产（生产已无调用） |
| `amo_idem.py:is_known` | 74 | 2/2 | 保留（测试/观测钩子） |
| `amo_immersion_guard.py:scrub_names` | 130 | 1/9 | 需先迁移：删/改测试，或接回生产（生产已无调用） |
| `amo_json_schema.py:spec` | 121 | 1/1 | 保留（测试/观测钩子） |
| `amo_json_schema.py:current_version` | 127 | 1/1 | 保留（测试/观测钩子） |
| `amo_message.py:strip_repeated_food_question` | 170 | 12/12 | 需先迁移（旧兼容入口，生产已走新实现）；⏳task-30 |
| `amo_reply_pipeline.py:reset_tone_naturalness` | 756 | 2/5 | 保留（测试/观测钩子）；⏳task-29 |
| `amo_reply_pipeline.py:reset_tone_llm_pick_stats` | 848 | 2/4 | 保留（测试/观测钩子）；⏳task-29 |
| `amo_reply_pipeline.py:tone_llm_pick_stats` | 854 | 8/11 | 保留（测试/观测钩子）；⏳task-29 |
| `amo_reply_pipeline.py:reset_repetition_memory` | 1059 | 11/17 | 保留（测试/观测钩子）；⏳task-29 |
| `amo_reply_pipeline.py:semantic_recheck_stats` | 1227 | 4/2 | 保留（测试/观测钩子）；⏳task-29 |
| `amo_shared_time.py:weekday_name` | 216 | 1/0 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_state_store.py:registered_domains` | 154 | 1/1 | 保留（测试/观测钩子） |
| `amo_state_store.py:domain_spec` | 159 | 2/2 | 保留（测试/观测钩子） |
| `amo_transit.py:stage_plan` | 29 | 1/1 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_transit.py:stage_at` | 39 | 2/2 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_transit.py:start_transit` | 53 | 1/1 | 需先迁移（旧兼容入口，生产已走新实现） |
| `amo_tts_guard.py:reset_records` | 149 | 2/0 | 保留（测试/观测钩子） |
| `amo_tts_guard.py:recent_records` | 154 | 6/0 | 保留（测试/观测钩子） |
| `clock.py:freeze` | 173 | 30/59 | 保留（测试/观测钩子） |
| `clock.py:unfreeze` | 190 | 16/46 | 保留（测试/观测钩子） |
| `compat.py:try_attr` | 54 | 3/3 | 保留（测试/观测钩子） |
| `errors.py:code_number` | 63 | 4/4 | 保留（测试/观测钩子） |
| `errors.py:describe_code` | 77 | 4/4 | 保留（测试/观测钩子） |
| `home_refs.py:build_room_prompt` | 27 | 1/1 | 需先迁移（旧兼容入口，生产已走新实现） |
| `memory_hooks.py:render_profile_line` | 67 | 2/2 | 需先迁移（旧兼容入口，生产已走新实现） |
| `memory_hooks.py:render_memory_block` | 89 | 2/2 | 需先迁移（旧兼容入口，生产已走新实现） |
| `observability.py:fingerprint` | 102 | 3/4 | 保留（测试/观测钩子） |
| `observability.py:redaction_installed` | 591 | 1/0 | 保留（测试/观测钩子） |
| `photo_rules.py:__getattr__` | 310 | 3/1 | 保留（测试/观测钩子） |
| `photo_scene.py:has_place_hint` | 275 | 3/3 | 保留（测试/观测钩子） |
| `photo_share.py:photo_failure_pool` | 199 | 4/4 | 需先迁移（旧兼容入口，生产已走新实现）；⏳task-23/24 |
| `reply_fact_guard.py:has_violation` | 755 | 5/5 | 保留（测试/观测钩子） |
| `social_learn_engine.py:parse_zhihu` | 96 | 2/2 | 需先迁移（旧兼容入口，生产已走新实现）；⏳task-30 |
| `wander.py:follow_offer_text` | 298 | 2/2 | 需先迁移（旧兼容入口，生产已走新实现） |
| `wander.py:follow_reject_text` | 307 | 2/2 | 需先迁移（旧兼容入口，生产已走新实现） |
| `domains/finance/__init__.py:yuan_text` | 33 | 5/5 | 需先迁移（旧兼容入口，生产已走新实现） |
| `domains/finance/__init__.py:cents_text` | 42 | 3/3 | 需先迁移（旧兼容入口，生产已走新实现） |
| `infra/config/__init__.py:clear_cache` | 68 | 2/2 | 需先迁移：删/改测试，或接回生产（生产已无调用） |
| `models/schema.py:get_hp` | 19 | 3/3 | 需先迁移（旧兼容入口，生产已走新实现） |
| `models/schema.py:update_hp` | 26 | 3/3 | 需先迁移（旧兼容入口，生产已走新实现） |
| `models/schema.py:get_sick` | 32 | 3/3 | 需先迁移（旧兼容入口，生产已走新实现） |
| `models/schema.py:set_sick` | 39 | 2/2 | 需先迁移（旧兼容入口，生产已走新实现） |
| `models/schema.py:sick_log` | 56 | 2/2 | 需先迁移（旧兼容入口，生产已走新实现） |
| `services/health_engine.py:get_severity` | 46 | 5/5 | 保留（测试/观测钩子） |
| `services/health_engine.py:get_sick_duration` | 51 | 2/2 | 保留（测试/观测钩子） |
| `services/health_engine.py:is_cured` | 93 | 2/2 | 保留（测试/观测钩子） |
| `services/life_state.py:compact` | 375 | 1/0 | 需先迁移（旧兼容入口，生产已走新实现）；⏳task-27 |
| `watch/guard.py:counters` | 41 | 3/0 | 保留（测试/观测钩子） |
| `watch/guard.py:reset_counters` | 46 | 4/0 | 保留（测试/观测钩子） |
| `watch/core/amo_contract.py:methods` | 121 | 6/1 | 保留（测试/观测钩子） |
| `watch/core/errors.py:code_number` | 63 | 3/0 | 保留（测试/观测钩子） |
| `watch/core/errors.py:describe_code` | 77 | 4/0 | 保留（测试/观测钩子） |
| `watch/core/observability.py:fingerprint` | 99 | 4/1 | 保留（测试/观测钩子） |
| `watch/core/observability.py:log_event` | 159 | 8/5 | 保留（测试/观测钩子） |
| `watch/services/assets.py:breakdown` | 48 | 2/0 | 需先迁移（旧兼容入口，生产已走新实现） |
| `watch/services/property_engine.py:amortize` | 79 | 2/0 | 需先迁移（旧兼容入口，生产已走新实现） |
| `watch/services/social_rules.py:is_terminal` | 152 | 2/0 | 需先迁移（旧兼容入口，生产已走新实现） |
| `watch/utils/clock.py:freeze` | 165 | 18/0 | 保留（测试/观测钩子） |
| `watch/utils/clock.py:unfreeze` | 182 | 11/0 | 保留（测试/观测钩子） |
| `watch/utils/humanize.py:has_cents` | 76 | 3/0 | 保留（测试/观测钩子） |
| `watch/utils/mood.py:reaction_line` | 32 | 12/0 | 需先迁移（旧兼容入口，生产已走新实现） |
| `data/core/errors.py:code_number` | 63 | 8/0 | 保留（测试/观测钩子） |
| `data/core/errors.py:is_known` | 72 | 3/0 | 保留（测试/观测钩子） |
| `data/core/errors.py:describe_code` | 77 | 9/0 | 保留（测试/观测钩子） |
| `data/core/observability.py:fingerprint` | 100 | 5/1 | 保留（测试/观测钩子） |
| `data/models/db_schema.py:reset_last_error` | 251 | 2/0 | 保留（测试/观测钩子） |
| `data/services/recall_index.py:rrf` | 27 | 4/0 | 保留（测试/观测钩子） |
| `data/utils/clock.py:freeze` | 158 | 14/0 | 保留（测试/观测钩子） |
| `data/utils/clock.py:unfreeze` | 175 | 9/0 | 保留（测试/观测钩子） |

### 2.5 零引用方法（110 个，已排除 dunder/decorated/exported）

方法的“引用数”= 生产里 `self./cls./obj.` 属性 + 字符串动态调用；**跨插件 bridge 的字符串方法名已计入**。`测试/work=0` 的是强候选；非 0 的需先改测试。

| 文件:类.方法 | 定义行 | 引用数(生产/测试/work) | 判定 |
|---|---|---|---|
| `amo_api.py:ApiMixin.api_dispatch` | 639 | 0/0/0 | 安全删（三处均零引用） |
| `amo_api.py:ApiMixin.api_gift_delete` | 912 | 0/0/0 | 安全删（三处均零引用） |
| `amo_api_schedule.py:ScheduleApiMixin._display_rows` | 1120 | 0/0/0 | 安全删（三处均零引用）；⏳task-30 |
| `amo_api_schedule.py:ScheduleApiMixin._display_rows_tree` | 1125 | 0/0/0 | 安全删（三处均零引用）；⏳task-30 |
| `amo_config_model.py:ConfigModel.spec` | 187 | 0/0/0 | 安全删（三处均零引用） |
| `amo_db.py:AmoDB.relations_for` | 775 | 0/2/2 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_db.py:AmoDB.active_users` | 926 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_db.py:AmoDB.clear_diaries` | 1222 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_db.py:AmoDB.clear_diaries_from` | 1227 | 0/2/2 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_db.py:AmoDB.mark_block_diaries_stale` | 1447 | 0/0/3 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_db.py:AmoDB.block_diary_identity_mismatches` | 1498 | 0/5/5 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_events.py:EventBus.publish` | 111 | 0/4/4 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_feature_api.py:FeatureApiMixin.circle_of` | 223 | 0/3/3 | 需确认（测试/work 仍引用，删前改调用）；⏳task-27 |
| `amo_feature_api.py:FeatureApiMixin.set_relation` | 235 | 0/5/5 | 需确认（测试/work 仍引用，删前改调用）；⏳task-27 |
| `amo_feature_api.py:FeatureApiMixin.clear_date_now` | 590 | 0/0/0 | 安全删（三处均零引用）；⏳task-27 |
| `amo_feature_api.py:FeatureApiMixin.set_date_place` | 598 | 0/0/0 | 安全删（三处均零引用）；⏳task-27 |
| `amo_feature_api.py:FeatureApiMixin.mark_hunger_cooldown` | 759 | 0/0/0 | 安全删（三处均零引用）；⏳task-27 |
| `amo_human_state.py:HumanState.mood_text` | 343 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_human_state.py:HumanState._qweather_payload` | 797 | 0/6/6 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_jobs.py:JobManager.run_now` | 183 | 0/17/7 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_lifecycle.py:LifecycleMixin.initialize` | 39 | 0/3/0 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_memory_data.py:DataSyncMixin._cleanup_auto_profiles` | 761 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_memory_ledger.py:FactLedger.active_state` | 386 | 0/3/0 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_message.py:MessageMixin._handle_group_directed` | 686 | 0/3/3 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_message.py:MessageMixin._typing` | 796 | 0/4/4 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_message.py:MessageMixin._outbound_allowed` | 1031 | 0/3/1 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_ports.py:SocialFeedPort.timeline` | 66 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_ports.py:SocialPublishPort.publish` | 77 | 0/4/4 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_proactive.py:ProactiveMixin._ritual_in_window` | 1063 | 0/3/3 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_proactive.py:ProactiveMixin._recent_greeting` | 1122 | 0/4/4 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_proactive.py:ProactiveMixin._user_spoke_today` | 1135 | 0/11/11 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_proactive.py:ProactiveMixin._mark_greeting` | 1196 | 0/2/2 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_proactive.py:ProactiveMixin._ritual_pending` | 1206 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_proactive.py:ProactiveMixin._ritual_should_send` | 1210 | 0/11/11 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_prompt_registry.py:PromptRegistry.has` | 75 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用） |
| `amo_schedule.py:ScheduleMixin._diary_ready_time` | 1433 | 0/0/0 | 安全删（三处均零引用）；⏳task-30 |
| `amo_schedule.py:ScheduleMixin._pick_weather_alert_umo` | 2296 | 0/2/2 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_shared_activity.py:SharedActivityMixin._sa_current_item` | 255 | 0/0/0 | 安全删（三处均零引用） |
| `amo_state_schedule.py:ScheduleStateMixin.patch_schedule` | 1128 | 0/3/3 | 需确认（测试/work 仍引用，删前改调用）；⏳task-30 |
| `amo_state_store.py:StateStore.iter_prefix` | 284 | 0/3/3 | 需确认（测试/work 仍引用，删前改调用） |
| `clock.py:_FixedOffset.dst` | 55 | 0/2/0 | 需确认（测试/work 仍引用，删前改调用） |
| `clock.py:_FixedOffset.tzname` | 58 | 0/2/0 | 需确认（测试/work 仍引用，删前改调用） |
| `ecommerce_engine.py:EcommerceEngine.remove_from_cart` | 309 | 0/0/0 | 安全删（三处均零引用） |
| `gift_engine.py:GiftEngine.pending_wishes` | 1113 | 0/6/6 | 需确认（测试/work 仍引用，删前改调用） |
| `housing.py:Housing.all_homes` | 560 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用） |
| `housing.py:Housing.set_style` | 728 | 0/2/2 | 需确认（测试/work 仍引用，删前改调用） |
| `housing.py:Housing.room_label` | 1109 | 0/0/0 | 安全删（三处均零引用） |
| `laundry_engine.py:LifeFacts.washing_items` | 123 | 0/0/0 | 安全删（三处均零引用） |
| `laundry_engine.py:LifeFacts.clean_items` | 127 | 0/0/0 | 安全删（三处均零引用） |
| `laundry_engine.py:LifeFacts.dry_clean` | 266 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用） |
| `laundry_engine.py:LifeFacts.organize` | 296 | 0/0/0 | 安全删（三处均零引用） |
| `memory_hooks.py:ChatBuffer.sessions` | 233 | 0/0/0 | 安全删（三处均零引用） |
| `photo_plan.py:PhotoPlan.outfit_keys` | 156 | 0/2/2 | 需确认（测试/work 仍引用，删前改调用）；⏳task-23/24 |
| `photo_prompt.py:PhotoPromptMixin._clean_place` | 444 | 0/2/2 | 需确认（测试/work 仍引用，删前改调用）；⏳task-23/24 |
| `photo_prompt.py:PhotoPromptMixin._outdoor_light` | 543 | 0/0/0 | 安全删（三处均零引用）；⏳task-23/24 |
| `photo_prompt.py:PhotoPromptMixin._pick_pose` | 622 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用）；⏳task-23/24 |
| `photo_prompt.py:PhotoPromptMixin._scene_place` | 911 | 0/1/1 | 需确认（测试/work 仍引用，删前改调用）；⏳task-23/24 |
| `pose_library.py:PoseLibrary.is_private_pose` | 636 | 0/1/3 | 需确认（测试/work 仍引用，删前改调用）；⏳task-23/24 |
| `schedule_skeleton.py:ScheduleSkeletonMixin._pick_amusement_spot` | 628 | 0/0/0 | 安全删（三处均零引用） |
| `wardrobe_daily.py:WardrobeDailyMixin._is_warm` | 165 | 0/0/0 | 安全删（三处均零引用） |
| `wardrobe_queries.py:WardrobeQueriesMixin.outfit_lock_is_suspended` | 408 | 0/16/16 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/main.py:WatchPlugin.initialize` | 210 | 0/3/0 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/core/amo_bridge.py:FinanceLink.transfer_in` | 531 | 0/4/0 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/core/amo_bridge.py:FinanceLink.memory_context` | 574 | 0/1/0 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/core/amo_bridge.py:FinanceLink._balance_text` | 745 | 0/0/0 | 安全删（三处均零引用） |
| `watch/core/life_state.py:SnapshotView.shopping_line` | 123 | 0/1/0 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/core/life_state.py:SnapshotView.shopping_stale` | 126 | 0/1/0 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/core/memory_tier.py:TieredMemory.compress_long` | 111 | 0/1/0 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/core/scheduler.py:Scheduler.run_now` | 185 | 0/17/7 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/models/ledger.py:FinanceRepo.fund_quote` | 224 | 0/1/0 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/models/ledger.py:FinanceRepo.fund_trade_facts` | 240 | 0/2/0 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/services/bank_engine.py:BankEngine.auto_deposit` | 248 | 0/0/0 | 安全删（三处均零引用） |
| `watch/services/bank_engine.py:BankEngine.assets_breakdown` | 297 | 0/0/0 | 安全删（三处均零引用） |
| `watch/services/candidates.py:CandidatePools.all_codes` | 173 | 0/0/0 | 安全删（三处均零引用） |
| `watch/services/event_engine.py:EventEngine._sick` | 53 | 0/6/6 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/services/event_engine.py:EventEngine.car_accident` | 144 | 0/0/0 | 安全删（三处均零引用） |
| `watch/services/event_engine.py:EventEngine.house_disaster` | 159 | 0/0/0 | 安全删（三处均零引用） |
| `watch/services/insurance_engine.py:InsuranceEngine.expire_tick` | 94 | 0/0/0 | 安全删（三处均零引用） |
| `watch/services/medical_engine.py:MedicalEngine.get_current_health` | 127 | 0/0/0 | 安全删（三处均零引用） |
| `watch/services/trade_engine.py:Trading.apply_dividend` | 363 | 0/0/0 | 安全删（三处均零引用） |
| `watch/services/trade_engine.py:Trading.force_close` | 382 | 0/0/0 | 安全删（三处均零引用） |
| `watch/services/work_engine.py:FinanceSalary.day_rate_cents` | 71 | 0/1/0 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/utils/clock.py:_FixedOffset.dst` | 47 | 0/2/0 | 需确认（测试/work 仍引用，删前改调用） |
| `watch/utils/clock.py:_FixedOffset.tzname` | 50 | 0/2/0 | 需确认（测试/work 仍引用，删前改调用） |
| `data/main.py:AmoDataPlugin._maintenance_loop` | 495 | 0/6/0 | 需确认（测试/work 仍引用，删前改调用） |
| `data/core/api.py:MemoryAPI._backup_dir` | 472 | 0/1/0 | 需确认（测试/work 仍引用，删前改调用） |
| `data/core/api.py:MemoryAPI.person_link` | 563 | 0/3/0 | 需确认（测试/work 仍引用，删前改调用） |
| `data/core/api.py:MemoryAPI.person_unlink` | 571 | 0/4/0 | 需确认（测试/work 仍引用，删前改调用） |
| `data/core/api.py:MemoryAPI.person_merge` | 577 | 0/3/0 | 需确认（测试/work 仍引用，删前改调用） |
| `data/models/memory_db.py:MemoryDB._repair_fts_trigger` | 262 | 0/0/0 | 安全删（三处均零引用） |
| `data/models/memory_db.py:MemoryDB._table_columns` | 331 | 0/0/0 | 安全删（三处均零引用） |
| `data/models/memory_db.py:MemoryDB._migrate_columns` | 335 | 0/0/0 | 安全删（三处均零引用） |
| `data/services/backup_engine.py:BackupEngine.backup_database` | 103 | 0/6/0 | 需确认（测试/work 仍引用，删前改调用） |
| `data/services/jobs.py:JobManager.run_now` | 182 | 0/17/7 | 需确认（测试/work 仍引用，删前改调用） |
| `data/services/person_engine.py:PersonEngine.list_identity_aliases` | 486 | 0/1/0 | 需确认（测试/work 仍引用，删前改调用） |
| `data/services/person_engine.py:PersonEngine.person_invariants` | 799 | 0/3/0 | 需确认（测试/work 仍引用，删前改调用） |
| `data/utils/clock.py:_FixedOffset.dst` | 50 | 0/2/0 | 需确认（测试/work 仍引用，删前改调用） |
| `data/utils/clock.py:_FixedOffset.tzname` | 53 | 0/2/0 | 需确认（测试/work 仍引用，删前改调用） |
| `photo/main_commands.py:OmniDrawCommandMixin._plugin_principal_status` | 983 | 0/0/0 | 安全删（三处均零引用） |
| `photo/main_commands.py:OmniDrawCommandMixin._image_quota_state` | 1063 | 0/0/0 | 安全删（三处均零引用） |
| `photo/main_commands.py:OmniDrawCommandMixin._record_generated_images_locked` | 1276 | 0/0/0 | 安全删（三处均零引用） |
| `photo/main_commands.py:OmniDrawCommandMixin._extract_compact_command_payload` | 1301 | 0/5/0 | 需确认（测试/work 仍引用，删前改调用） |
| `photo/main_commands.py:OmniDrawCommandMixin._match_preset_trigger` | 1440 | 0/9/0 | 需确认（测试/work 仍引用，删前改调用） |
| `photo/models.py:PluginConfig.get_persona` | 235 | 0/0/0 | 安全删（三处均零引用） |
| `photo/core/chain_manager.py:ChainManager.run_chain` | 40 | 0/3/0 | 需确认（测试/work 仍引用，删前改调用） |
| `photo/providers/base.py:BaseProvider.encode_local_image_to_base64` | 850 | 0/0/0 | 安全删（三处均零引用） |
| `photo/providers/custom_endpoint_impl.py:CustomEndpointProvider._get_image_bytes` | 30 | 0/0/0 | 安全删（三处均零引用） |
| `photo/providers/custom_endpoint_impl.py:CustomEndpointProvider._encode_image_to_data_url` | 34 | 0/0/0 | 安全删（三处均零引用） |
| `photo/providers/gemini_official_impl.py:GeminiOfficialProvider._get_image_bytes` | 48 | 0/0/0 | 安全删（三处均零引用） |
| `photo/providers/gemini_official_impl.py:GeminiOfficialProvider._inline_image_part` | 52 | 0/0/0 | 安全删（三处均零引用） |

### 2.6 零引用常量（79 个，低优先级）

常量可能是“文档性配置”或只被文档引用；删除收益低、冲突概率高。建议**随模块清理顺手删**，不单独开批次。

| 文件:常量 | 定义行 | 引用数(生产/测试/work) | 判定 |
|---|---|---|---|
| `amo_config_model.py:NUM_TYPES` | 26 | 0/0/0 | 安全删（零引用） |
| `amo_current_action.py:PRIORITY` | 24 | 0/0/0 | 安全删（零引用） |
| `amo_diary_audit.py:MIN_INTERVAL_SECONDS` | 48 | 0/0/0 | 安全删（零引用） |
| `amo_diary_guard.py:_HOME_PLACES` | 114 | 0/0/0 | 安全删（零引用） |
| `amo_fact_timeline.py:VIEW_FIELDS` | 1030 | 0/1/1 | 需确认（测试/work 引用） |
| `amo_fact_timeline.py:VIEW_SOURCES` | 1035 | 0/0/0 | 安全删（零引用） |
| `amo_food_cost.py:MAX_MEALS_PER_DAY` | 43 | 0/0/0 | 安全删（零引用） |
| `amo_food_event.py:QUIET_START_HOUR` | 40 | 0/0/0 | 安全删（零引用） |
| `amo_food_event.py:QUIET_END_HOUR` | 41 | 0/0/0 | 安全删（零引用） |
| `amo_grounding_critic.py:_REACTION` | 22 | 0/0/0 | 安全删（零引用） |
| `amo_image_common.py:OMNI_PLUGIN_NAME` | 64 | 0/13/13 | 需确认（测试/work 引用） |
| `amo_image_common.py:OMNI_PLUGIN_NAME_LEGACY` | 76 | 0/11/11 | 需确认（测试/work 引用） |
| `amo_image_common.py:PLACE_MAX_CHARS` | 82 | 0/0/0 | 安全删（零引用） |
| `amo_immersion_guard.py:ROSTER` | 101 | 0/2/2 | 需确认（测试/work 引用） |
| `amo_persona_guard.py:_WORD` | 117 | 0/0/0 | 安全删（零引用） |
| `amo_proactive_gate.py:EVENT_KINDS` | 15 | 0/0/0 | 安全删（零引用） |
| `amo_purchase_guard.py:_PRICE` | 15 | 0/0/0 | 安全删（零引用） |
| `amo_reply_pipeline.py:TONE_POOL_CURATION_NOTE` | 737 | 0/1/1 | 需确认（测试/work 引用） |
| `amo_reply_pipeline.py:TONE_LLM_PICK_MAX_CANDIDATES` | 841 | 0/0/0 | 安全删（零引用） |
| `amo_scene_ledger.py:KNOWN_QUALITIES` | 84 | 0/0/0 | 安全删（零引用） |
| `amo_shared_activity.py:PATTERNS` | 39 | 0/0/0 | 安全删（零引用） |
| `amo_state_consistency.py:_FAKE_SHARE` | 52 | 0/0/0 | 安全删（零引用） |
| `amo_transit.py:TRANSIT_KEY` | 17 | 0/0/0 | 安全删（零引用） |
| `amo_transit.py:TRANSIT_MINUTES` | 18 | 0/0/0 | 安全删（零引用） |
| `amo_transit.py:TRANSIT_RULE` | 19 | 0/1/1 | 需确认（测试/work 引用） |
| `amo_transit.py:DESTINATION_MINUTES` | 20 | 0/0/0 | 安全删（零引用） |
| `amo_transit.py:DESTINATION_WORDS` | 21 | 0/0/0 | 安全删（零引用） |
| `amo_transit.py:INDOOR_PLACES` | 22 | 0/0/0 | 安全删（零引用） |
| `amo_transit.py:EMOTION_HINT` | 23 | 0/0/0 | 安全删（零引用） |
| `draw_engine.py:PHOTO_WORLDVIEW_NEGATIVE` | 183 | 0/0/0 | 安全删（零引用） |
| `ecommerce_engine.py:PLATFORMS` | 20 | 0/0/0 | 安全删（零引用） |
| `gift_engine.py:WISH_TTL_DEFAULT` | 79 | 0/0/0 | 安全删（零引用） |
| `gift_engine.py:_GENERIC_HOME` | 98 | 0/0/0 | 安全删（零引用） |
| `laundry_engine.py:WASH_COST` | 17 | 0/0/0 | 安全删（零引用） |
| `laundry_engine.py:DRY_CLEAN_COST` | 18 | 0/0/0 | 安全删（零引用） |
| `laundry_engine.py:BEDDING_COST` | 19 | 0/0/0 | 安全删（零引用） |
| `micro_events.py:BANDS` | 66 | 0/0/0 | 安全删（零引用） |
| `milestone.py:MILESTONE_KIND` | 16 | 0/0/0 | 安全删（零引用） |
| `photo_room.py:ROOM_REF_HINT` | 21 | 0/0/0 | 安全删（零引用） |
| `photo_rules.py:LEGACY_ZONE_ALIAS` | 162 | 0/0/0 | 安全删（零引用） |
| `photo_share.py:PHOTO_FAILURE_FALLBACK` | 149 | 0/3/3 | 需确认（测试/work 引用） |
| `pose_library.py:ROOM_LABELS` | 156 | 0/0/0 | 安全删（零引用） |
| `relation_learn.py:RELATION_CATEGORIES` | 17 | 0/0/0 | 安全删（零引用） |
| `social_learn_engine.py:ZHIHU_URL` | 73 | 0/0/0 | 安全删（零引用） |
| `wander.py:DEFAULT_CITY` | 21 | 0/0/5 | 需确认（测试/work 引用） |
| `domains/finance/__init__.py:WALLET_METHODS` | 52 | 0/2/2 | 需确认（测试/work 引用） |
| `domains/memory/__init__.py:TERMINAL_METHODS` | 39 | 0/2/2 | 需确认（测试/work 引用） |
| `domains/photo/__init__.py:PLAN_FIELDS` | 10 | 0/2/2 | 需确认（测试/work 引用） |
| `domains/photo/__init__.py:PLAN_REQUIRED_FIELDS` | 17 | 0/2/2 | 需确认（测试/work 引用） |
| `domains/photo/__init__.py:PLAN_ENTRY_POINTS` | 19 | 0/2/2 | 需确认（测试/work 引用） |
| `domains/reply/__init__.py:STAGE_FIELDS` | 10 | 0/2/2 | 需确认（测试/work 引用） |
| `domains/reply/__init__.py:ENTRY_POINTS` | 15 | 0/2/2 | 需确认（测试/work 引用） |
| `domains/schedule/__init__.py:DAY_VIEW_KEYS` | 13 | 0/2/2 | 需确认（测试/work 引用） |
| `domains/schedule/__init__.py:EVENT_REQUIRED_FIELDS` | 15 | 0/0/0 | 安全删（零引用） |
| `domains/schedule/__init__.py:CURRENT_EVENT_FUNC` | 17 | 0/0/0 | 安全删（零引用） |
| `tools/redact_prod_logs.py:REPORT_PATH` | 36 | 0/1/1 | 需确认（测试/work 引用） |
| `watch/api/decisions_ext.py:KIND_ORDER` | 36 | 0/0/0 | 安全删（零引用） |
| `watch/api/routes.py:BASE` | 4 | 0/0/0 | 安全删（零引用） |
| `watch/services/bankruptcy_engine.py:LIQUIDATE_RATIO` | 14 | 0/0/0 | 安全删（零引用） |
| `watch/services/consume_engine.py:RANGES` | 33 | 0/0/0 | 安全删（零引用） |
| `watch/services/date_engine.py:_AGREE_EXACT` | 33 | 0/0/0 | 安全删（零引用） |
| `watch/services/date_engine.py:GENERIC_YES` | 34 | 0/0/0 | 安全删（零引用） |
| `watch/services/gift_engine.py:GIFT_POOL` | 15 | 0/0/0 | 安全删（零引用） |
| `watch/services/life_engine.py:RENT_RANGE` | 31 | 0/0/0 | 安全删（零引用） |
| `watch/services/medical_engine.py:SELF_RATIO` | 32 | 0/0/0 | 安全删（零引用） |
| `watch/services/medical_engine.py:CHEAP_MEALS` | 34 | 0/0/0 | 安全删（零引用） |
| `watch/services/npc_roster.py:MALE_RULE` | 29 | 0/0/0 | 安全删（零引用） |
| `watch/services/shopping_state.py:PURCHASE_CLAIM_STATE` | 53 | 0/0/0 | 安全删（零引用） |
| `watch/services/social_engine.py:SOCIAL_KINDS` | 87 | 0/0/0 | 安全删（零引用） |
| `watch/utils/mood.py:POSITIVE` | 18 | 0/0/0 | 安全删（零引用） |
| `watch/utils/mood.py:NEGATIVE` | 19 | 0/0/0 | 安全删（零引用） |
| `data/core/memory_tooling.py:RECALL_POLICY` | 13 | 0/0/0 | 安全删（零引用） |
| `data/core/web_response.py:_UNSET` | 62 | 0/0/0 | 安全删（零引用） |
| `data/models/memory_db.py:FTS_TRIGGER_SQL` | 189 | 0/0/0 | 安全删（零引用） |
| `photo/constants.py:API_TIMEOUT_DEFAULT` | 4 | 0/0/0 | 安全删（零引用） |
| `photo/constants.py:API_TIMEOUT_SLOW` | 5 | 0/0/0 | 安全删（零引用） |
| `photo/main_commands.py:PLUGIN_PRINCIPAL_VERSION` | 88 | 0/0/0 | 安全删（零引用） |
| `photo/core/image_options.py:GIF_MODELS` | 11 | 0/0/0 | 安全删（零引用） |
| `photo/providers/base.py:IMAGE_LOG_KEY_MARKERS` | 370 | 0/0/0 | 安全删（零引用） |

## 3. 迁移后 fallback / 旧逻辑逐项判定

这是本任务的核心：**不能把“当前还能跑的回滚/降级路径”当死代码直接删**。逐项给出现状、引用数、判定和前置条件。

| # | 文件:符号（行） | 现状（为什么算旧逻辑） | 引用数（生产/测试/work） | 判定 | 前置条件 / 回归测试 |
|---|---|---|---|---|---|
| 1 | `amo_presence.py:102` `current_scene` 的旧 fallback（L128-154） | 账本读失败时回落 `plugin.state.current_activity()`——这等于重新引入第二场景源，与 SceneLedger 收口目标相反；当前生产只有 `services/life_state._location` 等问它。 | current_scene：1/3/3；fallback 分支 0 测试覆盖 | **安全删**（保留 import/异常保护，失败返回空场景；不要再读 `state.current_activity`） | 先确认 `services/life_state` 能容忍空场景；回归 `tests/test_presence_gates.py`、`test_scene_ledger.py`、`test_life_state_snapshot.py` |
| 2 | `amo_state_snapshot.py:202` `_ledger_scene` 的 `unavailable` 判据（L218-224）+ `build_snapshot` 旧读（L257-267）+ `snapshot_with_scene` 保留旧 activity（L358） | 为“老宿主/单测桩没有 db”保留的兼容：没有 db 时退回 `state.snapshot()["activity"]`。生产插件必然有 `db`，该分支只在单测桩可达；但它会把旧 activity 重新当事实。 | build_snapshot：18/11/51；snapshot_with_scene：45/4/0；`_ledger_scene` 内部 2 次 | **需先迁移**：更新无 db 的测试桩 → 删旧 activity 读分支；`snapshot_with_scene` 本体保留（task-30 兼容读） | 改 `tests/test_state_snapshot.py::BuildSnapshotTests`（`Plugin` 无 db）、`tests/test_scene_bypass.py`、`tests/test_scene_writers.py`；回归 `test_scene_bypass/test_state_snapshot/test_scene_writers` |
| 3 | `amo_state_jit.py:820` `_jit_persist` 直写 kv `current_action`（L823-830） | JIT 计划已过 `_scene_constrain_plan`，但 current_action 本身没走 `amo_current_action.set_action`（不过 StateStore TTL 逻辑/统一写入口），是 task-30 列的“剩余直写”。 | _jit_persist：1/1/3 | **需先迁移**：改调 `amo_current_action.set_action(..., source="jit")`（保留 `until/hour`），再删直写 | task-30 收口后；回归 `tests/test_current_action.py`、`test_scene_bypass.py` |
| 4 | `amo_state_schedule.py:918` `_mark_transit_action` 直写 kv `current_action`（L950-955） | 通勤块到达时直接写 current_action，绕开统一写入口/ledger 硬约束（病假日通勤仍可能被写出）。 | _mark_transit_action：2/0/6 | **需先迁移**：改调 `set_action(source="transit_block")` | task-30；回归 `test_current_action`、`test_scene_ledger`、`test_transit*` |
| 5 | `micro_events.py:656` `_set_action` 的兜底 `kv_set("current_action")`（L668-675） | 仅当 `self.p.set_current_action` 缺失/抛错时走；生产宿主始终有该方法。 | `_set_action` 生产属性引用 4；该 fallback 无独立引用 | **保留（异常降级）**；确认不再支持旧宿主后可删 | —；`tests/test_scene_bypass.py::MicroEventSceneTests` |
| 6 | `amo_feature_api.py:692` `set_current_action` 的 scene_write+vk 兜底（L713-740） | 主路径已走 `amo_current_action.set_action`；兜底只为旧宿主，且先过 ledger。 | set_current_action：4/8/7 | **保留（异常降级）**，不建议删 | —；`tests/test_feature_api_attach.py` |
| 7 | 三处 current_action 清空直写：`amo_schedule.py:893`、`amo_schedule_tools.py:122`、`amo_state_diary.py:1179` | 直接 `kv_set("current_action", {})`；而统一清空函数 `amo_current_action.clear_action` 反而**零调用**（见 2.1）。 | clear_action：0/0/0 | **需迁移（收口）**：三处改调 `clear_action()`；否则按 2.1 删 `clear_action` | 回归 `test_manual_schedule_reconcile`、`test_current_action` |
| 8 | `photo_share.py:472` `_notify_failure` | 本地 10 分钟节流已按 task-3 删除（docstring 有记录）；仍保留 `push_to_owner` 缺失时 `context.send_message` 的旧直发降级。 | 4/5/13 | **保留（旧宿主降级）**；本地节流已删，无需再动 | `test_photo_failure*`、`test_photo_share*`、`test_draw_engine.py` |
| 9 | `draw_engine.py:1091` `_notify_failure` + `:1068` `missing_omni_notice` 的本地 600s 节流；`:1122` `_send_text` 末尾 `event.send` 直发降级（L1144-1155） | 两处 `now-last<600` 本地节流与 OutboundGateway（90s 同 source / 10min 近似文本）重叠；`_send_text` 直发是 task-16 之前的旧出口。 | `_notify_failure` 4/5/13；`missing_omni_notice` 1/6/8；`_send_text` 2/0/4 | **需先迁移**：先把 draw 失败语接入 gateway kind 级抑制，再删本地节流；直发降级随旧宿主淘汰删 | task-23 收口后；回归 `tests/test_draw_engine.py`、`test_media_gateway.py` |
| 10 | `photo_share.py:149` `PHOTO_FAILURE_FALLBACK`；`:199` `photo_failure_pool` | 旧失败话术入口/选择器；统一出口已是 `amo_media.failure_line` + `PHOTO_FAILURE_LINES`。 | `PHOTO_FAILURE_FALLBACK` 0/3/3；`photo_failure_pool` 0/4/4 | **需先迁移**：测试改走 `amo_media.failure_line` 后删；`PHOTO_FAILURE_FALLBACK` 可直接删 | task-23；回归 `test_photo_failure*`、`test_photo_share*` |
| 11 | `watch/services/shopping_engine.py:286` `_push` / `:674` 直调 `amo.push_to_owner` | “直发”本身已不存在：`_push` 只是 `push_to_owner` 的薄封装，forever 内最终仍落到 `amo_outbound`。但它没走 `request_push`（少 kind/在场语义），属于**收口**而非删死代码。 | `_push` 本文件 7 处；watch 全包 `push_to_owner` 21 处（jobs/fund_actions/date_engine/bridge） | **保留（跨插件兼容 API）**；若要收口到 `request_push` 是迁移任务（task-16 遗留），不是删除 | watch `test_shopping_flow`、`test_cross_plugin`、`test_shopping_state` |
| 12 | `amo_schedule.py:2296` `_pick_weather_alert_umo` | docstring 自称“兼容旧调用”：旧单会话选择器，已被 `_weather_alert_umos()` 多平台循环取代；生产 0 调用。 | 0/2/2 | **安全删**（同步删 2 个测试调用点） | `tests/test_cov2_sched_schedule_mixin.py:545,559`；回归 `test_weather_alert.py`、`test_weather_alert_slots.py`、`test_weather_budget.py` |
| 13 | `旧天气发送`（`amo_schedule._maybe_weather_alert` vs `amo_proactive._weather_repeat_blocked`） | 未发现第二个“发送”入口：唯一发送点是 `_maybe_weather_alert` → `_send_to_umo(source="system", kind="weather")`；`amo_proactive` 里只剩话题去重（不发送）。 | `_maybe_weather_alert` 1/16/14；`_weather_repeat_blocked` 2/4/0 | **无删除项**（链路已收口）；两者都保留。若有人想删 `_weather_repeat_blocked` 会重新出现“连续两天天气话题” | `tests/test_weather_alert*.py` |
| 14 | `expand_hour 回滚`：`amo_schedule._diary_expand_callable`(L1135)/回退(L1142)；`amo_state_diary.expand_hour`(L227)/`_expand_hour_impl`(L245)/`expand_block` 回退(L389-393)；`_backfill_hours`(L279)；`_backfill_rollover` 旧整点分支(L303-329)；`_block_key`/`_save_diary_text`/`_diary_cached_text` 旧整点表分支 | 全部只在 `block_diary_enabled=false` 时可达，是“一键回滚旧整点表”的安全阀；默认 true（`_conf_schema.json:1393-1397`）。生产是否还有 false 配置需人工核实。 | `expand_hour` 1/15/26；`_expand_hour_impl` 2/0/6；`_backfill_hours` 0/5/5；`_backfill_rollover` 0/8/8；`_diary_expand_callable` 1/3/7 | **保留（回滚安全阀）**；删除需产品决策 + 数据迁移（旧整点表→block_diary）+ 删除面板开关；**不要按死代码删** | `test_block_diary.py`、`test_cov2_sched_diary_tasks.py`、`test_diary_bg_integration.py`、`test_schedule_diary_chain.py`、`test_spec_block_diary_consistency.py` |
| 15 | `7 天补写` | 旧“连续缺几天最多补 7 天”逻辑**已在 task-22 删除**：现在自动补写只允许 T+1（`DIARY_BACKFILL_WINDOW_DAYS=1`），更早缺日只写 `diary_skipped:<day>`；`_stale_daily_days(lookback=7)` 只是扫描最近 7 天 stale 标记，不是补写。 | `_diary_days_to_write` 1/4/4；`_stale_daily_days` 1/0/4 | **无删除项（已迁移）**；保留 T+1 窗口与 stale 扫描，禁止回滚多日补写 | `test_diary_p0_orphan_version.py`、`test_diary_fact_fixes.py`、`test_task22*`（若有） |
| 16 | `_ACTION_FALLBACK`（`amo_message.py:242-246`） | 数据与 `domains/reply/repetition_guard.FALLBACKS["action.shy|laugh|default"]` **逐字重复**（task-18 只迁了数据、调用点待接）；调用点 `action_only_fallback` 仍用本地 dict。 | 定义 local=3、生产 import=0、字符串=0、测试=1；`action_only_fallback` 生产 import=6/测试=2/work=14 | **需先迁移**：`action_only_fallback` 改 `registry.pick("action.*")` → 删 dict → 把 `amo_message.py` 加进 `test_fallback_registry` 的私有池守卫 | `tests/test_fallback_registry.py`、`tests/test_message_helpers.py`、`tests/test_segment_engine_rules.py` |
| 17 | `fallback 别名`：`amo_runtime_reply.py` 三处硬编码 `or "…"`（L425/L435/L466） | 同一句已在 `FALLBACKS["align.*"]` 注册；`_fallback_text()` 正常返回非空，`or` 默认串实际不可达，属重复兜底。 | 字面量 0 引用（registry 有数据） | **安全删**（只删 `or "…"`，调用仍走 `_fallback_text`） | `tests/test_arrival_guard.py`、`test_scene_guards.py`、`test_post_rewrite_recheck.py` |
| 18 | `fallback 别名`：`MODEL_LEAK_DEFLECTIONS` / `SHORT_FALLBACKS` / `EMOTION_FALLBACKS` / `_GENERIC_REPLIES` | 都是 `FALLBACKS` 同一对象的别名（不是第二份数据），测试用 `assertIs` 钉死单一数据源。 | 各 1+ 生产引用 | **保留**（删了会破坏回滚兼容与测试约束）；真正的重复是第 16、17 项 | `test_fallback_registry.py` |
| 19 | 旧名 re-export 门面：`amo_repetition_guard.py`、`brushoff.py`、`day_types.py`、`diary_engine.py`、`diary_lines.py`、`amo_place.py`、`amo_numbers.py`、`amo_schedule_health.py`、`schedule_*_segments.py`、`photo_view.py`、`wardrobe.py` 等 | Phase 11 的“旧导入路径保留”，模块本身 def=0、只 re-export；生产仍有 import（不是死模块）。 | 各模块生产 import ≥1（见 `dead_code_scan.json` modules） | **保留**（删会破坏旧导入/测试/外部扩展）；不要因“看起来只是别名”而删 | 各 `test_*_split.py`、`test_fallback_registry.py` |

### 3.1 结论速记

- **可以现在就删的旧逻辑**：#1（presence 旧 fallback）、#12（旧天气单会话选择器）、#17（align 重复默认串）、#10 的 `PHOTO_FAILURE_FALLBACK`、§2 全零符号。
- **必须先迁移、不能直接删**：#2（snapshot 旧 activity 读）、#3/#4/#7（current_action 直写/清空）、#9/#10（draw/photo 旧失败通知节流）、#16（_ACTION_FALLBACK）。
- **明确保留、禁止当死代码删**：#5/#6（降级）、#11（watch 推送 API）、#13（天气链路）、#14（expand_hour 回滚）、#15（7 天补写已删，勿回滚）、#18/#19（fallback 别名/re-export 门面），以及 §0.1 全部白名单。

## 4. 重复测试 / 过期快照

### 4.1 测试重复度（`tests/` 490 个 py 文件）

| 检查 | 结果 | 判定 |
|---|---|---|
| 整文件内容完全相同的测试 | **0 组** | 无整文件复制 |
| 同名测试函数跨文件（42 组） | 抽检全部是不同类/不同场景的通用名（`test_empty`、`test_limit`…），非复制 | 不动 |
| 同一文件内重复 `def test_*`（会被后者覆盖） | **0 处**（5 个文件同名函数都在不同 `class` 里） | 不动 |
| 0 test 函数的测试文件 | 10 个（`conftest.py`、`astrbot_stub.py`、`fixtures.py`、`support/fakes.py` 等） | 是夹具/桩，不是测试，保留 |
| **重复测试树** | `work/audit/_scratch/repo_pin/tests/` 是整棵 tests 的旧副本：467 个 test 文件，其中 **447 个与当前逐字节相同**、19 个不同、1 个已删（`test_part_dedupe.py`） | **过期快照，删**（见 4.2） |

> 结论：仓库内**没有需要删的重复测试**；真正的“重复测试”是 repo_pin 整棵测试副本。不要为了“去重”删当前 tests/ 里的文件。

### 4.2 过期快照 / 历史基线（可清理，非生产代码）

| 路径 | 体量 | 状态（实测） | 判定 / 前置 |
|---|---|---|---|
| `work/audit/_scratch/repo_pin/` | 1968 文件 / 35.95 MB | 整仓旧快照：782 个 py 中 699 与当前相同、78 已改、5 个当前已不存在（`amo_part_dedupe.py`、`tests/test_part_dedupe.py` 等）；含重复 tests 树 | **保留到 task-29 收口**（`work/task29/pin_grounding_before.py`、`make_diff.py` 仍引用），之后整目录删 |
| `work/audit/_scratch/mem_persona/pin_20261122-104956/` | 249 个 py | 193 同 / 56 已改；task-11 审计临时 pin | 审计报告已定稿 → **删** |
| `work/audit/_snap_20261122/` | 12 文件 / 0.56 MB | 6 同 / 6 已改（amo_schedule、amo_state_diary 等修复前副本） | **删**（报告/审计已定稿） |
| `work/fix-money-food/before/` | 7 文件 / 0.59 MB | 7/7 已改（task-21 修复前基线） | 报告已含 diff → 可删（低优先） |
| `tests/golden/prompts_golden.json.bak-20261118`、`tests/golden_split.json.bak-20261120`、`tests/data/photo_plan_ab_baseline*.json.bak-task23`（2 个） | 96/33/37/37 KB | 旧 golden/基线备份，无引用 | **删**（当前 golden/基线才是准） |
| `tests/contracts/Astrbot_amoupup_watch_surface.json` | 3 B，内容 `{}` | **0 个代码/测试引用**（`watch_surface` grep=0）；task-27 要重建跨插件 surface | **删**（过期契约占位） |
| `work/sim/remind_report_prev*.json`、`remind_report_before_roadfix.json` | 7 文件 / ~2.13 MB | 旧提醒仿真报告，当前只认 `remind_report.json/md` | **删**旧报告 |
| `pytest_after_fix.log`、`pytest_full_after.log`、`tmpgate.log` | 38/2/0.2 KB | 历史日志 | **删**；`pytest_task30_full.log`（11:49 生成）**保留到 task-30 收口** |
| `__pycache__/`、`.pytest_cache/`、`.mypy_cache/`（含 `work/__pycache__`） | 根 218 文件 5.0 MB + work 32 文件 0.5 MB | 可再生缓存 | **删**（任何时刻，先停 pytest 即可） |
| `work/amo_forever.db.bak-cleanfake-20261120-232833` | 1.0 MB | 假日期清理前 DB 备份 | **不要删**；建议移到 `backup/` 保留回滚 |
| `work/audit/_scratch/` 其余（real_audit.json/md、dump_*.json、photo_log_extract.txt 2.8 MB 等） | — | task-7/8/11/13 等审计过程产物 | 报告已定稿可整目录清；**repo_pin 子目录除外**（等 task-29） |

## 5. 删除顺序（防止后续打架）

原则：**先无行为变化的删除，再“迁移后删除”，回滚/降级路径最后且需产品决策**；同一文件只在一个批次动，避免与并行任务冲突。

| 批次 | 内容 | 前置条件 | 回归测试 |
|---|---|---|---|
| **P0 产物清理（零代码风险）** | §4.2 的缓存/日志/`.bak-*`/空契约 `{}`/旧 sim 报告；`pytest_task30_full.log` 与 `repo_pin` **除外** | 无 | 不需要（不碰代码）；跑一次 `pytest -q` 确认环境仍装得上即可 |
| **P1 死模块（无行为）** | 删 `amo_idem.py`、`compat.py`、`models/database.py`、`models/schema.py`、`domains/photo/room_ab.py`；`amo_food_audit.py` 移到 `work/` 并改 work 调用；`domains/memory/` 与 `amo_ports.py` 暂留 | 删对应测试（`test_idem.py`、`test_models_schema.py`、`test_engineering_seams.py` 里 amo_ports 部分按保留处理） | `pytest -q`（全量）+ `pytest tests/test_domain_boundaries.py` |
| **P2 全零符号（无行为）** | §2.1 的 39 个函数、§2.2 的 3 个 work-only、§2.3 的 4 个类、§2.5 中 **测试/work=0** 的方法、§2.6 中零引用常量；**同一文件先攒后一次删** | 该文件不在 task-23/24/27/29/30 写范围（见 §5.1） | 该模块对应 `tests/test_<module>*.py` + 全量 `pytest -q` |
| **P3 只测试引用的旧兼容入口** | §2.4 中标“需先迁移/旧兼容入口”的项（`amo_fact_pool.chat_facts/activity_facts/_routine_facts/_filter_window_facts`、`amo_transit.stage_plan/stage_at/start_transit`、`domains.finance.yuan_text/cents_text`、`models.schema.*`、`amo_compat.*`、`photo_share.photo_failure_pool`、`amo_schedule._pick_weather_alert_umo` 等）+ 同步删/改测试 | P2 完成；逐个确认没有外部扩展 import | 对应 `test_fact_pool/test_transit/test_finance/test_compat`、`test_cov2_sched_schedule_mixin` |
| **P4 fallback 数据去重（要改调用点）** | #16 `_ACTION_FALLBACK` → registry；#17 align `or` 默认串；#10 `PHOTO_FAILURE_FALLBACK`（若未在 P3 删） | 先跑 `test_fallback_registry.py` 确认注册表数据一致；**改 `amo_message.action_only_fallback`，不是直接删 dict** | `test_fallback_registry.py`、`test_message_helpers.py`、`test_segment_engine_rules.py`、`test_arrival_guard.py`、`test_scene_guards.py` |
| **P5 场景兼容读删旧读（需改测试桩）** | #1 presence 旧 fallback；#2 `_ledger_scene`/`build_snapshot` 的 `unavailable` 旧 activity 读 | 先更新无 db 的测试桩（`test_state_snapshot.py::BuildSnapshotTests` 等）；**改完先跑场景测试再删代码** | `test_presence_gates`、`test_scene_ledger`、`test_scene_bypass`、`test_state_snapshot`、`test_scene_writers` |
| **P6 current_action 直写收口** | #3 JIT、#4 通勤、#7 清空点 → 统一 `amo_current_action.set_action/clear_action`；删 fallback kv 直写 | **task-30 收口后**；确认所有宿主都有 `set_current_action` | `test_current_action`、`test_scene_bypass`、`test_manual_schedule_reconcile`、`test_scene_ledger` |
| **P7 旧失败通知节流** | #9 draw/photo 本地 600s 节流、`_send_text` 直发降级；#8 photo direct-send 降级 | **task-23 收口后**；先给 gateway 增加 draw/photo failure 的 kind 级抑制，再删本地节流 | `test_draw_engine`、`test_media_gateway`、`test_photo_failure*`、`test_photo_share*` |
| **P8 回滚安全阀（产品决策，暂不删）** | #14 `expand_hour` 整条回滚路径 + 旧整点表分支 | 需核实所有部署 `block_diary_enabled=true`、旧整点表数据已迁移、面板开关下架；**当前阶段不删** | `test_block_diary`、`test_cov2_sched_diary_tasks`、`test_diary_bg_integration`、`test_schedule_diary_chain`、`test_spec_block_diary_consistency` |
| **P9 过期快照清理** | `repo_pin`、`mem_persona/pin_*`、`_snap_20261122`、`fix-money-food/before`、`work/audit/_scratch` 其余 | **task-23/24/27/29/30 全部收口**（repo_pin 被 task-29 引用）；确认审计报告都已定稿 | 无（不碰生产代码） |

### 5.1 并行任务冲突矩阵（本审计时点）

| 并行任务 | 写范围（与本清单重叠的文件） | 对本清单的影响 |
|---|---|---|
| task-23 fix-media-gateway | `photo_share.py`、`draw_engine.py`、`amo_api_photo.py`、`amo_commands.py`、`segment_engine.py`、`amo_media.py`、`pose_library.py`、`photo_engine/prompt/plan/generate.py` | 不要现在删这些文件里的死符号；P7 依赖它 |
| task-24 fix-shopping-voice | watch `services/shopping_engine.py`、`shopping_state.py`、`wardrobe*`、`work_events.py` | watch 死符号删除推迟；#11 保留 |
| task-27 fix-metrics | `amo_feature_api.py`、`services/life_state.py`、面板/指标相关 | #6 保留；`services/life_state.py` 不是死代码 |
| task-29 fix-post-critic | `amo_reply_pipeline.py`、`amo_grounding_critic.py`、`amo_state_consistency.py`、`amo_question_guard.py` | 这些文件的死符号（如 `amo_grounding_critic.fallback_bodies`）等它收口；repo_pin 依赖它 |
| task-30 fix-scene-bypass | `amo_message.py`、`amo_proactive.py`、`amo_schedule.py`、`amo_runtime_context.py`、`amo_runtime_reply.py`、`micro_events.py`、`social_learn_engine.py`、`amo_api_schedule.py`、`amo_state_jit.py`、`amo_state_schedule.py` | #1/#2/#3/#4/#7/#16 都依赖它；**不要与它同时改同一文件** |

## 6. 回归测试清单（按批次）

全量（每批合并前至少一次）：
```powershell
cd D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever
& D:\dafeiyu\uv\tools\astrbot\Scripts\python.exe -m pytest -q
# 历史全量约 5797 passed / 7 skipped / 113 subtests，约 11 分钟（见 pytest_after_fix.log）
```

按批次的定点回归：

| 批次 | 命令 |
|---|---|
| P1 死模块 | `pytest tests/test_domain_boundaries.py tests/test_engineering_seams.py tests/test_idem.py -q`（删测试后调整文件列表） |
| P2/P3 死符号 | `pytest tests/test_current_action.py tests/test_weather_alert.py tests/test_weather_alert_slots.py tests/test_weather_budget.py tests/test_cov2_sched_schedule_mixin.py tests/test_message_helpers.py tests/test_arrival_guard.py -q` |
| P4 fallback 去重 | `pytest tests/test_fallback_registry.py tests/test_message_helpers.py tests/test_segment_engine_rules.py tests/test_arrival_guard.py tests/test_scene_guards.py tests/test_post_rewrite_recheck.py -q` |
| P5 场景旧读 | `pytest tests/test_presence_gates.py tests/test_presence_policy.py tests/test_scene_ledger.py tests/test_scene_bypass.py tests/test_state_snapshot.py tests/test_scene_writers.py -q` |
| P6 current_action 收口 | `pytest tests/test_current_action.py tests/test_scene_bypass.py tests/test_manual_schedule_reconcile.py tests/test_scene_ledger.py tests/test_cov2_sched_state_schedule.py -q` |
| P7 失败通知 | `pytest tests/test_draw_engine.py tests/test_media_gateway.py tests/test_photo_failure.py tests/test_photo_share.py -q`（文件名以仓库实际为准） |
| P8 回滚路径（只在不删时跑） | `pytest tests/test_block_diary.py tests/test_cov2_sched_diary_tasks.py tests/test_diary_bg_integration.py tests/test_schedule_diary_chain.py tests/test_spec_block_diary_consistency.py -q` |
| 跨插件契约 | `pytest tests/test_cross_plugin_contract.py tests/test_cross_plugin_state_contract.py tests/test_data_plugin_disabled.py -q`（后两个若已存在） |

## 7. 附录：扫描方法与可复现命令

- 扫描器：`work/audit/dead_code_scan.py`（纯 AST，不 import 目标代码；输出 `dead_code_scan.json` / `dead_code_lists.json`）。
- 复现：
```powershell
& D:\dafeiyu\uv\tools\astrbot\Scripts\python.exe -B work\audit\dead_code_scan.py work\audit\dead_code_scan.json
```
- 扫描器口径：解析 4 插件生产 `.py`（排除 `work/`、`tests/`、`__pycache__`、缓存/资源目录），收集顶层 def/class/方法/大写常量；引用来自 import、`Name` 本地引用、模块属性、`self./cls.`、`getattr/hasattr/setattr` 字符串、`globals()[...]`、字符串字面量；测试/work 单独标记。
- 已知局限：
  1. 运行时拼出来的方法名（如 `getattr(obj, "_" + name)`）无法静态解析——本报告已排除所有出现字符串/动态引用的符号，但仍建议删除前对强候选再 `grep 符号名` 一遍；
  2. watch 的 `_load(pkg, "module", "attr")` 动态加载已按字符串匹配计入（watch 各 service 模块因此不被误判为死模块）；
  3. AstrBot 装饰器 handler 已按 `decorated` 过滤；`main.py`/`__init__.py` 入口未判死；
  4. 审计期间工作树并发变化，**行号以符号名为准**；跑删除前用 `dead_code_scan.py` 重跑一次对比本清单。

### 7.1 本清单“可直接动手”的最短路径

1. P0 产物清理（不碰代码）→ 立即执行。
2. 等 task-29 收口后删 `work/audit/_scratch/repo_pin`（35.95 MB，含 447 个重复测试）。
3. 等 task-23/24/27/29/30 全部收口后：P1 → P2 → P3 → P4 → P5 → P6 → P7；每批全量 pytest。
4. P8（expand_hour 回滚）与 P9（其余快照）最后做，且 P8 需产品确认。
