# 人设自进化 A 阶段施工报告（2026-11-24）

> 范围：docs/persona_roadmap.md 第 0 批 T0–T4 + A 阶段 A1–A6。
> 不做：B 阶段（等 3 天采集观察 + 明确说开）、C 阶段、上传/发版/改版本号。
> 版本保持 7.6.5，未上传、未改 metadata/CHANGELOG。

## 备份与回滚

| 用途 | 路径 |
|---|---|
| T0 测试 + 将改插件文件 | D:\dafeiyu\backup\persona-tests-20261124-1130\（tests 58 文件 + plugin_files） |
| A 阶段改动前 | D:\dafeiyu\backup\persona-a-20261124-1135\（13 个插件/文档文件 + work/persona_ui 两个脚本，共 15 个） |
| 日志 | 同目录 baseline-411.log、probe_a1.log、ui_test.log、reverse-*.log、rollback_drill.log |

回滚：把 persona-a-20261124-1135 里的文件按同名覆盖回插件目录；测试回滚用
persona-tests-20261124-1130\tests 覆盖 tests/。改前均先备份，未动 card 内核/称呼/禁语。

## P0（阻断/回归）

- T0：备份存在；上面的回滚路径可一键拷回。
- T1–T3 验收：
  - pytest tests/test_wardrobe.py -q → 81 passed
  - pytest tests/test_photo_data_split.py -q → 12 passed（golden sha256 与备份一致）
  - pytest tests/test_schedule_engine_split.py -q → 13 passed（方案 B：删 2 项源码 sha256 哈希锁；golden_split.json 只留 behavior）
  - 三项合并：106 passed
- T4：python -m pytest tests -q（不带 --ignore）→ 411 passed / 44 subtests / 0 collection error。
  命令固化到 tools/pytest_baseline.py（--expect 411，输出 BASELINE OK）；CI .github/workflows/test.yml 本来就跑同一命令。
- 默认行为零变化：persona_auto_evolve=false、persona_auto_approve=false（schema 默认）；A 阶段采集不打开，B 的自动通过更不会写。
- 真实链路：插件热重载 200 成功，Astrbot_amoupup_forever 7.6.5 activated；最近 300 行日志 0 ERROR。
  线上 /api/plug/Astrbot_amoupup_forever/persona/status 返回新字段（auto_evolve=false、pool_count=0、recent_auto=[]），旧字段 candidates 仍是纯文本数组。

## P1（正确性/一致性）

### A1 检索

- amo_persona_pack.retrieve：
  - scene in ("", general, manual) +1 基础分（精确场景 +2、mood +1、无关 0）；
  - 通用/进化语料最多 1 槽，固定第 3 条；只有整包没有任何非通用语料时才放宽；
  - 补位按分数排；last_used 让刚抽过的句子排后。
- 4 场景 probe（真实默认包 + 2 条 manual/general）：home_bed/quarrel/sick/office 都是
  「2 条场景语料 + 第 3 条通用」，通用条数 = 1。见 backup/persona-a-20261124-1135/probe_a1.log。
- 写入真实 scene：add_candidate/采集都写当天 zone（如 home_bed），不再固定 manual。
- 反向验证：去掉 score += 1 → test_a1_generic_base_score_is_one 红（reverse-a1-plus1.log）。

### A2 采集

- 聊天回复在 amo_runtime_reply.on_decorating_result 真正交给发送链后写 state.pending_review
  （直发 / 分段引擎 / 交回核心三条路径都接）；主动/推送仍走 _send_to_umo，但 source!=chat 不记。
  只收私聊 + 纯文本；群聊/主动/系统/照片语音/发送失败都不写。
- 下一轮 _compose_reply（仅私聊）先判反应：正向/负向/中性/超时，正向才进 A3。
- 测试：test_a2_only_private_chat_success_is_pending、test_a2_positive_review_adds_to_pool_negative_does_not。

### A3 过滤器

- 6–40 汉字、禁语（card.forbidden）、AI/技术词、括号动作/模板、相似度 ≥0.8 拒绝
  （对比她的池/固定 33 条/他最近 10 条）、极端情绪（mood 或 mood_score ≤20）不学、每天 ≤3 条。
- 反向验证：禁用 _too_similar → test_a3_similar_to_fixed_or_his_words 红（reverse-a3-similar.log）。

### A4 注入

- learned_style_block：优先她自己的池（当前意图最高分 2 条），池空回落固定 33 条；
  prefix「你以前这样说过（只学语气，不要逐字重复）」；总增量 ≤120 字。
- 反向验证：把固定池回落改成 [] → test_a4_empty_pool_falls_back_to_fixed_33 红（reverse-a4-fallback.log）。

### A5 池上限/衰减/快照

- 每意图 ≤6、总 ≤30；抽中 +1 分；30 天未用按天 ×0.98；<0.5 进 style_archive；
  写前 snapshot("before_style_pool")；撤销前 snapshot("before_undo_auto")。
- 反向验证：DECAY_RATE=1.0 → test_a5_decay_and_archive 红（reverse-a5-decay.log）。
- 回滚演练：池 1 条 → 入池 2 条 → 恢复快照回 1 条，耗时 0.025s（ROLLBACK_DRILL_OK）。

### A6 面板

- 候选行来源标签「自动 · / 手动 ·」；名片「自动进化 开/关 + 今日 +N」+ 暂停按钮；
  「最近自动进化」列表（场景 + 句子 + 撤销）；预览标注「她的池 N 条 / 固定池兜底」。
- work/persona_ui/ui_test.py → PERSONA_UI_OK（31 项，原 21 项只增不减）。
- 接口只增字段：candidate_rows/sample_sources/learned/auto_evolve/auto_approve/today_added/
  pool_count/archive_count/pending_review/recent_auto，旧 candidates 不变。

### 离线 A/B（检索层代理指标，不是模型输出 A/B）

work/persona_ab/run_ab.py，45 轮/臂，结果在 work/persona_ab/ab_metrics.json：

| 指标 | 固定 33 条 | 她自己的池 | 变化 |
|---|---|---|---|
| 复读率（开头第一句相同） | 81.82% | 0% | −81.82%（未升 >20%，不回退） |
| 平均句长 | 15.0 | 9.24 | −5.76 |
| 负向反应率 | 0% | 0% | 0 |
| prompt 增量 | — | — | −11.51 字（≤120） |

## P2（性能/冗余/可读性）

- 判定全部走规则，没有新增每轮 LLM 调用。
- A4 注入最多 2 条 ×40 字 + prefix，实测最长 52 字（A/B），远低于 120 字预算。
- last_used 只对「进化语料 / 她自己的池」落盘；纯基础包语料不再每条私聊写 state.json
  （写盘从「每条私聊 1 次」降为 0 次；有池内句子时才 1 次）。
- mark_used 用递增 used_seq 轮换，避免同一秒内多轮高分句霸屏；style_archive 上限 100。
- 顺手清掉 4 处 pyflakes 噪音（amo_bootstrap 未定义导出、amo_llm 未用局部变量、
  amo_runtime_context/amo_runtime_context_extra 未用变量），check_pyflakes 通过。

## 未做 / 门禁状态

- B 阶段未开始：等 3 天采集观察；A 阶段只入池，不写 candidates。
  要开始观察，在面板打开「自动进化」或配置 persona_auto_evolve=true；
  persona_auto_approve 保持 false，等你明确说开再动。
- quality_gate.py --fast：schema 246 / compile / routes 114 / dashboard JS / pyflakes 均过；
  停在历史遗留的静默 except 预算（amo_fileio/amo_shared_config/amo_transition/wardrobe_queries，
  不是本轮新增；新模块 0 条裸 except: pass）。
- 未上传、未发版、未改版本号（仍 7.6.5）。
