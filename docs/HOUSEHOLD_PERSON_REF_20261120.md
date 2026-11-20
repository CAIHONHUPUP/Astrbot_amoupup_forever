# 居住关系 + 人物指代（2026-11-20，本地 7.5.9）

## 1. 解决了什么

1. **同居可切换**：新增 `amo_household.py`，三档行为 `apart / part_time / together`；
   日程、小日记、大日记、聊天上下文、约会收尾、世界观都从**同一个 `scene_at(day,start,end,place)`** 取“他在不在”。
2. **消灭裸「他」**：新增 `amo_person_ref.py`，每段第一次出现、没有先行词的「他」补成「老公」；
   `其他/他们/他人/他乡/他日/他杀/他律/他妈的/吉他` 全部保护；幂等。
3. **改源头**：小日记聊天素材标签 `他：` → `老公：`；日记提示词统一“用户是老公，第一次必须写老公”。
4. **改人格**：AstrBot「大肥鱼」人格里硬编码的“同城不同住 / 各回各家”改成“以【居住】为准”，
   并新增【你们怎么住】段与优先级 ⑤。

## 2. 怎么用

| 操作 | 命令 | 说明 |
|---|---|---|
| 看状态 | `/居住` | 当前档位、他是否在家、过夜进度 |
| 正式同居 | `/同居` → `/同居 确认` | 管理员；任意档直接到 together |
| 回到分居 | `/搬回` → `/搬回 确认` | 管理员；回到 apart |
| 半同居 | 配置 `household_stage=part_time` 后重启 | 或后续接自然演进 |

默认 `apart`，行为与 7.5.9 逐字节一致。配置项：

- `household_enabled`（默认 true）
- `household_stage`（apart/part_time/together）
- `household_to_together_nights`（默认 30）
- `person_ref_enabled`（默认 true）

## 3. 改动文件

新增：

- `amo_household.py` — Scene / 状态 / 迁移 / 文案 override / 过夜计数
- `amo_person_ref.py` — 指代归一
- `tests/test_household.py`、`tests/test_person_ref.py`
- `work/persona_household_20261120.py`（人格补丁，已执行）
- `work/person_ref/backfill_diaries.py`（历史日记清洗，已执行）

接入：

- `amo_state_schedule.py` — 主语校验按块时间 + 指代归一
- `amo_diary_guard.py` — `fact_rules(scene)`、`_RULES_HOME`、`partner_home` 进 `_diary_contradictions`
- `schedule_diary.py` — scene 传入小日记提示词、`_sanitize_hour_diary(partner_home)`、出口归一
- `schedule_plan.py` — `diary_presence_rules(scene)` 同居分支
- `amo_fact_pool.py` / `amo_fact_timeline.py` — meta `partner_home`、同居事实块
- `amo_state_diary.py` — 聊天标签 `老公：`、落库前归一
- `diary_engine.py` — 大日记身份规则
- `amo_memory_context.py` — 记忆标签 `老公：`
- `amo_llm_service.py` — 世界观按档位
- `amo_llm.py` — 聊天边界块按档位（apart 回落原文）
- `amo_runtime_context.py` — 私聊注入【居住】
- `amo_schedule.py` — 大日记住法规则
- `amo_commands.py` — `/居住`、`/同居`、`/搬回`
- `amo_date_facts.py` / `amo_date_context.py` — 约会收尾按档位
- `_conf_schema.json` — 4 个配置键
- `tools/quality_gate.py` — 全量测试改走 pytest（unittest discover 不加载 conftest，时钟不冻结会假红）

## 4. 验收方案（P0/P1/P2）

### P0 阻断/回归

1. 备份与回滚点：
   - 插件：`D:\dafeiyu\backup\household-perfect-20261120-111121`
   - 人格：`D:\dafeiyu\astrbot\data\data_v4.db.bak-persona-20261120`
   - 日记清洗：`amo_forever.db.bak-personref-20261120-112749` + `diary-bak-personref-*`
2. 全量 pytest：`python -m pytest tests -q` → **5369 passed / 7 skipped / 113 subtests passed**（18:45）。
3. 门禁：`python tools/quality_gate.py` → 静态项已过（routes 105 / pyflakes 190 文件 clean /
   silent except 0/58 / mypy 0 errors / 233 文件）；测试项即上面同一套 pytest。
4. L0 零漂移：`pytest tests/test_prompt_registry.py tests/test_dynamic_context_snapshot.py`；
   apart 档提示词与 7.5.9 一致（新增【居住】行是唯一新增，属于预期）。
5. 回滚：`/搬回 确认`；或配置 `household_enabled=false`；或恢复备份。

### P1 正确性

1. 场景矩阵：`pytest tests/test_household.py`（apart/part_time/together × 工作日/周末/夜里/外面/约会）。
2. 时间作用域：测试 `test_schedule_guard_uses_block_time`（23:00 生成明天 10:00 不允许“和老公”）。
3. 同居不被误杀：`test_presence_claim_allowed_at_home`、`test_claims_today_interaction_allows_home_daily`。
4. 指代：`pytest tests/test_person_ref.py`（首现、幂等、其他/他们/他妈妈/他的公司）。
5. 反向验证：
   - 去掉 `scene.partner` → L2 用例红；
   - 去掉 `partner_home` → 同居在家断言被删；
   - 去掉标签修复 → 首现用例红。
6. 历史清洗：`python work/person_ref/backfill_diaries.py --dry` 应为 0 处需改（已清洗完）。

### P2 性能/可读性

- `scene_at` 纯函数、无模型调用；日程一天最多 24 次 kv 读，可接受。
- 所有 override 在 apart 返回空串，不复制旧文案，避免双份维护。
- `amo_household.py` < 120 行/函数无裸 `except: pass`（quality gate `silent except 0/58`）。

## 5. 上线后人工验收

1. 重启 AstrBot core（人格/插件代码生效），或热重载插件。
2. `/居住` → 应显示「同城分居 / 他不在 / 0/30」。
3. `/同居` → `/同居 确认` → 再 `/居住` 应显示「同居 / 他在家」。
4. 私聊发「你在干嘛」：她应能说“在家”，且不再出现裸「他」。
5. 等一块小日记生成（或 `/schedule` 触发），检查正文第一次出现的是「老公」不是「他」。
6. `/搬回 确认` → 恢复分居，确认行为回到原样。

## 6. 追加：对话质量修复（2026-11-20 下午）

针对线上四张截图：

1. **动作-only**：`amo_message.is_action_only()/action_only_fallback()`，整条只有括号动作时补一句；
   `segment_engine._merge_action_segments()` 在分段级把动作并进相邻文字段，全段动作才补一句；
   `strip_repeated_urge()` 删完催促句后如果只剩动作，也会补一句。
2. **语境/人设**：`llm.catch_answer` 增加“先接住内容再评价、别先哈哈哈再否定、别说‘你们男人’、
   追问时沿用上一条的名字”；`llm.rule.core` 增加“括号动作不能单独成条”。
3. **编人名**：`amo_names.strip_unbacked_names()`——素材里没出现的名单人名（如“柳如烟”）换成“同事”；
   `ROLE_LINE` 固定人物关系（季博达=男同事，只限工作、不单独约会）。
4. **主动消息复读/两件事**：`proactive_same_topic()` 主题词去重（笑话/咖啡/午饭…）；
   `strip_mixed_checkin()` 删掉“分享+查岗”的查岗句；记忆块标注“这是过去的事，别当新鲜话题”。
5. **追问**：`_followup_text_live()` 优先让模型按人设现写一句，失败回落模板；模板删掉“醒醒，在等你呢”；
   追问下限 15 分钟、默认 20 分钟（原 5 分钟）。
6. **里程碑**：prompt 只收重要事件，日常玩笑输出 `[SKIP]`；`amo_runtime_tick` 识别 `[SKIP]` 不落库。
7. **工具失败**：拍照提示词增加“工具报错/超时就说‘这张没拍好，等我重拍’”。

## 7. 已知边界

- AstrBot 人格在 DB 里，插件热重载不会重读人格；需要重启 core 或等 `LLMService.persona` 300s 缓存过期。
- 历史日记已按“首现归一”清洗，只改人称，不改语义；备份可回滚。
- `part_time` 的周末窗口固定为周五 20:00–周日 22:00；如需可配置后续再加。
- 本次未上传 GitHub、未升版本号（按“说上传才上传”）。
