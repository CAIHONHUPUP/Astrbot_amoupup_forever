# 日常对话 50 组模拟报告（隔离模拟器 / 模拟 provider）

生成时间：2026-11-22 23:22:53

> 本轮结论来自 **可复现的模拟 provider**，不是真实模型。
> 真实模型语义质量未覆盖（拿不到隔离 provider，详见第 9 节）。

## 0. 两层结论（先看这个）

- **模拟层（本轮，覆盖全部 50 组 / 614 轮）**：组通过 50/50，单轮通过 100.0%；注入偏差 204 次，修回 173、残留 0；守卫自己改坏 0 次。
- **真实模型层**：已跑（model=deepseek-chat）。
  真实模型复跑脚本 real_probe.py 只接受环境变量/本机 ollama provider，不读 AstrBot 配置、不碰生产库。

## 1. 结论摘要

| 指标 | 数值 |
|---|---|
| 组数 | 50 |
| 通过组 / 失败组 | 50 / 0（组通过率 100.0%） |
| 轮数 | 614 |
| 单轮通过 / 失败 | 614 / 0（100.0%） |
| 含探针的轮数 | 300 |
| 对照组（不注入偏差）轮数 | 118，失败 0 |
| 无任何矛盾（只算地点/动作/同伴/旧场景/吃饭/健康）的组 | 50 / 50（100.0%） |
| 含矛盾的轮数 | 0（0.0%） |

关键点：

- 注入偏差 204 次，守卫修回 173 次，残留 0 次；另有 **守卫自己改坏 0 次**（候选本来是干净的）。
- 对照组 0 失败：干净输入没有被守卫改坏。

## 2. 失败分类

| 主因 | 轮数 |
|---|---|

同一轮可能有多个问题（例如「旧场景」同时是地点矛盾），上表按优先级只记主因；下表是全量计数。

| code | 出现轮数 |
|---|---|

## 3. 注入偏差 -> 修复矩阵

| 偏差类型 | 注入 | 其中 edge 写法 | 修回 | 残留 | 候选本来就干净 |
|---|---|---|---|---|---|
| action | 35 | 12 | 27 | 0 | 8 |
| companion | 33 | 8 | 31 | 0 | 2 |
| health | 5 | 3 | 3 | 0 | 2 |
| irrelevant | 53 | 7 | 52 | 0 | 1 |
| meal | 39 | 8 | 21 | 0 | 18 |
| place | 39 | 12 | 39 | 0 | 0 |

## 4. 探针回答率

「干净提问」= 这一轮没有注入任何偏差，是模型（模拟层）本来就答对的输入；它更能说明守卫自己有没有把答案改没。

| 探针 | 全部提问 | 全部未答 | 干净提问 | 干净未答 | 干净回答率 |
|---|---|---|---|---|---|
| doing | 67 | 0 | 30 | 0 | 100.0% |
| eaten | 56 | 0 | 26 | 0 | 100.0% |
| when_return | 61 | 0 | 27 | 0 | 100.0% |
| where | 58 | 0 | 23 | 0 | 100.0% |
| who | 58 | 0 | 21 | 0 | 100.0% |

干净提问共 127 轮，其中被守卫改成不答 0 轮（0.0%）。

## 5. 按日程模板

| 模板 | 组 | 通过组 | 轮 | 失败轮 |
|---|---|---|---|---|
| cafe_work | 5 | 5 | 60 | 0 |
| date_day | 5 | 5 | 57 | 0 |
| gym_day | 5 | 5 | 64 | 0 |
| hospital_day | 5 | 5 | 59 | 0 |
| office_early | 5 | 5 | 59 | 0 |
| rest_home | 5 | 5 | 63 | 0 |
| scenic_trip | 5 | 5 | 65 | 0 |
| sick_home | 5 | 5 | 62 | 0 |
| weekend_mall | 5 | 5 | 61 | 0 |
| workday | 5 | 5 | 64 | 0 |

## 6. 前 10 个脱敏样例

样例是合成对话（非真实用户），脱敏=去掉 uid、把「老公」写成「他」。

## 7. 上下文一致性 / 单点复现

| 检查 | 结果 | 说明 |
|---|---|---|
| prompt_matches_state | PASS | 10:30 商场块的官方 snapshot_text 里地点=商场 |
| expired_jit_falls_back | PASS | kv current_action 的 until 已过期 -> get_current_action 应回落到日程 |
| two_readers_agree | FAIL | get_current_action(照片/事实链路) 与 state.current_activity(聊天/守卫链路) 是否同一答案 |
| narrative_place_cache_stale | FAIL | 视图缓存(2s TTL)内 narrative_place 用缓存里的 now；跨块后仍返回旧地点 |
| place_answer_not_rewritten | PASS | 回答「你在哪」的纯地点句「我在公司呢」不该被 meal 的 activity 规则改成「我在吃饭呢」 |
| outside_eating_not_deleted | PASS | zone=restaurant 时「吃饭」被当居家动作整句删掉（复现） |
| tone_guard_keeps_content | PASS | 口头禅去复读把句子削成残句（复现） |
| mall_arrival_not_road | PASS | zone=mall 时 arrival 兜底句应说商场/到家，不该说在路上（复现） |
| guard_output_grammar | PASS | 地点改口 + 动作改口 + 语气池叠加后是否出现「我在家在休息呢」这类叠字 |
| dedup_keeps_answer | PASS | 整句复读兜底会把探针回答换成固定短句（复现） |
| status_dedup_keeps_answer | PASS | 状态去重删空时应保留「几点到家」这类答案，而不是兜底成「还是老样子。」 |
| share_opener_keeps_tail | PASS | 她重复“我刚刷到…视频”时只替换开头，后面内容要保留 |
| share_role_keeps_tail | PASS | 「你快看这个视频，…，后面还有一只狗」整句被删时后文也丢了 |
| meal_rewrite_grammar | PASS | 非饭点改口应整句收干净，不能留「等会儿弄点吃的两口面包」「刚等会儿弄点吃的」 |
| place_rewrite_grammar | PASS | 去掉地点短语后不能留下「刚在转了一圈」这种病句 |
| question_when_matches_place | PASS | 在办公室被问「几点回」，兜底句应按当前地点/日程，不能一律「还在路上，快到了」 |
| question_when_no_false_trigger | PASS | 「我妈问我们什么时候回去」是转述，不是问她的行程，不该被替换成行程句 |
| arrival_estimate_not_rewritten | PASS | 在办公室说「17:00 差不多到家」（回答几点回）不该被改成「我还在外面呢」 |
| arrival_request_not_rewritten | PASS | 「到家跟我说一声」是在对他说，不该被改写成她自己的位置句 |
| question_intent_coverage | PASS | 回不回来/到公司了吗/旁边有人吗/偷懒 这些口语问法也要被问句守卫认出来并答到点上 |
| share_context_not_hijacked | PASS | 历史里有分享痕迹时，状态问句不能被分享接话改写成「就是事的事，…」 |
| work_present_claim_while_sleeping | PASS | 睡觉时「还在改方案」要改口，「刚开完会」是近过去可保留 |
| place_orient_keeps_answer | PASS | 「我在家这边躲雨」要换成当前地点并保留方位词，不能删成「我在躲雨」 |
| segment_dedupe_keeps_answer | PASS | 分段整句去重删掉答案时，答案句要保留（不许只剩「你呢？」） |

每轮 prompt 与锁定快照不一致：0 轮；场景切换后 prompt 仍含上一场景：0 轮；守卫异常：0 轮。

## 8. 疑似根因（按证据强度排序）

1. 【已修复·问句意图覆盖】amo_question_guard._category 只认标准句式（几点回/在干嘛/吃了吗），「今晚回不回来」「到公司了吗」「你旁边有人吗」「你是不是在偷懒」认不出来 → 答非所问时兜不了底。本轮把这些口语问法归到同一意图，并加「第三人称作主语不算问她」（我同事摸鱼≠问她干嘛）。证据：failure probe_unanswered 31→0；context_checks.question_intent_coverage PASS。
2. 【已修复·分享接话劫持状态问句】amo_state_consistency._reply_demand 把「忙什么呢/你在哪呢」当成分享的后续追问，拿历史里带「刷到」的旧分享句把正常回复改写成「就是事的事，…」（guard_introduced 2 轮）。本轮：状态问句不参与分享接话 + 认不出对象时说「就是刚才那件事」。证据：guard_introduced 2→0；irrelevant 残留 24→0；context_checks.share_context_not_hijacked PASS。
3. 【已修复·动作 edge】sleep/health/rest 下「还在改方案」这类进行式工作断言被有意跳过（原设计只放行「刚开完会」这种近过去），独立 checker 仍判动作矛盾。本轮把进行式/近过去拆开：进行式按当前状态改口，近过去保留。证据：action 残留 5→0；context_checks.work_present_claim_while_sleeping PASS。
4. 【已修复·事实守卫空转】narrative_view 有事件但 now 落在两个日程块之间（早于第一块）时 activity 为空，事实守卫什么都不改（G46/t1 的「刚开完会」因此漏网）。本轮视图没有 current 时回落 state.current_activity。证据：矛盾轮 2→0。
5. 【已修复·地点方位语法】换/删地点短语时「我在家这边躲雨」被删成「我在躲雨」，把「在哪」的答案一起删掉。本轮换地点保留方位词（我在咖啡店这边躲雨）。证据：place 残留 2→0；context_checks.place_orient_keeps_answer PASS。
6. 【已修复·去重吃掉答案】分段整句去重/状态去重把「他这一问」的答案删掉后只剩通用短句（G09/t5 只剩「你呢？」、G43/t6·t9 短句兜底）。本轮出口问句守卫把删前答案要回来 + 分段去重保留答案句。证据：probe_unanswered 31→0；context_checks.segment_dedupe_keeps_answer PASS。
7. 【已修复·send 阶段旧问句】_send_to_umo 扫近聊取 user_text 时取到更早的 user 行，后置复检/问句补答拿着上一轮的旧问题判这一轮。本轮统一用本轮登记问题（_turn_question 口径）。证据：dedup_keeps_answer / status_dedup_keeps_answer PASS，clean_probe_unanswered 2→0。
8. 【口径·模拟 harness】模拟器聊天回复发送改为 source=chat（与生产 _send_now / imm_harness 一致）：不传会 fail-closed 成 push，出口人设守卫按主动消息口径把「写代码」这类词整条丢弃（对照组 G20/t2 空回复）。这是 harness 口径修正，不是放宽判据。
9. 【P2 残余·未修】two_readers_agree FAIL：get_current_action（JIT/照片链路）与 state.current_activity（聊天/守卫链路）仍可能两套「此刻」。
10. 【P2 残余·未修】narrative_place_cache_stale FAIL：narrative_view 的 2 秒缓存带旧 now，跨块短窗口仍返回旧地点。
11. 【P2 建议·未做】问句兜底仍是整条替换而不是追加，原句语气/反问会丢（「正洗澡，怎么啦？」→「我在洗澡。」）；建议 append，或只在完全无信息时替换。

## 9. 隔离与局限

- 真实模型探测：env provider=None；本机 ollama 对话模型=None；real_llm_layer.json 无 evidence
- 本报告确定性签名：d7822c347946d7e7（同参数、同代码重跑应一致；本轮连跑两次一致）
- 生产库指纹（运行前）：size=1204224 mtime=1795359879 sha256=f82cf88f7f24868a
- 生产库指纹（运行后）：size=1204224 mtime=1795359879 sha256=f82cf88f7f24868a
- 运行期拦截：AmoDB 实例化 0 次、指向生产库的 sqlite 连接 0 次（都应保持 0）。
- 所有状态（日程/kv/健康/历史/语气池）都在进程内存；报告只写 work/sim/。
- 没有接 AstrBot 事件循环、分段引擎、TTS、生图、记忆插件：segment_engine=None。
- 代码快照（本轮实际加载的守卫文件 sha256 前 16 位）：
  - amo_state_consistency.py：13ae05fd6070632a
  - tone_guard.py：89659d29759a8239
  - reply_fact_guard.py：e345a588dd566c2c
  - place_guard.py：2e85d04e3f1fd593
  - amo_reply_pipeline.py：9720b5c75c5b9f44
  - amo_diary_guard.py：0ae929c46c473768
  - amo_runtime_reply.py：fe96804cfe515ea0
  - amo_message.py：bfc560fdd7b7b02f
  - amo_current_action.py：7f067db87870d033
  - domains/reply/tone.py：2aaa7a239021eb2a
  - domains/reply/repetition_guard.py：cf6e8a1f665bc4b8
- 模拟 provider 只按模板拼句，**不能证明真实模型的语义质量**；真实模型复跑见 real_llm_layer.json。

## 10. 复现

```
D:/dafeiyu/uv/tools/astrbot/Scripts/python.exe work/sim/run_daily.py
D:/dafeiyu/uv/tools/astrbot/Scripts/python.exe work/sim/real_probe.py
```