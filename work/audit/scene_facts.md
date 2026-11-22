# 场景事实审计：她说的话 vs 当时日程活动/地点（task-8）

- 审计时点：2026-11-22 10:18–10:40（Asia/Shanghai）
- 审计对象：`D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever`（代码只读）、`plugin_data\Astrbot_amoupup_forever\amo_forever.db`（日程/块日记/kv）、`plugin_data\Astrbot_amoupup_data\amo_data.db`（消息/摘要）、`data\logs\astrbot.log`（2026-11-16 07:00 起）、`work\sim`（离线仿真报告）
- 只读声明：两个生产 DB 均以 `sqlite3.connect("file:...?mode=ro", uri=True)` 打开；未改任何生产代码、未 reload、未 commit/push、未写生产库；本报告是唯一落盘文件。
- 限制：
  1. 日志从 2026-11-16 07:00 开始，11-08～11-15 的“她说的话”只能从 `amo_data.db.messages` 里 role=summary 的对话摘要取证（二手证据，已标注“摘要”）；
  2. 审计时点代码已含 2026-11-22 10:14–10:18 的新增闸门（文件 mtime 晚于 09:00 事故，不能排除是事故后热修）；本报告以“审计时点代码 + 事故当时日志”对照，历史事故是否被新代码完全堵住见 §5。
  3. 只读审计无法复现 JIT/过渡状态机的内部时序，个别入口给出“候选入口 + 置信度”，未把推测写成结论。

## 0. 结论摘要（按严重度）

| # | 结论 | 严重度 | 关键证据 |
|---|------|--------|----------|
| 1 | “在家说商场/试衣”是**多发**问题，根因是地点黑名单在家场景**只禁职场/交通词，不禁商场/逛街/试衣/小馆/食堂**；`shopping_prompt` 又不做场景校验 | P0 | 11-22 09:00:31、11-21 21:00:28、11-21 13:40、11-21 12:04、11-22 照片 |
| 2 | **照片场景与文案/事实不一致**：同一条 prompt 内同时出现“地铁车厢/被子”“浴室/客厅”“楼下小馆/家里家具”；跨图文（11-22 照片在家、文案在商场） | P0 | 11-16 08:01、11-17 15:11、11-18 08:01/19:43、11-22 09:00 |
| 3 | **病假/事假事实不是硬约束**：病假当天日记、JIT、过渡状态机仍会写“去公司/食堂/加班”，并与用户对话互相坐实 | P0 | 11-18 12:00、13:40、21:18、23:00；11-13 事假 vs 全天公司日程 |
| 4 | **“唯一事实源”实际有 5 套**：`get_current_action` / `state.current_activity()` / `build_snapshot` / `amo_presence.current_scene` / 约会 Scene 各读各的；日程空档用 `stale_slot` 无限沿用旧块 | P1 | 代码 §4，11-21 21:00 用 14:00 商场解释 |
| 5 | **事实库自身脏**：`block_diary` 同一时刻存在“家”和“公司”两条 status=valid（invalidated_at=0）；日程被 watch/日期源/快照反复改写（12:00 妈妈@楼下小馆→父母家→家） | P1 | 11-18 块日记、11-21 SCHEDULE_DEBUG |
| 6 | **没有出差实例**：16 天数据 0 条“出差/外派”；但生成路径存在且未纳入场景类型/黑名单 | P2 | §3.4 |

## 1. 事实源清单（谁写、谁读、哪里会漂）

| 事实源 | 载体/键 | 写者（入口） | 主要读侧 | 已知漂移 |
|---|---|---|---|---|
| 日程主干 | `schedule` 表（day→items JSON：start/end/activity/place/zone/category/event_id） | `amo_state_schedule`（backbone/jit/tick/transition）、watch `amo_bridge.add_schedule`、日期源、社交事件、面板 | `state.current_activity()`（40+ 处）、快照、生图、presence、日记、主动消息 | 同一小时可被多来源反复覆盖；category/place/activity 语义错位；空档靠 stale_slot 兜 |
| 块日记 | `block_diary`（day/block_id/start/end/place/text/status/invalidated_at/version/event_id） | `amo_state_diary` 按块生成 | 日记面板、每日总结、fact guard、记忆 | 日程替换后旧块未失效（11-18）；同刻双地点；stale 与 valid 混存 |
| 小时日记 | `schedule_diary`（day/hour/text/status） | 小时生成 | 日记/总结 | 有 stale，但无地点字段 |
| 此刻动作 | kv `current_action`（JIT，带 until）、`micro_event*`（微型事件）、`activity_now`（到达态） | `amo_current_action.set_action`、JIT、微型事件、transition | `get_current_action` 少量读者；大部分代码仍读日程 | 与日程优先级相反（micro>jit>schedule） |
| 快照 | `build_snapshot`（内存，标注【当前状态·最高事实】） | 读 `state.snapshot()` | 回复地点守卫、记忆注入、prompt | 名为最高事实，实际仍以日程为源，且与 get_current_action 不同步 |
| 逛街/购物 | kv `shopping_prompt`（line/until）、watch `shop:pending`/`shop:events` | watch `shopping_engine.tick/flush` → `amo.note_shopping_pending` | 聊天 prompt（`amo_runtime_context.py:1124`）、照片绑定 | 注入时不校验场景；11-22 09:00 在家仍注入“在商场试…” |
| 约会 | kv `date_fact/date_ending/date_incident/date_mood_line/date_prep`、`diary_together:*`、`amo_date_session` 状态 | watch date_engine / `amo_date_session` / `date_state` | 回复 guard、prompt、日记、主动消息 | 与日程双轨：11-20 19:09 把“和老公吃饭”写进日程，同一时段已有“柳如烟火锅” |
| 请假 | kv `sick_decision:<day>`、`mood_applied:leave:*`、watch 工资单/记忆 | `amo_human_state.decide_sick_action`、watch `work_engine.apply_leave`（会 add_schedule） | JIT 的 `on_sick_leave`、日记 | 11-13 事假未进日程；11-18 病假被 JIT/过渡覆盖 |
| 摘要/记忆 | `amo_data.db.messages`（role=summary）、`memories`、forever `proactive_log` | data 插件、`amo_memory*`、里程碑 | prompt 注入、记忆检索 | 摘要/里程碑把错场景固化为长期事实，且不经过地点守卫 |
| 日志 | `astrbot.log` 的 `分段回复 ->`/`主动 ->`/`照片已分享`/`SCHEDULE_DEBUG` | 各发送路径 | 人工审计/仿真 | 是“实际说出去的话”和“日程改写原因”的最可靠时间线 |

## 2. 矛盾时间线（证据化）

> 判定口径：以该时刻 `schedule` 的覆盖块（无覆盖则看 `current_activity_info` 的 stale/jit/transit 回退结果，见 `amo_state_diary.py:1089-1147`）为“当时日程/地点”；照片以日志 `照片已分享` 的 prompt 为“照片场景”；消息文本来自 `amo_data.db.messages`（11-19 起为逐条消息，之前为 summary）与日志 `主动 ->`/`分段回复 ->`（11-16 起）。

### 2.1 在商场说在家 / 在家说试衣 / 地点串台

| 时间 | 她说的话（原文/摘要） | 当时日程/地点 | 判定 | 证据/入口 |
|---|---|---|---|---|
| 2026-11-22 09:00:31 | 「这件米白色针织开衫我试了真好看，就是985，我卡里还有4072，你说买不买？」 | 09:00-10:00 在家歇着 @家（log 81845） | **在家说试衣/商场** | log `主动 ->` 09:00:31；kv `shopping_prompt`（09:00:31 写入，until 09:15:18）；watch 逛街 tick → push（§4.2） |
| 2026-11-22 09:16:15 | 「我刚试完那件衣服等你回消息，等了十五分钟，衣服都放回去了。」 | 同上，家 | **在家说试衣** | log `主动 ->` 09:16:15（watch `shopping_engine.flush` 的 pending 追问，只过“沉默闸”，不过地点闸） |
| 2026-11-21 21:00:28 | 「柳如烟试衣服试上瘾了，我腿都站酸了」 | 该时刻无覆盖块；当天 schedule 止于 14:00-17:00 商场，18:00 与 21:00 两次补了 17:00-17:24“刚出商场，打车回家”通勤块（log 78496/79488），stale_slot≈17:00 商场/路上 | **在家说还在商场试衣** | log 21:00:28 `主动 ->`；log 79492 21:00 v14 日程；`_pick_stale`（amo_state_common.py:266-289） |
| 2026-11-21 13:40:06 / 13:40:12 | 「老公你看！我试了这件米白色米白色针织开衫，好看吗？」「我回来啦！今天买了米白色针织开衫，花了649元…」 | 13:30-14:00 空档；12:00-13:30 妈妈回家吃饭 @家；14:00 起才去商场 | **在家说试衣/已买** | log 13:40:06/13:40:12 `主动 ->`（watch 购物 flush，双条连发、颜色重复） |
| 2026-11-21 12:04:55 | 「跟妈妈在小馆等着上菜呢」 | 12:00-13:30「妈妈中午回家吃饭」@家（最终版）；中间版本 v7（09:01:19，watch）写成 @楼下小馆（log 62196），之后变成 @父母家，17:00 再修正为 @家（log 78210） | **日程/话术各说一套（家 vs 小馆）** | log 12:04:55 `主动 ->`；log 62196（v7 @楼下小馆）；log 78210（修正 @家） |
| 2026-11-17 08:02:54 | 「早，我在楼下吃粉呢」「早，我在楼下小桌边吃粉」 | 08:00-09:00「在家煮了碗热粉」@家（病假第 2 天） | **在家说在楼下** | log `主动 ->` 08:02:54；同日 08:22 分段回复 |
| 2026-11-15 09:01（摘要） | 「刚在工位上刷到一个巨好笑的视频」 | 11-15 为周日休息日；08:00-09:00 买包子 @楼下小馆，09:18 热汤面 @家，无工位 | **休息日说在工位**（摘要证据，置信度中） | `amo_data.db.messages` role=summary（[2026-11-15 共5条]） |
| 2026-11-08 11:00（摘要） | 「我刚从公园溜达回来，坐草地上发了好一会儿呆」 | 11:00-12:00「商场里人不少，慢慢逛着」@商场（周日外出日） | **说公园 vs 日程商场**（摘要，置信度中） | summary（[2026-11-08 共8条]） |

### 2.2 通勤说公司 / 公司说路上

| 时间 | 她说的话 | 当时日程/地点 | 判定 | 证据/入口 |
|---|---|---|---|---|
| 2026-11-09 10:20:42 | 「上班路上，刚挤上地铁，手抓着吊环晃得不行」 | 10:00-11:00 跑前跑后临时救火 @公司（work） | **公司说路上** | `proactive_log`（commute）+ 同期 summary |
| 2026-11-09 11:56:48 | 「我这边路上饭点还没到」 | 11:00-12:00 楼下随便扒两口饭 @公司 | **公司说路上** | `proactive_log`（lunch）+ summary |
| 2026-11-10 08:31:43 | 「今天地铁上人特别多…到工位了」 | 08:00-09:00 临时被叫去帮忙开会 @路上（commute） | 路上说公司（到岗时点略早，次级） | `proactive_log`（commute） |
| 2026-11-12 17:49:51（摘要） | 「现在在地铁上，人挤人」 | 17:00-18:00 地铁上听着歌晃回家 @**公司**（category=work, place=公司） | **日程 place 写错导致“路上说公司”** | summary；schedule 11-12 17:00 块 |
| 2026-11-13 10:00:13 | 「我在去商场的路上堵了一会儿」 | 10:00-11:00 开早会，听人讲方案 @公司 | **公司说去商场**；且当天为事假（见 3.2） | `proactive_log[commute]` 10:00:13 |
| 2026-11-13 08:03:03（摘要） | 「刚吃完饺子，还穿着拖鞋呢」 | 08:00-09:00 地铁里塞着耳机听歌 @路上（通勤） | **通勤时段说在家穿拖鞋** | summary |
| 2026-11-13 15:36:54（摘要） | 「下班啦，我往家走呢」 | 15:00-16:00 整理文档 @公司 | **公司说下班回家（早退 2.5h）** | summary；事假事实见 §3.2 |
| 2026-11-16 15:46:53 | 「（在沙发上翻了个身）看完啦，医生说就是普通感冒」 | 15:41-16:07 刚出医院，打车回家 @路上 | **路上说在家沙发** | log `分段回复 ->` 15:46:53；road 黑名单只禁工位/办公室/被窝/床上，不禁沙发/被子（amo_state_snapshot.py:47） |
| 2026-11-18 14:59:47 | 「（在被窝里翻了个身）嗯，睡着没？」 | 14:40-15:00 出门去楼下小馆（在路上）@路上 | **路上说在被窝**（守卫未命中，具体竞态待复现） | log `分段回复 ->` 14:59:47/14:59:59 |
| 2026-11-18 23:00:40 | 「你睡了没，我还在公司磨一份东西，好饿。」 | 当时日程已被 21:18 过渡改成 21:00-23:00 在工位上忙工作 @公司（log 41701）；但**当天 sick_decision=take_sick_leave、日记/每日总结写“请假在家”** | **病假日说在公司**；文本与“当时日程”自洽、与“请假硬事实”冲突 | log 41701（v19 公司）、41695（类别纠偏）、proactive_log 23:00:40；23:27 里程碑“他半夜催我马上回家…骂公司十一点还让人加班”（log 43124） |

### 2.3 照片场景 vs 文案

| 时间 | 照片 prompt 场景（日志） | 同时/同一对话的文案 | 当时日程/事实 | 判定 |
|---|---|---|---|---|
| 11-16 08:01:54 | outfit/自拍：`在真实地铁车厢…（不是沙发、不是床）…` **且** `她正躺在/坐在床上或沙发上，盖着被子` | 「（缩在沙发上，鼻子有点堵）好看什么呀，我还烧着呢。」 | 08:01 尚未做病假决策（sick_decision at 08:41:09），当时日程仍是工作日通勤版（outfit 由 zone=road/“通勤”触发）；09:00:17 才重建出“请假在家”（log 4604） | **同 prompt 内“地铁车厢+被子”互斥；照片场景(地铁通勤)≠文案(在家发烧)；08:56 主动仍说去地铁站（病假决策后 15 分钟）** |
| 11-17 15:11:05 | daily/自拍：`在浴室…画面里只有卫生间本来就有的东西：洗手台、镜子…没有床铺` **且** `背景是她的客厅` **且** `她正躺在/坐在床上或沙发上，盖着被子` | 「水汽糊了镜头…」「我在被子里捂着出汗呢」（15:02/15:20） | 15:08-16:05 吃了药卧床捂汗 @家 | **同一 prompt 三套房间（浴室/客厅/床）；照片与“卧床捂汗”不一致** |
| 11-18 08:01:08 | outfit/自拍：`在真实地铁车厢…` | 「早，我到楼下买早餐了…」（08:11 主动）；08:42「请了半天假」；08:56「刚下楼买早餐呢…去地铁站」 | sick_decision 11-18=take_sick_leave（08:00:46）；08:00-10:00 日程空档 | **病假日照片=通勤地铁；且空档时守卫失去“当时地点”** |
| 11-18 19:43:03 / 19:48:39 | daily/自拍：`在楼下小馆…` **且** `画面里只有家里本来就有的东西：沙发、抱枕、茶几…没有办公桌、车厢或街景` | 19:43 summary：在小馆和沈幼微；「看看脚趾」 | 19:00-20:00 沈幼微 @楼下小馆 | **地点句=小馆，陈设/守卫句=家；图像内容与地点句互斥** |
| 11-22 09:00:53 / 10:01:09 | daily/weekend 自拍：`在家里的客厅，沙发、抱枕和茶几` | 09:00:31「在商场试米白开衫…」 | 09:00-10:00 在家歇着 @家 | **照片(家) vs 文案(商场)** |
| 11-21 09:01:19 / 10:01:43 | daily/weekend 自拍：`在家里的客厅…` | 同日 09:00 后无商场文案（对照） | 09:00-10:00/10:00-11:00 在家歇着 @家 | 一致（对照项） |

### 2.4 约会 / 出差 / 病假 / 事假 / 周末加班

| 场景 | 时间 | 矛盾事实 | 证据 |
|---|---|---|---|
| 约会（与闺蜜双写） | 11-15 19:15-20:31 | 摘要说“我收拾完就出发…我已经出门了…我到楼下了”“火锅吃完这一身味儿，回去得好好洗洗”“到家了…腿都走软了”（和老公约会吃火锅/一起回家）；同一时段日程：19:00-20:00 在家收拾屋子 @家，20:00-20:27 和闺蜜火锅 @楼下小馆，22:00 和闺蜜看电视 @家 | 11-15 summaries；schedule 11-15；kv `date_fact/day=2026-11-15`、`date_ending`(20:13 单人坐锅底)、`date_closed_at`=20:27、`diary_together:2026-11-15=true` |
| 约会（与闺蜜双写） | 11-20 19:09 / 21:00 | 19:09 日期源把 20:00「和老公吃饭 @楼下小馆」写进日程；18:30-20:00 同槽位已有“柳如烟下班吃火锅 @楼下小馆”；21:00 主语校验又改回“和闺蜜吃饭” | log 61645（date 源 v8）、61953（主语校验） |
| 病假 | 11-16 | 病假=全天在家；但 08:01 照片=地铁通勤、08:56 主动=「下楼买早餐…去地铁站」（病假决策 08:41:09 之后仍说去地铁站）、09:00:17 才重建“请假在家休息” | kv `sick_decision:2026-11-16`（at=08:41:09）；log 4604（09:00:17 重建）；`主动 ->` 08:56 |
| 病假 | 11-17 | 病假=在家卧床；08:02 主动=在楼下吃粉；15:11 照片=浴室/客厅混景 | log `主动 ->`；照片 prompt |
| 病假 | 11-18 | 病假=全天在家；实际：12:00「食堂太咸」→12:00:55 自我纠正“在家”；13:40 块日记=到公司上班；21:18 过渡改写 21-23 公司加班；23:00「还在公司」；23:27 里程碑记录“去公司加班”（用户顺着说后固化） | log 24829（12:00 公司食堂）、25273（13:00 去公司）、41695/41701（21:18 公司 v19）、43124（里程碑）；block_diary 13:40-14:40@公司 等 |
| 事假 | 11-13 | kv 记录“事假一天”；但日程仍是上班日（通勤/公司），且 10:00 说去商场、15:36 说下班回家；watch `apply_leave` 本应 `add_schedule(“事假休息（全天）”)` | kv `mood_applied:leave:2026-11-13:事假一天:mood`；schedule 11-13；watch `services/work_engine.py:177-228` → `add_schedule` |
| 出差 | — | 16 天数据中 0 条“出差/外派/差旅”事实（messages、proactive_log、日志、kv 全量检索 0 命中） | 生成路径存在：`domains/schedule/rest_segments.py:14 business_trip_segments`、`schedule_engine.py:16`、`amo_household` 的“他出差” away_state |
| 周末加班 | 11-15（周日） | 摘要“刚在工位上…”（休息日）；11-15 日程全天家/小馆 | summary；schedule 11-15 |

### 2.5 事实库自相矛盾（影响“当时地点”的可信度）

1. **block_diary 同刻双地点且都 valid**：11-18 同时存在 `13:00-14:40@家`、`13:40-14:40@公司`、`21:18-23:00@家`、`21:18-21:23@公司`、`23:06-23:19@公司`、`23:32-23:46@地铁`，`status=valid`、`invalidated_at=0`；而最终 schedule（v25）没有任何公司块。对比 11-21 的旧块被正确标 `stale`（`invalidated_at=1795276740`），说明“日程替换→块日记失效”的机制存在，但 11-18 这条 JIT/过渡路径漏了。→ 谁读 block_diary 当天地点，都会读到两套故事。
2. **日程自身被多源反复改写**：11-21 12:00「妈妈中午回家吃饭」先 @楼下小馆（watch v7，log 62196）→ @父母家（17:00 log 78210 的 old）→ @家（v10+）；11:32 watch 又把 14-17 改成“柳如烟逛商场”（log 62361）；18:00/21:00 两次补同一段 17:00-17:24“刚出商场，打车回家”（log 78496、79488）。→ 同一时刻的地点取决于你读哪个版本。
3. **category/place/activity 语义错位**：11-09 17:00-18:00 work「累瘫在地铁上晃回家」@公司；18:00-19:00 commute「瘫着点外卖不想动」@路上；11-12 17:00-18:00 work「地铁上听着歌晃回家」@公司。→ 这类块让“当时地点”本身不可靠（§2.2 例）。
4. **摘要/里程碑污染长期事实**：11-18 23:27 里程碑把“去公司加班”写成共同记忆；11-19 06:00 每日总结又写“我请假在家歇着”；11-22 06:00 每日总结把 11-21 的商场购物与家里自拍混写。→ 错误场景经“用户顺着说”被固化为长期事实并通过 prompt/记忆回流。
5. **日程空档使用旧块**：`stale_slot` 把“今天最近开始的一段”无限期当当前（`amo_state_common.py:266-289`）。11-21 21:00（空档 4h+）仍用 14:00 商场解释；11-22 11:20-19:00 长空档同理。

## 3. 入口与守卫现状（谁拦得住、谁拦不住）

### 3.1 回复链路（拦得住一部分）
- `_send_to_umo` 唯一出口有 `send.zone_guard`（wired=True，`amo_reply_pipeline.py:1552`）、`decorate.zone_guard`（1537）、`generate.self_location`（1489）、`decorate.self_location`（1513）、`amo_state_consistency.rewrite`（主动消息 1493 调用）。
- 但黑名单**非对称且词表不全**（`amo_state_snapshot.py:42-55`）：
  - `home` 只禁 工位/公司/办公室/写字楼/会议室/单位/地铁/公交/车站/机场/客户/上班/打卡 —— **不禁商场/逛街/试衣/试穿/开衫/小馆/食堂/公园/外面/楼下**（→ 11-22 09:00、11-21 12:04/13:40 全部放行）；
  - `road/office/mall` 只禁 工位/办公室/被窝/床上 —— **不禁沙发/被子/厨房/睡衣/煮面**（→ 11-16 15:46 沙发放行）；
  - `blacklist_hit` 不做否定词剥离，`force_rewrite` 只按词替换。
- 结论：不是“没有守卫”，是**词表按 zone 做成了单向**（防“在家说职场”，没防“在家说外出/在途说居家”），且部分空档/竞态下取不到 zone。

### 3.2 主动推送（push）路径
- `push_to_owner`（`amo_feature_api.py:411-479`）只做 `can_push`（沉默 6h）、免打扰、5 分钟节流；不判地点。`can_proactive`（`amo_presence.py:185-196`）只对 shopping/selfie 类判地点，`greet/share/nudge/commute` 等**不判地点**。
- watch 逛街/试穿：`shopping_engine.tick` 已有 `_can_shop`（`services/shopping_engine.py:41-49,74`，审计时点代码），但 `flush` 的 pending 追问只过 `_can_engage(kind="nudge")`（`196-221`），nudge 不是地点门类 → 11-22 09:16「试完等十五分钟」在家的追问仍可能发。`shopping_prompt` 注入（`amo_runtime_context.py:1124-1126`）只校验 TTL。
- `date_mood_line` 注入（1049-1051）同理，带 until=11-22 22:40 的“他忘了你们的约会”会被注入到无约会日程的日子。

### 3.3 照片链路
- 已有 `presence.photo_binding`（`amo_presence.py:219-246`）和 `can_photo`，`photo_share._share`（`photo_share.py:534-556`）在生图前绑定当前事件，`claim_photo_event` 去重。
- 但：① `bed` 判定用 `any(word in (where+act) for word in ("床","被窝","沙发"))`（`photo_prompt.py:512-529`），而地铁场景文本自带“**不是沙发、不是床**” → 误判 bed，追加“躺在床上盖被子”；② 同一 prompt 里 `where` 来自 `scene_for(activity)`、守卫/房间句来自 `plan.zone`/`housing.room_scene_line`，两套 zone 可以不同（11-18 19:43 小馆 vs 家；11-17 15:11 浴室 vs 客厅）；③ 生图与发送之间有 TTL/异步窗口，plan 时的场景与发送时可能已变（TOCTOU）。
- `photo_engine._candidates`（`photo_engine.py:455-490`）的 outfit 触发条件是“工作日 07:00-09:30 且 zone=road 或 category=上班/通勤”——11-16（照片 08:01 早于 08:41 病假决策）、11-18（JIT 08:00:13 早于病假 08:00:46）都是在病假决策**前**产出“地铁车厢”自拍，随后病假重建，照片不会重拍。

### 3.4 请假/出差/约会的事实约束
- 病假：`sick_decision:<day>` 是唯一记录（`amo_human_state.decide_sick_action`），JIT 会读（`amo_state_jit.py:619`，`domains/schedule/jit_plan.py:127-152` 有 sick/leave 分支），但**transition/共享活动/日期源仍可覆盖日程**（11-18 21:18 v19 改 21-23 公司，log 41701；13:00 补“去公司”通勤 log 25273）。病假决策与首轮 JIT/照片存在竞态：11-18 是 30 秒级（08:00:13 通勤 JIT vs 08:00:46 病假决定），11-16 是 40 分钟级（08:01 照片/通勤 vs 08:41:09 病假决定），09:00 才重建出“请假在家”。
- 事假：watch `work_engine.apply_leave`（`services/work_engine.py:177-228`）会 `finance.add_schedule(day,"事假休息（全天）")`，但 11-13 的 schedule 里找不到该块（当天仍是上班日）→ 请假事实停留在工资/情绪/记忆，没成为日程硬约束。
- 出差：只有“他出差”的 away_state 与生成骨架；**没有“她出差/外派”的一等场景类型**，place 会是“城市·客户公司/酒店”这类组合地点，现有 zone 映射/黑名单/照片房间规则未覆盖（风险项，无实例）。
- 约会：`amo_date_session`（together）与日程的 companion 双轨，11-15/11-20 已出现同槽位“老公 vs 闺蜜”。`date_state.prompt_line` 的“刚和老公一起…一直在一起”可覆盖“现在在家”的日程事实。

### 3.5 摘要/记忆/日记
- `amo_data.db.messages` 的 summary 由数据插件生成，`_guard_zone_reply` 管不到；块日记/每日总结/里程碑写入时没有“场景版本”字段，日程替换后也不会回滚。`amo_memory_bridge` 有 blacklist 过滤（`amo_memory_bridge.py:134/172`），但只过滤注入，不追溯已写事实。

## 4. “统一场景源”方案（建议，不在本次修改）

### 4.1 数据模型：SceneLedger / SceneRecord
新增唯一场景账本（或把 `amo_current_action` 扩成账本），每条记录：

```json
{"scene_id":"sc_...","day":"2026-11-22","start":"09:00","end":"10:00","ts":...,
 "place":"家","zone":"home","activity":"在家歇着","category":"rest",
 "event_id":"ev_...","companion":"","together":false,
 "source":"schedule|jit|transit|date|leave|trip|manual","version":4,
 "quality":"covering|transition|continuation|unknown","constraints":["sick_leave:2026-11-18"]}
```

- 只允许一个写入口 `scene_ledger.write(event)`：先做**硬约束校验**（leave/trip/date/生病 → 允许的地点集合），再落库并 bump `scene_version`；写审计（before/after/reason/source）——现有 `schedule_edit_audit` 的经验直接复用。
- 只允许一个读入口 `scene_at(now) -> (scene, quality)`；其它模块（回复 prompt、snapshot、presence、photo plan、wardrobe、shopping/date 注入、日记、摘要、记忆）全部改成读它。

### 4.2 场景优先级（替换现在的多套规则）
1. 显式硬约束：`take_sick_leave` → 家/医院；`trip` → 出差城市；`date together` → 与老公同场景；`manual_override`。
2. 覆盖当前分钟的日程块（有 event_id）。
3. 正在进行的 transition/transit（带目的地、ETA、有效期）。
4. JIT `current_action`（带 until）。
5. 微型事件动作（带 until）——只允许覆盖“动作”，不允许覆盖 zone。
6. 延续：同一 intent（睡觉/上班/通勤）且距上一块结束 ≤30 分钟才可 continuation；否则 `quality=unknown`，**禁止出现具体地点词**，prompt 用“这会儿没细想在哪/在忙别的事”。
7. 小时兜底只在“今天+昨天都没有日程”时使用，并标低置信。

### 4.3 不变量（写成单测/审计脚本）
1. 同一分钟最多一个 canonical scene；其它来源必须是派生（derived_from=scene_id）。
2. 病假/事假/出差/约会期间，任何写者不得把 place 写到允许集合之外（JIT、过渡、共享活动、日期源、面板都受约束）。
3. 每个 block_diary 行带 `scene_id + scene_version`；`scene_version` 变化时由**同一个失效函数**把旧行标 `stale`（11-18 的双地点就是反例）。
4. 任何“说出去的话”（chat/主动/push/照片文案/摘要/记忆）在发送/写入前都过 `scene_guard(text, scene)`：对称黑名单 + 否定词剥离 + 过去时放行 + 未知质量时禁地点断言。
5. 照片：`plan` 时记录 `scene_id/version`，发送前复核；prompt 必须“单 zone”（禁止出现第二个 zone 的守卫句/房间句）；bed/陈设判断先剥离“不是…”。
6. `shopping_prompt/date_mood_line` 等注入事实必须带 `scene_id`，场景切换即失效；注入前校验与当前 zone 匹配。
7. 一份“场景词表”唯一来源（现在 `amo_state_snapshot.ZONE_BLACKLIST`、`photo_scene.PLACE_HINTS`、`domains/photo/*`、`amo_presence.SHOP_WORDS/HOME_WORDS` 各有一份）。

### 4.4 落地步骤（建议顺序，先审计后改）
1. **先加离线审计器**：`work/sim/scene_checks.py`，直接读生产 DB（mode=ro）与日志，按本报告口径扫描“每条消息/主动/照片 prompt vs 当时 scene”，输出矛盾清单/计数；先跑历史 16 天，基线即本报告 20+ 条。
2. **只读构建 scene 视图**：用 `schedule + block_diary + kv(jit/transit/date/leave) + log` 生成 7 天 scene 账本（不改写），验证覆盖与回放能力；把 `stale_slot` 换成 `quality` 判定。
3. **对称化黑名单**：以“home ↔ office/mall/road/restaurant/hospital/scenic”互斥表替换现有单向词表；补 小馆/食堂/商场/逛街/试衣/开衫/沙发/被子/厨房/睡衣/煮面/楼下 等；加否定词剥离。
4. **入口收口**：push_to_owner、shopping/date 注入、摘要/记忆写入、照片 prompt 全部接入 `scene_guard`；`shopping_prompt/date_mood_line` 加 scene 校验。
5. **硬约束**：leave/trip/date 作为约束进 `scene_ledger.write`；JIT/transition 在其允许集合内行事；病假决策前移（先 decide 再 JIT/照片，消除 30 秒竞态）。
6. **失效链**：block_diary/块日记/每日总结/里程碑统一带 scene_version，版本变更统一失效重算。
7. **门禁**：把 §4.3 不变量写进 pytest；`work/sim/daily_report` 增加 push/照片/摘要路径的“场景一致”指标；回归用例直接用本报告的证据时间点（11-22 09:00、11-21 21:00、11-21 12:04、11-20 19:09、11-18 12:00/13:40/21:18/23:00、11-17 08:02/15:11、11-16 08:01/15:46、11-13 10:00/15:36、11-15 09:01、11-09 10:20/11:56）。

### 4.5 验收标准
- 历史 16 天回放：§2 的 P0/P1 矛盾在“允许的过期/过去时”解释后应为 0；summary/照片 prompt 另有专项 0 内部冲突。
- 7 天仿真（work/sim）：`place_conflict + stale_scene + photo_prompt_conflict + push_scene_conflict` 全 0；现有 `daily_report` 的无矛盾率保持 ≥96% 且不再依赖“只测回复链路”。
- 每个外部出口（chat/主动/push/照片/摘要/记忆）都有 scene_id + version 可追溯。

## 5. 审计时点代码的热修核查（结论：方向对，仍有洞）

- 审计时点 `amo_presence.py`（mtime 10:15:51）、`amo_feature_api.py`（10:18:28）、`amo_message.py`（10:16:51）、watch `shopping_engine.py` 已含 `can_shop/can_photo/photo_binding/shopping_pending_offsite` 等闸门，注释直接引用“2026-11-22 真实飞书记录 09:00”。这说明 09:00 事故后已经补过一圈。
- 但按 §3，以下洞仍在：
  1. home 黑名单不含商场/小馆/食堂/试衣 → 即使 `can_shop` 拦了 watch tick，`shopping_prompt` 注入/其它 push 仍可让聊天说出商场；
  2. nudge/试穿追问（kind=nudge）不过地点闸；
  3. `date_mood_line`、`episodic_memory` 里旧的错误场景仍会被注入；
  4. 病假/事假不是硬约束，JIT/过渡可覆盖；
  5. 空档 stale_slot 与照片 prompt 的两套 zone 未收口。
- 建议：用 §4.5 的回归用例在“审计时点代码”上跑 work/sim（只读/仿真，不碰生产），验证 11-22 09:00、11-21 21:00 两类是否真的不再复现；不要仅凭 mtime/注释判断已修复。

## 6. 现有自动化检查覆盖（work/sim + tests）

| 检查 | 位置 | 覆盖 | 不覆盖 |
|---|---|---|---|
| 聊天回复地点/动作一致 | `work/sim/daily_checks.py`、`daily_report.md`（48/50 无矛盾=96%，P1 问句兜底地点错 7 轮、P2 narrative 旧场景） | 回复链路的地点黑名单/旧场景/问句兜底 | 主动 push、照片 prompt、summary、记忆 |
| 分享链路地点 | `work/sim/share_sim.py:130 check_place`、`share_report.md` | 在家说路上/外出说沙发等 | 同上 |
| 日程地点漂移/过渡块 | `work/sim/schedule_sim.py`、`schedule_report.md`（地点跳变、通勤块） | 日程生成侧 | 运行期多源改写 |
| 日记地点断言 | `work/sim/diary_checks.py`（地点词表、块 zone 证据、强动作、transition） | 日记↔当天块 | 摘要/里程碑/记忆回流 |
| 单元测试 | `tests/test_place_guard.py`、`test_state_snapshot.py`、`test_presence_*.py`、`test_photo_event_binding.py`、`test_consistency_audit.py` | 守卫单点 | 端到端时间线、push/photo/summary 组合 |

## 7. 建议的最小修复优先级（不改代码，仅排序）

1. **P0** 对称黑名单 + 否定词剥离（`amo_state_snapshot.py:42-55`），补 home/road/office/mall 的反向词表。
2. **P0** `shopping_prompt/date_mood_line` 注入前场景校验（`amo_runtime_context.py:1049-1051,1124-1126`）。
3. **P0** 病假/事假硬约束进 JIT/过渡/日期源；病假决策先于 08:00 JIT/照片（`amo_state_jit.py:619`、`domains/schedule/jit_plan.py:127-152`）。
4. **P0** 照片 prompt 单 zone + 否定词剥离 + plan/send 复核（`photo_prompt.py:512-529`）。
5. **P1** 空档 quality=unknown（替换 `_pick_stale`，`amo_state_common.py:266-289`）。
6. **P1** block_diary/摘要/记忆场景版本与失效链（11-18 双地点回归）。
7. **P1** push（`push_to_owner`）与 nudge 追问接地点闸。
8. **P2** 出差/旅行场景类型与黑名单、照片房间/城市规则。

## 8. 附录：只读取证与复现

- 只读打开：
  ```python
  sqlite3.connect("file:D:/dafeiyu/astrbot/data/plugin_data/Astrbot_amoupup_forever/amo_forever.db?mode=ro", uri=True)
  sqlite3.connect("file:D:/dafeiyu/astrbot/data/plugin_data/Astrbot_amoupup_data/amo_data.db?mode=ro", uri=True)
  ```
- 日程/块日记：`select day,items from schedule`；`select day,start,end,place,status,invalidated_at,version,event_id,text from block_diary`
- 此刻/待办/注入事实：`select key,value from kv where key in ('current_action','activity_now','shopping_prompt','date_fact','date_incident','date_ending','date_mood_line','sick_decision:2026-11-18','mood_applied:leave:2026-11-13:事假一天:mood','schedule_audit:2026-11-21','schedule_edit_audit')`
- 消息：`select * from messages where created_at between ...`（role=summary 即摘要）；forever 侧用户消息另有 `proactive_log`
- 日志关键 grep：`主动 ->`、`分段回复 ->`、`照片已分享`、`SCHEDULE_DEBUG`、`跨地点补通勤块`、`病假`、`过渡`、`已分享（`
- 本次审计的证据快照（临时目录，非仓库）：`C:\Users\123\AppData\Local\Temp\amo_audit\`（corpus.json / sched.json / kv_all.json / evidence1-2.txt / summaries_place.txt / place_cands.json / amolog.json）

## 9. 未验证项 / 风险

- 只读审计无法复现 JIT/transition 的精确触发链；11-18 21:18 “去公司”的触发源只定位到 transit（log 41695/41701），上游用户消息在 summary 里被压缩，无法还原完整因果（建议后续用同一时点 DB 快照 + 日志回放）。
- watch 插件（`Astrbot_amoupup_watch`）不在本仓库；它的 `date_engine/daily_engine/shopping_engine` 是事实写者，本次只按日志/DB 证据定位，未逐行审计其内部场景来源。
- 11-08～11-15 的“她说的话”来自摘要，可能经过压缩/改写，置信度低于 11-16 起的日志原文。
- 审计时点仓库有 10:14–10:18 的新增改动（无 git 可用，无法确认提交/未提交）；若这批改动已上线，§5 的洞需要在同一版本上重跑回归确认。
