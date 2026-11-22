# 隔离日程日记仿真报告（3/5/7 天）

- 生成时间：2026-11-22T16:24:59
- 仿真脚本：work/sim/diary_sim.py；核对器：work/sim/diary_checks.py
- 仓库：Astrbot_amoupup_forever（只读测试，未改生产代码/未写生产库）
- 日程来源：live_engine（生产 ScheduleEngine 正常生成）
- 本轮代码指纹：schedule_skeleton.py=a84abcd257d30be0；schedule_plan.py=8f0573608c8a8309；schedule_engine.py=1adf3cdd2ff480a3；amo_schedule.py=f5d9eb3c9be98ec2；amo_fact_timeline.py=dbc9abdae5a537bd
- 生成层：real / deepseek-chat（真实模型（deepseek-flash）生成；提示词/事实池/守卫/落库都是生产代码）

> 限制：只跑生产日程生成器（work/simulate_weeks.build_day_skeleton）+ 生产大日记链路；没有跑逐小时 JIT/过渡/块小日记调度（块日记表为空 -> chat_lines/hourly_lines 都是空，等价于「数据插件没接」的生产分支，这会让提示词多一句「非常平淡」，已在根因里单列）；固定 7 天序列（2026-11-01..07，golden 同款种子），3/5 天窗口是它的前 3/5 天累计，不是三次独立重跑；聊天/账本为空：没有真实对话与消费，只验证日程/健康/天气/同伴维度；健康/约会为注入测试条件：健康在当天 20:00 用生产 set_health 写入，再由生产 read_health 落 health_day:<day> 快照（第二天补写时按天读回，不是读生成时刻），约会用生产 begin/finish + 一个 date 日程块；除这两项外日程块全部来自生产生成器；地点判定用独立词表（work/sim/diary_checks.py），可能有个别口语漏判/误判；报告里每条失败都带原文与事实证据，可人工复核；生成层 temperature=0.8 单次采样：同一 harness 连跑多次，分类命中会波动（本次保留最后一次采样）；确定性结论（digest 死链路、提示词日期口径、人名兜底误伤、健康事实取生成时刻、天气无素材）另有代码级证据，不依赖采样

## 1. 结论摘要

| 窗口 | 天数 | 日记生成成功 | 问题总数 | 分类统计 |
|---|---|---|---|---|
| 前 3 天 | 3 | 3 | 0 | 无 |
| 前 5 天 | 3 | 3 | 0 | 无 |
| 前 7 天 | 3 | 3 | 0 | 无 |

硬检查：4 项，失败 0 项；失败项：无

## 2. 隔离与只读证据

- 生产库前后一致：True
- 生产库指纹（before/after）：{"amo_forever.db": {"same": true, "before": {"path": "D:\\dafeiyu\\astrbot\\data\\plugin_data\\Astrbot_amoupup_forever\\amo_forever.db", "exists": true, "size": 1191936, "mtime": 1795324670.399, "sha256": "4db4f17ea2d5e297e16b47f037a82f1a5f2d81d2637d3f855c4aaaaed4a9e6a5"}, "after": {"path": "D:\\dafeiyu\\astrbot\\data\\plugin_data\\Astrbot_amoupup_forever\\amo_forever.db", "exists": true, "size": 1191936, "mtime": 1795324670.399, "sha256": "4db4f17ea2d5e297e16b47f037a82f1a5f2d81d2637d3f855c4aaaaed4a9e6a5"}}, "amo_data.db": {"same": true, "before": {"path": "D:\\dafeiyu\\astrbot\\data\\plugin_data\\Astrbot_amoupup_data\\amo_data.db", "exists": true, "size": 6811648, "mtime": 1795311777.681, "sha256": "1b0d9b942d6d927addf54c8ca576f8b85e5e0d1e8b204c9991e81b2e2a19f4ba"}, "after": {"path": "D:\\dafeiyu\\astrbot\\data\\plugin_data\\Astrbot_amoupup_data\\amo_data.db", "exists": true, "size": 6811648, "mtime": 1795311777.681, "sha256": "1b0d9b942d6d927addf54c8ca576f8b85e5e0d1e8b204c9991e81b2e2a19f4ba"}}}
- 启动/结束：2026-11-22T16:24:59 -> 2026-11-22T16:25:15
- 隔离守卫计数：{"amo_db_init": 0, "sqlite_blocked": 0, "open_blocked": 0, "sqlite_total": 12}
- 临时目录：<PATH>（已清理：True）

## 3. 事实表 vs 日记命中（每天）

| 天 | 类型 | 块数 | 去过的地方 | 出门 | 三餐 | 健康(当天/生成时) | 天气 | 日记命中 | 问题数 |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-01 | rest | 17 | 家、路上、商场、商场·餐厅 | 是 | 3 | 健康 / 健康 | 阴 23度 | 地点:Y 出门:Y 饭:Y 健康:Y 同伴:Y | 0 |
| 2026-11-02 | workday | 22 | 家、路上、公司、写字楼附近 | 是 | 2 | 健康 / 健康 | 阴 17度 | 地点:Y 出门:Y 饭:Y 健康:Y 同伴:Y | 0 |
| 2026-11-03 | workday | 22 | 家、路上、公司、写字楼附近 | 是 | 2 | 低烧 / 健康 | 晴 23度 | 地点:Y 出门:Y 饭:Y 健康:Y 同伴:Y | 0 |

## 4. 失败分类明细

| 分类 | 次数 | 说明 |
|---|---|---|

## 5. 健康 / 天气 / 同伴专项

### 提示词日期口径（确定性）
- 补写昨天时「今天/昨日/已经过完的某天」三处口径混用：0/3 天
- 本次 home_lock（日程有外出却写一直在家人）失败：0 处

### 人名兜底误伤（确定性 probe）
- 生产路径 scrub_names_guarded 是否把普通词当人名替换：未命中；底层 foreign_names 原始候选命中=True；样例：['中午下楼吃了碗牛肉粉', '晚上在楼下小馆吃了碗面', '喝了一杯热咖啡']

### 健康
- 2026-11-03：当天=低烧，生成时=健康，提示词带健康事实=True，日记提到生病=True
  - 生产 state 注入：当天 20:00 写入 低烧，health_day 快照=低烧；次日早上 read_health 回读=低烧（ok=True）

### 天气
- 2026-11-02：当天=阴 17度，提示词带天气=True；日记写：今天阴天

### 同伴（正向探针）
- 条件：注入约会块 + 生产约会会话（begin/finish）；日期=2026-11-06
- date_windows=[{"start": "19:00", "end": "21:30", "activity": "和老公约会，一起吃火锅", "place": "东莞·商场·餐厅", "source": "schedule", "ended": true, "scene_id": ""}]
- companion.date_window：通过（生产 date_windows=[{"start": "19:00", "end": "21:30", "activity": "和老公约会，一起吃火锅", "place": "东莞·商场·餐厅", "source": "schedule", "ended": true, "scene_id": ""}]）
- companion.together_mentioned：通过（日记提到在一起的证据={'fact_together': True, 'diary_together_mentioned': True, 'hit': True}）
- companion.no_false_presence：通过（同伴矛盾 0 个）

## 6. digest 注入最近三天

- 生产原样：episodic_memory=0 条，注入行=''（line_empty）
- 静态扫描：note_recent_topic 定义 1 处、生产调用点 2 处；episodic_memory kv_set 4 处、kv_get 4 处
- 播种路径：amo_schedule._seed_day_digest（生产 note_recent_topic + fold_day + 跨天去重 + day_digest 上限裁剪）；单条 day_digest 上限 72 字
- 播种 2026-11-01：事实 5 条（关键 3）-> digest 47 字；跨天去重/截断丢掉：无
- 播种 2026-11-02：事实 5 条（关键 3）-> digest 55 字；跨天去重/截断丢掉：无
- 播种 2026-11-03：事实 6 条（关键 4）-> digest 50 字；跨天去重/截断丢掉：['今天在家急急忙忙洗漱，准备出门', '晚上还在敷面膜']

## 7. 模板复读

- 跨天重复整句：0 句
- 4-gram 最相近的天对：[{"days": ["2026-11-02", "2026-11-03"], "score": 0.137}, {"days": ["2026-11-01", "2026-11-02"], "score": 0.048}, {"days": ["2026-11-01", "2026-11-03"], "score": 0.033}]
- 每天最大重叠：{"2026-11-01": 0.048, "2026-11-02": 0.137, "2026-11-03": 0.137}

## 8. 前 10 脱敏样例


## 9. 疑似根因

## 10. 局限

- 只跑生产日程生成器（work/simulate_weeks.build_day_skeleton）+ 生产大日记链路；没有跑逐小时 JIT/过渡/块小日记调度（块日记表为空 -> chat_lines/hourly_lines 都是空，等价于「数据插件没接」的生产分支，这会让提示词多一句「非常平淡」，已在根因里单列）
- 固定 7 天序列（2026-11-01..07，golden 同款种子），3/5 天窗口是它的前 3/5 天累计，不是三次独立重跑
- 聊天/账本为空：没有真实对话与消费，只验证日程/健康/天气/同伴维度
- 健康/约会为注入测试条件：健康在当天 20:00 用生产 set_health 写入，再由生产 read_health 落 health_day:<day> 快照（第二天补写时按天读回，不是读生成时刻），约会用生产 begin/finish + 一个 date 日程块；除这两项外日程块全部来自生产生成器
- 地点判定用独立词表（work/sim/diary_checks.py），可能有个别口语漏判/误判；报告里每条失败都带原文与事实证据，可人工复核
- 生成层 temperature=0.8 单次采样：同一 harness 连跑多次，分类命中会波动（本次保留最后一次采样）；确定性结论（digest 死链路、提示词日期口径、人名兜底误伤、健康事实取生成时刻、天气无素材）另有代码级证据，不依赖采样

## 11. 复跑与代码指纹

- 复跑：python work/sim/diary_sim.py --days 7（默认读 provider.env；加 --no-real-llm 用确定性 mock）
- 输出：work/sim/diary_report.json / work/sim/diary_report.md
- 代码指纹（sha256_16）：{"schedule_engine.py": {"sha256_16": "1adf3cdd2ff480a3", "bytes": 8794, "mtime": 1794987550}, "schedule_skeleton.py": {"sha256_16": "a84abcd257d30be0", "bytes": 42089, "mtime": 1795295138}, "schedule_plan.py": {"sha256_16": "8f0573608c8a8309", "bytes": 26057, "mtime": 1795144698}, "amo_schedule.py": {"sha256_16": "f5d9eb3c9be98ec2", "bytes": 134444, "mtime": 1795318353}, "amo_fact_pool.py": {"sha256_16": "4b346b66828f432e", "bytes": 15964, "mtime": 1795144736}, "amo_fact_timeline.py": {"sha256_16": "dbc9abdae5a537bd", "bytes": 81241, "mtime": 1795298254}, "amo_diary_guard.py": {"sha256_16": "05e0142695a9ac5c", "bytes": 54880, "mtime": 1795314529}, "amo_location_rules.py": {"sha256_16": "3f389b9e35b5e0fe", "bytes": 22056, "mtime": 1794816390}, "amo_episodic.py": {"sha256_16": "83cf84796f6ee3c4", "bytes": 2358, "mtime": 1795257432}, "amo_runtime_context.py": {"sha256_16": "9a1c4104f82b7dd7", "bytes": 83974, "mtime": 1795318567}, "amo_feature_api.py": {"sha256_16": "262627f1f496ad54", "bytes": 45182, "mtime": 1795320715}, "domains/diary/engine.py": {"sha256_16": "73b8df090ce9ffdb", "bytes": 11788, "mtime": 1794959706}, "work/sim/diary_checks.py": {"sha256_16": "8151156a4d626a75", "bytes": 42770, "mtime": 1795314246}, "work/sim/diary_sim.py": {"sha256_16": "9d8b66d565dc9373", "bytes": 93524, "mtime": 1795314411}}
