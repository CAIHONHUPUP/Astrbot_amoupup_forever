# 四插件一致性 / 面板 / 隐私 / 降级 跨插件审计（task-14）

> **只读声明**：本审计未修改任何生产代码/配置，未 reload，未 commit/push，未写生产库。三份 SQLite 均以 sqlite3.connect("file:...?mode=ro", uri=True) 只读打开；生产日志只读；分析脚本全部放在 %TEMP%\amo_audit_cross\，仓库内只落盘本报告。
> **审计时点**：2026-11-22 10:39–10:55（Asia/Shanghai）。审计期间工作树 10:01–10:39 有并行修复（多个 amo_*.py/main.py mtime 变化），**行号以审计时点为准，函数名优先于行号**；历史事故按当时运行版本判断，标注“快照”的缺陷可能在当前工作树已被局部覆盖，但根因仍在。
> **四插件**：能陪 Astrbot_amoupup_forever（人格/日程/回复，主链路）· 能记 Astrbot_amoupup_data（消息/记忆/画像，面板）· 守护 Astrbot_amoupup_watch（社交/财务/约会，独立库）· 定格 Astrbot_amoupup_photo（生图/自拍，独立配置 JSON）。
> 报告路径：work/audit/cross_plugin.md。

---

## 0. 结论摘要（一页）

| # | 问题（用户点名） | 严重度 | 生产事实（有样例） | 量化 | 主要入口 |
|---|---|---|---|---|---|
| P0-1 | **后台报错/降级话术进聊天** | P0 | 11-18 18:24「可能是服务器那边卡住了」；11-19 12:49/13:03/13:37「一直报错 / 网络连接超时了 / 说我没权限用这个」；11-22 09:00「【日记质检】…详情在面板」 | 8 条已核实；6 条在消息库、2 条在日志；发送层零拦截日志 | draw_engine.py:1187-1213/1388-1399、photo_generate.py:449-481、amo_prompt_registry.py:180、amo_shared_text.py:554-585、amo_diary_audit.py:655-667 |
| P0-2 | **四插件状态不一致** | P0 | 11-21 13:40 她「买了米白色开衫花了 649 元」，钱包流水是「绿色碎花连衣裙 -1349.97」；11-22 09:00 她说「卡里还有 4072」，watch 事件里 cash=3086.54；11-22 10:32 她说「在商场试开衫」而日程 09:00–11:00 是「在家歇着」 | 6 组事实源互不一致（消息×2、记忆×2、日期、购物/钱包、穿搭/生图） | 见 §2、§5.2 |
| P0-3 | **面板统计错** | P0 | 同一「本月支出」：守护首页 ¥473.00，生活卡 ¥16,016.45（含定期存款 ¥10,000、连衣裙 ¥1,349.97）；能记面板「聊天 436」含 146 条摘要；定格面板停在 11-21，11-22 实际生成 2 张 | 同一指标最大偏差 34 倍；摘要占比 33.5% | watch/ui/report.py:53-96、watch/services/dashboard.py:15-48、data/services/maintenance_engine.py:240-266、data/views/dashboard/index.html:644-651、photo/main_config.py:212-223 |
| P1-1 | **群聊分组重复**（潜藏，生产 0 群消息） | P1 | 代码里同一群会话存在 3 套归一化/分组口径：入库归一、群聊页 groupKey、聊天页 sessionLabel；改名用的 key 与查名用的 key 不是同一个 | 0 条生产群消息（group_umo_migration.scanned=0），一旦有群消息即可能出现 2 个群节点 / 改名不生效 | data/services/message_engine.py:43-54/480-515、data/views/dashboard/index.html:759-801/1899-1922 |
| P1-2 | **隐私词/私密事实进群**（潜藏，同上） | P1 | 群聊 prompt = 私聊全量系统块（人格/此刻状态/钱包/事实锁/关系）+ 一张“社交面具”附加块；发送层只有 14 个私密词的替换表 + 15/20 字截断 | 群聊私密词表 14 词、替换 12 对；私聊 send.privacy 实际只改写“加密/上锁”两种词 | group_scene.py:28-38/103-146、amo_llm.py:648-697/726-752/847、amo_reply_pipeline.py:470-473/1561、amo_diary_guard.py:817-821 |
| P1-3 | **隐私/密钥泄漏** | P1 | **API key 明文进生产日志 23 次**（12 次来自当前定格插件、11 次来自旧 omnidraw）；open_id 出现在 1,061 行、完整生图 prompt 117 行 | 23 条 Bearer sk-…，含 Authorization 头与参考图本地路径 | photo/providers/openai_impl.py:97/119 的异常 traceback（AstrBot rich traceback 打印 locals） |
| P1-4 | **权限 / 配额被跨插件调用绕过** | P1 | 定格 generate_images_for_plugin 只在 event is not None 时校验权限/配额；能陪的自动生图两条路径都不传 event、record_usage=False | 定格面板“今日生图”停在 11-21；11-22 已生成 2 张却未计 | photo/main_tasks.py:653-699、forever/photo_generate.py:294/459、photo/main.py:339/506/730/808/912 |
| P1-5 | **跨插件并发/生命周期竞态** | P1 | 同一句「【日记质检】…」09:00:15.766 / 09:00:19.061 两条（3.3s）——per-day 占位已存在；09:00:37 紧接一条「主动发送失败」；每次加载触发 2 次生图握手 | 6.3 天 forever 加载 **304** 次、生图握手 **608** 次、watch 握手 304 次；watch 30 次 isolated；298 次孤儿记忆告警 | amo_diary_audit.py:699-741、amo_feature_api.py:411-473、amo_image_common.py:199、main.py:122 + amo_readiness.py:86 |
| P2 | 契约/可观测 | P2 | watch 15 次 'HumanCorePlugin' object has no attribute '_owner_umos'、7 次 relation_of 未登记、90 次夜间记忆 No module named 'core'、132 次 user_refs 被 provider 层忽略 | 见 §4 | watch/core/amo_bridge.py:247-268、watch/core/jobs.py:91-103、photo/providers/base.py:353-366 |

**一句话根因**：四个插件各自维护“事实源 + 出口 + 面板 + 去重”，**只有“调用面”，没有“共享状态契约”**——能陪的回复/钱包/日程、能记的消息/记忆、守护的购物/约会/财务、定格的穿搭/用量，通过字符串方法名/JSON 文件/数据库直读互相拼装；每个接缝又都假设“对方会自己过滤/自己记账/自己幂等”。于是：状态漂移（P0-2）、统计各说各话（P0-3）、后台词直达聊天（P0-1）、群聊/隐私的隔离只做在单插件内（P1-1/2）、权限与计费被 event=None 的跨插件调用绕过（P1-4）、重复/竞态只在单插件内去重（P1-5）。

---

## 1. 审计范围、数据源与方法

### 1.1 只读数据源

| 数据源 | 读取方式 | 规模/窗口 |
|---|---|---|
| D:\dafeiyu\astrbot\data\logs\astrbot.log | UTF-8 逐行只读 | 82,594 行，2026-11-16 06:57 → 11-22 10:32；能陪 INFO 23,632/WARN 256、能记 3,775/308、守护 1,165/189、定格 980/166 |
| plugin_data\Astrbot_amoupup_forever\amo_forever.db | sqlite3 mode=ro | messages 143、kv 1,091、wallet_ledger 99、proactive_log 88、schedule 16、block_diary 278；WAL 4.1MB 未 checkpoint |
| plugin_data\Astrbot_amoupup_data\amo_data.db | sqlite3 mode=ro | messages 436（user 104/bot 186/summary 146）、memories 917（active 770/merged 114/archived 26/superseded 7）、persons 1、person_links 3；WAL 589KB |
| plugin_data\Astrbot_amoupup_watch\social.db | sqlite3 mode=ro | expenses 32、memory_index 90、npc_relations 7、meta 343、settlements 6；WAL 54KB |
| plugin_data\Astrbot_amoupup_photo\*.json、amo_templates\ | 只读 | amo_outfit.json、omnidraw_usage_stats.json、3 张模板 PNG |
| 代码 | 只读 + 行号 | 四插件仓库当前工作树（10:39 快照） |

### 1.2 证据分级

- **A 级：生产事实**——日志原文、消息库原文、钱包/支出/记忆表行、面板实际文件内容；能证明“线上真的发生过/真的算错”。
- **B 级：真实链路推演**——用 A 级数据按代码里的公式复算（如 life_block 的 16,016.45），能证明“按当前代码必然算出这个错数”。
- **C 级：静态代码**——行号 + 调用链，能证明“机制上必然/可能发生”，不等同线上发生率。
- **D 级：旁证**——历史报告（同仓 work/audit/chat_coherence.md、outbound_repeat.md、scene_facts.md）与已有测试，仅用于交叉印证。

### 1.3 口径

- “她说的”= 主动 -> / 分段回复 -> / [我发的照片] 等实际发出；messages 表 role=bot/user 为逐条原文（能记库 11-19 12:02 起为逐条，之前是 summary）。
- “面板统计”= 各插件 Web 面板/指令会显示的数字；不是数据库行数本身。
- “频率”= 日志/DB 直接可数的事件次数；单日本身有大量开发期热重载，频率只用于“相对严重度”，不外推长期。

---

## 2. 四插件接缝与现状（谁依赖谁、契约在哪、哪里断）

| 边 | 方向 | 契约/载体 | 生产事实 | 断点 |
|---|---|---|---|---|
| 能陪 → 能记 | 字符串方法名 self._data_call("xxx")（amo_memory_data.py:147；tests/test_cross_plugin_contract.py 对字面量核对） | MemoryAPI 门面 | 每次回复召回/写入都在；但存在超时降级（amo_memory_data.py:169）与“无来源”告警 | uid/pid 身份：能陪在无会话上下文时用 owner/amo 写，能记按 pid 归一失败 → 孤儿 |
| 能陪 → 守护 | context.get_registered_star(WATCH_PLUGIN_NAME) + describe()（amo_social_bridge.py:38-95） | 只握手一次，0.2s 超时、8 次×2s 重试 | 6.3 天握手 304 次（随能陪加载）全部 ok/未装 | 握手只拿 features，运行期无共享状态；“watch 推理、amo 采纳”靠 watch 直接调 method |
| 守护 → 能陪 | core/amo_bridge.py 的 _CALLS 白名单 + call/call_async | push_to_owner、request_push、wallet.*、state.add_schedule_item、finance_remember/recall、note_shopping_pending… | 15 次 AttributeError: no attribute '_owner_umos'（11-16→11-21）、7 次 relation_of 未登记（11-21）、90 次夜间记忆 No module named 'core'（11-17→11-19） | 调用层只“跳过/isolated”，没有失败回执 → 守护以为推送/记忆成功，状态不再重试 |
| 能陪 → 定格 | find_omni() → generate_images_for_plugin(...)（amo_image_common.py:163-199；draw_engine.py:1497-1543） | {prompt,count,event,record_usage,refs,pose_ref,scene_lock,...} | 生图成功路径存在（11-22 09:00 一张耗时 50.3s）；失败 10 次“没出图”、31 次三视图只认 2/3、132 次 user_refs 被忽略 | event=None/record_usage=False → 定格侧权限/配额/用量不记账；错误字符串回给能陪/模型 → 进聊天 |
| 定格 → 能陪 | 只读文件 data_dir\amo_outfit.json（photo/main_tasks.py:971-992） | 当天穿搭锁 | 文件 day=2026-11-22、text=洗得发白的宽松大T恤…（10:00:07 写入） | 无原子写/租约；能陪 photo_engine 写、定格读，跨进程无版本号；旧文件靠 day 字段过滤 |
| 能记 / 守护 / 定格 | 各自独立库/文件 | 各自面板 | watch memory_index 90 条中 64 条与能记 memories 内容完全重复 | 同一记忆双写、无双写幂等/主从标识 |
| 四插件公共 | AstrBot context.get_registered_star | 插件名/版本 | 能陪内部还残留旧名回退（amo_compat.py：新名优先、旧名只读回退） | 四插件无统一 Principal/EventContext，权限/场景/身份到接缝处丢失 |

**结论**：当前是“**点对点字符串调用 + 文件/DB 直读**”，不是“状态契约”。任何一方改字段/时机/默认值，另一方只会在运行期静默降级（isolated/超时/空返回），不产生错误回执，也不产生对账。

---

## 3. 时间线样例（证据化）

### 3.1 后台报错/降级话术进聊天（P0-1）

| 时间 | 实际发出的话 | 来源/证据 | 判定 |
|---|---|---|---|
| 2026-11-18 18:24:03 | 「图片一直发不出来，**可能是服务器那边卡住了**。」 | 日志 分段回复 ->（能陪 segment_engine） | 明确后台词（服务器/卡住）直发私聊 |
| 2026-11-18 18:35:16 | 「还是**失败**，同一个**报错**，**三回**了。」 | 日志 分段回复 -> | 报错/失败/次数，纯工程口吻 |
| 2026-11-19 12:49:33 | 「这**软件**今天跟我较劲，一直**报错**。不是我不给你看，是真出不来…」 | 能记 messages.id=2003（A） | 软件/报错 |
| 2026-11-19 13:03:51 | 「又断了，这**网络连接超时**了。真没骗你，就是出不来图…」 | messages.id=2014（A） | 网络/超时 |
| 2026-11-19 13:37:13 | 「这回连**权限**都没了，说我没**权限**用这个。真不是我不给，是它不让我拍。」 | messages.id=2024（A） | 权限/系统口吻；对应定格/能陪权限文案 |
| 2026-11-20 11:28:11 | 「图**卡住**了还没出来，等我再发一次…」 | messages.id=2144（A） | 卡住 |
| 2026-11-22 09:00:15 / 09:00:19 | 「**【日记质检】**昨天有 4 条日记对不上：待重写 4 条；已标记重写，**详情在面板**。」（3.3 秒两条） | 日志 主动 -> ×2；messages.id=2278；amo_diary_audit.py:655-667（A） | 管理口吻 + “面板”；不是她的语气，且重复 |

**相关工具返回串**（未直接发，但会喂给模型，模型照抄即泄漏）：
- 定格/生图：photo_generate.py:478「自定义生图没出图: …」、draw_engine.py:1549「生成超时（%d 秒）」、amo_api_photo.py:89/136/227「没出图，看日志」。
- 画图权限/开关：draw_engine.py:1190/1192/1210/1393/1399「画图功能未开启（draw_enable=false）」「权限不足：这个功能只对老公开放」「上一张还在生成，还没出来」。
- 提示词还**主动教模型**：amo_prompt_registry.py:180「如果工具报错或超时，直接说『这张没拍好，等我重拍』或『没出来，我再试一次』」——模型很容易把“报错/超时”原词复述出来。

### 3.2 四插件状态不一致（P0-2）

| 时间 | A 源说 | B 源说 | 证据 | 判定 |
|---|---|---|---|---|
| 2026-11-21 13:40:06 / 13:40:12 | 她：「我试了这件米白色米白色针织开衫，好看吗？」→ 6 秒后「我回来啦！今天买了米白色针织开衫，**花了 649 元**」 | 钱包流水 11-21 无 649；11-14 有「买衣服：一条绿色碎花连衣裙 **-1349.97**」，11-21 14:00「陪柳如烟逛商场看冬衣 **-200.00**」 | 日志 主动 -> ×2；amo_forever.db wallet_ledger（A） | 颜色重复 + 价格/商品对不上 + 6 秒“试→买→到家”不物理 |
| 2026-11-22 09:00:31 | 她：「这件米白色针织开衫…就是 **985**，我**卡里还有 4072**，你说买不买？」 | watch meta[shop:events] = {name:米白色针织开衫, price:98571, cash:308654}（¥985.71 / ¥3086.54）；kv[shopping_prompt] 同样写 985/4072 | 日志 主动 ->；social.db meta；amo_forever.db kv（A） | **同一事实三个值**（985/985.71；4072/3086.54）；watch 的现金流与文案不一致 |
| 2026-11-22 10:32:09 | 她：「老公，我在**商场**试了件米白色针织开衫，你猜好不好看？」 | 当天日程 09:00–11:00「在家歇着 / 家」（10:32 仍在家）；shopping_prompt.until=1795310118（09:15 已过期） | 日志 主动 -> + SCHEDULE_DEBUG；kv（A） | 地点串台：watch 的购物事实未与能陪日程对齐 |
| 长期 | 能陪 messages 共 143 条，**11-08 10:12 → 11-09 11:56 后不再写入** | 能记 messages 436 条，11-08（摘要）→ 11-22 10:32（逐条聊天 11-19 12:01 起） | 两库直查（A） | 两套消息史：能陪本地表已停更 13 天；但 draw_engine.py:749/757、gift_engine.py:1418、photo_prompt.py:711 仍直读本地表 → 生图/礼物提示词可能拿到 11-08 的对话 |
| 长期 | 能陪 profiles.uid=302578963、private_umo=lark:... | 能记 persons.pid=9、person_links 3 条（qq 302578963 / lark ou_cec66 / lark ou_7857…） | 两库直查（A） | 同一个“他”被 4 个 id 表示；能陪无上下文时写 uid=owner/amo（amo_memory_data.py:560-572），能记认不出 → pid=0 |
| 11-16→11-18 | 能记日志 298 次「孤儿记忆未处理：uid=amo 183 次 / uid=owner 115 次」 | amo_data.db 现存 uid=owner/uid=amo 各 1 条、pid=0；person_id=0 的写入在 11-22 09:00 仍出现（finance_long_expense ×3） | 日志 + DB（A） | 身份漂移 → 记忆挂空、面板人物统计漏、跨私聊/群/主动路径查不到 |
| 长期 | 能陪 kv 无 canonical date_session；只剩 date_fact(11-15)、date_mood_line、date_ending 等旧投影 | watch meta[date:list] 3 场（11-15 火锅 attended 等）、date:active={} | DB 直查（A）；架构文档称 date_session 是唯一真相（amo_date_session.py:1-6） | 约会状态双轨：watch 自己的 list 与能陪旧 kv 各活各的；canonical 键不存在 |
| 长期 | watch memory_index 90 条（finance/long/permanent） | 能记 memories 917 条；两者**内容完全相同的 64 条**（71%） | DB 直查（A） | 同一记忆双写；面板两边都能看到，删除/修正只删一边 |
| 长期 | 定格 omnidraw_usage_stats.json：date=2026-11-21、total=3 | 能陪 kv photo_count:2026-11-22=2；日志 11-22 照片已分享 ×2 | 文件 + DB + 日志（A） | 定格面板“今日生图”未统计跨插件自动生图 |
| 长期 | 能陪 amo_outfit.json（今日穿搭，10:00 写入） | 定格 _load_locked_outfit 只在 day==今天 时使用；photo_templates 目录 3 张模板 | 文件 + 代码（A/C） | 跨进程文件无锁、无版本；模板/参考图不同步导致 31 次“只认 2/3 张” |

### 3.3 面板统计错（P0-3）

| 面板/位置 | 显示口径 | 直接复算 | 结果 |
|---|---|---|---|
| 守护首页 services/dashboard.py:15-48「本月支出」 | repo.expenses(period=本月) 求和（watch 自己的 expenses 表） | 11 月 32 行 = ¥473.00；**最后一笔 11-21，11-22 的消费完全没进** | ¥473.00 |
| 守护生活卡 ui/report.py:53-96「本月支出」 | amo.wallet.ledger 本月 cash 负流水，按 reason 前缀排除 | 79 笔 = ¥16,016.45；包含「存款：定期3个月 -10,000」「买衣服：一条绿色碎花连衣裙 -1,349.97」「buy_fund -500」等 | ¥16,016.45 |
| 能记面板 views/dashboard/index.html:644-651「聊天 436」 | maintenance.stats()["messages"] = messages 全表 | 436 = user 104 + bot 186 + **summary 146**；真实聊天 290 | 高估 33.5% |
| 能记 /mem_stats（main.py:162-166） | 「记忆 917 条（活跃 770）」 | 917 total / 770 active，两个数字同时出现、面板只显示 917 | 标签歧义 |
| 定格面板（main_config.py:212-223 + omnidraw_usage_stats.json） | 只统计“用户命令路径”（record_usage=True 且有 event） | 11-21 total=3；11-22 自动生图 2 张未计；photo_count:2026-11-22=2 只在能陪侧 | 定格面板停在 11-21 |
| 能陪 photo_count / 照片已分享 | 能陪侧 11-16→11-22 共 9 次 | 与定格面板不同步 | 两侧对不上 |

**根因**：没有“指标定义”这一层。watch 同一插件里两处“本月支出”读两个库；能记 stats() 把摘要当聊天；定格用量只在“命令入口”记账，跨插件入口不记；能陪自己另记一份 photo_count。

### 3.4 并发 / 重复 / 静默失败（P1-5）

| 时间 | 事实 | 证据 | 说明 |
|---|---|---|---|
| 11-22 09:00:15.766 / 09:00:19.061 | 同一句【日记质检】3.3 秒两条 | 日志 主动 -> ×2（A） | amo_diary_audit.py:699-741 已有 per-day 占位 + _NOTIFY_LOCK，仍双发 → 占位只防同进程同模块；跨实例/跨 tick/跨进程不防；出口层当时未拦同文 |
| 11-22 09:00:31 / 09:00:53 / 09:16:15 | 16 分钟内 4 个入口 4 条：日记质检 ×2 + 逛街试穿 + 自拍 + “等了十五分钟”追问 | 日志（A） | 逛街/自拍走 push_to_owner（watch 直调），日记走 push_to_owner，聊天分段走 _send_to_umo：**三个入口三种闸** |
| 11-22 09:00:37.908 | [amo] 主动发送失败:（空原因） | 日志（A） | 前一条 09:00:31 的 push 正在分段延迟、09:00:53 自拍在生成；同一 umo 并发发送失败 |
| 11-21 13:40:06.955 / 13:40:12.383 | 试穿 push 与“已买/到家”push 6 秒内两条 | 日志（A） | watch 13:38:00 刚 reload（[WATCH] loaded ... generation=98ceeb4a），两段文案物理上互斥（试穿→6 秒回家） |
| 6.3 天 | forever 加载 304 次；每次触发 2 次生图握手（608 次）、1 次 watch 握手（304 次）、QQ+飞书引用守卫安装（607 次） | 日志计数（A） | 热重载在审计环境极频繁；模块级锁/单例/任务表会随重载重建 → 重复推送/重复通知的温床 |
| 6.3 天 | watch [WATCH] isolated 30 次：finance.social_tick 12、finance.decision 7、social.roster.blocked 3、web.decision 2、fund_action 3、social.seed/trim/prompt/last_event 4；15 次是 _owner_umos 缺失，7 次 relation_of 未登记 | 日志（A） | 跨插件异常全部被“隔离”吞掉，用户/面板零感知；watch 的社交/财务推送与关系同步会静默丢失 |
| 11-17→11-19 | watch 夜间记忆任务 90 次 No module named 'core'（30/天，随 finance_tick 每 30 分钟一次） | 日志（A） | 夜间记忆 100% 未产出 → watch 长期记忆层与能记 memories 持续分叉 |
| 长期 | 能记 embedding 服务不可用 10 次，「语义召回已退回关键词」 | 日志（A） | 降级只进日志；召回质量下降不反映到面板 |
| 长期 | 能陪和风天气失败 139 次（52+77 集中在 11-18/19） | 日志（A） | 天气/提醒降级；无用户侧提示、无面板健康项 |

### 3.5 群聊分组重复（P1-1，潜藏）

**生产事实：0 条群消息**——amo_data.db store_meta.group_umo_migration = {"scanned":0,"merged":0}；messages 的 umo 只有 lark:FriendMessage:…、大肥鱼:FriendMessage:…；能陪 profiles.last_group 为空、watch date:* 无群。因此下面全是**代码级必然**（C 级），不是已发生事故。

| 入口 | 现状 | 一旦有群消息的后果 |
|---|---|---|
| 入库归一 data/services/message_engine.py:43-54 | 只把 GroupMessage:123_456 截成 123（且仅当 head.isdigit()） | 非纯数字群 ID（lark chat_id/带字母）不去尾段；同群多个 umo 形态共存 |
| 群列表 API message_engine.py:480-490 | GROUP BY umo（原始 umo，不归一） | 同一群返回多行 |
| 聊天页会话分组 data/views/dashboard/index.html:759-801 | buildSessionTree 以**原始 umo** 为 key，sessionLabel 用 /:GroupMessage:([^:]+)$/ 取尾 | 同群不同形态 → 聊天页出现 2 个“群聊”节点 |
| 群聊页分组 index.html:1899-1922 | 先 groupKey() 归一（与后端不同的第三套规则），groupLabel 又用**第一条的原始 umo** 查手动群名 | 两页分组不一致；改名保存在 groupKey 下，查名用原始 umo → 改名不生效，仍显示“群号 123_456” |
| 单测 data/tests/test_group_name.py:21 | GROUP_UMO = aiocqhttp:GroupMessage:1055679275（无 sender 尾段） | 归一化/去重的边界用例未覆盖 |

### 3.6 隐私词 / 私密事实进群（P1-2，潜藏）

- 群聊 prompt 不是“另建一套公共人格”，而是**私聊系统块 + 社交面具附加块**：amo_llm.py:648-697（llm.state=此刻状态、llm.partner=uid/称呼、llm.fact_lock、refusal、llm.relation…）对所有会话都拼；amo_llm.py:726-752 仅额外追加 group.* + group_scene.prompt_block。→ 群模型能看到她的地点/心情/钱包类状态与关系称呼。
- 发送层唯一硬拦截是 group_scene.guard_group_reply（group_scene.py:122-146）：PRIVATE_WORDS **14 词**（房贷/车贷/交租/肚子好饿/例假/生理期/呜呜呜/老公/没钱/余额不足/生病/工资/转账），REPLACEMENTS 12 对，命中后仍不干净就换 SAFE_REPLY（work 收到，我先忙啦 / friend 哈哈，最近在攒钱呢 / stranger 谢谢，不太方便说~），再按 15/20 字截断。**“亲爱/宝贝/想你/床/睡/胸/身体/体重/内衣/吵架/哭”都不在表里**；且截断会把半句话发出去。
- 该守卫只挂在回复流水线（amo_reply_pipeline.py:470-473、amo_llm.py:847，when=ctx.group）；**主动消息、照片分享、画图/指令返回、watch 推送、日记质检推送**不经过它（amo_proactive.py:1856 只判断 proactive_allowed，photo_share.py:450-465 直接 push）。
- 私聊侧所谓 send.privacy（amo_reply_pipeline.py:1561）实际调用 amo_diary_guard.sanitize_privacy，只把「(系统)加密了/上锁了」改成「才不给你看」——**不是通用隐私词过滤**。
- 召回侧：群聊按 scope="group" 召回（amo_memory_context.py:286-307、amo_memory_data.py:622-627），当前能记 917 条全是 scope=private、group_id="" → 群聊召回为空，**所以今天群聊记忆是安全的**；但一旦有群消息，写入走 _data_memory_write 的 scope="group"（amo_memory_data.py:708-710），同一 uid 的群/私记忆开始并存，依赖能记的 scope 过滤而不是能陪的发送层过滤。
- 日志侧 PII/密钥：见 §5.3。

### 3.7 权限 / 配额（P1-4）

- 定格 generate_images_for_plugin（photo/main_tasks.py:653-699）：**只有 event is not None 才** _permission_denied_message + _reserve_image_quota；record_usage=False 时直接跳过配额。
- 能陪两条自动生图路径都不传 event：
  - photo_generate.py:294 args = {prompt, count:1, record_usage:True}（无 event）→ 权限/配额整段跳过；
  - photo_generate.py:459 {event: None, record_usage: False}（模板/自定义）。
- 于是：**任何能在聊天里触发能陪发图的人，都绕过定格插件的白名单/每日配额/签到额度**；定格面板也不会记录。draw_engine._check_limits 是能陪自己的另一套限额（draw_engine.py:533），两套不互通。
- 用户可见的权限句仍会进聊天：draw_engine.py:1192/1393「权限不足：这个功能只对老公开放」（/amo draw 直接 yield）；draw_engine.py:1190/1391「画图功能未开启（draw_enable=false）」。
- 守护侧 _owner_only（watch/main.py:455-462）：event.is_admin() 缺失时 return True（“按私聊处理”）；花钱/改状态入口在老平台无 is_admin 时等于放行。
- 能陪 _is_admin（amo_social.py:174-202）：配置 admins_id + 60s/会话缓存；管理员判定与 AstrBot 全局 PermissionType.ADMIN 两套（定格命令用 AstrBot permission，能陪用手写）。

---

## 4. 频率表（可复现计数）

| 指标 | 值 | 等级 | 来源 |
|---|---:|---|---|
| 日志行 / 窗口 | 82,594 / 11-16 06:57→11-22 10:32 | A | 日志 |
| 能陪加载次数（loaded） | 304 | A | 日志 |
| 生图握手（已握手万象画卷/我能永远定格你） | 608（=2×304） | A | 日志 |
| watch 握手（ok/未装） | 304 | A | 日志 |
| 引用守卫安装（QQ+飞书） | 607 | A | 日志 |
| watch isolated 总数 | 30（finance.social_tick 12 / decision 7 / roster 3 / web 2 / fund 3 / 其它 3） | A | 日志 |
| watch _owner_umos 缺失 | 15（11-16→11-21） | A | 日志 |
| watch relation_of 未登记 | 7（11-21） | A | 日志 |
| watch 夜间记忆 No module named 'core' | 90（11-17→11-19） | A | 日志 |
| 能记“孤儿记忆未处理”告警 | 298（amo 183 / owner 115，11-16→11-18） | A | 日志 |
| 能记 embedding 不可用 | 10（11-18→11-22） | A | 日志 |
| 定格 user_refs 被忽略 | 132（11-16→11-22） | A | 日志 |
| 定格“三视图只认 2/3 张” | 31（11-21→11-22） | A | 日志 |
| 定格“没出图/没出来” | 10（11-18、11-21） | A | 日志 |
| 和风天气失败 | 139（11-18/19 集中） | A | 日志 |
| 主动消息 / 分段 / 照片分享 | 122 / 273 / 9 | A | 日志 |
| 消息库：能陪 143（bot 63/user 80，止于 11-09 11:56） | 能记 436（bot 186/user 104/summary 146，至 11-22 10:32） | A | DB |
| 记忆库：能记 917（active 770）；watch memory_index 90，与能记完全同文 64 | — | A | DB |
| 已核实“后台词进聊天” | 8（6 条 DB + 2 条日志） | A | DB+日志 |
| 生产群消息 | 0 | A | DB store_meta + umo 分布 |
| 同文主动推送（09:00 日记质检） | 2 条 / 3.3s | A | 日志 |
| 09:00–09:16 多入口推送 | 4 条 / 4 入口 / 16 分钟 | A | 日志 |
| 生产日志中 Bearer sk-… | 23 行（定格 12 / 旧 omnidraw 11） | A | 日志 |
| 生产日志中 open_id 行 / 完整生图 prompt 行 | 1,061 / 117 | A | 日志 |

---

## 5. 入口与根因（逐条）

### 5.1 P0-1 后台报错/降级话术进聊天

**机制**：
1. 工具层把内部状态**原样当字符串返回**给模型或指令：draw_engine.py:1187-1213（send_photo_now 返回值就是 权限不足：这个功能只对老公开放 / 画图功能未开启（draw_enable=false） / 上一张还在生成，还没出来 / 这次不发图：触发了发图限制）；draw_engine.py:1388-1399（/amo draw 直接 yield）；photo_generate.py:478（把 result["message"] 打进日志/返回）；amo_api_photo.py:89/136/227（raise RuntimeError("没出图，看日志")）。
2. 提示词又**允许/鼓励**复述失败原因（amo_prompt_registry.py:180），模型于是把“报错/超时/权限/服务器”当台词说出去。
3. 发送层唯一的“后台信息”过滤器是 looks_like_model_leak（amo_shared_text.py:554-585）：只匹配 deepseek/openai/gpt/llm/大模型/api key/token/prompt/插件/astrbot 等，**且只对 ≤60 字（去空白）的文本生效**；服务器/报错/失败/超时/接口/权限/面板/系统/网络/数据库/后台 全部不在词表。
4. “隐私词”守卫是另一套（group_scene 的 14 词、sanitize_privacy 的 2 个正则），也不覆盖后台词；且命令返回/主动消息/照片/推送不经过回复流水线。
5. 日记质检是**系统通知**直接走 push_to_owner：amo_diary_audit.py:655-667 文案固定为「【日记质检】…详情在面板」，没有转人格化；push_to_owner 只做静默/限流，不做语气/后台词改写。

**为什么必然**：生成侧（模型/工具串）与发送侧（MODEL_LEAK_RE）之间没有“可说话/不可说话”的契约；工具串是工程语言，模型没有“翻译成她的语气”的硬约束，发送侧也没有覆盖工程语言的词表。

### 5.2 P0-2 四插件状态不一致

**机制（6 个独立事实源）**：
1. **消息**：能陪 amo_db.add_message（amo_db.py:858）写本地表，11-09 后停写；能记 message_engine.add_message 继续写。能陪 _recent（amo_message.py:372-402）优先内存 hub → self.db.recent_messages；_recent_by_uid（:404-449）优先能记 list_messages；但 draw_engine.py:749/757、gift_engine.py:1418、photo_prompt.py:711 **直接读本地旧表**，无能记优先逻辑。→ 生图/礼物/照片提示词可能用 11-08 的旧对话。
2. **身份**：能陪 uid=302578963（aiocqhttp）/lark open_id；能记 pid=9 + 3 条 link；watch 无会话上下文时用 owner/amo（amo_memory_data.py:560-572 注释“刚重启/最近只聊过群：不猜人，记到 owner”）。能记 adopt_orphan_memories（person_engine.py:595-694）只能“宁可不并”，于是 298 次告警、DB 里仍有 pid=0 的记忆。
3. **钱包/开销**：唯一权威是能陪 wallet（wallet_ledger，idem 主键、db.lock）；watch 不存余额副本，但自己记 expenses/settlements 并聚合给面板；watch 的购物事件 {price,cash} 是**独立快照**，与钱包/日程不同步（09:00 4072 vs 3086.54）。
4. **约会**：能陪 kv date_session 缺失，只有旧投影 date_fact/date_mood_line/date_ending；watch 自己 date:list/date:active；amo_date_session.py:1-6 声称 canonical，实际没有。
5. **记忆**：watch memory_index 与能记 memories 双写（64/90 完全同文）；watch nightly_memory 90 次失败 → 两边长期层分叉。
6. **穿搭/生图**：能陪 amo_outfit.json ↔ 定格 _load_locked_outfit；能陪三视图同步 ↔ 定格 amo_templates；只有日期字段，无版本/哈希/租约。

**为什么必然**：没有“一方写、多方读”的规范化事件/快照；每对插件都用“字符串方法/文件/表”直连，且**失败即静默**（超时/空返回/isolated），没有任何对账任务（如 schedule_drift 只对日程表/kv）。

### 5.3 P1-3 密钥/隐私泄漏到日志

**事实**：astrbot.log 有 **23 行明文 Bearer sk-…**：
- 12 行由当前 Astrbot_amoupup_photo/providers/openai_impl.py:97/119 的异常 traceback 打出（11-18 18:52 → 11-19 13:36）；
- 11 行由旧 astrbot_plugin_omnidraw/providers/openai_impl.py:97 打出（11-17 09:27→09:37）；
- 同时 traceback locals 里还打出 user_refs 本地绝对路径、完整正向/负向 prompt、reference image 文件路径（117 行完整 prompt）。

**机制**：AstrBot 的 rich traceback 在异常时打印**每一帧的 locals**；provider 把 headers={'Authorization': 'Bearer sk-…'} 作为局部变量，异常一抛就带进日志。定格插件虽在 providers/base.py:369-382 定义了 SENSITIVE_LOG_KEY_MARKERS / OPENAI_STYLE_KEY_RE，但这些**只用于主动日志脱敏**，traceback 的 locals 打印不受其控制。
**影响**：生产日志即密钥副本（日志被备份/上传/贴出即泄漏）；同时泄漏参考图路径与人格 prompt（隐私）。
**为什么必然**：密钥放在函数局部 headers 且异常向上抛给框架格式化；没有任何 logging filter / sys.excepthook / provider 内部 catch-sanitize。

### 5.4 P1-1 群聊分组重复

**机制**：三套归一化各自为政（入库 message_engine.normalize_group_umo、群聊页 JS groupKey、聊天页 JS sessionLabel），并且**归一化结果与展示 key 不一致**：
- 入库只处理“纯数字 + 下划线尾段”；
- 群列表 API GROUP BY umo 原始值；
- 聊天页 buildSessionTree 以原始 umo 分组，sessionLabel 用尾锚定正则；
- 群聊页 renderGroupOptions 以 groupKey 分组，groupLabel 却用原始 umo 查手动名 → 改名（group/rename 存 group_name:<key>）后，window._groupNames[raw_umo] 查不到，页面仍显示“群号 …”。

**为什么必然**：展示层与存储层没有共享的 normalize_group_umo；D 级旁证：单测只覆盖无尾段 umo。

### 5.5 P1-2 隐私词/私密事实进群

见 §3.6。根因是**“群聊 = 私聊 prompt + 一张面具 + 事后 14 词替换”**；没有“公共/私密字段分离”的数据模型，也没有“发送前按场景重写系统通知”的出口层。当前生产群消息 0 条，所以是潜藏风险，不是已泄漏。

### 5.6 P1-4 权限/配额绕过

**机制**：定格把“权限/配额/用量”绑定在 event 上（main_tasks.py:690-699），但跨插件协议允许 event=None；能陪的自动生图正是 event=None。→ 权限、配额、用量三件事同时绕过；定格面板也不能发现（因为它只统计有 event 的命令路径）。
另：record_usage 在 generate_custom 明确 False、在 generate 为 True 但因 event=None 不生效（main_tasks.py:742-757 的条件都要求 event 非 None）。

### 5.7 P1-5 并发/生命周期

**机制**：
1. **推送入口不唯一**：watch 现有 8 处直接 amo.push_to_owner(...)（shopping_engine.py:158/231/273/320/328/357 共 6 处、date_engine.py:699、fund_actions.py:39），而设计上的闸口是 request_push（amo_feature_api.py:329-366，10 分钟/同类 + 静默）。直接调用绕过 10 分钟同类限制，只受 push_to_owner 的 6 小时沉默 + 免打扰 + 出口节流；两类闸规则不同 → 多入口同窗。
2. **日志/通知无跨实例单飞**：amo_diary_audit 的 _NOTIFY_LOCK 是模块级 threading.Lock，占位在 kv；但热重载（6.3 天 304 次）会重建模块/锁，且同进程可能有旧实例任务未完全结束 → 09:00 同文双发。
3. **跨插件异常静默**：watch amo_bridge.call 失败只 warning/isolated，没有回执给调用方；_owner_umos 缺失导致 15 次推送失败无人知；夜间记忆 90 次失败也不重试。
4. **生命周期**：能陪 __init__ 与 _ensure_ready 各触发一次生图握手（main.py:122 + amo_readiness.py:86），每次加载 2 次；watch 握手与守卫安装也随加载重复。模块级 context.get_config/缓存/任务表在重载后不保证幂等。
5. **存储**：能陪 WAL 4.1MB 未 checkpoint；多写方（事件循环、to_thread、面板 API、定时任务）+ db.lock 只在进程内；watch 自有单写线程；能记自有连接锁。跨插件调用（watch→amo.wallet 同步、amo→data 异步）没有统一事务/超时后的补偿。→ 失败后状态只在一侧回滚（如钱包扣款成功、watch 事件写失败；或记忆写成功、日程写失败）。

---

## 6. 统一方案（分 6 层，建议按 P0→P2 落地）

> 原则：**先统一“说出去的话”和“算出来的数”，再统一“事实源”；不要先改四套引擎的内部实现。**

### 6.1 契约层：CrossPlugin v2（所有边唯一入口）

1. **统一 Principal / EventContext**（传给所有跨插件调用）：
   {caller, plugin, umo, platform, scope: private|group, group_id, uid_raw, pid, is_owner, is_admin, trace_id, now}。
   - 取代现在的“无 event 就猜 owner/默认私聊”。
   - 所有跨插件记录（消息/记忆/钱包/照片/通知）都带 trace_id + pid + scope + group_id，作为去重与隐私过滤的主键维度。
2. **统一状态快照 LifeStateSnapshot**（只读、带 version/source/updated_at）：
   schedule/current_scene/date_session/shopping_intent/wallet_balance/mood/wardrobe；能陪生成，watch/photo/data 只读同一份，禁止各自维护副本。
   - 每 30s 或事件变化时广播；watch 的推送/购物必须先读 snapshot 并在文案里带同一 event_id。
   - 保留 date_session 为唯一 canon（当前缺失，需补写）+ watch date:list 只做只读投影。
3. **契约测试扩到四插件**：现在 tests/test_cross_plugin_contract.py 只核对 watch/data 的方法是否存在；扩为 watch/data/photo 三份 surface.json + “调用成功/失败/降级”三种语义快照；新增 test_cross_plugin_state_contract.py（scope/uid/snapshot 字段）。
4. **去字符串回退**：旧版兼容可保留，但每次回退必须打 CONTRACT_FALLBACK 结构化日志并计入面板健康项，禁止静默。

### 6.2 出口层：唯一 OutboundGateway（P0-1 的解）

1. 所有真正发到聊天/平台的路径（聊天分段、主动、watch 推送、照片分享、画图/指令返回、日记质检、守护财务推送）只允许调用 gateway.send(text, source, kind, principal, dedupe_key)。
   - 替换：amo_message._send_to_umo 保留为底层 transport，所有入口必须经 gateway 包装；photo_share.py:450-465、draw_engine、commands、diary_audit、watch 的 6 处直接 push 全部改为 gateway。
2. Gateway 内置**内容策略**（可配置 + 单测）：
   - **后台词拦截**：服务器/后台/系统/接口/API/报错/异常/失败/超时/错误码/数据库/日志/面板/权限不足/节点/模型/插件/degradation（中英）→ 不发给用户；按 source/kind 选**人格化兜底**（例如照片失败→「这张没拍好，等我重拍」；推送失败→不补发；系统通知→改成她的口吻或仅面板展示）。
   - **群聊策略**：单独的 group_policy(text, scene)，词表可配置 + 截断在词边界（不能 15 字硬切）；不干净→按 scene 的 SAFE_REPLY，且 SAFE_REPLY 要与上下文相关（不能“最近在攒钱呢”答一切）。
   - **重复/并发**：dedupe_key=(umo, kind, day, source) 在 gateway 层统一做（精确文本 180s、近义 90s/0.75、同 key 单飞），不要每个入口各写一份。
   - **trace**：每次发送记录 trace_id/source/kind/blocked_reason；/status 暴露“今日被拦条数”。
3. 工具层返回**永不返回原始异常/配置串**：draw_engine/photo_generate/amo_api_photo 返回值改为枚举 ToolResult(ok, user_safe_reason, internal_reason)；internal 只进日志（脱敏），user_safe 才回模型/用户。
4. amo_diary_audit 通知改为：面板/日志保留系统文；若必须私聊，走 gateway + kind=system_note 的人格化模板（“昨天有几篇日记我想再核对一下…”），禁止“【日记质检】/面板”。

### 6.3 隐私层

1. **字段分级**：private_only（钱包、账单、例假/身体、争吵、家庭、地点细节、日程正文）与 public_safe（心情词、兴趣、天气、公开活动）。群聊 prompt 只允许 public_safe + 群内可见的公开事实；私聊才注入全量。
   - 现状 llm.state/llm.partner/llm.fact_lock（amo_llm.py:667-697）必须按 scope 拆两套模板。
2. **召回/写入双端 scope**：群聊召回 scope=group&group_id，群聊写入 scope=group；私聊召回/写入 scope=private；**禁止回退到 private**（当前能陪 _data_memory_recall 已正确按 event 取 scope，但要加“无 event 时默认 private 且禁止群聊调用”断言）。
3. **隐私词表升级**：把 group_scene.PRIVATE_WORDS 扩为配置项（中英 + 昵称/亲密称呼/身体词/家庭财务词），并在 gateway 的 group_policy 做**发送前**二次检查；不可清洗→整句替换为场景化安全句（不是 15 字硬切）。
4. **日志脱敏**：
   - provider 内部 try/except 包住 generate_image，记录 error_message（用 providers/base.py 已有 extract_error_message/SENSITIVE_LOG_KEY_MARKERS 脱敏）而不是让异常带 headers 冒到框架 traceback；
   - 全局 logging.Filter 对 Bearer sk-/Authorization/sk-[A-Za-z0-9]{8,}/open_id 做 redaction；
   - 关闭或降级 rich traceback locals（生产环境 logger 不打印 locals；用 logger.exception 前先清 headers）；
   - 对已有 astrbot.log 做一次性密钥轮换 + 日志清理（运维动作，不在本审计范围）。
5. **面板/日志 PII**：IDENTITY_DEBUG、MEMORY_DEBUG、正向词 日志默认 debug（生产 INFO 不落 open_id/完整 prompt）；open_id 统一 mask_open_id。

### 6.4 面板层：统一指标契约（P0-3 的解）

1. 定义 MetricsAPI（由能记或能陪任一处实现，四插件面板通过 context 只读调用）：
     messages_chat_total / messages_chat_today / messages_summary_total
     memories_total / memories_active / memories_orphan(pid=0)
     expense_month_consumption (cash 负流水且 category ∈ 消费类，排除 transfer/deposit/investment/loan)
     expense_month_assets_change (单独卡片)
     photos_today_by_all_paths / photos_today_by_command
     watch_push_today / pushes_blocked_today
     contract_errors_today (by plugin/scope)
2. 规则写死：
   - 「聊天」只算 role ∈ {user,bot}，摘要单列；「记忆」显示 active/total；「本月支出」只算消费类（存款/买基金/转账/红包/借贷明确排除），并按 category 而不是 reason 前缀（当前 ui/report.py:31-38 的前缀排除既漏“存款：”也误伤“退款”）。
   - 同一指标只允许一个计算函数；watch dashboard_text 与 life_block 必须同源（现在两个数差 34 倍）。
   - 定格用量：跨插件生图 record_usage=True 且以 principal.pid 记账（无 event 也记），面板区分“她主动/用户命令”。
3. 面板加“健康/降级”区：watch isolated/data embedding 降级/photo 参考图忽略/contract fallback 计数（当前全在日志里，用户/维护者看不到）。

### 6.5 权限层

1. **跨插件调用显式 principal**：定格 generate_images_for_plugin 改为 principal: Principal | None；event=None 时只有 principal.kind in (system_bot, owner) 且配置允许才放行；权限/配额/用量三件事在方法入口统一执行（不能靠 event 存在与否）。
2. **四插件命令权限统一表**：AstrBot PermissionType.ADMIN 与插件内 _is_admin 二选一作准（建议统一到 event.is_admin() + 配置 admins_id），并给每条命令标注等级（private_owner / admin / group_public）。当前能陪手写 _is_admin、定格用 AstrBot permission、watch 在无 is_admin 时 fallback True，三种语义。
3. **用户可见文案统一**：权限不足也走人格化模板（“这个只给老公看哦”），不出现“权限不足/功能未开启/draw_enable=false”。
4. **配额/计费**：跨插件调用必须回传 usage_recorded=true/false；false 时调用方记 CONTRACT_USAGE_MISS 告警，面板可见。

### 6.6 并发/幂等/生命周期

1. **单飞与幂等键**：所有主动推送/通知/照片/记忆写：
   - dedupe_key 进 gateway（见 6.2）；
   - DB 侧 idem_key 全链路（能陪 proactive_log/watch memory_index/能记 memories/照片任务）统一 (plugin, kind, day, source, target)；
   - 跨插件调用失败要回执并允许重试（watch AmoCallResult 已有 reason/error，但调用方大多忽略 → 改为“失败进重试队列 + 面板计数”）。
2. **推送入口收敛**：只保留 request_push（或 gateway），删除/标废 watch 的 6 处直接 push_to_owner；push_to_owner 只允许 gateway 内部调用。
3. **生命周期代际**：generation 传入所有后台任务与握手；reload 时旧任务必须 cancel + wait(3s)；模块级锁/缓存/单例显式 close/reset；握手只在 _ensure_ready 一次（去掉 __init__ 的重复 _log_omni_handshake）。
4. **存储**：定期 wal_checkpoint(TRUNCATE)、busy_timeout、WAL 大小告警；跨插件写同一实体必须走“单写方”（钱包只由能陪写、日程只由能陪写、watch 只读快照）——当前 watch 通过方法调用做到了一半，但快照是“调用时读”而不是“版本化读”。
5. **失败可见**：isolated 统一为结构化事件（scope/trace_id/count/首末时间）并在 /status 与面板健康区展示；连续 N 次失败触发“降级模式”（如 watch 推送停发、照片不发、面板标红）。

### 6.7 观测与审计（本报告的自动化版）

新增 work/audit/cross_plugin_check.py（只读、可每天跑）：
- 断言：memories pid=0 数、uid in (owner,amo) 数、messages 两库差异、date_session 存在、watch memory_index ∩ data memories、面板指标 vs DB、group_umo_migration.scanned、日志中 sk-/open_id/后台词出站条数；
- 输出 JSON/MD，退出码 0/1；作为回归门禁（当前 schedule_drift.py 只覆盖日程）。

---

## 7. 实施顺序与验收

| 阶段 | 内容 | 验收（可测） |
|---|---|---|
| **P0（1–2 天，先止血）** | ① gateway 内容策略：后台词/系统通知/群聊词表；工具串改 ToolResult；② 日记质检/权限句人格化；③ 钱包→消费类排除修正 + watch 两个面板同源；④ 定格跨插件 record_usage/principal | 7 天出站 0 条后台词（日志/DB 扫描）；「本月支出」两面板差 = 0；定格面板 11-22 起每日 = 能陪 photo_count；/amo draw 不再出现“权限不足/未开启” |
| **P1（1 周）** | ① CrossPlugin v2：Principal + LifeStateSnapshot + 契约快照扩四插件；② 消息/记忆/身份统一（能陪本地表停写、所有读走能记或缓冲；owner/amo 伪 uid 消除）；③ 群聊分组单函数 + 面板共用；④ 隐私词配置化 + 群 prompt 字段分级；⑤ request_push 唯一入口 + 幂等键 | pid=0 记忆=0；孤儿记忆告警=0；群聊压测（合成 2 种 umo）只出现 1 个群节点、改名生效；群聊出站扫描 0 私密词；同 key 24h 重复推送=0 |
| **P2（2–4 周）** | ① 指标 API + 健康面板；② 日志脱敏/密钥轮换/rich traceback 关闭；③ 生命周期 generation/重载幂等；④ 自动审计脚本 + 质量门 | 日志 sk-=0、API key 轮换完成；reload ×10 后推送/通知计数与基线一致；cross_plugin_check.py 每日绿 |

**回归测试建议**（只读/离线，不连生产）：
- 现有：forever/tests/test_cross_plugin_contract.py、test_reply_pipeline.py、test_data_plugin_disabled.py；data/tests/test_group_name.py、test_spec_data_behavior.py；watch/tests/test_contract_surface.py、test_cross_plugin_contract.py；photo/tests/test_custom_endpoint_security.py、test_scene_lock_fallback.py。
- 新增：test_outbound_gateway.py（后台词/群聊/去重）、test_principal_quota.py（event=None 仍记账/拒绝）、test_life_state_snapshot.py、test_group_normalize_single.py、test_metrics_contract.py、test_log_redaction.py。

---

## 8. 审计局限

1. **群聊/私密词是潜藏项**：生产库 0 条群消息（group_umo_migration.scanned=0），无法用生产流量证明泄漏；本报告只给代码级必然性与压测建议。
2. **权限/配额绕过**：只读审计无法调用插件 API 触发；结论来自 event=None + record_usage 的条件分支（C 级）与面板文件/日志（A 级）的联合证据。
3. **行号**：审计期间工作树被并行修改（10:01–10:39），行号是 10:39 快照；以函数名/文件为准。
4. **消息库**：能陪 messages 11-09 后停写，能记 messages 11-19 12:02 前是 summary；跨库逐条对照只能覆盖 11-19 12:02 后。
5. **WAL**：能陪 4.1MB / 能记 589KB 未 checkpoint，mode=ro 读取的是已提交页；极新的写入可能不在快照，不影响本文结论。
6. **未执行**：未 reload、未跑 API、未写库、未跑生产 pytest；未验证“修复后”行为。

---

## 附录 A：本文关键数字的复现口径

    日志：D:\dafeiyu\astrbot\data\logs\astrbot.log（82,594 行）
      频率：按正则计数（scripts 在 %TEMP%\amo_audit_cross\counts.py）
    DB：sqlite3.connect("file:<path>?mode=ro", uri=True)
      messages：SELECT role,count(*) ...；两库 (role,content) 交集 = 2
      memories：status/scope/group_id/pid 分布；watch memory_index ∩ data memories = 64
    钱包：wallet_ledger 11 月 cash 负流水；按 reason 前缀排除 → 16,016.45；watch.expenses 11 月 = 473.00
    照片：kv photo_count:2026-11-22=2；日志“照片已分享”11-22=2；omnidraw_usage_stats.json date=2026-11-21,total=3
    密钥：日志正则 sk-[A-Za-z0-9]{8,} = 23 行（定格 12 / 旧 omnidraw 11），均为 traceback locals

## 附录 B：审计时点关键文件（mtime 快照）

| 文件 | mtime |
|---|---|
| forever/amo_message.py | 2026-11-22 10:39:18 |
| forever/amo_state_consistency.py | 10:36:47 |
| forever/amo_outbound_guard.py | 10:35:27 |
| forever/amo_outbound.py | 10:38:55 |
| forever/amo_feature_api.py | 10:22:42 |
| forever/amo_reply_pipeline.py | 10:23:42 |
| data/main.py | 2026-11-21 22:42:41 |
| watch/main.py | 2026-11-21 19:08:54 |
| photo/main.py | 2026-11-19 09:25:08 |

> 复核建议：修复落地后重跑本报告 §4 的计数脚本与 §6.7 的 cross_plugin_check.py，对比“后台词出站 / pid=0 记忆 / 面板两值差 / 推送重复 / 日志 sk-”五项即可判定是否收敛。
