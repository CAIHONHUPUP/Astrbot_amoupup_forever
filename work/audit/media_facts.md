# 照片 / 穿搭 / 语音 / 媒体一致性审计（task-13，只读）

- 审计时点：2026-11-22 10:39–10:56（Asia/Shanghai）
- 审计对象：D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever（代码只读）、plugin_data\Astrbot_amoupup_forever（amo_forever.db / wardrobe.json / poses.json / photo_cache / outfit_refs）、plugin_data\Astrbot_amoupup_data（amo_data.db：messages / memories）、plugin_data\Astrbot_amoupup_watch（social.db：shop 状态）、data\logs\astrbot.log（2026-11-16 06:57 起）
- 只读声明：两个生产 DB 均以 sqlite3 打开 mode=ro；未 reload、未 commit/push、未写生产库、未改生产代码；本报告和 work\audit\_scratch 下的取证脚本是唯一落盘内容。
- 限制：
  1. 仓库有多个修复任务在**并发热修**（审计期间 photo_share.py / photo_prompt.py / draw_engine.py / amo_message.py / amo_state_consistency.py 的 mtime 从 10:41 到 10:47 仍在变）。本文行号以审计时点工作树为准，代码入口以「文件:函数」为准；历史事故是否被热修堵住见 §6。
  2. 11-16 之前的聊天记录在 amo_data.db 里已压缩成 summary（二手证据），逐条消息从 11-19 起；照片/语音的“真实发送”以日志（照片已分享 / Tool generate_selfie Result / Prepare to send Record）为准。
  3. 无 git 可用（git 不在 PATH），无法确认当前工作树相对线上版本改了哪些行。
  4. 本任务与 task-8（场景事实）有重叠：照片“场景 vs 文案”的完整时间线见 work\audit\scene_facts.md §2.3；本文只补媒体链路（记录/去重/语音/衣橱/失败话术）侧的证据与统一规则。

## 0. 结论摘要（按严重度）

| # | 结论 | 严重度 | 关键证据 |
|---|------|--------|----------|
| 1 | 照片不是一条流水线，是**三条互不知情的链路**：自动分享（photo_share）、模型工具 send_photo（draw_engine）、模型直接调生图插件 generate_selfie。后两条**不写媒体记录、不占每日配额、不走事件去重、不写 memory**，于是“发过照片”在聊天/记忆里查不到、同一天可远超 cap | P0 | 11-16~11-22 自动分享 7 张（7/7 有 [我发的照片]），工具链路成功 25 张（0/25 有记录）；11-19 一天工具成功 10 张而 photo_count 缺失（cap=4） |
| 2 | 自动照片的 prompt **同一条内场景/姿势互斥**：地铁车厢 + “躺在/坐在床上盖被子”、浴室 + 客厅家具 + 卧床、客厅 + “单手撑床/正坐床上/肩倒立”。否定词“不是沙发、不是床”被当成床场景（11-22 已热修 _bed_like），但姿势库/房间/守卫仍是三套来源 | P0 | 11-16 08:01、11-17 15:11、11-18 08:01、11-18 19:43、11-21 09:01、11-21 10:01、11-22 09:00、11-22 10:01 的“照片已分享”prompt 原文；日志 1478/19872/23014/62195/62211/81941/82555 |
| 3 | 姿势库被批量导入成 **300 条**（MAX_POSES=60 形同虚设），含“挤胸/性感·大胆/特殊：肩倒立·V字打开”等成人向/杂技姿势；家里随机池 245 条，客厅照可随机到床姿/倒立，既有内容政策风险也直接造成“图文不符” | P0 | poses.json：poses=300，selfie=245/couple=55；11-22 09:00 日志“候选 245 个 / 姿势选择：肩倒立・V字打开”；11-17 15:20 生图因内容政策失败；11-18 有 2 次 policy fallback |
| 4 | [我发的照片] 占位符不只是“进记忆”，**10:32:47 被当正文真发到飞书**：一条回复拆 2 段，第一段含“（笑）[我发的照片] 自拍 老公，我在商场试了件米白色针织开衫，”。根因是媒体记录被当可复读上下文 + 分段前无 strip | P0 | astrbot.log 82589-82593 原文；amo_data.db messages id=2287；同分钟 10:32:09 还有一条 watch 试穿 push（人其实在家） |
| 5 | 语音与落库文字**对不上**：按需语音有 4 次（有 Record 无文字），核心随机 TTS 11-21 12:31 把动作“（夹了一筷子菜，抬头笑了一下）”也念了、并拆成 3 条语音，但 amo_data.db 只落了一条纯文本、无 [我发的语音]；11-21 15:18 动作文字和语音同发，DB 只记语音标记，动作文字丢失；TTS 动作过滤守卫 0 次接管日志 | P1 | 日志 62401/62549/65599、TTS request 62452/62543/62546；messages 2215/2235；bot_media 7 条语音标记 vs 真实语音 5+ 次 |
| 6 | 衣橱/购买/试穿**没有事务边界**：11-21 13:40 说“买了米白色针织开衫，花了 649 元”，账本无这笔、wardrobe.json 无这件；11-22 09:00 又回到“985 元买不买”，watch 的 home 事件（due 11-22 12:00:18）还会再推一次“买了”；shopping 文案“米白色米白色”重复，985/4072 精确数字进了聊天 | P1 | messages 2220/2221/2279/2285；wallet_ledger 无 649/985；wardrobe.json items=147 无该单品；social.db meta shop:events；watch shopping_engine.tick/flush/handle_reply |
| 7 | 失败通知三套、且**把底层错误直接喂给模型**：模型自己复述“服务器卡住了/同一个报错三回了/网络连接超时/软件跟我较劲”，11-18~11-19 共 6 条技术性话术进私聊；photo_share 的温柔失败池几乎不出现，draw_engine 的固定话术也不入 messages 表 | P1 | 日志 29872/34550（failure_notice）、30239/31157、48642/49051/46755；messages 2003/2014/2021；messages 2240 summary |
| 8 | 衣橱有两套“今天穿什么”：面板/计划 today() 是卡其风衣+半裙+乐福鞋，实际在家的 current() 是 T恤+居家裤（照片和 amo_outfit.json 用的是后者），用户问“看看穿搭”时两边会各说一套 | P2 | wardrobe.json daily 11-22；amo_outfit.json text=“洗得发白的宽松大T恤、米色居家长裤、居家棉拖鞋”；11-22 两张照片 prompt 同 |
| 9 | 重复图/同类型刷屏是**发送层无签名去重**：11-19 6 小时 10 张（6 张同一“工位桌下脚趾”）、11-21 21:23+21:25 两张比基尼、11-22 09:00+10:01 同穿搭同场景两张、11-18 19:43+19:48 两张同 prompt 测试图 | P2 | 日志 tool_ok 时间线；photo_cache 26 个文件哈希全不同（不是同文件重发，是同场景重复生成） |

## 1. 数据源、口径、证据分级

### 1.1 只读数据源与取证方式

| 数据源 | 用途 | 只读打开 |
|---|---|---|
| amo_data.db.messages | 她说的话（11-19 起逐条；之前 summary）、媒体标记 | sqlite3.connect("file:...amo_data.db?mode=ro", uri=True) |
| amo_data.db.memories | 长期记忆里的 bot_media（17 条） | 同上 |
| amo_forever.db.kv | photo_count / photo_roll / photo_outfit / shopping_prompt / photo_last_share / draw_cache | mode=ro |
| amo_forever.db.schedule | 当时日程块（place/activity/zone/category） | mode=ro |
| wardrobe.json | items/outfits/daily（今天计划穿什么、实际穿什么） | 按 UTF-8 读，不写 |
| poses.json | 姿势库（300 条，含 usage/note） | 按 UTF-8 读，不写 |
| plugin_data\...\photo_cache | 实际生成的图片文件（26 个，md5 全不同） | 只读列目录+哈希 |
| Astrbot_amoupup_photo\amo_outfit.json | 给生图插件的今日着装 sidecar | 只读 |
| social.db (watch) | shop:pending / shop:events / shopping:<day> | mode=ro |
| astrbot.log | 照片已分享 / Tool generate_selfie Result / 分段回复 -> / 主动 -> / TTS request|result / PHOTO_DEBUG | 只读逐行扫 |

### 1.2 关键口径

- **真实发图**：日志出现“照片已分享（…，自拍/街景）”= 自动分享已发送；日志“Tool generate_selfie Result: 系统提示：已成功生成并下发了 1 张图”= 工具链路已发送；日志“Prepare to send - … [ComponentType.Record]”= 语音已发送。
- **媒体记录**：messages 表里以 [我发的照片]/[我发的语音] 开头的行，或 memories.kind=bot_media。只有自动分享与按需语音会写；工具链路和核心随机 TTS 不写。
- **图文不符**：同一时刻（±2 分钟）的照片 prompt 场景 vs 聊天文案/日程；或同一条 prompt 内出现两个互斥 zone/姿势。
- 频率表的分母是“审计窗口 11-16 07:00–11-22 10:56”，11-13~11-15 只用于 marker/记忆对照。

## 2. 总频率表

| 指标 | 数值 | 说明 |
|---|---|---|
| 自动分享成功（照片已分享） | 9 次（11-16:1、11-17:1、11-18:1 自动+2 测试、11-21:2、11-22:2） | 全走 photo_share，7 条日常 + 2 条测试 |
| 工具链路 generate_selfie 尝试 / 成功 / 画图失败 / 超时 | 38 / 25 / 10 / 3 | 11-19 最多：10 成功、6 失败、1 超时 |
| 实际照片发送合计（11-16~11-22，不含测试） | 32 张（自动 7 + 工具 25） | 其中只有 7 张有 [我发的照片] 记录 → **记录覆盖率 21.9%** |
| 自动分享 prompt 含互斥场景/姿势 | 7/7 | 见 §3.1 |
| 用户明确要照片/自拍/比基尼/脚 | 24 条（11-19~11-22 逐条消息） | 工具链路成功 25 次，失败 13 次；3 个失败回合用户当场追问“拍不出来吗” |
| [我发的照片] 记录 | messages 行 8 条；memories 10 条（11-13~11-22） | 只覆盖自动分享；工具链路 0 条 |
| [我发的语音] 记录 | memories 7 条（11-13 3、11-20 2、11-21 2） | 按需语音有记录；核心随机 TTS 无记录 |
| 核心随机 TTS | 11-21 12:31 一回合 3 个 TTS request / 3 个 Record | 动作也被念；DB 只有一条纯文本 |
| 按需语音 | 11-20 18:10/18:34、11-21 11:39/15:18（4 次） | 11-20 17:55 一次“没有 TTS 供应商，回退文字” |
| 失败话术泄漏（服务器/报错/超时/软件） | 6 条 | 11-18 18:24/18:35、11-19 12:49/13:03/13:12 + 11-18 18:23 summary |
| 内容政策失败 / policy fallback | 1 / 2 | 11-17 15:20 工具失败（内容政策）；11-18 19:35/19:36 fallback |
| 购买声称 vs 账本/衣柜 | 11-21 13:40 声称买 649 元；账本 0、衣柜 0 | 11-22 09:00 又回到 985 元决策；12:00 还有一条 home 事件会再声称“买了” |
| photo_count（自动分享计数） | 11-16:1、11-17:1、11-18:1、11-21:2、11-22:2；11-19/11-20 缺失 | 工具链路 12 张成功完全不计数 |

## 3. 逐项审计

### 3.1 照片地点 / 场景 / 穿搭 / 姿势 vs 日程

#### 3.1.1 时间线样例（prompt 原文摘自日志“照片已分享”）

| 时间 | 类别 | 照片 prompt 里的场景 | 穿搭 | 姿势 | prompt 内互斥 | 当时日程 |
|---|---|---|---|---|---|---|
| 11-16 08:01:54 | outfit/自拍 | “在真实地铁车厢……（不是沙发、不是床）……站在车厢里，一只手扶吊环” | 白色荷叶边上衣、白棉麻长裤、黑色小皮鞋 | 站车厢扶吊环 | **又写“她正躺在/坐在床上或沙发上，脚上没有穿鞋……脚盖在被子里”+“画面里绝对不要出现鞋”；同时“不要躺下/不要坐在座位上”** | 07:46-08:00 睡@家、08:00-09:00 吃@家；病假决策 08:41 才发生（见 scene_facts §2.4） |
| 11-17 15:11:05 | daily/自拍 | “在浴室，生活里的日常陈设和暖光……站在洗手台前” | 纯棉长袖、灰运动短裤、软底室内拖鞋 | 站洗手台 | **同 prompt 三套房间：浴室（洗手台/毛巾/瓷砖）+ 客厅（二手布艺沙发/茶几/晾衣架）+ 卧床捂汗** | 15:08-16:05 吃药卧床捂汗@家 |
| 11-18 08:01:08 | outfit/自拍 | “在真实地铁车厢……站在车厢里，一只手扶吊环” | 浅灰针织长裙、裸色高跟鞋 | 站车厢 | **又写“她正躺在/坐在床上或沙发上，盖着被子；画面里绝对不要出现鞋”** | 病假；08:00-10:00 日程空档（sick_decision 08:00:46） |
| 11-18 19:43:03 / 19:48:39（测试图×2） | daily/自拍 | “在楼下小馆……” | 浅灰针织长裙、裸色高跟鞋 | “双脚交叉坐（坐在床上……）” | **地点句=小馆，陈设/守卫句=家里客厅；姿势=床上**；两张相隔 5 分钟同 prompt 同场景 | 19:00-20:00 沈幼微@楼下小馆 |
| 11-21 09:01:19 | daily/自拍 | “在家里的客厅，沙发、抱枕和茶几” | 纯棉长袖、米色居家长裤 | 姿势：**单手撑床（侧坐在床上……）** | 客厅 + 床姿 + “她正躺在/坐在床上或沙发上，盖着被子” | 09:00-10:00 在家歇着@家 |
| 11-21 10:01:43 | weekend/自拍 | “在家里的客厅……” | 同上 | 姿势：**正坐看手机（正坐在床上……）** | 客厅 + 床姿 + 盖被 | 10:00-11:00 在家歇着@家 |
| 11-22 09:00:53 | daily/自拍 | “在家里的客厅……” | 洗得发白的宽松大T恤、米色居家长裤 | 姿势：**肩倒立・V字打开（特殊），第三人称虚空相机全身**；日志“候选 245 个” | 客厅 + 倒立 + 虚空全身 + 盖被 | 09:00-10:00 在家歇着@家 |
| 11-22 10:01:09 | weekend/自拍 | “在家里的客厅……” | 同上 | 姿势：**悬空后倚（全身·坐姿），第三人称虚空相机** | 客厅 + 悬空 + 盖被 | 10:00-11:00 在家歇着@家 |

#### 3.1.2 频率

- 窗口内自动分享 7 条日常 prompt：**7/7 含“场景/姿势互斥或与日程不符”**；2 条测试图同样互斥。
- 问题分三类：
  1. **否定词误判床场景**（11-16、11-18 08:01）：地铁场景文本里的“不是沙发、不是床”命中 BED_WORDS，追加“躺床盖被+不要鞋”。审计时点 photo_prompt._bed_like 已用 strip_negated 修（2026-11-22 热修）。
  2. **地点/房间多源**（11-17、11-18 19:43）：where 来自 scene_for(activity) 或调用方 scene，守卫/房间句来自 plan.scene_key + housing.room_scene_line；两套 zone 可不同（浴室 vs 客厅 vs 小馆 vs 家）。审计时点已加 _reconcile_where/_finalize_prompt_zone（单 zone sanitize），但仍是“事后清洗”，不是单一来源。
  3. **姿势与房间不匹配**（11-21、11-22）：home_generic/home_sofa 的 allow_library_pose=true，random_pose 在 home 池里 245 条全放行；姿势库条目是床姿/杂技/性感系，客厅照会随机到“单手撑床/肩倒立/悬空后倚”。
- 穿搭：自动照片读 wardrobe.current()（在家时 home_outfit），与照片一致（11-22 T恤/居家裤）；但面板 today() 是 11-22 卡其风衣+半裙+乐福鞋（见 §3.6），两套视图不同。

#### 3.1.3 代码入口（审计时点工作树，函数为准）

| 入口 | 职责 | 风险点 |
|---|---|---|
| photo_engine.PhotoEngine._candidates() | 按时间/zone 掷骰决定 outfit/travel/shopping/weekend/daily；返回 scene 恒为空串 | 类别与地点由同一时刻读两遍（activity 缓存按分钟） |
| photo_share.PhotoShareMixin._share() | 绑事件 → build_plan → 选自拍/姿势 → 拼 prompt → 生成 → claim_photo_event → 发送 → note_sent/remember | plan 时场景与发送时场景是两次读；marker 在发送成功后才写 |
| photo_prompt.PhotoEngine._selfie_prompt_for() | 拼自拍 prompt：where/motion/camera/light/bed/outfit/guard/room/state | where 来自 scene_for，guard/room 来自 plan/housing，state_hint 来自 activity/health —— 四个来源没有一致性断言 |
| photo_prompt._bed_like() | 判床/沙发场景（11-22 已 strip_negated） | 热修前用裸关键词，制造 11-16/11-18 地铁+被子 |
| photo_prompt._reconcile_where() / _finalize_prompt_zone() | where 服从 plan zone、prompt 单 zone sanitize（11-22 新增） | 只对“地点”做单 zone，不含姿势/床句/陈设句；同一条里仍可出现床姿+客厅 |
| photo_plan.build_plan() / pick_pose_for_plan() | 日程→PhotoPlan（scene_key/pose/guard）；居家场景允许姿势库 | allow_library_pose=true 的 home 场景直接吃 300 条姿势库 |
| photo_scene.scene_for() / fitting_room_scene() | zone/place/activity→中文场景；逛街 pending 强制试衣间 | 按活动词细分 home_bed/bath/sofa；与 plan.scene_key（按 category）可能不同源 |
| pose_library.PoseLibrary.random_pose() / is_private_pose() | 按 zone 过滤姿势 | 只过滤含“床/被窝/枕/跪/抬腿/翘臀/蹲坐”的；性感/挤胸/杂技条目全部放行；home 池 245 |
| photo_data.selfie_state_hint() | 生病/洗完澡/睡衣/躺床 细节 | 只按 activity 词，不校验房间；“躺床”会拼进浴室/客厅 prompt |
| photo_room.room_for_plan() + housing.room_scene_line() | 家里分房间参考图/家具句 | 与 where 不同源：where=浴室时仍可追加客厅家具 |
| photo_engine.outfit_today() / photo_prompt._selfie_outfit_text() | 今日穿搭 / 床景去鞋 | 与 wardrobe.today()（面板计划穿搭）不是同一个视图 |

#### 3.1.4 根因

1. 照片 prompt 由 4 个来源拼装：scene_for(activity)（文本）、plan.scene_key（category/zone）、housing.room_scene_line（房间）、selfie_state_hint（health/activity 词）。四者没有“同一 scene_id”约束，任何两个都能互相打架。
2. 姿势是独立第三来源：pick_pose_for_plan 只看 scene_key 是否 allow_library_pose，不看“这是客厅/浴室/户外”，也不看姿势的私密/杂技属性。
3. 否定句/否定词没有统一剥离（11-22 才给 bed 加 strip_negated；zone/服装/姿势句没有同样的处理）。
4. 生成异步、发送前没有“场景版本复核”：plan 在 09:00:00 定型，09:00:53 才发送；期间日程/JIT 可能已变（TOCTOU）。
5. 记录侧不保留机器可读场景：marker 只有“[我发的照片] 自拍”，没有 scene_id/zone/place/outfit/pose，事后审计只能靠日志。

### 3.2 重复图 / 同类型刷屏 / 每日配额

#### 3.2.1 时间线

| 时间 | 现象 | 证据 |
|---|---|---|
| 11-19 09:16–13:44 | 6 小时内工具成功 3 张、失败 8 次，全是“工位桌下脚趾”同一构图 | tool_ok 09:16/12:08/12:39/13:44；tool_fail 12:49×2/12:51/13:03/13:12/13:37；timeout 12:36 |
| 11-19 17:51–20:40 | 4 小时内 7 张（比基尼/脚趾），其中 19:58/20:01/20:05 三张 7 分钟内连发 | tool_ok 17:51/17:57/19:58/20:01/20:05/20:40；用户 17:52/17:56/19:57/19:59 连续点单 |
| 11-21 21:23:11 / 21:25:01 | 2 张比基尼照相隔 110 秒，均由同一次“穿黑色比基尼和丝袜给我拍一张”触发 | 日志 79598/79631；messages 2255 |
| 11-22 09:00:53 / 10:01:09 | 同一天同穿搭（T恤/居家裤）同场景（客厅）两张，间隔 60 分钟；仅因 daily+weekend 两个候选各掷中一次 | 日志 81941/82555；photo_roll 11-22 daily/weekend 各 1 |
| 11-18 19:43:03 / 19:48:39 | 两张“测试图”相隔 5 分钟，prompt 完全相同（小馆+浅灰长裙+双脚交叉坐） | 日志 34300/34711；photo_share 测试图不占配额、不写记忆 |
| 11-16 12:19 / 18:12 / 21:39 | 同一天 3 张病中沙发自拍（不同 prompt 细节，同场景） | tool_ok 三连；summary 11-16 |
| 11-17 15:11 / 15:20 / 15:43 / 20:28 / 23:15 | 同日 1 张自动 + 4 张工具，全部“洗完澡/沙发/浴室门口”同类 | 日志 19872/20285/20384/21071/21892 |

- photo_cache 26 个文件 md5 全不同：**不是同文件重发**，是“同场景重复生成”。
- 未发现 resend_last 触发的“原图重发”（日志无“他要重发”）；该能力存在但窗口内 0 次。

#### 3.2.2 计数与配额失真

- photo_count 只由 photo_share._bump_count() 维护（自动分享），测试图不计数；工具链路（send_photo / generate_selfie）从不计数。
- 结果：11-19 实际成功 10 张、11-20 成功 2 张，但 photo_count:2026-11-19 / 11-20 键不存在；11-19 单日 10 张是 cap=4 的 2.5 倍。
- claim_photo_event 只对 photo_share 的 event_id 生效；工具链路没有 event 概念，同一 event 可发多张。
- 11-18 的 2 张测试图不占配额、不写记忆，但用户端就是收到了两张重复图。

#### 3.2.3 代码入口

- photo_share._run_share/_share：cap、event claim、note_sent 都在这一条链路。
- photo_engine.PhotoEngine._bump_count/today_count：只服务自动分享。
- draw_engine.DrawEngine.send_photo_now()：_check_limits 只按 uid/轮次，不是每日 cap；_run→_send_image 直接发，不 note_sent、不 claim event、不 remember。
- amo_llm._photo_already_sent()：只查 photo_guard_state + _last_tool_photo_at；“模型直接调外部插件 generate_selfie”只有 omni._selfie_turn_store 一条证据，draw_engine 自己发的图它看不到 → 回复里出现拍照意图词时可能再补一张（_enforce_photo_intent / enforce_photo_claim / enforce_photo_retry 三处都会调 send_photo_now）。
- 去重没有内容签名（scene+pose+outfit+hash），只有 90 秒/10 分钟的文字出口去重（amo_outbound）。

### 3.3 图文不符 / 照片请求未兑现

#### 3.3.1 请求→发送时间线（11-19~11-21）

| 请求 | 实际 | 结果 |
|---|---|---|
| 11-19 12:03:57 看看脚趾 | tool_ok 12:08:14 | 兑现（5 张前的成功） |
| 12:34:24 发给我看看 | timeout 12:36:35；用户 12:37:41 “拍不出来吗？”；tool_ok 12:39:35 | 先失败后兑现；模型把失败讲成“刚才一直没弄好” |
| 12:49:00 看看脚趾 | tool_fail 12:49:11/12:49:19（can only concatenate str (not “tuple”) to str）、12:51:36（模型未定价）、13:03:38（网络）、13:12:01（网络）、13:37:02（403） | **连续失败 5 次**，用户连问；模型复述“软件报错/网络连接超时” |
| 13:42:37 拍给我看看 | tool_ok 13:44:19 | 兑现 |
| 17:38–20:38 比基尼/脚趾 8 条 | tool_ok 17:51/17:57/19:58/20:01/20:05/20:40 | 兑现 6 次，均为相似构图 |
| 11-20 11:35 脚趾 | timeout 11:28、tool_ok 11:36:35 | 兑现 |
| 11-20 19:06 拍张照 | tool_ok 19:07:42 | 兑现 |
| 11-21 09:31 脚趾 | tool_ok 09:32:47 | 兑现 |
| 11-21 11:01 / 13:47 比基尼 | 无生图，回复“在外面呢，等我到家再拍” | 延迟（当时在通勤/商场，可接受） |
| 11-21 21:21 / 21:23 | tool_ok 21:23:11 / 21:25:01 | 兑现（两张） |

- 24 条明确请求，工具链路成功 25 次（含未请求的主动发送），失败 13 次（10 画图失败 + 3 超时）。
- “拍好了/图发了”的书面承诺 10 条，逐条都能在 ±1 分钟找到一次真实发送（承诺本身基本不假）；问题在**失败回合**：模型把后端失败当成“她自己的原因”讲出来（见 §3.7）。

#### 3.3.2 图文不符

- 同分钟：11-22 09:00 照片在家（客厅/T恤），09:00:31 主动消息说“在商场试米白色开衫…985/4072”；10:01 照片在家，10:32:09 又说“在商场试了件…你猜好不好看”。
- 同 prompt：见 §3.1（7/7 互斥）。
- 跨日：11-21 13:40 “试了/买了米白色米白色针织开衫”，11-22 09:00 又“试了…买不买”，11-22 10:32 “这个真拍了也没地方穿啊”——同一件衣服“已买/在试/没买”三种状态并存（§3.6）。
- 照片没有 caption（自动分享只发 Image 组件），用户看到的“图文关系”全靠同一时段聊天；两路各说各话时无法互相校正。

#### 3.3.3 代码入口

- photo_share._share：只有这一条链路有 event 绑定/claim/presence；失败只 return False，调用方 _run_share 才 _notify_failure。
- draw_engine.send_photo_now/_run/_send_image：生成/发送失败只 _notify_failure(explicit)；“话题自动配图失败”直接静默（_notify_failure explicit=False）。
- amo_llm._compose_reply/_enforce_photo_intent/enforce_photo_claim/enforce_photo_retry：三处兜底可能重复 send_photo_now（force=True 绕过限额）。
- amo_message.looks_like_photo_resend_request/resend_last：要“刚才那张”时重发上一张（窗口内未触发）。
- 失败状态：photo_guard_state 只认 photo_share 与 omni._selfie_turn_store，不认 draw_engine 发送成功 → 回复守卫可能误判“没发过”。

### 3.4 [我发的照片] 占位符

#### 3.4.1 生产 P0 原文（10:32:47）

日志 82589-82593（asterbot.log）：

    82589 [2026-11-22 10:32:47.788] … [amo] 分段回复 -> lark:FriendMessage:ou_cec66...: （窝在沙发上，头发有点乱，笑了一声）
    82590 你又来了。这个真拍了也没地方穿啊。（笑）[我发的照片] 自拍 老公，我在商场试了件米白色针织开衫，
    82591
    82592 你猜好不好看，也太可爱了吧。
    82593 [2026-11-22 10:32:47.789] … [amo] 分段回复完成，共 2 段

amo_data.db messages id=2287 同文：

    （窝在沙发上，头发有点乱，笑了一声） 你又来了。这个真拍了也没地方穿啊。（笑）[我发的照片] 自拍 老公，我在商场试了件米白色针织开衫， 你猜好不好看，也太可爱了吧。

同分钟还有 10:32:09 的一条主动消息“老公，我在商场试了件米白色针织开衫，你猜好不好看？”（watch 的 tryon 事件，人其实在家），以及 09:00:31 的“985/4072”购物消息。三条旧事实在同一轮里被复读。

#### 3.4.2 写侧：谁会写标记、写的是什么

| 写者 | 内容 | 问题 |
|---|---|---|
| photo_share.PhotoShareMixin._share → plugin._remember_bot_media(umo, “[我发的照片] 自拍/随手拍[，背景：X]”) | 自动分享成功后才写 | scene 参数是 str，代码里 isinstance(scene, dict) 才取背景 → 实际永远没有背景；只有“自拍/随手拍”两个词 |
| amo_runtime_reply.on_decorating_result（检测到 Record）→ “[我发的语音] ”+Record.text 或全文 | 核心随机 TTS / 按需语音都可能触发 | 两种路径记录口径不同（见 §3.5） |
| amo_runtime_reply._maybe_send_voice_reply → “[我发的语音] ”+voice_text | 按需语音 | 只记台词，不记动作 |
| amo_message._remember_bot_media | 写 messages（私聊也写 memories kind=bot_media） | 同一条既进聊天又进长期记忆，两处都可能被当上下文 |
| draw_engine._send_image / amo_message._send_media_to_umo | **不写任何媒体记录** | send_photo 工具链路 25 张全无记录 |

实际数据：memories bot_media 共 17 条（照片 10、语音 7）；messages 里 [我发的照片]/[我发的语音] 行 19 条（含 summary）。照片记录 10 条正好对应 11-13~11-22 的自动分享，工具链路 0 条。

#### 3.4.3 读侧与出站侧

- 读侧：amo_message._recent() 现在统一走 sanitize_context_rows（is_media_record 整行剔除、strip_media_markers 剥正文、demote_past_claims 降旧场景）；amo_memory_context._clean_context_message 同样剔除/剥；这是 11-22 10:39-10:47 的热修（mtime 10:43/10:47）。
- 出站：amo_message.strip_media_markers 用于 photo_share caption；segment_engine._validate_assembly → amo_state_consistency.validate_reply_assembly 在切段前 strip 媒体标记 + 去复读 + 去当前场景冲突（11-22 10:43 热修）。
- **但**：非分段路径（draw_engine._send_text、amo_feature_api.push_to_owner、photo_share._notify_failure、commands）没有统一 strip；只要模型把标记写进文本，仍可能从这些出口出去。P0 发生在上午 10:32，当时 segment_engine 里还没有 validate（10:43 才加），所以不能把“已修”当结论。

#### 3.4.4 根因

1. 媒体记录是“给审计/记忆用的内部字符串”，却和普通人话共用同一条 messages 表/同一个上下文槽；读侧没有唯一过滤（热修前）。
2. 记录粒度过粗：只有 kind 标签，没有 media_id/scene_id/时间/文件哈希，模型只能看到“[我发的照片] 自拍”，于是把它当一句话复读，且无法判断“这是旧照片、不是现在”。
3. 分段引擎按句切分后逐段发送，strip 加在“切段前”才能覆盖；任何绕过 segment_engine 的出口都没有第二道 strip。
4. 工具链路不写记录 → 模型对“她刚发过图”没有可靠上下文，反而更容易编“图发了/拍好了”。

### 3.5 语音与落库文字

#### 3.5.1 时间线

| 时间 | 日志 | DB/记忆 | 判定 |
|---|---|---|---|
| 11-20 17:55:12 | “按需语音：这个会话没有 TTS 供应商，回退文字” | messages 无对应行（回退文字应发出） | TTS 供应商可用性不稳定；回退只写日志 |
| 11-20 18:10:12 | “按需语音已发送: （在地铁上靠着车门…）”；18:10:12 [ComponentType.Record] | messages 2161 / bot_media 标记**包含动作** | 旧实现把动作也念/也记 |
| 11-20 18:34:37 | 同上，动作“（夹着毛肚…）” | 2165 标记含动作 | 同上 |
| 11-21 11:39:44 | “按需语音已发送: （靠着车厢门…）”；11:39:44 Record | 2209 标记**仍含动作** | 该时点仍是旧行为或双写 |
| 11-21 12:31:31–46 | 3 个 TTS request：动作“（夹了一筷子菜，抬头笑了一下）”、“嗯，我跟我妈先吃。”、“你那边忙完了没？”；Prepare to send 3×Record | messages 2215 只有一条纯文本（动作+两句），**无 [我发的语音]、无 Record 记录** | 核心随机 TTS 把动作念了；插件记录不到 |
| 11-21 15:18:32 | “按需语音已发送: （靠在店门口的墙上…）”；Prepare to send “（靠在店门口的墙上，笑了一下，声音放软） [ComponentType.Record]” | 2235 标记**只有台词**，动作文字没有进 messages | 动作按文字发、语音念台词（新行为），但落库缺动作文本 |
| 11-13 14:09 / 14:13 / 14:19 | 早期日志不在文件内 | bot_media 3 条语音标记（含“看运气，不一定能念出来”） | 按需语音有记录 |

- 关键事实：**amo_tts_guard.wrap_tts_providers 在整个日志里 0 次“TTS 动作过滤已接管”**，即“语音只念台词”的守卫从未成功接管；11-21 12:31 动作被念就是证据。
- 核心随机 TTS 是 AstrBot result_decorate 在插件 on_decorating_result 之后执行的，插件看不到最终 chain，也不写记录 → DB 说“文字”，用户收到“语音”。
- 按需语音的 chain 是 [Plain(动作), Record(台词)]；DB 只落 marker（台词），动作文字丢了。11-20 的旧实现 marker 把动作一起写进去，与 15:18 的新实现口径不同。

#### 3.5.2 代码入口

- amo_voice.voice_requested()/voice_parts()/clean_voice_text()/split_actions()：识别“发语音/说句我爱你给我听”；拆动作/台词。
- amo_runtime_reply._maybe_send_voice_reply()（约 511-597）：TTS 供应商→voice_parts→[Plain(actions), Record(voice_text)]→写 marker→log。
- amo_runtime_reply.on_decorating_result Record 分支（约 119-133）：看到 Record 就写 marker（voice_text 取 Record.text 或全文）。
- amo_tts_guard.wrap_tts_providers（main.py on_astrbot_loaded 调用）：包 get_audio，剥动作；**日志 0 接管**。
- AstrBot core result_decorate.stage：随机 TTS 的实际执行者（日志 TTS request/result）。
- 第三方 tts_sanitizer 插件：透明包装 TTS Provider，过滤符号；与 amo_tts_guard 是两套幂等包装，加载顺序会互相覆盖（日志 61178/61235/61879/62324）。

#### 3.5.3 根因

1. 语音有三条路径（核心随机 TTS、amo 按需语音、第三方 TTS 包装插件），**谁都没有一个“本次到底发了什么”的权威记录**。
2. 记录 marker 由不同层写：core 路径由 on_decorating_result 看 Record 写；按需路径由 _maybe_send_voice_reply 自己写；两者对“动作/台词”的取舍不同。
3. 动作和语音混在一条消息链里，落库只落其中一部分；消息表没有“audio=1 / text=…”的结构字段。
4. TTS 动作过滤没有生效验证（wrapped=0 时不打日志，只在 debug 打失败），无法从监控上发现“动作被念”。
5. 随机 TTS 的开关/概率在核心配置，插件既不能保证“不随机”，也不能感知“已随机”。

### 3.6 衣橱 / 购买 / 试穿状态

#### 3.6.1 数据现状

- wardrobe.json：items=147、outfits=276、daily 保留最近若干天。
- 11-21 daily：牛仔外套 + 浅色牛仔裤 + 白色老爹鞋（外出，符合 14:00 商场）。
- 11-22 daily：卡其色风衣 + 卡其色半身裙 + 米色乐福鞋（“计划穿搭”）。
- 同时 amo_outfit.json（实际穿着 sidecar，给生图插件）：day=2026-11-22，text=“洗得发白的宽松大T恤、米色居家长裤、居家棉拖鞋”（在家 home_outfit）。
- 11-22 两张自动照片的 prompt 用后者（T恤/居家裤），与 sidecar 一致；但面板“今日穿搭”/wardrobe.today() 是前者。
- photo_outfit:2026-11-10 / 11-11 是旧的 photo_engine 缓存，11-12 之后没再写（已被 wardrobe.current 取代），审计时点属于死数据。

#### 3.6.2 购买/试穿时间线（矛盾）

| 时间 | 文本/数据 | 账本/衣柜 | 判定 |
|---|---|---|---|
| 11-21 13:40:06 | “老公你看！我试了这件**米白色米白色**针织开衫，好看吗？” | 无试穿记录；颜色重复 | 文案 bug：color+name 拼接重复 |
| 11-21 13:40:12 | “我回来啦！今天买了米白色针织开衫，花了 **649 元**，穿上真的超好看！” | wallet_ledger 无 649/衣服；wardrobe items 无该单品；无 add_closet_item | **假购买**（同一次购物被拆成“试了”+“买了”两条） |
| 11-21 15:15:22 | “打折最后一天，你倒是给句话呀” | watch shop:pending 已过期/清空 | 试穿追问（人 14:00-17:00 在商场，可接受） |
| 11-22 09:00:31 | “这件米白色针织开衫我试了真好看，就是 **985**，我卡里还有 **4072**，你说买不买？” | kv shopping_prompt line 含“价格 985 元，卡里余额 4072 元”；当时日程 09:00-10:00 在家 | 在家说在商场试衣 + 精确数字进聊天 |
| 11-22 10:32:09 | “老公，我在商场试了件米白色针织开衫，你猜好不好看？” | watch shop:events 的 tryon（tick 09:00:18 + 90min=10:30:18）触发；can_engage 过、can_shop 未过（推断：审计时 shop:events 只剩 home、tryon 已消费，时间吻合） | 在家再次说商场试衣 |
| 11-22 12:00:18（未到） | watch shop:events 里还有一条 home 事件：“买了米白色针织开衫，花了…”，due=1795320018 | 购买从未发生（shop:pending 清空、账本无记录） | **待发生的假购买炫耀** |

- 账本里唯一一笔“买衣服”是 11-14 的“一条绿色碎花连衣裙” -1349.97 元（wallet_ledger idem=shop:2026-11-14:item），说明 handle_reply 路径本身能记账；11-21 的 649 元不是这一笔，也不在账本。
- 计数单位 bug：social.db 的 event price=98571（分），但 11-22 消息里说的是 985 元；watch 的 _money_words 本应把 98571 说成“快一千了”，但 shopping_prompt 注入的是精确“985 元/4072 元”，模型照抄。
- “米白色米白色”根因：watch shopping_engine 用 “%s%s” % (color, name) 拼“在商场试了%s%s”，而 SHOP_MALL 里 name 已经是“米白色针织开衫”，color=“米白色” → 颜色重复。note_shopping_pending 的 line 同样重复。
- 11-22 10:03:08 还有里程碑归档把“985 元/4072 元”写成长期记忆（log 82565），错误事实被固化。

#### 3.6.3 代码入口

| 入口 | 职责 | 风险 |
|---|---|---|
| wardrobe.Wardrobe.current() | 实际穿着：按 hour/at_home/at_hotel/swim 选 home_outfit/night/today，并 _sync_sidecar | 面板/聊天/生图各自调不同方法（today vs current） |
| wardrobe.Wardrobe.today() | 当天计划穿搭 | 与 current 在家时不同，面板展示的是它 |
| wardrobe.Wardrobe.home_outfit() | 在家便服（T恤/居家裤） | 照片用这个；today 不用 |
| wardrobe._sync_sidecar() | 写 amo_outfit.json 给生图插件 | 只写实际穿着 |
| photo_engine.outfit_today() | 照片穿搭：先 wardrobe.current，再 photo_outfit 缓存，再 OUTFIT_POOL | 与面板 today 两套 |
| watch shopping_engine.tick() | 在商场才触发；扣交通/奶茶/午饭；选单品；写 shop:pending + shop:events（+90min tryon、+180min home）；push 一条 | 事件队列与“是否真的买”无关；home 事件必然声称“买了” |
| watch shopping_engine.flush() | tryon/home 到点 push；只过 can_engage，不看 can_shop | 11-22 10:32 在家推“在商场试” |
| watch shopping_engine.handle_reply() | 用户说买 → 扣款 + add_closet_item + permanent_memory + push“买啦” | 唯一正道；但 11-21/11-22 的购买文案不是它出的 |
| amo_feature_api.note_shopping_pending() | 写 kv shopping_prompt（line/until） | line 里带精确价格/余额，颜色重复 |
| amo_runtime_context._dynamic_context 购物段 | 把 shopping_prompt 注入聊天 prompt（11-22 10:39 加了 scene_guard 过滤离线事实） | 热修前无场景校验 → 在家说商场 |
| amo_purchase_guard.rewrite() | 没有账本记录时把“买了/花了”改成“试了还没买” | 11-21 13:40 时该守卫可能还没生效（文件 11-21 16:51）；11-22 的 message 是“买不买”问句，不命中 _CLAIM |

#### 3.6.4 根因

1. “试穿 → 购买”没有一个状态机/事务：tick 时排了 tryon/home 两条“演出事件”，与用户是否同意购买完全解耦；home 事件到点必说“买了”，而真正购买只在 handle_reply 才发生。
2. 计划穿搭（today）和实际穿着（current）是两个公开 API，聊天/照片/面板各取一个；没有“worn_now”唯一视图。
3. 购物事实注入是自然语言行（含精确价格/余额），没有结构化字段，也没有“禁止报账式数字”的统一过滤（只有 _money_words 在 watch 生成话术时用，注入/里程碑不复用）。
4. 颜色+单品名拼接没有去重；同一件衣服的“试/买/没买”可以同时出现在上下文（shopping_prompt、shop:events、messages、milestone）。

### 3.7 失败通知

#### 3.7.1 现状

- photo_share._notify_failure()：自动分享失败时从 8 条温柔话术池（infra/config/photo_failure_lines.json）选一条，push_to_owner；同一会话避开上一条。
- draw_engine._notify_failure()：只有 explicit（他明确要图/工具调用）才出声；固定一句“这次没拍成，等会儿再给你拍；不是不理你。”；10 分钟/会话节流；日志 [DRAW] failure_notice=1（窗口内 2 次：11-18 18:23、19:44）。话题自动配图失败直接静默（[DRAW] failure_silent）。
- draw_engine.missing_omni_notice()：没装生图插件时回一句“我这台还没装画图插件…”，10 分钟/会话。
- 工具链路失败时，**原始异常字符串作为 Tool result 回给模型**，例如：“系统提示：画图失败 (所有绘图节点请求失败…RuntimeError(HTTP 400: Model gpt-image-2.5-1k has not been priced…))”“画图失败 (can only concatenate str (not “tuple”) to str)”“error: tool generate_selfie execution timeout after 120 seconds”。模型据此自由发挥。

#### 3.7.2 话术泄漏样例（6 条）

| 时间 | 说出去的话 | 真实失败原因 | 泄漏类型 |
|---|---|---|---|
| 11-18 18:23:11 summary | “图片一直发不出来，可能是**服务器**那边卡住了。等我再试试…”“还是不行，报的跟刚才**一样的错**。图先不发了，**服务器**那边得有人管，我也没辙。” | 11-18 18:23:54 Tool 画图失败（模型未定价 HTTP 400） | 服务器/报错/运维 |
| 11-18 18:35:16 | “（把手机扣在腿上，摊手）还是失败，**同一个报错**，三回了。” | 11-18 18:35:07 Tool 画图失败 | 报错/次数 |
| 11-19 12:49:33 | “这**软件**今天跟我较劲，一直**报错**。” | 12:49:11 Tool 画图失败（tuple 拼接 bug） | 软件/报错 |
| 11-19 13:03:51 | “又断了，这**网络连接超时**了。” | 13:03:38 Tool 画图失败（ClientConnectorError） | 网络/超时 |
| 11-19 13:12:13 | “还是断，真的不行…**它连图上不去**。” | 13:12:01 Tool 画图失败（ClientConnectorError） | 连图/上传 |

- 同一窗口 photo_share 温柔池与 draw_engine 固定话术**都没有出现在 amo_data.db.messages**（grep messages 无“没拍成/不是不理你”）；失败话术走 push_to_owner/_send_text，不写聊天记忆，导致：用户记得被“报错”敷衍过，记忆里却没有这条，下一轮又重复。

#### 3.7.3 根因

1. 失败通知四套：photo_share 池、draw_engine 固定句、missing_omni 说明、模型自由发挥（tool result 原文）。没有统一出口/统一话术/统一节流。
2. 工具错误字符串直接进 LLM 上下文：模型会把它当“她自己的处境”编成第一人称技术解释；现有守卫（place/消费/anti-nag）都不拦“服务器/报错/网络/软件/接口/API/节点/模型”这类词。
3. draw_engine 的 explicit 判定依赖 reason（tool/manual/explicit），但模型直接调外部插件 generate_selfie 的失败不走 draw_engine，photo_share 也看不到 → 失败静默或模型自由发挥。
4. 失败通知不落 messages → 没有“这句话回过没有”的依据，也不会被 outbound 去重/频控完整覆盖（push_to_owner 有 5 分钟窗口，但内容不同就不拦）。

### 3.8 姿势库 / 内容安全（媒体侧额外发现）

- poses.json：300 条（MAX_POSES=60 未生效；save_all 只截 60，但导入/加载路径没截）；usage=selfie 245 / couple 55。
- note 目录结构明显是批量导入的图库分类：上半身单人·可爱系 / 挤胸·强调事业线系 / 性感·大胆系 / 全身单人·性感系·站姿/坐姿/跪立·四肢着地 / 特殊：肩倒立·V字打开 等；含“手扶墙顶胯（站立后入待位）”“双手揉胸”“M字开腿”等成人向描述。
- 随机逻辑：PoseLibrary.random_pose(zone) — 非私密 zone 只剔除 note/name 含“床/被窝/枕/趴坐/跪/抬腿/翘臀/蹲坐”的；home/home_bed/home_sofa 视为私密，**不过滤任何条目** → 245 条全进池。11-22 09:00 日志“候选 245 个”直接证明。
- 结果：客厅照可随机到肩倒立/悬空后倚/挤胸类；这些动作既与“坐在客厅”图文不符，也容易触发生图内容政策（11-17 15:20 内容政策失败；11-18 2 次 policy fallback）。
- 另外 camera_mode() 对“双手/背对/趴/躺/倒立/悬空/后仰/抱腿”等返回 void（第三人称虚空相机全身），与“手机前置自拍”的人设叙事冲突；日志里 11-22 两张照片都是“自拍”但 prompt 是虚空相机全身。
- 建议：姿势库当作“已审核白名单”，按 room 再分池（bedroom/sofa/bath/outdoor），客厅只允许 sit_sofa/stand 类；MAX_POSES 在加载/导入处强制；成人向条目默认关闭，需显式开启才进池；禁止 void 相机用于“自拍”话术。

## 4. 根因总览（统一规则提案）

### 4.1 一句话根因

照片/语音/穿搭的“生成、发送、记账、失败话术、占位符”分散在 3 条照片链路、3 条 TTS 路径、2 套穿搭视图里，任何一条都不拥有完整事实；于是场景互相打架、发过的图查不到、没买的说买了、失败原因由模型即兴解释。

### 4.2 统一规则（建议写进代码的“媒体不变量”）

1. **单一媒体出口 MediaGateway**：所有图片/语音必须经 amo_media.send(umo, media, meta) 发送；photo_share / draw_engine / 外部 generate_selfie 回执 / resend / test / commands 一律不得直接 context.send_message 或 event.send 媒体。Gateway 内完成：presence、scene 绑定、每日/每事件配额、签名去重、outbound 频控、记账、日志。
2. **单一媒体账本 MediaLedger（SQLite 表）**：每条记录 media_id、kind（photo/voice）、source（auto/tool/on_demand/test/resend）、scene_id+scene_version、place/zone、outfit_ids、pose_id、file_sha1、event_id、umo、msg_id、voice_text、tts_provider、created_at、status（sent/failed/blocked）。photo_count、去重、审计、面板统计全部从账本派生，不再各写各的 kv。
3. **单一场景事实**：照片 plan 必须绑定 scene_id（task-8 的 SceneLedger）；prompt 生成只允许一个 scene_id，禁止再把 scene_for/plan.scene_key/housing.room/state_hint 四个来源拼一起；prompt 生成后跑 verify_photo_prompt 断言“单 zone + 单姿势 + 无否定句残留”，不通过就不发（宁可发失败话术，不发互斥图）。
4. **姿势白名单 + 房间约束**：poses.json 只保留审核过的 ≤60 条；按 room 建池（home_bed/home_sofa/home_bath/living/outdoor）；random_pose 只从当前 room 池取；void 相机姿势只能在明确“全身/抓拍”请求时用；成人向条目默认关闭。MAX_POSES 在加载/导入处强制。
5. **媒体记录不是人话**：账本用结构化字段；messages/长期记忆里不再写 [我发的照片]/[我发的语音] 字符串，或写了也只在审计视图可见；所有读侧（_recent、记忆注入、时间回看）默认过滤；所有出站口（分段、push、draw_text、commands、caption）统一 strip 媒体标记，作为 Gateway 的最后一道。
6. **单一穿搭视图 worn_now**：wardrobe.worn_now(scene) = 真正身上这套（current/home/night/swim）；聊天、照片、面板默认都读它；“今日计划穿搭”单独字段只给面板展示；照片 prompt 的 outfit 必须来自 worn_now + room（床景去鞋、外套保留）。photo_outfit 旧缓存删除/迁移。
7. **购物状态机**：shopping item 状态 = pending_tryon → approved(账本扣款 + 衣柜入库 + 长期记忆) / rejected / expired；只有 approved 才允许出现“买了/花了”；shop:events 的 home/tryon 推送必须带购物状态与 scene_id，状态不是 approved 时禁止“买了”文案；试穿 pending 追问必须校验当前 zone=商场。
8. **金额与描述规范化**：注入/推送/记忆一律用口语金额（_money_words），禁止精确价格/余额进聊天上下文；颜色+单品名去重（name 已含 color 时不重复拼接）；同一购物事件只保留一条叙事（试穿问句与已买句二选一，amo_purchase_guard 已有，需覆盖 push 与 watch）。
9. **单一失败出口 media_failure**：所有链路失败（自动/工具/外部插件/语音）统一调用 media_failure(kind, reason, explicit) → 一句温柔话术（复用 photo_failure_lines）、per-umo 10 分钟节流、不写“服务器/报错/接口/API/模型/节点/超时/网络”等词；工具/插件返回给 LLM 的只能是“失败/稍后再试”的语义化结果，禁止回传异常原文；同时在回复守卫加“技术词黑名单”，命中即改写。
10. **语音单一记录**：voice 发送必须记录“实际 TTS 文本 + 是否带动作文字 + provider”；按需语音链保持 [Plain(动作), Record(台词)] 并同时把动作写入 messages；核心随机 TTS 要么在 on_decorating_result 之前接入（保证可见/可记录），要么关闭随机 TTS；amo_tts_guard 必须在所有插件加载完成后重新包装并打“接管 N 个 Provider”日志，0 接管视为失败告警。
11. **去重签名**：签名 = sha1(图片 bytes) 或 crop hash + scene_id + pose_id + outfit_ids；同一签名 6 小时内自动不重发；同一事件最多 1 张；同一轮最多 1 个媒体；测试图独立通道（不计配额、不写记忆、不主动推送），且同 prompt 测试图 10 分钟内最多 1 张。
12. **观测**：所有媒体发送必须有一条“media -> media_id/status”日志；每天生成媒体日报：请求数、真实发送数、记录覆盖数、场景冲突数、重复签名数、失败话术泄漏数、voice 记录一致性。

### 4.3 落地顺序（只读建议，不在本次改）

- P0（先止血，1–2 小时）：
  a) 媒体标记全出口 strip（Gateway + push + draw_text + caption）；b) 工具失败结果语义化，禁止异常原文进 LLM；c) 技术词黑名单（服务器/报错/网络/超时/接口/API/节点/模型/软件）；d) 姿势库先缩到 home 安全子集（sit_sofa/stand，禁用倒立/悬空/挤胸/跪趴），MAX_POSES 生效；e) photo prompt 单 zone + 单姿势断言（不通过不发）。
- P1（结构性，1–2 天）：
  a) MediaGateway/MediaLedger 落地，三条照片链路全部改走；b) 语音 VoiceGateway + 记录实际文本；c) 穿搭 worn_now 单一视图；d) 购物状态机（approved 才能说买了，home/tryon 事件带状态）；e) 失败统一出口。
- P2（验收，持续）：
  a) 媒体日报指标进 work/sim；b) 历史回放 11-16~11-22 的 32 张发送/17 条 bot_media，验证记录覆盖 100%；c) 回归用例直接用本报告时间点（11-16 08:01、11-17 15:11、11-18 08:01/19:43、11-19 12:49、11-21 09:01/12:31、11-22 09:00/10:32）。

### 4.4 验收指标（建议）

| 指标 | 目标 |
|---|---|
| 照片发送记录覆盖率（账本条数/真实发送） | 100%（当前窗口 21.9%） |
| 同一 prompt 内 zone/姿势冲突 | 0（当前 7/7） |
| 每日照片数 > photo_daily_cap 的天数 | 0（当前 11-19 10 张） |
| 同一签名/同场景 6 小时内重复发送 | 0 自动（用户明确要除外） |
| 聊天里出现媒体占位符 | 0（当前 11-22 10:32 一次） |
| 聊天里出现服务器/报错/超时/接口/API/模型/节点 | 0（当前 6 条） |
| 语音：真实 TTS 文本与记录不一致 | 0（当前核心随机 TTS 0 记录、动作被念 1 次、动作丢失 1 次） |
| 购买声称与账本/衣柜一致 | 100%（当前 11-21 649 元、11-22 985 元两处反例） |
| 面板今日穿搭 vs 实际穿着 | 同一视图或显式标注“计划/实际”（当前两套） |

## 5. 现有自动化检查覆盖

| 检查 | 位置 | 覆盖 | 不覆盖 |
|---|---|---|---|
| 照片事件绑定/去重 | tests/test_photo_event_binding.py | photo_share event_id、统一出口 | 工具链路、外部插件、签名去重 |
| 语音按需/动作 | tests/test_voice_request.py、test_voice_actions.py、test_tts_guard.py | voice_requested/voice_parts/tts_guard 单点 | 核心随机 TTS 端到端、落库一致性 |
| 媒体标记 strip | tests/test_cov2_message.py（remember_media）、test_proactive_coherence.py | _remember_bot_media/测试图跳过 | 出站全路径、分段前 strip（新代码未见回归测试） |
| 照片场景/守卫 | tests/test_photo_*、tests/test_scene_* 等 | scene_for/bed_like/plan 单点 | prompt 端到端“单 zone+单姿势” |
| 购物/衣橱 | tests/test_wardrobe_*、watch 侧测试 | 衣柜增删/每日穿搭 | 试穿→购买状态机、push 文案、账本一致性 |
| 仿真 | work/sim/daily|share|remind|diary | 文字回复链路 | 媒体链路（照片/语音/购物 push）没有专项仿真 |

## 6. 审计时点代码热修核查（方向对，仍有洞）

审计期间（10:39–10:56）下列文件仍在被 task-3/task-15 修改：

- photo_prompt.py（10:41:59）：_bed_like 已加 strip_negated；_reconcile_where/_finalize_prompt_zone 已在（单 zone 清洗）。
- photo_share.py（10:44:02）：_notify_failure 改为只走统一出口（注释：节流只保留 push_to_owner 5 分钟 + outbound 去重）；caption strip 媒体标记。
- draw_engine.py（10:41:40）：媒体/说明改走 _send_media_to_umo（统一出口），但**仍不写媒体账本**。
- amo_presence.py（10:41:11）：photo_binding/can_photo/claim_photo_event 已收口；photo_share.tick 调 can_photo。
- amo_message.py（10:47:30）：新增 is_media_record/strip_media_markers/sanitize_context_rows；_recent 统一清洗。
- amo_state_consistency.py（10:43:19）：validate_reply_assembly/dedupe_reply_segments/demote_past_claims。
- 11-22 10:39 有人给 amo_runtime_context 的 shopping_prompt 注入加了 scene_guard（离线购物事实在家不注入）。
- 11-22 10:32 之后 photo_engine/pose_library 没有为“姿势库 300 条/客厅床姿”做收敛。

结论：**P0 的 10:32 占位符泄漏与“在家说商场”已有针对性热修（方向正确）**，但热修是“读侧过滤 + 分段前清洗 + 注入前校验”，不是“单一账本/单一出口”；§3.2（工具链路不记账/超 cap）、§3.5（语音记录）、§3.6（购物状态机）、§3.7（失败原文进 LLM）、§3.8（姿势库）在审计时点仍成立。是否真的不再复现，必须在 10:47 之后的版本上重跑 11-22 09:00/10:32、11-21 12:31 三个回放用例，不能只看 mtime/注释。

## 7. 附录：只读复现与证据

### 7.1 只读打开

    import sqlite3
    d = sqlite3.connect("file:D:/dafeiyu/astrbot/data/plugin_data/Astrbot_amoupup_data/amo_data.db?mode=ro", uri=True)
    f = sqlite3.connect("file:D:/dafeiyu/astrbot/data/plugin_data/Astrbot_amoupup_forever/amo_forever.db?mode=ro", uri=True)
    s = sqlite3.connect("file:D:/dafeiyu/astrbot/data/plugin_data/Astrbot_amoupup_watch/social.db?mode=ro", uri=True)

### 7.2 关键查询

    -- 消息/摘要
    select id,role,created_at,content from messages where content like '%我发的照片%' or content like '%我发的语音%' order by created_at;
    -- 媒体长期记忆
    select content,created_at from memories where kind='bot_media' order by created_at;
    -- 照片/购物状态
    select key,value,updated from kv where key like 'photo_%' or key like 'shopping_%' or key like 'draw_cache:%';
    -- 日程
    select day,items from schedule order by day;
    -- 购物事件/账本
    select k,v from meta where k like 'shop%';   -- social.db
    select * from wallet_ledger where item like '%衣服%' or amount in (-64900,-98500);

### 7.3 日志 grep（astrbot.log）

- 照片发送：照片已分享（ / 照片耗时 / 测试照片已发
- 工具链路：使用工具：generate_selfie / “Tool generate_selfie Result:” / LLM 自拍工具失败
- 失败：failure_notice / failure_silent / PHOTO_POLICY / 照片发送失败 / 照片未绑定当前事件 / 同一事件只发一张图
- 语音：按需语音 / TTS request: / TTS result: / Prepare to send - .*Record / TTS 动作过滤
- 发送原文：分段回复 -> / 主动 -> / 直接发送
- 场景：PHOTO_DEBUG / 姿势选择：

### 7.4 证据文件

- 本次审计的取证脚本与导出（只读生成，均在 work\audit\_scratch）：photo_kv.py、msgs.py、media_facts_data.py/.txt、media_events2.txt、pdebug.py、summary.py、poses.py、mem.py、last.py、p0log.py。
- 相关审计：work\audit\scene_facts.md（照片场景 vs 文案的完整时间线）、work\audit\chat_coherence.md（对话复读/占位符复读上下文）、work\audit\outbound_repeat.md（10:32:09 与 10:32:47 的重复发送）。

### 7.5 未验证 / 风险

- 11-16 之前照片只从 bot_media 记忆取证，无法逐张还原 prompt（日志从 11-16 06:57 开始）。
- 无法确认 11-22 12:00 的 shop home 事件最终是否真的推送（审计时点在 10:56，事件 due=1795320018 未到）；按代码必推。
- 无法确认 amo_tts_guard 是“从未被调用”还是“调用了但 0 provider”（两种都会导致 0 日志）；无论哪种，结果都是动作被念。
- 工具链路 25 张成功图片的实际图像内容无法逐张核对（photo_cache 只保留最近 26 个文件，且部分为测试/姿势图）；本文的“图文不符”以 prompt 与聊天/日程对照为准，未做图像识别。
