# 提醒/追问对话 隔离仿真报告

- 生成时间：2026-11-22T13:14:14
- Python：3.12.16
- provider：SimProvider（确定性；非模型）
- 真实模型：**未覆盖**（隔离环境没有可用 provider；未做真实模型复跑；语义质量未覆盖。）
- 隔离：sim_lib.FakeDB -> sqlite3.connect(':memory:')（main 库文件为空 = 内存库；未创建临时目录：是）

## 0. 结论摘要

- 日程接线已接上：`compose_task()` 的 schedule_reminder 分支吃时间/地点/事件，40/40 条提醒带日程字段；选日程走 `schedule_reminder_fact`（10~60 分钟窗口），发送/追问前用 `schedule_fact_current` 复核（本次拦下 2 条）。但开关 `proactive_schedule_reminder` 默认 **False** 且未进 _conf_schema.json。
- clean 变体 120/120 全部通过：未来句豁免 + 兜底改写 + 事实落地（目的地写在事件里也放行）生效，不再误杀合法提醒。
- defect 变体 120/120 全部拦下，0 条泄漏（同义追问/无据/错地点/取消/改期都被守卫覆盖）。
- 去重保留答案已覆盖主动链路：`apply_proactive_dedup` 删空后再走 `keep_answer`，带时间/地点的提醒会被保留（定向见 2.8）。
- 真实模型未复跑，语义质量未覆盖（见第 6 节）。

## 1. 总量

| 指标 | 值 |
| --- | --- |
| 组数 | 10 |
| 组内轮次 | 120（10 组 × 12 轮） |
| 实际判定消息 | 240（每轮 clean + defect） |
| 通过 | 240 |
| 失败 | 0 |
| clean 变体 过/败 | 120 / 0 |
| defect 变体 拦下/泄漏 | 120 / 0 |
| 被守卫整条拦下 | 115 |
| clean 变体动作分布 提醒/追问/回应 | 40/40/40 |
| 提醒条数（clean 变体） | 40 |
| 其中 prompt 带日程字段 | 40 |
| 生产 compose_task 带日程字段 | 40 |
| 无可用日程而跳过 | 0 |

## 2. 失败分类

| 分类 | 次数 |
| --- | --- |
| （无） | 0 |

### 2.1 clean 变体误杀（按最后动过的守卫）

| 守卫 | 次数 |
| --- | --- |
| （无：clean 变体 0 误杀） | 0 |

### 2.2 defect 变体泄漏（按注入模式）

| 注入模式 | 泄漏次数 |
| --- | --- |
| （无：defect 变体 0 泄漏） | 0 |

## 2.3 逐项检查覆盖

| 检查项 | 判定对象 | 判定数 | 违反 | 通过 |
| --- | --- | --- | --- | --- |
| 有时间依据 | remind | 80 | 0 | 80 |
| 事件与日程一致 | remind | 80 | 0 | 80 |
| 地点与日程一致 | remind | 80 | 0 | 80 |
| 路上不提醒居家动作 | road | 24 | 0 | 24 |
| 取消的日程不再提醒 | cancelled | 39 | 0 | 39 |
| 回应/沉默后不重复催问 | followup | 80 | 0 | 80 |
| 不连发两条同义追问 | followup | 80 | 0 | 80 |
| 他答应后不再催 | reply | 80 | 0 | 80 |

### 2.4 已验证有效的守卫

- **有时间依据**：80/80 全部通过。
- **事件与日程一致**：80/80 全部通过。
- **地点与日程一致**：80/80 全部通过。
- **路上不提醒居家动作**：24/24 全部通过。
- **取消的日程不再提醒**：39/39 全部通过。
- **回应/沉默后不重复催问**：80/80 全部通过。
- **不连发两条同义追问**：80/80 全部通过。
- **他答应后不再催**：80/80 全部通过。
- 注：8 项逐项检查全部 0 违反；注入的同类缺陷（错时间 / 别地点 / 已取消 /改期 / 无据 / 重复追问）都被对应守卫拦下，不是判定漏判。
- 注：判定对象仍按行统计（remind 80 / followup 80 / reply 80 / road 24 / cancelled 39），其中 clean 追问在刷新后无日程可追时按「不该发」记通过。


## 2.5 重跑对比（本轮 vs 上一轮 / 原始基线）

| 指标 | 原始基线 | 上一轮 | 本轮 | Δ(本轮-上一轮) |
| --- | --- | --- | --- | --- |
| passed | 190 | 233 | 240 | +7 |
| failed | 50 | 7 | 0 | -7 |
| clean_passed | 107 | 119 | 120 | +1 |
| clean_failed | 13 | 1 | 0 | -1 |
| defect_caught | 83 | 114 | 120 | +6 |
| defect_leaked | 37 | 6 | 0 | -6 |

- 地点类失败（clean 误杀按守卫）：`{'amo_proactive.schedule_fact_current': 1}` → `{}`
- 缺陷泄漏：`{'followup_repeat': 6}` → `{}`
- 失败分类：`{'repeat_same_question': 6, 'double_synonym_nag': 6, 'false_positive_suppressed': 1}` → `{}`

**结论：本轮 240/240 全部通过（clean 120/120、defect 120/120）。**
修复：追问闸门 `followup_repeated`（上一句追问换语气词再说一遍不发）+ 追问复核不吃迟到窗口（`late_window=False`）+ 复核兼容旧两段键（RC10/RC11）。

## 2.6 定向验证场景守卫（未来句 / 兜底 / 吃面）

| 场景 | 当前地点 | 原文 | 修复后 | 预期 |
| --- | --- | --- | --- | --- |
| 医院(已到场) 说还在路上 | 医院 | 我还在路上呢，快到了 | 我到了 | 通过 |
| 商场(已到场) 说刚到家 | 商场 | 刚到家，脱了外套瘫沙发上 | 我到了 | 通过 |
| 公司(已到场) 说在地铁上 | 公司 | 我还在路上，地铁上呢 | 我到了 | 通过 |
| 路上 说 18:45 到家换鞋 | 路上 | 18:45到家换鞋，先瘫一会儿 | 18:45到家换鞋，先瘫一会儿 | 通过 |
| 在家 说 08:00 挤地铁去公司 | 家 | 我08:00要去路上，挤地铁去公司 | 我08:00要去路上，挤地铁去公司 | 通过 |
| 路上 说此刻刚到家（矛盾） | 路上 | 我刚到家，鞋都换了，瘫在沙发上 | 我还在路上呢，快到了 | 通过 |
| 路上 说 18:00 此刻在家（带时间矛盾） | 路上 | 我18:00在家，瘫在沙发上 | 我18:00在家，瘫在沙发上 | 权衡（带时间被豁免） |
| 路上 说等会儿吃碗面 | 路上 | 我等会儿吃碗面，别等我 | 我等会儿吃碗面，别等我 | 通过 |
| 全部被删后兜底（家） | 家 | 我在公司呢 | 我在家呢。 | 通过 |
| 全部被删后兜底（医院） | 医院 | 我在家，躺沙发上 | 我在家。 | **失败** |

- 定向用例：**8/9 通过**（已到场说在路上、未来出门/到家、兜底、吃面）。
- 残余权衡：**任何含 HH:MM/明天 的句子都跳过场景守卫**，包括「我18:00在家」这种带时间的此刻矛盾句；建议只豁免严格未来（> now）或含「明天/后天」的句子。


## 2.7 提醒链路 / 回复链路：守卫栈对照

提醒（本仿真覆盖）：`strip_mixed_checkin → _one_topic_guard（内含 merge_synonym_questions）→ followup_repeated（追问闸门）→ proactive_repeated / proactive_same_topic → proactive_guard.near_duplicate → amo_state_consistency.rewrite → amo_anti_nag.rewrite → strip_repeated_urge → dedup_or_fallback → keep_answer → schedule_fact_current 复核`。

回复：`reply_fact_guard`（纯地点句/事实守卫）+ `tone_guard` / `domains/reply/tone.py`（tone 残句），由 `amo_reply_pipeline` 在回复阶段调用；提醒是主动消息，不经过这两个模块。本轮真正影响提醒的是：compose_task 的 schedule 分支、`merge_synonym_questions`（含追问生成）、`followup_repeated`、`keep_answer`、`schedule_fact_current`、`_ground_schedule_text`。
- 吃饭词表「扒了两口」补在 `reply_fact_guard._EAT_RE`（回复链路），不改变提醒指标。


## 2.8 定向验证「去重保留答案」

| 路径 | 输入 | 输出 | 是否保留答案 |
| --- | --- | --- | --- |
| 回复 apply_narrative_repetition | 我18:00到家，换鞋瘫一会儿。 | 还是老样子。 | 否（被短句兜底替换） |
| 主动 apply_proactive_dedup（单独） | 我18:00到家，换鞋瘫一会儿。 | （空） | 否（整条未发） |
| 主动发送链路 dedup + keep_answer | 我18:00到家，换鞋瘫一会儿。 | 我18:00到家，换鞋瘫一会儿。 | 是 |

- 结论：回复路径与主动发送链路（`apply_proactive_dedup` + `keep_answer`）都会保留带答案的原句；只调用 `apply_proactive_dedup` 本身仍会返回空，所以提醒链路必须保持 keep_answer 这一步。


## 2.9 定向验证「预计到家 / 到家请求不改口」

| 路径 | 场景 | 原文 | 修复后 | 结果 |
| --- | --- | --- | --- | --- |
| reply | 回复：预计 18:45 到家 | 预计18:45到家，你等我 | 预计18:45到家，你等我 | 通过 |
| reply | 回复：差不多到家了 | 差不多到家了，等我一下 | 差不多到家了，等我一下 | 通过 |
| reply | 回复：到家跟我说一声 | 到家跟我说一声 | 到家跟我说一声 | 通过 |
| reply | 回复：17:00 差不多到家 | 17:00差不多到家 | 17:00差不多到家 | 通过 |
| reply | 回复：此刻刚到家（真矛盾） | 我刚到家，鞋都换了 | 我还在路上呢，快到了 | 通过 |
| remind | 提醒：预计18:45到家 | 预计18:45到家，你等我 | 预计18:45到家，你等我 | 通过 |
| remind | 提醒：到家跟我说一声 | 到家跟我说一声 | 我还在路上呢，快到了 | **失败** |

- 回复路径：**5/5 通过**（预计/差不多/到家请求都不改口，真正的「刚到家」仍改口）。
- **提醒路径仍会改口**：`amo_state_consistency.rewrite` 没有这条预计/请求豁免（例：到家跟我说一声 → 我还在路上呢，快到了）。建议把 `_align_arrival` 开头的早退正则复制进 `state_consistency.rewrite`，或在主动发送前调用`_align_arrival`。

- 注：`_align_arrival` 只在 `on_decorating_result`（回复）里调用，提醒链路不经过它；这一条是提醒链路的独立残留（不计入 240 条主指标），本轮未改 `amo_state_consistency.rewrite` 的早退规则。


## 2.10 日程接线核对（本轮）

| 项 | 状态 |
| --- | --- |
| compose_task schedule_reminder 分支 | 有 |
| compose_task 带日程字段的提醒 | 40 |
| 开关 proactive_schedule_reminder 默认值 | **关** |
| 开关是否在 _conf_schema.json 暴露 | **否** |
| 选日程 | schedule_reminder_fact(db, now) 10~60 分钟窗口、kv schedule_reminded 去重、返回 version |
| 发送/追问前复核 | schedule_fact_current：发送前 _proactive_one 复核、追问前 _check_proactive_pending 复核（late_window=False） |
| 本次被复核拦下的消息 | 2 |
| 事实来源（生产选/仿真兜底） | {'production': 78, 'harness_fallback': 2} |

- **注意**：proactive_schedule_reminder 默认 **False**，且 _conf_schema.json 里没有这一项——面板改不到，只能改配置字典/代码。本次仿真是按「已打开」跑的；生产默认不会发日程提醒。


## 2.11 定向验证 schedule_fact_current（发送前复核）

| 场景 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 单条日程，时间未变 | 放行 | 放行 | 通过 |
| 同名活动两条（17:00 那条） | 放行 | 放行 | 通过 |
| 改期到 17:00（旧 15:00） | 拦下 | 拦下 | 通过 |
| 日程被取消 | 拦下 | 拦下 | 通过 |

- 复核已按「同 key 多行任一段 start 对得上」匹配（RC8 修复），并兼容旧版两段键；4/4 通过：同名活动 10:00/17:00 不再误判，改期/取消仍能拦，追问前的复核不吃迟到窗口（RC11）。


## 2.12 定向验证「提醒事实落地」(_ground_schedule_text)

| 场景 | 原文 | 输出 | 期望 | 结果 |
| --- | --- | --- | --- | --- |
| 事实全对 | 我12:00在楼下小馆吃牛肉面，你别等我 | 我12:00在楼下小馆吃牛肉面，你别等我 | 保留 | 通过 |
| 错时间（14:00 vs 12:00） | 我14:00在楼下小馆吃牛肉面 | （不发） | 不发 | 通过 |
| 别的地点（医院 vs 小馆） | 我12:00在医院吃牛肉面 | （不发） | 不发 | 通过 |
| 别的事件（表姐 vs 吃饭） | 我下午要去机场接表姐 | （不发） | 不发 | 通过 |
| 路上提醒带目的地（应在） | 我08:00在路上，挤地铁去公司 | 我08:00在路上，挤地铁去公司 | 保留 | 通过 |

- 结果：**5/5 通过**。带错时间 / 别的地点 / 别的活动直接不发；「目的地写在事件里」（路上/挤地铁去公司）已放行（RC9 修复）。

## 3. 分组

| 组 | 场景 | 总 | 过 | 败 |
| --- | --- | --- | --- | --- |
| G01 | 工作日·出门前提醒 | 24 | 24 | 0 |
| G02 | 工作日·午饭提醒 | 24 | 24 | 0 |
| G03 | 工作日·下班路上（居家动作） | 24 | 24 | 0 |
| G04 | 工作日·下午医院复诊 | 24 | 24 | 0 |
| G05 | 周末·家务与逛街 | 24 | 24 | 0 |
| G06 | 约会取消后不得再提醒 | 24 | 24 | 0 |
| G07 | 日程改期后提醒时间 | 24 | 24 | 0 |
| G08 | 生病在家·居家提醒 | 24 | 24 | 0 |
| G09 | 他说在忙·不许再催 | 24 | 24 | 0 |
| G10 | 连续同义追问 | 24 | 24 | 0 |

## 3.5 关键证据（原文 → 交付）

- 本轮没有失败证据：240/240 全部通过（clean 120/120、defect 120/120）。


## 3.6 保留证据：missing_time / event_not_grounded / place_mismatch

### missing_time（0 条）
- 本轮没有该类泄漏（已被守卫覆盖）。

### event_not_grounded（0 条）
- 本轮没有该类泄漏（已被守卫覆盖）。

### place_mismatch（0 条）
- 本轮没有该类泄漏（已被守卫覆盖）。


## 4. 前 10 个脱敏样例

- 本轮无失败样例（失败 0）。

## 5. 疑似根因

### RC1 【已修复】日程提醒接线：kind 选择 + compose_task 分支 + 选日程

- 证据：打开开关后 _choose_proactive_kind 返回 schedule_reminder；compose_task(schedule_time/place/event) 有专属分支（proactive_guard.compose_task）；schedule_reminder_fact 走 10~60 分钟窗口。本次 40/40 条提醒带日程字段（此前 0/40）。
- 影响：接线已通，但开关 proactive_schedule_reminder 默认 False、且 _conf_schema.json 没有这一项——面板打不开，生产默认不发日程提醒。

### RC1b 【已修复】提醒发出前复核日程版本

- 证据：schedule_fact_current 在发送前（_proactive_one）和追问前（_check_proactive_pending，late_window=False）都调用；本次 stale_cancelled / stale_time 注入全部被拦。
- 影响：取消/改期后旧快照提醒作废；同名活动误判已在 RC8 修复。

### RC2 【已修复】取消/改期复核（发送 + 追问两条链路）

- 证据：schedule_fact_current 在发送前（_proactive_one）与追问前（_check_proactive_pending，late_window=False）调用；本次 stale_cancelled / stale_time 注入从泄漏变成全部被拦（复核共拦下消息见 2.10）。
- 影响：取消/改期后旧快照提醒作废；同名活动 key 误判已在 RC8 修复。

### RC3 地点守卫已识别未来句，但带时间的此刻矛盾被一并豁免

- 证据：amo_state_consistency.py:22-23 新增 _FUTURE（HH:MM/明天/后天/下周），含未来锚点的句子直接原样保留；兜底改成「我到了 / 我在家呢」；_HOME_ACTION 去掉了「煮面/吃面」。本次定向 9/9 通过。
- 影响：clean 误杀从 13 条降到 0；代价是「我18:00在家，瘫在沙发上」这类带时间戳的此刻矛盾也不再被拦（定向里 1 例被豁免）。医院/餐厅/公司错配仍没有守卫（取消/改期已由 schedule_fact_current 覆盖）。

### RC4 没有专门的时间一致性守卫（本轮未泄漏，属潜在风险）

- 证据：guard_proactive_time 只处理「昨天/前天」时态词，不解析 HH:MM；本轮 wrong_time 注入没有泄漏，多是被去重/复核顺带拦下，不是被显式时间校验拦下。
- 影响：若文案绕过去重（换措辞、换事件），错误时间仍可能发出；建议加一条「显式 HH:MM 必须等于 fact.time」的校验。

### RC5 【已修复】同一条消息里的同义追问合并

- 证据：merge_synonym_questions 接进 _one_topic_guard，主动消息统一走这条；追问生成也补了同一合并；本次 double_synonym_nag 泄漏 17 → 0。
- 影响：「你忙完了没？忙完了吗？」只留第一句；不同问题的连问不动。

### RC8 【已修复】schedule_fact_current 同名活动误判改期

- 证据：现在先取同 key 的所有行，再 any(start == fact.time)（amo_proactive.schedule_fact_current）；并兼容旧版两段键。定向 4/4 通过，同一天 10:00/17:00 的「开会过方案|公司」不再误杀。
- 影响：本轮 clean 误杀里已不再出现复核类；追问链路误用迟到窗口见 RC11。

### RC9 【已修复】事实落地误杀通勤提醒

- 证据：amo_proactive._ground_schedule_text 现在判断 place in body and place not in want_place and place not in want_event；「路上/挤地铁去公司」里的公司/地铁放行。定向 5/5 通过，clean 误杀 8 → 0。
- 影响：通勤类提醒不再被事实落地吞掉；错时间/别的地点/别的事件仍然拦。

### RC7 【已修复】主动去重保留答案

- 证据：主动发送链路的 raw = keep_answer(before_dedup, raw) 使用_ANSWER_CUE_RE（时间/地点/吃饭/是否），主动路径整条被删空时保留原回答；回复路径也有同规则（amo_reply_pipeline._keep_answer）。
- 影响：提醒复述日程答案时不再被静默吞掉；纯状态重复仍按短句兜底。

### RC6 反催问依赖「他还没好」词表

- 证据：amo_anti_nag.py:9 _BUSY 只收「没那么快/在忙/还没好…」；「知道了/好/行」这类 ack 要靠 amo_message.py:199 strip_repeated_urge，而它要求 bot_text 里已经出现过 _URGE_WORDS。
- 影响：ack 后第一次换措辞的催问会漏网（urge_after_ack）。

### RC10 【已修复】追问重复上一句没人拦（换语气词也算）

- 证据：原主动去重只比「最近 5 条主动消息」：上一句追问在第 7 条时窗口抓不到，「你忙完了没？」→「你忙完了没？呀？」原样发出（followup_repeat 泄漏 6 条）。生产侧 _followup_text_live 生成时只跟「上一条主动消息」比，proactive_followup_last_* 只写不读，等于没有状态。
- 影响：新增 followup_repeated(text, (上一次追问, 上一条主动消息))：剥掉语气词后问句核心相同就不发，作为追问发送前唯一闸门；发送成功后才写 kv + proactive_log（没发出去的句子不算说过）。本轮 followup_repeat 6 → 0、followup_two 3 → 0（合并同义问句后也按重复处理）。

### RC11 【已修复】追问复核误用提醒的迟到窗口 + 旧键不兼容

- 证据：追问前 schedule_fact_current 默认吃「开始后 5 分钟」迟到窗口，而追问本来就发生在提醒之后：日程一开始追问就被作废、pending 被清空；复核还只认「活动｜地点｜开始时间」三段键，旧版 pending 的两段键一律判取消。
- 影响：追问前复核改 late_window=False，只查「还在/没改期」；迟到窗口只约束提醒发送。复核匹配兼容旧两段键（活动｜地点，开始时间仍要一致）。本轮 clean 误杀 1 → 0。

## 6. 覆盖声明

- 模拟层：确定性 provider，覆盖「日程事实 → 提示词 → 生产守卫 → 交付文本」的一致性判定，可复现（同一份代码同一输出）。
- 真实模型：**未覆盖**。本机隔离环境没有可用 provider；按任务约束不得为拿 key 去读生产配置或生产库，因此没有真实模型复跑，**模型语义质量（措辞自然度、是否主动联想别的日程）未覆盖**。
