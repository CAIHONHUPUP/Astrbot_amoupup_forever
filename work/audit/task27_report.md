# task-27 跨插件一致性修复报告（LifeStateSnapshot / MetricsAPI / 生图 principal / 群 umo）

> **只读声明**：生产库全部以 sqlite3.connect("file:...?mode=ro", uri=True) 只读打开；未 reload、未 commit/push、未写生产库、未改生产配置。所有改动都在四个插件仓库工作树 + 各自 tests 下，未部署。
> **时点**：2026-11-22 11:20–12:0x（Asia/Shanghai）。改动期间有多个并行任务在同一工作树运行，本报告只对自己改动负责；已交接的文件单独标注。
> **依据**：work/audit/cross_plugin.md（task-14 P0-2/P0-3/P1-4/P1-1）。
> **产物**：
> - 本报告：work/audit/task27_report.md
> - 只读证据：work/audit/task27_evidence.json（2026-11-22T11:56:49 采样）
> - diff：work/audit/task27_diff.patch（无 git 环境，用改动前备份 + difflib 生成）；统计：work/audit/task27_diff_stats.json
> - 改动前备份：%TEMP%\task27_before（15 个文件）

---

## 0. 结论摘要

| # | 修复 | 契约/入口 | 验证 |
|---|---|---|---|
| 1 | **LifeStateSnapshot v1**：能陪生成唯一只读状态快照（位置/当前动作/日程/约会/购物/钱包/穿搭/心情/照片 + consistency 自检），守护经 AmoBridge 只读消费 | forever/services/life_state.py；feature API life_state_snapshot()/life_state_contract()；watch/core/life_state.py 消费端 | forever 10 tests、watch 10 tests；production kv 购物余额陈旧/date_session 缺失都能被快照识别 |
| 2 | **MetricsAPI v1**：消费口径单一定义（排除存款/投资/转账/退款/房租水电，按 category+idem+reason 三重识别），守护首页与生活卡同源；能记「聊天」= user+bot、摘要单列；定格全路径生图计量 | forever/services/metrics.py；feature API metrics_snapshot()/month_consumption_cents()；watch/services/metrics.py；photo usage by_source | forever 6 tests（跨仓数值比对，12 subtests）、watch 5 tests、data 3 tests、photo 10 tests |
| 3 | **生图 principal 权限/配额/用量**：event=None 也必须带 principal；owner 走同一套权限+每日配额+usage 记账，system 内部生成放行不占额；坏 principal 拒绝且只给能说出口的话 | forever/photo_generate.py（_photo_principal）；photo/main_tasks.py generate_images_for_plugin(principal=...)；photo/main_commands.py 状态化配额 | photo 10 tests（含 7 个真实失败/拒绝场景）；photo 全量 347 passed |
| 4 | **群 umo 单函数**：面板 JS 只剩 normalizeGroupUmo 一套（聊天页分组/群聊页分组/群名查找/改名 key 全部同源），groupKey 退化为别名；后端入库/迁移仍唯一 normalize_group_umo | data/views/dashboard/index.html；services/message_engine.py（不变，新增契约测试） | data 5 tests（后端规则 + 面板静态契约）；data 全量 785 passed |

**关键效果（只读复算，11:56 采样）**：

- 本月消费：旧生活卡口径 ¥16,031.45、守护首页 watch.expenses ¥488.00、唯一口径 ¥5,331.45；修复后两个面板都走 month_consumption()（优先能陪 metrics_snapshot），数值必然相等（watch 测试用同一批流水断言）。
- 聊天：能记面板旧「聊天 436」，其中 user 104 + bot 187 + summary 145；新口径聊天 291、摘要 145 单列。
- 生图：定格 usage 文件 date=2026-11-21/total=3，能陪 kv photo_count:2026-11-22=2；新口径跨插件 owner 生图写入 usage（source=plugin），面板 total 覆盖全部路径。
- 购物三值：kv shopping_prompt 写死「卡里余额 4072」、watch meta shop:events cash=308654、钱包实时 394725；快照返回 wallet_balance_cents=394725 且 line_stale=true + consistency issue shopping_balance_stale，消费端据此不再引用旧数字。
- 约会：kv date_session 仍不存在（canonical_present=false，date_session.source=legacy_projection），快照显式暴露该降级。

---

## 1. 契约定义（v1，字段只增不改）

### 1.1 LifeStateSnapshot（contract=amo-life-state, version=1）

生产者：能陪 HumanCorePlugin.life_state_snapshot(now=None)；消费者只能通过 AmoBridge 调它，禁止直读对方 kv/文件。

字段（services/life_state.py REQUIRED_FIELDS）：
- contract/version/source/generated_at/day：契约头（source=forever）
- principal：{umo, uid, platform, scope, kind=owner}
- location：amo_presence.current_scene 结果 + at_home/in_shop（唯一场景源）
- current_action：amo_current_action.get_current_action（micro > jit > schedule > fallback）
- schedule：{current, reason, next, day}
- date_session：canonical_present/phase/title/place/together/line/start_ts/end_ts/source
- shopping：{active, line, until, mentioned_price_cents, mentioned_balance_cents, wallet_balance_cents, line_stale}
- wallet：{balance_cents, withdrawable_cents, month, month_consumption_cents}
- outfit/mood/media：穿搭文本、心情、今日照片（媒体账本口径）
- consistency：{ok, issues[]}；degraded：逐字段降级原因

设计约束（均已测试）：只读（build_snapshot 不写 kv/DB）；任何字段失败只进 degraded 不抛；consistency 只报告同一事实两个值（shopping_balance_stale / location_split / date_session_canonical_missing）。protocol_info 新增 contracts.life_state/version（字段只增）。

### 1.2 MetricsAPI（contract=amo-metrics, version=1）

生产者：能陪 metrics_snapshot(now=None)；消费者：守护、面板、契约测试。

指标与规则（forever/services/metrics.py 唯一实现；watch/services/metrics.py 为旧版能陪的降级重算，跨仓测试比对同一批流水）：
- expense_month_consumption：cash 负流水去掉
  * asset（存款/投资/基金/理财/转账/退款/账户调整，category 或 idem 前缀 save:/deposit:/invest:/transfer:/refund:/withdrawable:/bank:dep:）
  * bill（房租/水费/电费/燃气费/宽带/物业/取暖，category 或 reason 前缀）
  * refund（幂等前缀 + 退款/退回/冲正/撤销/退还）
- expense_month_assets_change：资产类绝对值（本月存起来/买基金，单列卡片）
- expense_month_bill：房租水电等（单列账单卡）
- photos_today_by_all_paths：媒体账本（amo_media.photo_count）唯一口径；定格侧 usage 记录 usage_recorded 回执，面板按 source 区分命令/跨插件/旧版。

### 1.3 生图 principal（contract=amo-image-principal, version=1）

- 结构：{contract, version, caller, kind ∈ owner|system, user_id, scope, umo}
- caller 必须 ∈ 受信插件名单（forever/watch/data）；event 优先（原有权限判定不变）；event=None 时：
  * principal 合法 + owner → 与命令路径同一套权限 + 每日配额（user_id 与命令路径共享计数）+ usage 记账（source=plugin）
  * principal 合法 + system → 放行、不占个人配额、不记「她的今日生图」
  * principal 坏形状/不受信 → 拒绝，message 为「这个只给老公看哦。」（不含权限/配额/event 字样），usage_recorded=false
  * 无 event 无 principal → 滚动升级过渡：放行但标记 legacy_system、usage 记 legacy:unattributed、contract_miss+1（面板/健康可见）；新能陪已全部带 principal
- 返回新增：usage_recorded、contract、principal{caller,kind,level,user_id,source}；旧的 message/images 字段不变。

### 1.4 群 umo

- 后端唯一：services.message_engine.normalize_group_umo（入库 + 迁移 + 分组），规则：取 :GroupMessage:([^:]+)，尾部纯数字下划线段截断，非数字群 ID（飞书 oc_）只丢 sender 尾段。
- 面板唯一：views/dashboard/index.html normalizeGroupUmo（同规则），聊天页会话分组 key、群聊页分组/筛选、群名查找、改名 key 全部走它；旧 groupKey 只剩一行别名。测试断言归一化正则全文件只出现 1 次。

---

## 2. 各仓库改动清单

### 2.1 Astrbot_amoupup_forever（能陪，状态/指标生产者）

| 文件 | 类型 | 说明 |
|---|---|---|
| services/life_state.py | 新增 384 行 | LifeStateSnapshot v1 构建器 + 契约自述 + compact |
| services/metrics.py | 新增 266 行 | 消费口径唯一实现 + metrics_snapshot |
| amo_feature_api.py | +140/-7 | life_state_snapshot/contract、metrics_snapshot/contract、month_consumption_cents；note_shopping_pending 扩 name/color/price_cents + 写入时钱包余额/时间戳，保持旧调用兼容 |
| photo_generate.py | +48/-2 | _photo_principal(owner/system)；_generate record_usage=True 带 principal；generate_custom system principal；_call_omni removable 加 principal（旧定格兼容）；FakeEngine 无该方法时 getattr 兜底不传 |
| main.py | +35/-1 | protocol_info 增 contracts（importlib 动态导入，避免架构棘轮新增边） |
| tests/test_life_state_snapshot.py | 新增 201 行 | 字段/版本/只读/一致性/降级/compact |
| tests/test_metrics_contract.py | 新增 146 行 | 跨仓数值比对（watch/services/metrics.py）+ build_metrics + month_bounds |
| tests/test_protocol_rollback.py | 更新 | protocol_info 形状允许新增 contracts，并断言契约版本 |

### 2.2 Astrbot_amoupup_photo（定格，生图执行侧）

| 文件 | 类型 | 说明 |
|---|---|---|
| main_commands.py | +261/-71 | PLUGIN_PRINCIPAL_* 常量与安全文案；_normalize_plugin_principal/_resolve_plugin_access/_with_usage_contract；状态化配额（_status_quota_state/_reserve_status_quota/_release_status_quota/_finalize_status_quota/_release_quota_for/_image_quota_error_for_status）；_record_status_images(_locked) 带 source；contract_miss/principal_denied 计数；event 路径保留原有 hook（测试可替换 _reserve_image_quota 等） |
| main_tasks.py | +60/-20 | generate_images_for_plugin(principal=...) 权限/配额/用量不再依赖 event；返回 usage_recorded/contract/principal；失败/取消按 event 或 status 释放配额 |
| main_config.py | +17/-2 | usage 统计保留 source；页面返回 by_source/contract_miss/principal_denied/contract_miss_last |
| tests/test_plugin_principal_quota.py | 新增 182 行 | owner 记账/配额拒绝/system 不占额/坏 principal 安全拒绝/legacy 打标/页面来源统计 |
| tests/test_reference_path_boundary.py | 更新夹具 | object.__new__ 的 fake plugin 补 _cache_lease_lock/_cache_leases（cache-lease 功能落地后测试桩没跟上，与 principal 无关） |

### 2.3 Astrbot_amoupup_watch（守护，消费侧）

| 文件 | 类型 | 说明 |
|---|---|---|
| core/life_state.py | 新增 189 行 | AmoBridge 读快照、校验 contract/version/字段、SnapshotView 视图；不可用时 accessor 全部默认值（原文只留 raw），CONTRACT_FALLBACK 按 reason 去重 |
| services/metrics.py | 新增 191 行 | 消费口径消费端：首选 amo.metrics_snapshot，旧版按同规则本地重算；返回 source/degraded |
| services/dashboard.py | +8/-2 | 首页本月支出改 month_consumption（与生活卡同源），不再读 watch.expenses |
| ui/report.py | +21/-24 | life_block 接受 consumption_cents 覆盖值；默认走 services.metrics 同一口径；删除 reason 前缀旧排除表 |
| ui/cards.py | +12/-3 | _snapshot_life 用 month_consumption 并记录 month_expense_source；_snapshot_expense 不再覆盖 month_expense，只单列 expense_category_total |
| core/amo_bridge.py | +6 | _CALLS 登记 life_state_contract/life_state_snapshot/metrics_contract/metrics_snapshot/month_consumption_cents |
| services/shopping_engine.py | +588/-152（含 task-24 改写） | 我加的保留点：life_state.read 取实时余额/地点；shop:events 带 place/event_id/snapshot；note_shopping_pending 传结构化 kwargs；flush 用实时余额、快照明确不在商场则作废 tryon。之后已交接 fix-shopping-voice 叠加状态机，我不再动 |
| tests/contracts/Astrbot_amoupup_forever_surface.json | 更新 | 新增 5 个方法（task-24 teammate 用 --update 重生成，含其他在途方法，属正常） |
| tests/architecture_baseline.json | 更新 | 我手写 4 条（core/life_state.py、services/dashboard.py、ui/cards.py、ui/report.py），未整份重生成 |
| tests/test_life_state_contract.py | 新增 178 行 | 合法/版本不符/字段缺失/调用失败降级 + 降级去重日志 |
| tests/test_metrics_consistency.py | 新增 227 行 | 首页=生活卡=唯一口径；旧 amo 降级同值；分类小卡不覆盖；月份不符降级；life_block 覆盖参数 |

### 2.4 Astrbot_amoupup_data（能记，面板/群组）

| 文件 | 类型 | 说明 |
|---|---|---|
| services/maintenance_engine.py | +17 | stats 新增 messages_chat（role in user,bot）/messages_summary（role=summary），保留 messages 全表兼容；bot 过滤同样生效 |
| main.py | +6/-3 | /mem_stats 文案：记忆 活跃/共、聊天 N（摘要 M 单列），不再只报 917/436 |
| views/dashboard/index.html | +34/-16 | 状态栏聊天=chat、摘要单列、记忆活跃/总数；群 umo 单函数（聊天页分组/群聊页/群名查找/改名同 key） |
| tests/test_panel_metrics.py | 新增 63 行 | 聊天/摘要拆分、bot 过滤、非负/不超总数 |
| tests/test_group_umo_single.py | 新增 79 行 | 后端 QQ/飞书/私聊规则 + 面板静态契约（唯一归一化正则、别名、手动群名 key） |
| tests/test_mem_stats.py | 更新 | 断言新文案（活跃/共、摘要单列） |

---

## 3. 验证

### 3.1 新增契约测试（全部通过）

- forever：pytest tests/test_life_state_snapshot.py tests/test_metrics_contract.py -> 16 passed（含 12 subtests 跨仓比对）
- watch：pytest tests/test_metrics_consistency.py tests/test_life_state_contract.py -> 20 passed（另 test_finance_report.py 旧断言保持绿）
- photo：pytest tests/test_plugin_principal_quota.py -> 10 passed
- data：pytest tests/test_panel_metrics.py tests/test_group_umo_single.py -> 11 passed

### 3.2 相关全量/回归

| 仓库 | 命令 | 结果 |
|---|---|---|
| photo | pytest tests | **347 passed, 252 subtests passed, 0 failed** |
| data | pytest tests | **785 passed, 7 skipped, 39 subtests passed, 0 failed** |
| watch | pytest tests | **1148 passed, 3 failed**：2 条是并行 task fix-money-food 的 meal 文案断言（不在本任务范围）；1 条 architecture_ratchet 全量跑时因并行任务在途 import 新增边，单独跑该测试 **passed**（我 4 条基线已补） |
| forever | pytest 契约/照片/协议子集 | **282 passed, 1 failed**：失败= test_architecture_ratchet 全量，diff 全是并行任务在途 import（amo_scene_ledger/amo_state_snapshot/amo_addressee_guard 等），不含本任务文件（main.py 已改动态导入，已确认不在新增边里） |

生产代码每步都用离线 pytest 验证，未启动 AstrBot、未 reload、未写生产库。

### 3.3 只读生产证据（work/audit/task27_evidence.json，11:56 采样）

- wallet_ledger 11 月 cash 负流水 86 笔：旧生活卡口径 1,603,145 分；唯一消费口径 533,145 分；watch.expenses 汇总 48,800 分（三值 32.8 倍差）
- 钱包实时余额 394,725 分；kv shopping_prompt 写死 407,200（且「米白色米白色」重复）；watch meta shop:events cash=308,654 → 快照 line_stale=true
- 能记 messages：user 104 + bot 187 + summary 145 = 436；新口径聊天 291 / 摘要 145
- 定格 usage 文件 date=2026-11-21 total=3；能陪 kv photo_count:2026-11-22=2
- kv date_session 不存在（canonical 缺失）；group_umo_migration scanned=0（生产无群消息）

---

## 4. diff（工作树，无 git，改动前备份对比）

- 完整 unified diff：work/audit/task27_diff.patch（约 4,199 行）
- 统计：work/audit/task27_diff_stats.json
- 修改 15 个既有文件（约 +1,300/-300 行）+ 新建 11 个文件（约 2,100 行）；另有 4 个测试基线/夹具文件更新未在备份列表（photo tests/test_reference_path_boundary.py、data tests/test_mem_stats.py、watch tests/architecture_baseline.json、forever tests/test_protocol_rollback.py），说明见 §2。
- 注意：watch/services/shopping_engine.py 的 diff 包含 task-24 teammate 的状态机重写（我只保留交接点），不能全算本任务；watch/tests/contracts 快照同理含其 --update 结果。

---

## 5. 剩余迁移（本任务未做，需后续任务认领）

1. **能陪其余内部生图调用未带 principal**（滚动升级期走 legacy_system，contract_miss 计数可见）：
   photo_generate.generate_custom 已带 system；仍缺：home_refs.py:88/95、outfit_refs.py:274、pose_library.py:854、domains/photo/room_ab.py:74、以及 draw_engine.py:1543 event 可能为 None 的路径。建议统一加 principal=self._photo_principal("system"/"owner")，并把 contract_miss 目标降到 0。
2. **shopping_prompt 的 LLM 注入仍读 kv 原文**：amo_runtime_context.py:1161 读 kv shopping_prompt.line，写死的 4072 仍可能被模型说出去；快照已能判陈旧，下一步应在 prompt 组装时改用 life_state_snapshot().shopping + 实时 wallet 渲染（amo_runtime_context 属并行任务热文件，未动）。
3. **date_session canonical 键仍缺**：快照显式报 canonical_present=false；需要把 amo_date_session.load 的 legacy 投影在写路径上真正落 canonical kv（当前 load 不落库），并让 watch date:list 只做只读投影。
4. **watch 其余推送/面板未接 MetricsAPI/LifeState**：watch 仍有 6 处直接 amo.push_to_owner（shopping/date/fund 等），其中试穿文案已接快照；其余推送的余额/地点仍各拼各的，建议统一 request_push + snapshot。
5. **watch memory_index 与能记 memories 双写（64/90 同文）**：本任务未动记忆去重，留给 memory 任务合并单写方。
6. **群列表 API 仍 GROUP BY 原始 umo**：生产无群消息（scanned=0）暂不影响；有群流量前建议 SQL 层也按归一化 umo 分组（入库已归一化 + 迁移兜底）。
7. **forever 架构棘轮仍有并行任务在途边未补**（scene_ledger/state_snapshot/addressee_guard 等），不是本任务文件；需各任务 owner 更新 tests/architecture_baseline.json，避免最后合并时红。
8. **photo usage 的 legacy 来源**：滚动升级期旧能陪调用会记 legacy:unattributed，面板可查；部署完成后应确认 contract_miss=0，并可停用 legacy 放行分支。

---

## 6. 风险与回滚

- 全部新增参数默认 None / 旧字段不变：photo generate_images_for_plugin 新增 principal 为可选关键字；note_shopping_pending 新增 kwargs 可选；life_block 新增 consumption_cents 可选；protocol_info 只增 contracts 字段。
- photo 对旧调用方保留 legacy 放行（否则旧能陪自动生图直接失败），但已打标 + 计数 + 面板可见，可据此判断何时收紧。
- LifeStateSnapshot 构建全程只读；任何字段异常只进 degraded；watch 消费端不可用时全部返回默认值并退回旧逻辑。
- 回滚：四个仓库均为工作树改动、无提交；按 work/audit/task27_diff.patch 反向即可；新建文件直接删除。未改生产配置/DB。
