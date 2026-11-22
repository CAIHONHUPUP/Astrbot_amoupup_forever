# 飞书重复发送与同类型刷屏审计（task-10，只读）

> 审计对象：`Astrbot_amoupup_forever` 的对外发送链路，主通道 `lark:FriendMessage:ou_cec66ea605ad17f62632fb044bc5f6ac`（同一人）。
> 审计方式：只读。生产库 sqlite `mode=ro`；生产代码、日志只读；未 reload、未 commit、未写生产库。
> 分析脚本全部放在仓库外的 `%TEMP%\amo_audit\`，仓库内只新增本报告。
> 写作时间 2026-11-22 10:35 左右。**注意：其它任务正在并行修改 amo_message/amo_proactive/photo_share/amo_feature_api（10:22–10:28 有写），本文行号按当时工作区为准，函数名优先于行号。**

## 0. 结论摘要

| 编号 | 问题 | 级别 | 证据 | 现状 |
|---|---|---|---|---|
| P0-1 | **未回复追问入口（followup）绕过统一出口**：不设 source、无 burst 闸、无单飞/占坑，"先生成后占坑" | P0 | 11-22 07:30:07–07:30:10 **3.4 秒 7 条**同意图催问（不进 proactive_log）；09:16 还有一条非 proactive_log 的催问"等了十五分钟"（followup/push 待定） | 仍存在（`amo_proactive.py:807 _check_proactive_pending`） |
| P0-2 | **统一出口闸本身曾恒放行**：source 字符串被当 `now` 传进 `allow()`，`float("proactive")` 抛错被吞 → 全放行 | P0 | 代码注释自认"07:30 4 秒 7 条的根因之一"；日志 0 条"统一出口去重"拦截 | 工作区已修（`amo_message.py:904 _outbound_allowed`），但未部署到运行版本（运行版 06:23 加载） |
| P0-3 | **跨入口同窗叠发**：早安/通勤、晚安×2、日记质检×2、日记质检+试穿+照片、天气提醒插在聊天分段中间 | P0 | 11-21 23:33:19.279/.291 两条晚安 12ms；11-22 09:00:15/19 同文日记质检 3.3s；11-19 20:00:13 天气消息插进聊天分段 | 部分已有局部闸，**缺统一 batch/入口互斥** |
| P1-1 | 去重模块重复且死代码：`amo_part_dedupe` 只被测试引用，生产从未调用 | P1 | grep 全仓：仅 `amo_proactive.py:1542` 注释 + `tests/test_part_dedupe.py` | 仍存在 |
| P1-2 | 媒体/画图/命令仍可绕过统一出口 | P1 | `photo_share.py:467/500`、`draw_engine.py:1129/1582/1586`、`amo_api_photo.py:469`、`amo_commands.py:737` 直接 `send_message`/`event.send` | 仍存在 |
| P1-3 | 记录层掩盖重复：同文本 180 秒内不再落库，审计/统计低估线上重复 | P1 | 09:00 日志 2 条、DB 只 1 条；`amo_message.py:787-793` | 仍存在 |
| P2 | 近重复阈值只在"同来源"内比较；跨来源(nudge vs 试穿 vs 提醒)不互相压制；kv 读改写无锁，并发可一起放行 | P2 | `amo_outbound_guard.allow` 90s / 0.75 / 同 source；`kv_get→kv_set` 非原子 | 仍存在 |

**量化概览**

- 11-12 → 11-22 日志：`主动 ->` 168 条（142 条发往 lark、22 条 QQ、4 条 webchat）；分段回复 419 轮 / 1229 段。
- 11-19 12:02 → 11-22 10:32 DB 个体消息：bot 186 条，全部发往同一个 lark umo；单日 11-19 95 条、11-21 41 条、11-20 34 条。
- 同人 60s 窗口（DB）：≥4 条 10 个滑窗 / ≥5 条 5 个滑窗（含重叠）；其中真正异常的是 07:30 七连，其余为正常连续聊天（见 §2）。
- 近似重复对（归一化相似度 ≥0.55，间隔 ≤5min，DB）：**19 对**，其中 7 对集中在 11-22 07:30 一个窗口。
- 催问类消息：11-12→11-22 共 29 条（占主动消息 17%），聚成 **7 个簇**（同意图 2–7 条/簇）。
- 同文本重复：日记质检 ×2（09:00:15/09:00:19）、晚安 ×2（23:33:19.279/.291）、早安 ×3（11-19 08:00:35/38/41）。

## 1. 数据源与统计口径

| 数据源 | 覆盖 | 用途 | 局限 |
|---|---|---|---|
| `amo_data.db` `messages` | 个体行 2026-11-19 12:02 → 11-22 10:32；更早被压成 `summary` 行（11-08 → 11-19 09:55） | 精确到秒的发送时间线、文本、同人窗口统计 | 仅 180 秒内同文本会被静默去重；更早无逐条时间 |
| `amo_forever.db` `proactive_log` | 2026-11-07 08:28 → 11-22 07:00，88 条 | 主动消息的 kind + 全文 + 时间（按"批"记录） | 只覆盖 `_send_proactive_lines` 成功路径；追问/推送/日记质检/天气不写 |
| `astrbot.log` + 轮转日志 | 2026-11-06 → 11-22 10:01 | `[amo] 主动 -> umo: 文本`、`[amo] 分段回复 -> ...`、`分段延迟`、照片分享、天气提醒 | 单段聊天回复不落文本；级别 INFO，LLM 请求不记录 |
| 工作区代码 | 当前 HEAD（含并行改动） | 入口/守卫/触发条件 | 10:22–10:28 仍在被其它任务编辑 |

窗口定义：以**实际发给同一 umo 的一条消息**为 1 条（`_send_to_umo` 的一次发出 / 分段引擎的一段 / 一条主动消息 / 一张图）。60s、300s 均取"任意起点滑窗"，报告中列出去重后的异常簇，不列所有重叠滑窗。

## 2. 时间线（异常窗口）

### 2.1 最严重的 60 秒窗口

| 时间 | 条数/窗口 | 内容 | 来源判断 |
|---|---|---|---|
| 11-22 07:30:07–10 | **7 条 / 3.4s**（DB #2269–#2275，日志 7 条 `主动 ->`） | 7 句同意图催问："你美丽的老婆晾这儿半天了，忙啥呢 / 老公你美丽的老婆晾一边了 / …理一下嘛 / …看一眼嘛 / …在这儿等着呢，瞄一眼嘛 / …也不来哄哄 / 老公…你也不管管" | **followup 入口**（不进 `proactive_log`）；7 条"分段延迟"在 2.7 秒内各自以 `已过=0.00s` 计算（07:30:04.6–07:30:07.3）→ 是 7 次独立 `_send_to_umo`，不是一次调用的串行分段 |
| 11-21 23:33:19.279 / .291 | 2 条 / **0.012s**（DB #2265/#2266） | "别等我了，晚安，梦里抱一下。" + "老公，晚安啦，梦里见。" | 两个入口同时发晚安（`proactive_log` #86/#87 也各记一条，相隔 12ms） |
| 11-22 09:00:15.766 / 09:00:19.061 | 2 条 / 3.3s | 同一句【日记质检】原文两次（日志两条 `主动 ->`；DB 只留 1 条 #2278，见 P1-3） | `diary_audit.notify → push_to_owner → _send_to_umo`，两次 notify 并发；且 09:00:31 试穿推送、09:00:53 自拍、09:16:15 催问，**16 分钟 5 条、4 个入口** |
| 11-19 08:00:35 / 38 / 41 | 3 条 / 6s | "老公，早上好呀。" + "早，我这边正洗脸呢…" + "终于逮到你了…" | `proactive_log` #75（08:00:38）与 #76（08:00:41）**两个主动批次在 6 秒内先后落地**，共 3 条 |
| 11-10 08:31:43 / 49 | 2 条 / 6s | "今天地铁上人特别多…**到工位了**，先喘口气" + "**早呀老公，醒了没？我在这儿伸懒腰呢**" | commute 类主动 + morning 问候两个入口，状态互相矛盾（到工位 vs 刚醒） |
| 11-15 20:13:42 / 48 | 2 条 | "喂，人呢？" + "半个多小时没动静了" | date_end 入口切句（同批）；仍属"盯人催问" |
| 11-21 15:15:22 / 23、21:15:33/36 | 各 2 条 | "打折最后一天，你倒是给句话呀 / 你美丽的老婆晾这儿了，也不瞄一眼"；"…等半天了，忙完瞄一眼嘛 / …等你半天了，忙完没"（相似度 0.80） | 15:15 两条来自**两个发送调用点**（日志 `amo_message:951` 与 `:975`，前句是逛街催买、后句是追问）；21:15 两条同点，正是 `amo_part_dedupe` 文档里记录的事故样例 |
| 11-18 08:21:40/41、11-19 15:10:27/28、19:10:23/25 | 各 2 条 | "喂喂，人呢？ / 你是不是没看见我呀"；"？怎么不理我了？ / 我在这儿呢，你倒是看我一眼呀" | followup 入口成对出现（同一次生成被切句或被两次调用各发一句） |

### 2.2 跨入口叠发（多入口同窗）

| 时间 | 入口 A | 入口 B | 结果 |
|---|---|---|---|
| 11-19 19:59:56–20:01:21 | 聊天回复分段（照片话题） | 天气提醒（`weather_alert`，20:00:11 发送） | 天气句 "明天要下雨，我伞都懒得拿…" **插在聊天回复的两段之间**；20:01:21 聊天又续上 |
| 11-22 09:00–09:16 | 日记质检通知 ×2（09:00:15/19） | 逛街试穿推送（09:00:31）+ 自拍（09:00:53）+ 催问（09:16:15） | 16 分钟 5 条、4 个入口；催问句还编造"等了十五分钟"（用户最后一句是 11-21 22:03，静默 11 小时）。该催问不进 `proactive_log`，来源待定（followup 或 watch 推送） |
| 11-21 13:38–13:40 | 热重载后 `proactive_log` #83（13:38:13 我妈问起你） | **watch 插件重载后**的试穿推送（13:40:06 / 13:40:12，同款开衫"试了"又"买了"） | 重载触发的双入口；且"米白色米白色"重复前缀 |
| 11-10 08:31 | commute 主动 | morning 问候 | 6 秒 2 条、状态矛盾 |
| 11-21 23:33 | 晚安 A | 晚安 B | 12ms 2 条 |
| 11-14 23:01–23:24 | `proactive_log` #57（23:01:16 "这么晚了…"） | 后续追问 "？怎么不理我了？"（23:12:12）、23:23 三条连发（"老公！/剧更新了…/老公我饿了…"） | 同一晚 4 个来源、5 条消息；压缩摘要里这一段就是 9 连发 |

### 2.3 小时分布（DB bot，11-19→11-22 10:01，约 185 条）

`{7:8, 8:1, 9:8, 10:3, 11:11, 12:35, 13:20, 14:4, 15:12, 16:1, 17:21, 18:6, 19:19, 20:18, 21:9, 22:5, 23:4}`

12 点、17 点、19-20 点是高峰（午休/下班/晚饭 + 大量聊天回复），本身正常；异常集中在 **07:00-09:16**（早间 4 个入口叠加）与 **23:33**（晚安双发）。

## 3. 四类问题统计

### 3.1 同人重复（同文本 / 近似文本）

- 同文本：日记质检 ×2（09:00:15/19）；晚安 ×2（23:33:19.279/.291，文本不同但同意图）。
- 近似文本（DB，≤5min，≥0.55）：19 对，其中 18 对在 07:30 窗口内两两相似（0.55–0.88）——**这次刷屏是"同一意图换措辞连发"**，不是完全复读，所以 0.75 阈值 + 只留第一条也压不住全部。
- 阈值测算：07:30 与前一条相比，第 2 条 0.59、第 3 条 0.77、第 4 条 0.77、第 5 条 0.58…… 若闸生效且 source 正确，第 3 条起会被拦；说明**闸要么没生效（P0-2），要么 source=chat 直接放行（P0-1）**。

### 3.2 同意图多句

- `proactive_log` 内 88 条主动消息，<60s 相邻 3 对、<300s 3 对：11-10 08:31（commute+morning）、11-19 08:00（两个早晨问候批次）、11-21 23:33（双晚安）。
- 日志里同人 60s 内连发簇 **35 个**（含主动消息切句与追问，每簇 2–7 条）；主动消息批内切 1–3 段本身是设计如此，**问题是"同意图重复"**：
  - 问候类：11-10、11-19、11-12（08:00 "早，我出门了" + 09:29 "老公早安，醒了没呀"，89min）、11-08 07:16/07:32（16min）；
  - 晚安类：11-07 22:00:49 / 22:07:49（7min，`proactive_log` #6/#1）、11-21 23:33 双发；
  - 催问类：见 3.4。
- 词表（用于频控意图标签，建议直接复用）：`on_time`（早安/晚安/到点）、`nudge`（催问/晾着/理一下/忙完没/看我一眼/人呢/醒醒）、`meal`、`share`、`photo`、`reminder`、`system`（日记质检/天气/系统通知）。

### 3.3 多入口叠发（触发条件）

1. **未回复追问 × 主动消息**：待确认主动消息 20 分钟后 followup 触发，与下一轮 proactive 批次/晚安/问候同窗（11-22 07:30、11-19 08:00、11-10 08:31）。
2. **晚安兜底 × 主动消息**：`_goodnight_due` 与 `_proactive_one` 其它分支都会在 23:00 触发；两处都各自写 pending/发送（11-21 23:33）。
3. **日记质检 × 逛街/照片/追问**：09:00 是日记质检通知时间，同时 push_to_owner 的试穿、photo_share 的定时自拍、followup 都可能在 9 点档触发。
4. **天气提醒 × 聊天回复**：`_maybe_weather_alert` 在整 20:00 直接 `_send_to_umo`，不等当前聊天回复批次结束（11-19 20:00:13）。
5. **热重载 × 双插件**：11-21 13:38:00 重载 watch、13:38:00–01 重载 forever，各自 tick 很快触发推送（13:38:13 主动消息 + 13:40:06/12 试穿推送）；reload 后 JobManager 首跑延迟 4s，多个入口同时醒。
6. **多平台/多会话**：同一人 lark / QQ / webchat 三个 umo；`_primary_channel_umo` 已收口主动消息，但 `push_to_owner`/`_weather_alert_umos` 走 `_owner_umos()` 列表，仍有跨平台兜底发送路径（历史 22 条发到 QQ）。

### 3.4 同类型刷屏

- **催问（nudge）**：11-12→11-22 共 29 条（其中 11-14 23:12 一条发往 QQ，其余 lark）。**5 分钟内 ≥2 条的簇 7 个**：11-15 20:13、11-18 08:21、11-19 15:10、11-19 19:10、11-21 15:15、11-21 21:15、**11-22 07:30（7 条）**。词表高度复用："喂喂，人呢？/醒醒，在等你呢。/我在这儿呢，你倒是看我一眼呀。/？怎么不理我了？"。
- **晚安**：两处入口各发一条（11-21 23:33；11-07 22:00/22:07）。
- **系统通知**：日记质检同文双发（09:00:15/19）。
- **试穿/买衣**：11-21 13:40 两条同款、11-22 09:00:31 + 10:32:09 同一件开衫又问一遍（跨 90 分钟，属"话题复读"，非发送重复）。

## 4. 来源入口清单（代码 → 触发条件 → 是否过统一出口）

**统一出口**：`amo_message.py:922 _send_to_umo(umo, text, quote_id, group)`（内含 `_outbound_allowed` 去重闸）；媒体 `amo_message.py:1000 _send_media_to_umo`。除此之外的直发都算旁路。

| 入口 | 代码 | 触发条件 | 走统一出口？ | 备注 |
|---|---|---|---|---|
| 聊天回复（分段） | `segment_engine.py:190 handle_segment_reply` → `event.send` 246-266 | 用户消息 → LLM 回复 | ❌（source=chat，设计上允许） | 走 `reply_pipeline` 守卫（含 repetition_guard/anti_nag），**不走 outbound 闸**；同轮自拆 1–4 段 |
| 聊天回复接管 | `amo_runtime_reply.py:195 _send_now` → `amo_message.py:860` | 需要绕开核心引用/@ 时 | ❌ | 直接 `event.send`，只过 `_remember_sent_reply` |
| 主动消息 | `amo_proactive.py:1413 _send_proactive_lines` → 1553 | `_proactive_one` 概率/上限/跳整点/晚安窗口 | ✅ 且设 `_outbound_source="proactive"`（1427-1450 有 90s burst 闸） | 批次内切 1–3 段；`proactive_log` 在此记录 |
| **未回复追问** | `amo_proactive.py:807 _check_proactive_pending` → 875 | pending 未回 ≥ `max(15, followup_minutes)` 分钟 | ❌ **不设 source（默认 chat）、无 burst、无单飞** | **07:30 7 条的入口**；生成在 `_followup_text_live`（761），发送后才写 `pending["followup"]` |
| 生日祝福 | `amo_proactive.py:1180` | 生日当天、每年一次 | 部分（无 source） | 低频 |
| 晚安兜底 | `amo_proactive.py:1680 _send_goodnight` → `_send_proactive_lines` | 23:00-00:30、5 分钟未说话、当天未说晚安 | ✅（经 _send_proactive_lines） | 与其它主动分支并发时无互斥（11-21 23:33） |
| 日记质检通知 | `amo_diary_audit.py:699 notify` → 729 `push_to_owner` | 每天 ≥09:00、有 HARD、开启推送 | ✅ source=push | 09:00 同文双发，当时并发 notify；当前工作区已有 `_NOTIFY_LOCK`+先占位（714-718） |
| 天气提醒 | `amo_schedule.py:2207` | 每天 20:00、次日变天、当天未发 | ✅（source=chat！） | **未设 source**，闸不拦；可插进聊天分段 |
| 延迟补话 | `amo_schedule.py:1303` | 私聊低概率延迟 4-18 分钟 | ✅（source=chat） | 有"已过期"检查，重复风险低 |
| 试穿/逛街/守护推送 | `amo_feature_api.py:411 push_to_owner` → 470 | watch 插件事件、网购、hunger 等 | ✅ source=push + 5min 内存节流（455-467） | 5min 节流是"读-判-写"非原子；与 proactive 共用 `_proactive_burst` 字典 |
| 照片/自拍 | `photo_share.py:471 _send` → `_send_media_to_umo` | 定时分享/请求出图/重发 | ✅（event_id 精确去重；文字 caption 无文本闸） | 回退分支 500 直发 |
| 照片失败话术 | `photo_share.py:447 _notify_failure` → `push_to_owner` | 出图失败 | ✅ source=push | 与照片同窗可能连发（失败话术+重试图） |
| 生图补图 | `draw_engine.py:1129/1582/1586` → `event.send` | 模型承诺但没调工具 | ❌ | 图 + 文案两条 |
| 画图测试/面板 | `amo_api_photo.py:469` → `context.send_message` | 面板操作 | ❌ | 人工触发 |
| 面板代说 | `amo_api.py:1020` | 面板"代她说一句" | ✅（source=chat） | 人工触发 |
| 命令回复 | `amo_commands.py:737` → `event.send` | 管理员命令 | ❌ | 低频 |
| TTS 语音 | `amo_runtime_reply.py:511 _maybe_send_voice_reply` | 语音回复 | 与文字同批 | 只过滤文本，不参与频控 |

> 结论：**当前有 4 类"主动/推送"（followup、天气、延迟补话、面板代说）未设非 chat source（等于不受闸），另有 4 类"直发"（分段聊天、_send_now、draw_engine、commands）完全独立于统一出口**；统一出口只覆盖了主动消息/晚安/推送/照片这几条路。

## 5. 根因

1. **双轨发送架构**：`_send_to_umo` 是"主动/推送"统一出口，但聊天回复走 `segment_engine`/`event.send`；照片有 `_send_media_to_umo`；draw/commands 直接 `event.send`。每加一个入口，就要记得再挂一次 `_outbound_source`/burst/占坑 —— 07:30 的 followup 就是漏挂。
2. **统一闸的"默认放行"语义**：`amo_outbound_guard.allow` 对 `source=chat` 直接 `return True`；而 `_outbound_source` 默认值就是 `"chat"`。**任何忘了设 source 的新入口都默认不受控**（fail-open）。加上历史 bug 把 source 字符串当 `now` 传给 `allow`（抛异常被吞），闸在一段时间内**对所有来源恒放行**。
3. **缺并发/幂等原语**：`_check_proactive_pending` 单次最多发 1 条，但"生成"与"占坑"之间是 await LLM（数分钟）；pending 的 `followup=True` 在**发送成功之后**才写。只要该函数被并发调用（或 tick 被 `_spawn` 而非 await），就会 N 次生成 + N 次发送。`proactive_log` 只有 #88（07:00:09）没有 07:30 的 7 条，说明 7 条**不是** `_send_proactive_lines` 发出的，而是 7 次 `_send_to_umo` 直调。
4. **生成层放大**：`_followup_text_live` 的提示词要求"撒娇、小小抱怨他晾着你、可自称你美丽的老婆"，没有"避免与最近追问同义"约束；模型对同一语义会给出多个变体（07:30 的 7 句就是同一意图的 7 种措辞），而切句/发送层没有"同意图只留一条"。
5. **去重规则分散且互相打架**：`amo_part_dedupe`（nudge 意图表，2026-11-21 为 21:15 事故写的）**从未接线**；`amo_repetition_guard` 只管聊天整句；`proactive_dedupe`（0.62）只管主动消息生成；`anti_nag` 只改写内容；`outbound_guard` 只 90s 同来源。四套规则四种窗口、四种阈值，没有单一事实源。
6. **记录层误导**：`_remember_bot_message` 对"180 秒内同文本 + 上一条用户消息未变"直接 return，**不发也不记**，所以 DB 里看不到第二条日记质检；`proactive_log` 只记录 `_send_proactive_lines`。用 DB/log 做审计会系统性低估重复。
7. **触发条件叠加**：早 07:00-09:30 同时存在 morning 问候、commute、日记质检、试穿、followup；23:00 同时存在晚安兜底、proactive、聊天回复。多个"到点"逻辑没有共享日级/小时级 slot。

## 6. 统一出口 / 频控方案（建议）

### 6.1 目标架构：单一 OutboundGateway

新增 `amo_outbound.py`（或扩展现有 `_send_to_umo` 为唯一 API）：

```
OutboundGateway.send(umo, parts, *, source, kind, batch_id=None,
                     dedupe_key=None, priority="normal", ttl=None) -> bool
```

- **唯一入口**：聊天分段、`_send_now`、主动消息、followup、晚安、日记质检、天气、试穿/守护推送、照片（含 caption）、draw 补图、命令回复全部改走这里；`segment_engine` 的 `event.send` 与 `draw_engine` 的 `event.send` 也收编（保留 AstrBot 引用/@ 适配，但发送动作由 gateway 执行）。
- **source 枚举**（必填，无默认）：`chat / proactive / followup / goodnight / reminder / push / photo / system / admin`。**fail-closed**：缺 source 视为 `push`（受控），不再默认 chat。
- **批（batch）语义**：一次回复/一次主动消息 = 一个 `batch_id`，parts 按 index 发出；频控按 batch 计"轮"，按 message 计"条"，同 batch 的段不触发跨入口节流，但会被意图去重。
- **每 umo 一把 asyncio.Lock** + `batch_registry`：同一 umo 同时只允许一个"非 chat 批"在发；chat 批在发时，非 chat 批进入 `pending_queue`，等 chat 批结束 + 冷却 30s 再发。
- **落库** `outbound_log(umo, batch_id, part_index, source, kind, text, ts, decision, reason)`：既是去重依据，也是审计事实源（替代 `proactive_log` 的部分职责）。

### 6.2 频控参数（建议初值，均可配置）

| 规则 | 参数 | 说明 |
|---|---|---|
| 单批段数 | ≤ 4 段 | 超过合并/截断（已有 `_segment` 上限） |
| 同 umo 突发 | 20s ≤ 4 条 | 覆盖"一次回复 3-4 段"的正常情况 |
| 同 umo 短窗 | 60s ≤ 6 条、300s ≤ 10 条 | 超过则非 chat 批排队 |
| 非 chat 入口间隔 | 同 source 90s；**跨 source 300s** | `followup/proactive/push/reminder/system` 共用 |
| 催问 nudge | 20min ≤ 1 次；1h ≤ 2 次；沉默 ≥6h = 0 | 与 `amo_presence` 一致，另加"同一 pending 只追问一次" |
| 早安/晚安 | 同日 ≤ 1 次（按 sleep_day 归日） | 两处入口共用一个日 slot |
| 日记质检/天气/晚安 | 同日同 kind ≤ 1 次 | kv 占位（先占后发） |
| 免打扰 | 23:00-07:30 非白名单 = 0 | 白名单沿用 `amo_proactive_gate.NIGHT_KINDS` |
| 日上限 | 非 chat 主动 ≤ 6 条/日 | 沿用 `DAILY_CAP` |

### 6.3 去重规则

1. **归一化**：去标点/空白、去"你美丽的/美丽的"前缀（已有 `_key`），意图打标（见 3.2 词表）。
2. **窗口 10 分钟**（当前 90 秒太短，覆盖不了 11-19 08:10 与 09:55 的跨小时同题；但 10 分钟足够覆盖一次会话）：与 `outbound_log` 最近 20 条比较，`SequenceMatcher ≥ 0.60` 或"同意图 + 近义关键词命中" → 丢弃并记 `reason=dup_intent`。
3. **跨来源**：不再按 source 分 key，改为 `outbound:<umo>` 一个 key，去重记录里带 source，用于解释"被谁压掉"。
4. **原子性**：去重 kv 的读-判-写放在 per-umo Lock 内，或落库用唯一约束 `(umo, dedupe_key)` + 事务（避免并发同时放行）。
5. **意图级规则**：`nudge` 20 分钟 1 条；`goodnight/morning` 同日 1 条；`system`（日记质检/天气）同日 1 条；`photo` 按 event_id 90s（保留现有精确去重，避免 16 位 event_id 相似度误判）。

### 6.4 并发与顺序

- **每入口单飞**：`_check_proactive_pending`、`_diary_audit_tick`、`push_to_owner`、`_maybe_proactive` 都加单飞（已有 `_proactive_running` 模式的通用实现）。
- **先占坑再生成**：followup 改为"生成前把 `pending.followup_at` 占上（带 TTL，生成失败回滚）"；生成后再校验 pending 是否已被别的任务处理。日记质检已有 `_NOTIFY_LOCK` + 先占位，保持。
- **批互斥**：chat 批在发时，非 chat 批等待并合并（同一 umo 30s 内多个非 chat 请求合并成一条）；绝不把推送插进聊天分段中间（修 11-19 20:00 的天气插队）。
- **重载保护**：插件重载后首个 tick 的主动/推送延后 30s（或 reload 后 60s 内只允许 chat），避免双插件同时醒（11-21 13:38）。

### 6.5 观测与回归

- 日志统一格式：`[outbound] send|drop umo=… source=… kind=… batch=… n=… reason=…`；统计口径直接复用本报告 §2 的滑窗查询。
- 新增 `/amo outbound-stats`（只读）：最近 24h 60s/300s 窗口 TopN、重复拦截计数、按 source 计数。
- 回归用例（按真实记录）：
  1. 07:30 场景：pending 未回 20min + 7 次并发 `_check_proactive_pending` → 只发 1 条；
  2. 11-21 23:33：晚安兜底 + proactive/其他入口同时 → 只发 1 条；
  3. 11-22 09:00：日记质检 notify 并发 2 次 → 只发 1 条；日记质检 + 试穿 push 同 umo 300s → 后者排队；
  4. 11-10 08:31：commute + morning 同窗 → 后到者排队且状态取最新；
  5. 11-19 20:00：chat 分段期间天气提醒 → 等 chat 批结束 + 30s；
  6. 11-21 21:15：两句同义催问（相似度 0.80）→ 只发 1 条；
  7. `source` 缺省/非法 → 按 push 受控（fail-closed），不可默认 chat。

### 6.6 落地顺序（最小改动，先止血再收口）

1. **止血**：`_check_proactive_pending` 套 `source("followup")` context + 单飞 + 生成前占坑；`weather_alert/delayed_reply/api_say` 补 source；`_send_media_to_umo` 文字 caption 过文本闸；`allow()` 的 `now` 参数 bug 已在工作区修复，确保随 10:26 版本部署。
2. **统一**：把 §4 表中的入口逐个改接 `OutboundGateway`（先接文本，再接图片/caption，最后接 `event.send` 路径）。
3. **频控**：接 per-umo 窗口 + batch 互斥 + 意图去重；删除被取代的局部规则（`amo_part_dedupe` 决定是否保留为 gateway 的意图表；否则删除死模块）。
4. **记录**：`outbound_log` 落库 + 统计接口 + 每日自检（>N 条/窗口告警）。
5. **验收**：用本报告 §2 的 6 个现场做回归，指标见下。

### 6.7 验收指标

- 0 个 60s 窗口 > 6 条（单轮 chat 批内部 ≤4 段且带 batch 标记；非 chat 批 ≤2 条）；
- 0 对"近似重复（≥0.60，10 分钟内）"发送；0 条 nudge 在 20 分钟内重复；
- 沉默 ≥6h 后 followup/催问 = 0；晚安/早安/日记质检/天气 同日同 kind 重复 = 0；
- 非 chat 入口 5 分钟内 ≥2 个来源同 umo = 0；chat 批进行中插入非 chat = 0；
- `outbound_log` 中每条丢弃都有 `reason`（可解释率 100%）。

## 7. 附：证据索引

- 07:30 七连：`astrbot.log` 2026-11-22 07:30:04.609–07:30:11.407（7 条 `分段延迟` + 7 条 `[amo] 主动 ->`）；`amo_data.db` messages #2269–#2275。
- 双晚安：`amo_forever.db` proactive_log #86/#87（created 1795275199.279/.291）；messages #2265/#2266。
- 日记质检双发：`astrbot.log` 2026-11-22 09:00:15.766 / 09:00:19.061；messages #2278。
- 09:16 编造等待：`astrbot.log` 2026-11-22 09:16:15.541；messages #2281；用户最后一条 11-21 22:03:27。
- 11-19 08:00 双批：proactive_log #75/#76（相隔 2.3s）；messages summary #2276（11-19 08:00:35，共 4 条）保留了这一段。
- 11-10 08:31：proactive_log #28/#29（相隔 6.1s）；messages summary #1085/#1086。
- 11-21 23:33 双晚安、13:38 双入口、21:15 同义催问：proactive_log #83/#86/#87；messages #2248/#2249。
- 11-19 20:00 天气插队：`amo_schedule` 日志 `天气提醒已发送` 20:00:11.399；messages 19:59:56–20:01:21 聊天分段；天气句 "明天要下雨…" 在 20:00:13。
- 催问词表与 29 条清单：`%TEMP%\amo_audit\final2.txt`（分析产物，未入仓库）。
- 代码位置（当前工作区）：`amo_message.py:904 _outbound_allowed / :922 _send_to_umo / :1000 _send_media_to_umo / :860 _send_now / :787 _remember_bot_message 去重`；`amo_proactive.py:807 _check_proactive_pending / :761 _followup_text_live / :1413 _send_proactive_lines / :1734 _proactive_one`；`amo_outbound_guard.py:33 allow`；`amo_part_dedupe.py:31 dedupe（未接线）`；`amo_feature_api.py:411 push_to_owner`；`amo_diary_audit.py:699 notify`；`segment_engine.py:190/246/277`；`photo_share.py:447/471/500`；`draw_engine.py:1129/1582/1586`。

## 8. 待确认（不闭环项）

1. 07:30 的 7 次 followup 调用为何并发：当前工作区 `_check_proactive_pending` 由 tick 单飞任务 await，理论上单次只发 1 条；06:23 运行版本可能存在"每 tick spawn + 生成前不复查 pending"路径。**建议**：修复合入后按 §6.5 用例 1 压测复现，确认单飞+占坑后不再出现。
2. 09:00 日记质检双发：工作区 `amo_diary_audit.py` 在 10:01 有改动（含 714-718 锁+先占位），但运行版本是 06:23 加载，无法从日志确认当时是否有锁。**建议**：部署后观察一天 `outbound_log` 的 `system` 来源。
3. 数据缺口：`messages` 的 180s 同文本去重会隐藏"完全复读"；如需审计这类问题，应以 `outbound_log`（新）或 AstrBot 平台侧发送日志为准。
4. 跨平台：QQ/webchat 的重复未在本次逐条展开（数据量小：11-12→11-22 QQ 主动 22 条、webchat 4 条）；统一出口落地后应自动覆盖。